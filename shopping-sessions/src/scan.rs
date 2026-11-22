//! Line scanner: for each line of a chunk, one pass of AVX2 compares finds the newline, the commas and the
//! quotes, applies the CSV dialect rules, and yields field bounds. No intermediate index.

use crate::util::{split, Chunk};

pub type Fields<const NF: usize> = [(u32, u32); NF];

/// The bytes of whole data lines that start in the chunk (header line excluded).
pub fn region(c: Chunk) -> &'static [u8] {
    let (data, a, b) = c;
    let p = if a == 0 {
        match memchr::memchr(b'\n', data) {
            Some(i) => i + 1,
            None => return &[],
        }
    } else if data[a - 1] == b'\n' {
        a
    } else {
        match memchr::memchr(b'\n', &data[a..]) {
            Some(i) => a + i + 1,
            None => return &[],
        }
    };
    let e = if b >= data.len() {
        data.len()
    } else if data[b - 1] == b'\n' {
        b
    } else {
        memchr::memchr(b'\n', &data[b..]).map_or(data.len(), |i| b + i + 1)
    };
    if p >= e {
        &[]
    } else {
        &data[p..e]
    }
}

#[cfg(target_feature = "avx2")]
#[inline(always)]
unsafe fn block(p: *const u8) -> (u64, u64, u64) {
    use std::arch::x86_64::*;
    let a = _mm256_loadu_si256(p as *const __m256i);
    let b = _mm256_loadu_si256(p.add(32) as *const __m256i);
    let cm = _mm256_set1_epi8(b',' as i8);
    let nm = _mm256_set1_epi8(b'\n' as i8);
    let qm = _mm256_set1_epi8(b'"' as i8);
    let m = |v, k| _mm256_movemask_epi8(_mm256_cmpeq_epi8(v, k)) as u32 as u64;
    (m(a, cm) | m(b, cm) << 32, m(a, nm) | m(b, nm) << 32, m(a, qm) | m(b, qm) << 32)
}

#[cfg(not(target_feature = "avx2"))]
#[inline(always)]
unsafe fn block(p: *const u8) -> (u64, u64, u64) {
    let (mut c, mut n, mut q) = (0u64, 0u64, 0u64);
    for i in 0..64 {
        match *p.add(i) {
            b',' => c |= 1 << i,
            b'\n' => n |= 1 << i,
            b'"' => q |= 1 << i,
            _ => {}
        }
    }
    (c, n, q)
}


/// Scan the line starting at `p`. Returns (line length without '\n', fields or None if malformed).
/// `buf` must have at least 128 readable bytes at `p`; bytes at or after `avail` must not be '\n', ',' or '"'
/// (zero padding), and `avail` is the number of real bytes.
#[inline(always)]
unsafe fn scan_line<const NF: usize>(p: *const u8, avail: usize, f: &mut Fields<NF>) -> (usize, bool) {
    let (c0, n0, q0) = block(p);
    let (c1, n1, q1);
    let l;
    if n0 != 0 {
        l = n0.trailing_zeros() as usize;
        (c1, n1, q1) = (0, 0, 0);
    } else {
        let t = block(p.add(64));
        (c1, n1, q1) = t;
        if n1 != 0 {
            l = 64 + n1.trailing_zeros() as usize;
        } else {
            // no newline within 128 bytes: last line without '\n', or a long line
            return slow_line::<NF>(p, avail, f);
        }
    }
    let (cm0, cm1, qm0, qm1) = if l < 64 {
        let m = (1u64 << l) - 1;
        (c0 & m, 0, q0 & m, 0)
    } else {
        let m = if l == 64 { 0 } else { (1u64 << (l - 64)) - 1 };
        (c0, c1 & m, q0, q1 & m)
    };
    let _ = n1;
    if (cm0.count_ones() + cm1.count_ones()) as usize != NF - 1 {
        return (l, false);
    }
    let (mut k, mut start) = (0usize, 0u32);
    for (mut m, base) in [(cm0, 0u32), (cm1, 64)] {
        while m != 0 {
            let e = base + m.trailing_zeros();
            *f.get_unchecked_mut(k) = (start, e);
            start = e + 1;
            k += 1;
            m &= m - 1;
        }
    }
    *f.get_unchecked_mut(k) = (start, l as u32);
    let nq = qm0.count_ones() + qm1.count_ones();
    if nq != 0 && !strip_quotes_masks::<NF>(f, cm0 as u128 | (cm1 as u128) << 64, qm0 as u128 | (qm1 as u128) << 64, l, nq) {
        return (l, false);
    }
    (l, true)
}

