//! Per-person sessionization, click credit/assists and aggregation.

use crate::bucket::Bucket;
use crate::ingest::*;
use crate::util::*;

const GAP_MS: i64 = 30 * 60 * 1000;
pub const NO_CAT: u16 = u16::MAX;

/// user_daily rows, stored column-wise as they are produced. Rows of one person are contiguous, so the user_id
/// column is a dictionary of names (PLAIN, length-prefixed) plus the number of rows per person.
#[derive(Default)]
pub struct UserCols {
    pub n: usize,
    pub names: Vec<u8>,
    pub person_rows: Vec<u32>,
    pub day: Vec<u32>,
    pub sessions: Vec<u32>,
    pub events: Vec<u32>,
    pub rev: Vec<u64>,
    pub has_cat: Vec<bool>,
    pub cat: Vec<u16>,
}

struct Sess {
    start: i64,
    day: u32,
    rev: u64,
    purch: bool,
}

pub struct State {
    pub ws: i64,
    pub dbase: i64,
    pub nday: usize,
    pub organic: u32,
    pub camp: Vec<[u64; 4]>, // [(campaign, day)] -> sessions, purchasing, revenue, assisted
    pub cols: UserCols,
    sess: Vec<Sess>,
    dfirst: Vec<usize>,
    covered: Vec<u32>,
    touched: Vec<u32>,
    cat_sum: Vec<u64>,
    cat_touched: Vec<u16>,
    // per-bucket scratch
    off: Vec<u32>,
    pos: Vec<u32>,
    coff: Vec<u32>,
    sorted: Vec<u64>, // ts << 32 | pc
    csorted: Vec<CRec>,
}

impl State {
    pub fn new(ws: i64, dbase: i64, nday: usize, ncamp: usize, ncat: usize, est_rows: usize) -> State {
        State {
            ws,
            dbase,
            nday,
            organic: ncamp as u32,
            camp: vec![[0; 4]; (ncamp + 1) * nday],
            cols: UserCols {
                n: 0,
                names: Vec::with_capacity(est_rows * 4),
                person_rows: Vec::with_capacity(est_rows / 3),
                day: Vec::with_capacity(est_rows),
                sessions: Vec::with_capacity(est_rows),
                events: Vec::with_capacity(est_rows),
                rev: Vec::with_capacity(est_rows),
                has_cat: Vec::with_capacity(est_rows),
                cat: Vec::with_capacity(est_rows),
            },
            sess: vec![],
            dfirst: vec![0; nday + 2],
            covered: vec![0; ncamp + 1],
            touched: vec![],
            cat_sum: vec![0; ncat],
            cat_touched: vec![],
            off: vec![],
            pos: vec![],
            coff: vec![],
            sorted: vec![],
            csorted: vec![],
        }
    }

    /// Group one person bucket's events and clicks per person (dense local id = pid >> PB) and process each person.
    /// `nlocal` is the number of local ids, `names` maps pid to the packed name.
    pub fn bucket(&mut self, b: usize, nlocal: usize, pvs: &[&Bucket<PRec>], cks: &[&Bucket<CRec>], names: &crate::links::Links) {
        let nc: usize = cks.iter().map(|v| v.len()).sum();
        // events: count per person, prefix sums, scatter
        self.off.clear();
        self.off.resize(nlocal + 1, 0);
        for v in pvs {
            for r in v.slices().flatten() {
                if r.pc != DEAD {
                    self.off[(r.pid >> PB) as usize + 1] += 1;
                }
            }
        }
        for i in 0..nlocal {
            self.off[i + 1] += self.off[i];
        }
        let live = self.off[nlocal] as usize;
        self.pos.clear();
        self.pos.extend_from_slice(&self.off[..nlocal]);
        self.sorted.clear();
        self.sorted.reserve(live);
        unsafe { self.sorted.set_len(live) };
        for v in pvs {
            for r in v.slices().flatten() {
                if r.pc != DEAD {
                    let l = (r.pid >> PB) as usize;
                    unsafe { *self.sorted.get_unchecked_mut(*self.pos.get_unchecked(l) as usize) = (r.ts as u64) << 32 | r.pc as u64 };
                    self.pos[l] += 1;
                }
            }
        }
        // clicks
        self.coff.clear();
        self.coff.resize(nlocal + 1, 0);
        for v in cks {
            for c in v.slices().flatten() {
                self.coff[(c.pid >> PB) as usize + 1] += 1;
            }
        }
        for i in 0..nlocal {
            self.coff[i + 1] += self.coff[i];
        }
        self.pos.clear();
        self.pos.extend_from_slice(&self.coff[..nlocal]);
        self.csorted.clear();
        self.csorted.reserve(nc);
        unsafe { self.csorted.set_len(nc) };
        for v in cks {
            for c in v.slices().flatten() {
                let l = (c.pid >> PB) as usize;
                unsafe { *self.csorted.get_unchecked_mut(*self.pos.get_unchecked(l) as usize) = *c };
                self.pos[l] += 1;
            }
        }
        let mut sorted = std::mem::take(&mut self.sorted);
        let mut csorted = std::mem::take(&mut self.csorted);
        for l in 0..nlocal {
            let (a, z) = (self.off[l] as usize, self.off[l + 1] as usize);
            if a == z {
                continue;
            }
            let pid = (l << PB) | b;
            let evs = &mut sorted[a..z];
            let cks = &mut csorted[self.coff[l] as usize..self.coff[l + 1] as usize];
            evs.sort_unstable();
            cks.sort_unstable_by_key(|r| r.ts);
            self.person(names.name(pid), evs, cks);
        }
        self.sorted = sorted;
        self.csorted = csorted;
    }

