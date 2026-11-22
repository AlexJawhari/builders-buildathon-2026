//! Shopping Sessions pipeline: mmap CSVs, parse in parallel, dedup, sessionize, write Parquet.

mod bucket;
mod encode;
mod ingest;
mod links;
mod scan;
mod parquet;
mod prof;
mod sessions;
mod util;

use bucket::Bucket;
use encode::*;
use ingest::*;
use scan::*;
use parquet::*;
use sessions::*;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering::Relaxed};
use util::*;

#[global_allocator]
static A: mimalloc::MiMalloc = mimalloc::MiMalloc;

/// Run `f(thread_index)` on n scoped threads and collect results.
fn par<R: Send>(n: usize, f: impl Fn(usize) -> R + Sync) -> Vec<R> {
    std::thread::scope(|s| {
        let hs: Vec<_> = (0..n).map(|t| s.spawn({ let f = &f; move || f(t) })).collect();
        hs.into_iter().map(|h| h.join().unwrap()).collect()
    })
}

fn json_str(m: &str, key: &str) -> i64 {
    let i = m.find(key).expect("manifest key");
    let rest = &m[i + key.len()..];
    let rest = &rest[rest.find(':').unwrap()..];
    let q1 = rest.find('"').unwrap();
    let rest = &rest[q1 + 1..];
    let q2 = rest.find('"').unwrap();
    parse_ts(rest[..q2].as_bytes()).expect("manifest ts")
}

fn read_pairs(path: &Path) -> Vec<(&'static [u8], &'static [u8])> {
    let data = mmap(path);
    let mut v = vec![];
    for c in chunks(&[data], usize::MAX) {
        for_lines(c, |l| {
            let mut f = [(0, 0); 2];
            if split(l, &mut f) {
                v.push((&l[f[0].0..f[0].1], &l[f[1].0..f[1].1]));
            }
        });
    }
    v
}