#[inline(always)]
fn pext128(x: u128, m: u128) -> u64 {
    use std::arch::x86_64::_pext_u64;
    unsafe { _pext_u64(x as u64, m as u64) | _pext_u64((x >> 64) as u64, (m >> 64) as u64) << (m as u64).count_ones() }
}

/// Same rule as `strip_quotes`, from the comma mask `cm` and quote mask `qm` of the line (bit i = byte i, line length
/// `l`): a field is quoted when it starts with a quote, and then needs two or more bytes and a closing quote;
/// the line has no other quotes.
#[inline(always)]
fn strip_quotes_masks<const NF: usize>(f: &mut Fields<NF>, cm: u128, qm: u128, l: usize, nq: u32) -> bool {
    let sb = cm << 1 | 1; // first byte of each field
    let eb = cm | 1u128 << l; // one past the last byte of each field
    let sq = qm & sb;
    let eq = qm << 1 & eb;
    let sqi = pext128(sq, sb);
    let eqi = pext128(eq, eb);
    if nq != 2 * sq.count_ones() || sqi & !eqi != 0 || sq << 1 & eb != 0 {
        return false;
    }
    for (i, x) in f.iter_mut().enumerate() {
        let q = (sqi >> i) as u32 & 1;
        x.0 += q;
        x.1 -= q;
    }
    true
}

/// Lines with no newline in 128 bytes: find it the slow way (or take the rest of the data).
#[cold]
#[inline(never)]
unsafe fn slow_line<const NF: usize>(p: *const u8, avail: usize, g: &mut Fields<NF>) -> (usize, bool) {
    let rest = std::slice::from_raw_parts(p, avail);
    let l = memchr::memchr(b'\n', rest).unwrap_or(avail);
    let mut f = [(0usize, 0usize); NF];
    if split::<NF>(&rest[..l], &mut f) {
        for i in 0..NF {
            g[i] = (f[i].0 as u32, f[i].1 as u32);
        }
        (l, true)
    } else {
        (l, false)
    }
}

const BATCH: usize = 16;