    fn flush_day(&mut self, day: i32, sessions: u32, events: u32, rev: u64) {
        let mut cat = NO_CAT;
        if !self.cat_touched.is_empty() {
            let mut best = 0;
            for &k in &self.cat_touched {
                let s = self.cat_sum[k as usize];
                if cat == NO_CAT || s > best || (s == best && k < cat) {
                    cat = k;
                    best = s;
                }
            }
            for &k in &self.cat_touched {
                self.cat_sum[k as usize] = 0;
            }
            self.cat_touched.clear();
        }
        let c = &mut self.cols;
        c.day.push(day as u32);
        c.sessions.push(sessions);
        c.events.push(events);
        c.rev.push(rev);
        c.has_cat.push(cat != NO_CAT);
        if cat != NO_CAT {
            c.cat.push(cat);
        }
        c.n += 1;
    }

    fn person(&mut self, name: u64, evs: &[u64], cks: &[CRec]) {
        let nday = self.nday;
        let ws = self.ws;
        let cbase = ws - WEEK_MS;
        self.sess.clear();
        let first_row = self.cols.n;
        let (mut uday, mut us, mut ue, mut ur) = (-1i32, 0u32, 0u32, 0u64);
        let mut prev = u32::MAX;
        for &k in evs {
            let e = PRec { pid: 0, ts: (k >> 32) as u32, pc: k as u32 };
            if prev == u32::MAX || (e.ts - prev) as i64 > GAP_MS {
                let start = ws + e.ts as i64;
                let day = (start.div_euclid(DAY_MS) - self.dbase) as i32;
                if day != uday {
                    if uday >= 0 {
                        self.flush_day((uday as i64 + self.dbase) as i32, us, ue, ur);
                    }
                    uday = day;
                    us = 0;
                    ue = 0;
                    ur = 0;
                }
                us += 1;
                self.sess.push(Sess { start, day: day as u32, rev: 0, purch: false });
            }
            prev = e.ts;
            ue += 1;
            if e.ty() == 2 {
                let price = e.price();
                ur += price;
                let s = self.sess.last_mut().unwrap();
                s.rev += price;
                s.purch = true;
                let k = e.cat() as usize;
                if self.cat_sum[k] == 0 {
                    self.cat_touched.push(e.cat());
                }
                self.cat_sum[k] += price;
            }
        }
        self.flush_day((uday as i64 + self.dbase) as i32, us, ue, ur);
        let nm = unpack(name);
        let c = &mut self.cols;
        c.person_rows.push((c.n - first_row) as u32);
        c.names.extend_from_slice(&(nm.len() as u32).to_le_bytes());
        c.names.extend_from_slice(nm);

        // credit
        let mut ci = 0;
        for s in &self.sess {
            while ci < cks.len() && cbase + cks[ci].ts as i64 <= s.start {
                ci += 1;
            }
            let mut camp = self.organic;
            if ci > 0 && s.start - (cbase + cks[ci - 1].ts as i64) <= WEEK_MS {
                let cts = cks[ci - 1].ts;
                let mut best = ci - 1;
                let mut j = ci - 1;
                while j > 0 && cks[j - 1].ts == cts {
                    j -= 1;
                    if unpack(cks[j].cid) < unpack(cks[best].cid) {
                        best = j;
                    }
                }
                camp = cks[best].camp;
            }
            let a = &mut self.camp[camp as usize * nday + s.day as usize];
            a[0] += 1;
            a[1] += s.purch as u64;
            a[2] += s.rev;
        }
        if cks.is_empty() {
            return;
        }

        // assists
        let n = self.sess.len();
        let mut p = 0;
        for d in 0..=nday + 1 {
            while p < n && (self.sess[p].day as usize) < d {
                p += 1;
            }
            self.dfirst[d] = p;
        }
        let (mut lo, mut hi) = (0usize, 0usize);
        for c in cks {
            let cts = cbase + c.ts as i64;
            while lo < n && self.sess[lo].start < cts {
                lo += 1;
            }
            while hi < n && self.sess[hi].start <= cts + WEEK_MS {
                hi += 1;
            }
            let k = c.camp as usize;
            let a = lo.max(self.covered[k] as usize);
            if a < hi {
                if self.covered[k] == 0 {
                    self.touched.push(c.camp);
                }
                self.covered[k] = hi as u32;
                let (da, db) = (self.sess[a].day as usize, self.sess[hi - 1].day as usize);
                for d in da..=db {
                    let l = a.max(self.dfirst[d]);
                    let h = hi.min(self.dfirst[d + 1]);
                    if h > l {
                        self.camp[k * nday + d][3] += (h - l) as u64;
                    }
                }
            }
        }
        for &k in &self.touched {
            self.covered[k as usize] = 0;
        }
        self.touched.clear();
    }
}