fn main() {
    let t0 = std::time::Instant::now();
    let profiling = std::env::var_os("SHOP_PROF").is_some();
    if profiling {
        prof::start();
    }
    let tm = std::env::var_os("SHOP_TIMING").is_some();
    let lap = |what: &str| {
        if tm {
            let mut ru: libc::rusage = unsafe { std::mem::zeroed() };
            unsafe { libc::getrusage(libc::RUSAGE_SELF, &mut ru) };
            let tv = |t: libc::timeval| t.tv_sec as f64 * 1e3 + t.tv_usec as f64 / 1e3;
            eprintln!("{:7.1} ms wall  cpu user {:7.1} sys {:7.1} flt {}  {what}", t0.elapsed().as_secs_f64() * 1e3, tv(ru.ru_utime), tv(ru.ru_stime), ru.ru_minflt);
        }
    };
    let env = |k: &str, d: &str| std::env::var(k).unwrap_or_else(|_| d.to_string());
    let data = PathBuf::from(env("DATA_DIR", "/data"));
    let out = PathBuf::from(env("OUTPUT_DIR", "/output"));
    let nt: usize = env("NCPU", "0").parse().ok().filter(|&n| n > 0).unwrap_or_else(|| {
        std::thread::available_parallelism().map_or(8, |n| n.get())
    });

    let nt = nt.min(15);
    let manifest = std::fs::read_to_string(data.join("manifest.json")).unwrap();
    let ws = json_str(&manifest, "window_start");
    let we = json_str(&manifest, "window_end");
    let dbase = ws.div_euclid(DAY_MS);
    let nday = ((we - 1).div_euclid(DAY_MS) - dbase + 1).max(1) as usize;

    // products -> category ids ranked in byte order (with "unknown")
    let prods = read_pairs(&data.join("products.csv"));
    let mut cats: Vec<&[u8]> = prods.iter().map(|p| p.1).collect();
    cats.push(b"unknown");
    cats.sort();
    cats.dedup();
    assert!(we - ws + WEEK_MS < u32::MAX as i64 && cats.len() < 63, "unsupported window length or category count");
    let cat_id = |c: &[u8]| cats.binary_search(&c).unwrap() as u32;
    let mut pmap = SmallMap::new(prods.len());
    for (p, c) in &prods {
        pmap.insert(hash(p), cat_id(c));
    }
    let camps = read_pairs(&data.join("campaigns.csv"));
    let mut cmap = SmallMap::new(camps.len());
    for (i, (c, _)) in camps.iter().enumerate() {
        cmap.insert(hash(c), i as u32);
    }

    let rio = read_io();
    let ev_files = csv_inputs(&data.join("events"), !rio);
    let ck_files = csv_inputs(&data.join("clicks"), !rio);
    let lk_files = csv_files(&data.join("links"));
    let ch: usize = std::env::var("SHOP_CHUNK").ok().and_then(|v| v.parse().ok()).unwrap_or(256 << 10);
    let lk_chunks = chunks(&lk_files, ch);

    let est = |files: &[Input]| -> usize {
        // events or clicks per thread, from the average line length of the first non-empty file
        let total: usize = files.iter().map(|f| f.len).sum();
        let Some(f) = files.iter().find(|f| f.len > 4096) else { return 0 };
        let sample = f.sample();
        let lines = memchr::memchr_iter(b'\n', &sample).count().max(1);
        (total as f64 / (sample.len() as f64 / lines as f64) * 1.15) as usize / nt
    };
    let (est_ev, est_ck) = (est(&ev_files), est(&ck_files));
    // size the link table from the number of lines
    let next = AtomicUsize::new(0);
    let counts = par(nt, |_| {
        let mut n = 0;
        loop {
            let i = next.fetch_add(1, Relaxed);
            if i >= lk_chunks.len() {
                return n;
            }
            let (d, a, b) = lk_chunks[i];
            n += memchr::memchr_iter(b'\n', &d[a..b]).count();
        }
    });
    let nlines: usize = counts.iter().sum::<usize>() + lk_chunks.len();
    let cx = Cx {
        ws,
        we,
        links: links::Links::new(nlines * 2 + (est_ev + est_ck) * nt / 40 + 4096, nlines * 2 + (est_ev + est_ck) * nt / 6 + 4096),
        prods: pmap,
        camps: cmap,
        unknown_cat: cat_id(b"unknown"),
    };

    lap("setup");
    // phase A: links
    let next = AtomicUsize::new(0);
    let fresh: Vec<Vec<u32>> = par(nt, |_| {
        let mut fresh = Vec::new();
        loop {
            let i = next.fetch_add(1, Relaxed);
            if i >= lk_chunks.len() {
                return fresh;
            }
            run_chunk::<2, true>(lk_chunks[i], hash, |h| cx.links.prefetch(h), |l, f, _| link_line(l, f, &cx, &mut fresh));
        }
    });
    lap("links parsed");
    let tmp = cx.links.tmp();
    par(nt, |t| cx.links.assign_roots(&fresh[t], &tmp));
    par(nt, |t| cx.links.assign_all(&fresh[t], &tmp));
    drop(tmp);
    drop(fresh);

    lap("links flattened");
    // phase B: parse events and clicks
    let ev_chunks = input_chunks(&ev_files, ch);
    let ck_chunks = input_chunks(&ck_files, ch);
    scan::NOPOP.store(std::env::var_os("SHOP_NOPOP").is_some(), Relaxed);
    let pool_bytes = nt
        * (est_ev * (std::mem::size_of::<PRec>() + std::mem::size_of::<Slim>())
            + est_ck * std::mem::size_of::<CRec>()
            + (est_ev / 6 + est_ck) * 16 // copied user and click ids
            + (2 * P2 + P1) * 4096
            + (2 << 20));
    let pool = bucket::Pool::new(pool_bytes);
    let next = AtomicUsize::new(0);
    let total = ev_chunks.len() + ck_chunks.len();
    let locals: Vec<Local> = par(nt, |t| {
        pool.populate(t, nt);
        let mut l = Local::new(t, pool);
        let mut buf: Vec<u8> = Vec::new();
        loop {
            let i = next.fetch_add(1, Relaxed);
            if i >= total {
                return l;
            }
            if i < ev_chunks.len() {
                let (fi, a, b) = ev_chunks[i];
                let pre = |h| cx.links.prefetch(h);
                let f = |ln: &'static [u8], fl: &_, uh| event_line(ln, fl, uh, &cx, &mut l);
                if rio {
                    run_region::<7, true>(read_region(&ev_files[fi], a, b, &mut buf), hash, pre, f);
                } else {
                    run_chunk::<7, true>((ev_files[fi].map, a, b), hash, pre, f);
                    drop_pages(ev_files[fi].map, a, b);
                }
            } else {
                let (fi, a, b) = ck_chunks[i - ev_chunks.len()];
                let pre = |h| cx.links.prefetch(h);
                let f = |ln: &'static [u8], fl: &_, uh| click_line(ln, fl, uh, &cx, &mut l);
                if rio {
                    run_region::<4, true>(read_region(&ck_files[fi], a, b, &mut buf), hash, pre, f);
                } else {
                    run_chunk::<4, true>((ck_files[fi].map, a, b), hash, pre, f);
                    drop_pages(ck_files[fi].map, a, b);
                }
            }
        }
    });
    lap("parse");
    if tm {
        eprintln!("pool used/cap MB: {} / {}", pool.stats().0 >> 20, pool.stats().1 >> 20);
    }

    // phase C: dedup by event id; losing copies are marked DEAD in their person bucket
    let next = AtomicUsize::new(0);
    par(nt, |_| {
        let mut table: Vec<u32> = vec![];
        let kill = |meta: u64| {
            if meta & META_INVALID == 0 {
                let (i, b, t) = ((meta & 0xFF_FFFF) as usize, ((meta >> 24) & ((1 << PB) - 1)) as usize, ((meta >> 34) & 15) as usize);
                unsafe { (*locals[t].pv[b].ptr(i)).pc = DEAD };
            }
        };
        loop {
            let p = next.fetch_add(1, Relaxed);
            if p >= P1 {
                return;
            }
            let n: usize = locals.iter().map(|l| l.sl[p].len()).sum();
            let cap = (n * 2).next_power_of_two().max(16);
            let mask = cap - 1;
            table.clear();
            table.resize(cap, 0);
            for (t, l) in locals.iter().enumerate() {
                let mut i = 0usize;
                for r in l.sl[p].slices().flatten() {
                    let (h, meta, ing) = (r.h, r.meta, r.ingest);
                    let mut s = h as usize & mask;
                    loop {
                        let e = table[s];
                        if e == 0 {
                            table[s] = ((t as u32) << 28 | i as u32) + 1;
                            break;
                        }
                        let e = e - 1;
                        let o = locals[(e >> 28) as usize].sl[p].get((e & 0x0FFF_FFFF) as usize);
                        if o.h == h && (o.meta >> 39) == (meta >> 39) {
                            if ing < o.ingest {
                                kill(o.meta);
                                table[s] = ((t as u32) << 28 | i as u32) + 1;
                            } else {
                                kill(meta);
                            }
                            break;
                        }
                        s = (s + 1) & mask;
                    }
                    i += 1;
                }
            }
        }
    });
    lap("dedup");

    // phase D: sessionize per person bucket, write user_daily parts
    // A reused output dir may hold part files of an earlier run (a thread without rows writes no file), which
    // would be read as duplicate rows: start every table folder empty of *.parquet.
    for t in ["user_daily", "campaign_daily"] {
        let d = out.join(t);
        std::fs::create_dir_all(&d).unwrap();
        for e in std::fs::read_dir(&d).unwrap().flatten() {
            if e.path().extension().is_some_and(|x| x == "parquet") {
                std::fs::remove_file(e.path()).unwrap();
            }
        }
    }
    let mut order: Vec<(usize, usize)> =
        (0..P2).map(|b| (locals.iter().map(|l| l.pv[b].len() + l.ck[b].len()).sum::<usize>(), b)).collect();
    order.sort_unstable_by(|a, b| b.cmp(a));
    let nlocal = (cx.links.npid() + P2 - 1) >> PB;
    let next = AtomicUsize::new(0);
    let states: Vec<State> = par(nt, |t| {
        let mut st = State::new(ws, dbase, nday, camps.len(), cats.len(), est_ev * 3 / 20);
        loop {
            let i = next.fetch_add(1, Relaxed);
            if i >= P2 {
                break;
            }
            let b = order[i].1;
            let pv: Vec<&Bucket<PRec>> = locals.iter().map(|l| &l.pv[b]).collect();
            let cv: Vec<&Bucket<CRec>> = locals.iter().map(|l| &l.ck[b]).collect();
            st.bucket(b, nlocal, &pv, &cv, &cx.links);
        }
        write_user(&out.join("user_daily").join(format!("part-{t}.parquet")), &st.cols, &cats, t == 0);
        st
    });
    lap("sessions+user parquet");

    // campaign table
    let mut rows: Vec<(usize, usize, [u64; 4])> = vec![];
    for k in 0..=camps.len() {
        for d in 0..nday {
            let mut a = [0u64; 4];
            for s in &states {
                for i in 0..4 {
                    a[i] += s.camp[k * nday + d][i];
                }
            }
            if a[0] > 0 || a[3] > 0 {
                rows.push((k, d, a));
            }
        }
    }
    let mut ids = Vec::new();
    let mut chans = Vec::new();
    let mut days = Vec::new();
    let mut nums: [Vec<u8>; 4] = Default::default();
    for (k, d, a) in &rows {
        let (id, ch): (&[u8], &[u8]) = if *k < camps.len() { camps[*k] } else { (b"organic", b"organic") };
        ids.extend_from_slice(&(id.len() as u32).to_le_bytes());
        ids.extend_from_slice(id);
        chans.extend_from_slice(&(ch.len() as u32).to_le_bytes());
        chans.extend_from_slice(ch);
        days.extend_from_slice(&((dbase + *d as i64) as i32).to_le_bytes());
        for i in 0..4 {
            nums[i].extend_from_slice(&(a[i] as i64).to_le_bytes());
        }
    }
    let n = rows.len();
    let col = |name, ptype, conv, data| mkcol(n, name, ptype, conv, data);
    let [n0, n1, n2, n3] = nums;
    write(
        &out.join("campaign_daily").join("part-0.parquet"),
        n,
        &[
            col("campaign_id", T_BYTES, Some(CONV_UTF8), &ids),
            col("channel", T_BYTES, Some(CONV_UTF8), &chans),
            col("day", T_I32, Some(CONV_DATE), &days),
            col("sessions", T_I64, None, &n0),
            col("purchasing_sessions", T_I64, None, &n1),
            col("revenue_cents", T_I64, None, &n2),
            col("assisted_sessions", T_I64, None, &n3),
        ],
    );
    lap("done");
    if profiling {
        prof::dump();
    }
    std::process::exit(0);
}