/// Run `f(line, fields, uh)` on every well-formed line of the chunk. `line` starts at the line's first byte.
/// If `PRE`, `uh` is the hash of field 1 (the user id) and its link-table slot is prefetched a batch ahead.
#[inline(always)]
pub fn run_chunk<const NF: usize, const PRE: bool>(
    c: Chunk,
    hash_pre: impl Fn(&'static [u8]) -> u64,
    prefetch: impl Fn(u64),
    f: impl FnMut(&'static [u8], &Fields<NF>, u64),
) {
    let r = region(c);
    if !NOPOP.load(std::sync::atomic::Ordering::Relaxed) && !r.is_empty() {
        populate(r);
    }
    run_region::<NF, PRE>(r, hash_pre, prefetch, f)
}

/// Same, over the lines of an already loaded region.
#[inline(always)]
pub fn run_region<const NF: usize, const PRE: bool>(
    r: &'static [u8],
    hash_pre: impl Fn(&'static [u8]) -> u64,
    prefetch: impl Fn(u64),
    mut f: impl FnMut(&'static [u8], &Fields<NF>, u64),
) {
    if r.is_empty() {
        return;
    }
    let len = r.len();
    let mut p = 0usize;
    // lines whose 128-byte window would run past the region go through a zero-padded copy
    let tail_start = len.saturating_sub(128);
    let mut bf: [Fields<NF>; BATCH] = [[(0, 0); NF]; BATCH];
    let mut bq = [(0u32, 0u64); BATCH];
    let mut bn;
    while p < tail_start {
        bn = 0;
        let mut q = p;
        while bn < BATCH && q < tail_start {
            let (l, ok) = unsafe { scan_line::<NF>(r.as_ptr().add(q), len - q, &mut bf[bn]) };
            if ok {
                let fl = &bf[bn];
                let uh = if PRE {
                    let u = &r[q + fl[1].0 as usize..q + fl[1].1 as usize];
                    let h = hash_pre(u);
                    prefetch(h);
                    if NF == 2 {
                        // link lines name two IDs: prefetch the first one's slot too
                        prefetch(hash_pre(&r[q + fl[0].0 as usize..q + fl[0].1 as usize]));
                    }
                    h
                } else {
                    0
                };
                bq[bn] = (q as u32, uh);
                bn += 1;
            }
            q += l + 1;
        }
        for i in 0..bn {
            f(&r[bq[i].0 as usize..], &bf[i], bq[i].1);
        }
        p = q;
    }
    if p < len {
        // tail: copy into a padded buffer for scanning; fields are relative to the line start, so the real region is used for data
        let mut buf = [0u8; 384];
        let rem = len - p;
        buf[..rem].copy_from_slice(&r[p..]);
        let mut q = 0usize;
        while q < rem {
            let mut fl: Fields<NF> = [(0, 0); NF];
            let (l, ok) = unsafe { scan_line::<NF>(buf.as_ptr().add(q), rem - q, &mut fl) };
            if ok {
                let uh = if PRE { hash_pre(&r[p + q + fl[1].0 as usize..p + q + fl[1].1 as usize]) } else { 0 };
                f(&r[p + q..], &fl, uh);
            }
            q += l + 1;
        }
    }
}

pub static NOPOP: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(true); // per-chunk MADV_POPULATE_READ measured slower than plain faults

/// Map the chunk's pages in one call instead of faulting them in 64 KB at a time.
fn populate(r: &[u8]) {
    const MADV_POPULATE_READ: i32 = 22;
    let a = r.as_ptr() as usize & !4095;
    let e = (r.as_ptr() as usize + r.len() + 4095) & !4095;
    unsafe { libc::madvise(a as *mut _, e - a, MADV_POPULATE_READ) };
}

#[cfg(test)]
mod fuzz {
    use super::*;

    fn check<const NF: usize>(line: &[u8]) {
        let mut buf = vec![0u8; line.len() + 1 + 256];
        buf[..line.len()].copy_from_slice(line);
        buf[line.len()] = b'\n';
        let mut fl: Fields<NF> = [(0, 0); NF];
        let (l, ok) = unsafe { scan_line::<NF>(buf.as_ptr(), line.len() + 1, &mut fl) };
        let got = ok.then_some(fl);
        assert_eq!(l, line.len());
        let mut f = [(0usize, 0usize); NF];
        let want = split::<NF>(line, &mut f).then(|| f.map(|(a, b)| (a as u32, b as u32)));
        assert_eq!(got, want, "line {:?}", String::from_utf8_lossy(line));
    }

    #[test]
    fn scan_matches_split() {
        let mut x = 0x9E37_79B9_7F4A_7C15u64;
        let alphabet = [b'a', b'b', b',', b'"', b'1', b'"', b','];
        for n in 0..400_000 {
            x ^= x << 13;
            x ^= x >> 7;
            x ^= x << 17;
            let len = (x % 120) as usize;
            let mut y = x;
            let line: Vec<u8> = (0..len)
                .map(|_| {
                    y = y.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
                    alphabet[(y >> 33) as usize % alphabet.len()]
                })
                .collect();
            match n % 3 {
                0 => check::<2>(&line),
                1 => check::<4>(&line),
                _ => check::<7>(&line),
            }
        }
        // well-formed quoted lines
        for q in 0..128u32 {
            let mut line = Vec::new();
            for i in 0..7 {
                if i > 0 {
                    line.push(b',');
                }
                if q >> i & 1 == 1 {
                    line.push(b'"');
                }
                line.extend_from_slice(b"abcdef"[..(i % 4)].as_ref());
                if q >> i & 1 == 1 {
                    line.push(b'"');
                }
            }
            check::<7>(&line);
        }
    }
}

#[cfg(test)]
mod bench {
    use super::*;

    /// `cargo test --release bench_scan -- --ignored --nocapture` with SCAN_FILE=<events csv>
    #[test]
    #[ignore]
    fn bench_scan() {
        let Ok(path) = std::env::var("SCAN_FILE") else { return };
        let mut v = std::fs::read(path).unwrap();
        v.truncate(48 << 20);
        let end = v.iter().rposition(|&b| b == b'\n').unwrap() + 1;
        v.truncate(end);
        let r: &'static [u8] = Box::leak(v.into_boxed_slice());
        let mut best = f64::MAX;
        for _ in 0..7 {
            let t = std::time::Instant::now();
            let (mut lines, mut sum) = (0u64, 0u64);
            run_region::<7, false>(r, |_| 0, |_| {}, |l, f, _| {
                lines += 1;
                sum += (f[3].1 - f[3].0) as u64 + f[6].0 as u64 + l[0] as u64;
            });
            std::hint::black_box(sum);
            let ns = t.elapsed().as_secs_f64() * 1e9 / lines as f64;
            best = best.min(ns);
        }
        eprintln!("scan: {best:.1} ns/line, {} bytes/line", r.len() as f64 / r.iter().filter(|&&b| b == b'\n').count() as f64);
    }
}