/// Dictionary-encode `vals` (written as `width` little-endian bytes per dictionary entry).
fn int_col(vals: impl Iterator<Item = u64>, width: usize) -> (Vec<u8>, usize, Vec<u8>) {
    let mut d = Dict::new();
    let idx: Vec<u32> = vals.map(|v| d.id(v)).collect();
    let bw = bit_width(d.values.len());
    let mut data = vec![bw as u8];
    hybrid(&idx, bw, &mut data);
    let dict = d.values.iter().flat_map(|v| v.to_le_bytes()[..width].to_vec()).collect();
    (dict, d.values.len(), data)
}

fn write_user(path: &Path, c: &UserCols, cats: &[&[u8]], force: bool) {
    if c.n == 0 && !force {
        return;
    }
    let n = c.n;
    // user_id: one dictionary entry per person, indices are runs
    let mut uidx = Vec::with_capacity(n);
    for (p, &k) in c.person_rows.iter().enumerate() {
        uidx.extend(std::iter::repeat(p as u32).take(k as usize));
    }
    let ubw = bit_width(c.person_rows.len());
    let mut udata = vec![ubw as u8];
    hybrid(&uidx, ubw, &mut udata);
    drop(uidx);
    let (dd, dn, dday) = int_col(c.day.iter().map(|&v| v as u64), 4);
    let (sd, sn, dses) = int_col(c.sessions.iter().map(|&v| v as u64), 4);
    let (ed, en, deve) = int_col(c.events.iter().map(|&v| v as u64), 4);
    let (rd, rn, drev) = int_col(c.rev.iter().copied(), 8);
    // top_category: dictionary of all category names, indices for the non-null rows
    let mut cdict = vec![];
    for name in cats {
        cdict.extend_from_slice(&(name.len() as u32).to_le_bytes());
        cdict.extend_from_slice(name);
    }
    let cbw = bit_width(cats.len());
    let mut cdata = def_levels(&c.has_cat);
    cdata.push(cbw as u8);
    let cidx: Vec<u32> = c.cat.iter().map(|&k| k as u32).collect();
    hybrid(&cidx, cbw, &mut cdata);
    let col = |name, ptype, conv, dict, entries, data| dictcol(n, name, ptype, conv, dict, entries, data);
    write(
        path,
        n,
        &[
            col("user_id", T_BYTES, Some(CONV_UTF8), &c.names, c.person_rows.len(), &udata),
            col("day", T_I32, Some(CONV_DATE), &dd, dn, &dday),
            col("sessions", T_I32, None, &sd, sn, &dses),
            col("events", T_I32, None, &ed, en, &deve),
            col("revenue_cents", T_I64, None, &rd, rn, &drev),
            Col { name: "top_category", ptype: T_BYTES, conv: Some(CONV_UTF8), optional: true, data: &cdata, dict: Some((&cdict, cats.len())), nvalues: n },
        ],
    );
}

fn dictcol<'a>(n: usize, name: &'static str, ptype: i32, conv: Option<i32>, dict: &'a [u8], entries: usize, data: &'a [u8]) -> Col<'a> {
    Col { name, ptype, conv, optional: false, data, dict: Some((dict, entries)), nvalues: n }
}

fn mkcol<'a>(n: usize, name: &'static str, ptype: i32, conv: Option<i32>, data: &'a [u8]) -> Col<'a> {
    Col { name, ptype, conv, optional: false, data, dict: None, nvalues: n }
}

/// Unmap the pages of a finished chunk (page cache keeps the data) so the kernel's teardown work is spread over
/// the worker threads now instead of running serially at process exit.
fn drop_pages(map: &'static [u8], a: usize, b: usize) {
    let (lo, hi) = ((a + 4095) & !4095, b & !4095);
    if hi > lo {
        unsafe { libc::madvise(map.as_ptr().add(lo) as *mut _, hi - lo, libc::MADV_DONTNEED) };
    }
}
