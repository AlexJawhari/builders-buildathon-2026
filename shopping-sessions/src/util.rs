//! Byte-level helpers: hashing, CSV field splitting, timestamps, mmap.

use std::fs::File;
use std::os::unix::io::AsRawFd;

const K0: u64 = 0x9E37_79B9_7F4A_7C15;
const K1: u64 = 0xD6E8_FEB8_6659_FD93;
const K2: u64 = 0xA076_1D64_78BD_642F;
const K3: u64 = 0xE703_7ED1_A0B4_28DB;

#[inline(always)]
fn mum(a: u64, b: u64) -> u64 {
    let r = (a as u128).wrapping_mul(b as u128);
    (r as u64) ^ ((r >> 64) as u64)
}

#[inline(always)]
fn rd8(b: &[u8], i: usize) -> u64 {
    u64::from_le_bytes(b[i..i + 8].try_into().unwrap())
}
#[inline(always)]
fn rd4(b: &[u8], i: usize) -> u64 {
    u32::from_le_bytes(b[i..i + 4].try_into().unwrap()) as u64
}

/// 64-bit hash plus an independent 32-bit check. Never returns h == 0.
#[inline(always)]
pub fn hash_chk(b: &[u8]) -> (u64, u32) {
    let len = b.len();
    let (a, c) = if len >= 16 {
        let mut h = len as u64 ^ K0;
        let mut i = 0;
        while i + 16 < len {
            h = mum(rd8(b, i) ^ K1 ^ h, rd8(b, i + 8) ^ K2);
            i += 16;
        }
        (rd8(b, len - 16) ^ h, rd8(b, len - 8))
    } else if len >= 8 {
        (rd8(b, 0), rd8(b, len - 8))
    } else if len >= 4 {
        (rd4(b, 0), rd4(b, len - 4))
    } else if len > 0 {
        (((b[0] as u64) << 16) | ((b[len / 2] as u64) << 8) | b[len - 1] as u64, 0)
    } else {
        (0, 0)
    };
    let l = len as u64;
    let lm = l.wrapping_mul(K2);
    let h = mum(a ^ K0, c ^ K1 ^ lm);
    let chk = (mum(a ^ K3, c ^ K0 ^ lm.rotate_left(29)) >> 32) as u32;
    (h | 1, chk)
}

#[inline(always)]
pub fn hash(b: &[u8]) -> u64 {
    hash_chk(b).0
}

/// Split one line (no '\n') into exactly N fields following the CSV dialect:
/// a field is plain (no quote) or wrapped whole in quotes (no comma or quote inside).
/// Returns false for a malformed line. out holds (start, end) of each field's content.
#[inline(always)]
pub fn split<const N: usize>(line: &[u8], out: &mut [(usize, usize); N]) -> bool {
    let n = line.len();
    let mut i = 0;
    let mut f = 0;
    loop {
        if f == N {
            return false;
        }
        let (s, e);
        if i < n && line[i] == b'"' {
            let mut j = i + 1;
            while j < n && line[j] != b'"' {
                if line[j] == b',' {
                    return false;
                }
                j += 1;
            }
            if j >= n {
                return false;
            }
            s = i + 1;
            e = j;
            i = j + 1;
            if i < n && line[i] != b',' {
                return false;
            }
        } else {
            let mut j = i;
            while j < n && line[j] != b',' {
                if line[j] == b'"' {
                    return false;
                }
                j += 1;
            }
            s = i;
            e = j;
            i = j;
        }
        out[f] = (s, e);
        f += 1;
        if i >= n {
            break;
        }
        i += 1;
    }
    f == N
}

pub const DAY_MS: i64 = 86_400_000;

fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

#[inline(always)]
fn d2(b: &[u8], i: usize) -> Option<i64> {
    let x = b[i].wrapping_sub(b'0');
    let y = b[i + 1].wrapping_sub(b'0');
    if x > 9 || y > 9 {
        None
    } else {
        Some((x * 10 + y) as i64)
    }
}

/// Parse a timestamp (F1, F2 or F3) to epoch milliseconds, None if bad.
#[inline(always)]
pub fn parse_ts(b: &[u8]) -> Option<i64> {
    let n = b.len();
    if (n == 20 || n == 25) && b[10] == b'T' {
        if b[4] != b'-' || b[7] != b'-' || b[10] != b'T' || b[13] != b':' || b[16] != b':' {
            return None;
        }
        let y = d2(b, 0)? * 100 + d2(b, 2)?;
        let mo = d2(b, 5)?;
        let d = d2(b, 8)?;
        let h = d2(b, 11)?;
        let mi = d2(b, 14)?;
        let s = d2(b, 17)?;
        if !(1..=12).contains(&mo) || d < 1 || h > 23 || mi > 59 || s > 59 {
            return None;
        }
        let leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
        let dim = match mo {
            2 => 28 + leap as i64,
            4 | 6 | 9 | 11 => 30,
            _ => 31,
        };
        if d > dim {
            return None;
        }
        let mut ms = (days_from_civil(y, mo, d) * 86_400 + h * 3600 + mi * 60 + s) * 1000;
        if n == 20 {
            if b[19] != b'Z' {
                return None;
            }
        } else {
            let sign = match b[19] {
                b'+' => 1,
                b'-' => -1,
                _ => return None,
            };
            if b[22] != b':' {
                return None;
            }
            let oh = d2(b, 20)?;
            let om = d2(b, 23)?;
            if oh > 23 || om > 59 {
                return None;
            }
            ms -= sign * (oh * 60 + om) * 60_000;
        }
        return Some(ms);
    }
    if n == 0 {
        return None;
    }
    if (9..=16).contains(&n) {
        // two overlapping 8-digit SWAR conversions: first 8 digits and last 8 digits
        let ta = rd8(b, 0) ^ ZERO8;
        let tc = rd8(b, n - 8) ^ ZERO8;
        if !all_digits(ta) || !all_digits(tc) {
            return None;
        }
        const P10: [u64; 9] = [1, 10, 100, 1000, 10_000, 100_000, 1_000_000, 10_000_000, 100_000_000];
        let k = P10[n - 8];
        return Some((parse8(ta) * k + parse8(tc) % k) as i64);
    }
    parse_f3_scalar(b)
}

/// Eight digit values (one per byte, first digit in the lowest byte) to their number.
#[inline(always)]
fn parse8(x: u64) -> u64 {
    let x = (x & 0x0F0F_0F0F_0F0F_0F0F).wrapping_mul(2561) >> 8;
    let x = (x & 0x00FF_00FF_00FF_00FF).wrapping_mul(6_553_601) >> 16;
    (x & 0x0000_FFFF_0000_FFFF).wrapping_mul(42_949_672_960_001) >> 32
}

fn parse_f3_scalar(b: &[u8]) -> Option<i64> {
    let n = b.len();
    if n == 0 {
        return None;
    }
    let mut i = 0;
    while i < n && b[i] == b'0' {
        i += 1;
    }
    if n - i > 19 {
        for &c in &b[i..] {
            if c.wrapping_sub(b'0') > 9 {
                return None;
            }
        }
        return None;
    }
    let mut v: u64 = 0;
    for &c in &b[i..] {
        let d = c.wrapping_sub(b'0');
        if d > 9 {
            return None;
        }
        v = v.wrapping_mul(10).wrapping_add(d as u64);
    }
    // 19 digits can exceed i64 (or wrap u64 only past 20 digits, excluded above)
    if v > i64::MAX as u64 {
        return None;
    }
    Some(v as i64)
}

/// Direct-mapped cache of "YYYY-MM-DD" text -> epoch ms of that UTC midnight.
pub struct DateCache {
    k: [(u64, u16); 256],
    v: [i64; 256],
}
impl DateCache {
    pub fn new() -> Self {
        DateCache { k: [(0, 0); 256], v: [0; 256] }
    }
}

const ZERO8: u64 = 0x3030_3030_3030_3030;
const HI8: u64 = 0x8080_8080_8080_8080;

/// SWAR: true when every byte of `t` (already xor 0x30) is a digit value 0..=9.
#[inline(always)]
fn all_digits(t: u64) -> bool {
    ((t.wrapping_add(0x7676_7676_7676_7676)) | t) & HI8 == 0
}

#[inline(always)]
fn date_slow(b: &[u8]) -> Option<i64> {
    if b[4] != b'-' || b[7] != b'-' {
        return None;
    }
    let y = d2(b, 0)? * 100 + d2(b, 2)?;
    let mo = d2(b, 5)?;
    let d = d2(b, 8)?;
    if !(1..=12).contains(&mo) || d < 1 {
        return None;
    }
    let leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
    let dim = match mo {
        2 => 28 + leap as i64,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    };
    if d > dim {
        return None;
    }
    Some(days_from_civil(y, mo, d) * DAY_MS)
}

/// Same as `parse_ts`, with a date cache and SWAR time-of-day parsing for F1/F2.
#[inline(always)]
pub fn parse_ts_c(b: &[u8], dc: &mut DateCache) -> Option<i64> {
    let n = b.len();
    if (n == 20 || n == 25) && b[10] == b'T' {
        let a = rd8(b, 0);
        let c2 = u16::from_le_bytes([b[8], b[9]]);
        let idx = ((a ^ ((c2 as u64) << 40)).wrapping_mul(K0) >> 56) as usize;
        let day_ms = if dc.k[idx] == (a, c2) {
            dc.v[idx]
        } else {
            let v = date_slow(b)?;
            dc.k[idx] = (a, c2);
            dc.v[idx] = v;
            v
        };
        // bytes 11..19: H H : M M : S S
        let t = rd8(b, 11) ^ ZERO8;
        const SEP: u64 = 0x0000_FF00_00FF_0000;
        if t & SEP != 0x0000_0A00_000A_0000 || !all_digits(t & !SEP) {
            return None;
        }
        let g = |i: u32| ((t >> (8 * i)) & 0xFF) as i64;
        let (h, mi, s) = (g(0) * 10 + g(1), g(3) * 10 + g(4), g(6) * 10 + g(7));
        if h > 23 || mi > 59 || s > 59 {
            return None;
        }
        let mut ms = day_ms + ((h * 60 + mi) * 60 + s) * 1000;
        if n == 20 {
            if b[19] != b'Z' {
                return None;
            }
        } else {
            let sign = match b[19] {
                b'+' => 1,
                b'-' => -1,
                _ => return None,
            };
            if b[22] != b':' {
                return None;
            }
            let (oh, om) = (d2(b, 20)?, d2(b, 23)?);
            if oh > 23 || om > 59 {
                return None;
            }
            ms -= sign * (oh * 60 + om) * 60_000;
        }
        return Some(ms);
    }
    parse_ts(b)
}

pub fn parse_price(b: &[u8]) -> Option<u32> {
    if b.is_empty() {
        return None;
    }
    let mut v: u64 = 0;
    for &c in b {
        let d = c.wrapping_sub(b'0');
        if d > 9 {
            return None;
        }
        v = v * 10 + d as u64;
        if v > 10_000_000 {
            return None;
        }
    }
    if v == 0 {
        None
    } else {
        Some(v as u32)
    }
}

/// Memory-map a file read-only; the mapping is never unmapped (pages are dropped chunk by chunk).
pub fn mmap(path: &std::path::Path) -> &'static [u8] {
    let f = File::open(path).unwrap_or_else(|e| panic!("open {path:?}: {e}"));
    let len = f.metadata().unwrap().len() as usize;
    if len == 0 {
        return &[];
    }
    unsafe {
        let p = libc::mmap(std::ptr::null_mut(), len, libc::PROT_READ, libc::MAP_PRIVATE, f.as_raw_fd(), 0);
        assert!(p != libc::MAP_FAILED, "mmap {path:?}");
        std::slice::from_raw_parts(p as *const u8, len)
    }
}

/// All `*.csv` files directly in `dir`, sorted, mapped.
pub fn csv_files(dir: &std::path::Path) -> Vec<&'static [u8]> {
    let mut v: Vec<_> = match std::fs::read_dir(dir) {
        Ok(rd) => rd
            .filter_map(|e| e.ok())
            .map(|e| e.path())
            .filter(|p| p.extension().map_or(false, |x| x == "csv") && p.is_file())
            .collect(),
        Err(_) => vec![],
    };
    v.sort();
    v.iter().map(|p| mmap(p)).collect()
}

pub type Chunk = (&'static [u8], usize, usize);

/// Split files into ~`size`-byte chunks. Lines belong to the chunk they start in.
pub fn chunks(files: &[&'static [u8]], size: usize) -> Vec<Chunk> {
    let mut out = vec![];
    for &f in files {
        let mut a = 0;
        loop {
            let b = (a + size).min(f.len());
            out.push((f, a, b));
            if b >= f.len() {
                break;
            }
            a = b;
        }
    }
    out
}

/// Call `f` for every data line starting in [a, b) (skipping the header line of the file).
#[inline(always)]
pub fn for_lines(c: Chunk, mut f: impl FnMut(&'static [u8])) {
    let (data, a, b) = c;
    let mut p = a;
    if a == 0 {
        match memchr::memchr(b'\n', data) {
            Some(i) => p = i + 1,
            None => return,
        }
    } else if data[a - 1] != b'\n' {
        match memchr::memchr(b'\n', &data[a..]) {
            Some(i) => p = a + i + 1,
            None => return,
        }
    }
    while p < b {
        let end = match memchr::memchr(b'\n', &data[p..]) {
            Some(i) => p + i,
            None => data.len(),
        };
        f(&data[p..end]);
        p = end + 1;
    }
}

/// Pack a string's address and length (pointer < 2^47) into one word.
#[inline(always)]
pub fn pack(s: &[u8]) -> u64 {
    s.as_ptr() as u64 | (s.len().min(0xFFFF) as u64) << 47
}

#[inline(always)]
pub fn unpack(v: u64) -> &'static [u8] {
    unsafe { std::slice::from_raw_parts((v & ((1 << 47) - 1)) as *const u8, ((v >> 47) & 0xFFFF) as usize) }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fast_ts_matches_slow() {
        let seeds: [&[u8]; 8] = [
            b"2026-10-03T23:58:10Z",
            b"2026-10-03T19:58:10-04:00",
            b"2028-02-29T00:00:00+05:30",
            b"1791071890000",
            b"2026-12-31T23:59:59-00:00",
            b"2027-06-15T12:34:56Z",
            b"00000001791071890000",
            b"0000000000001791071890000",
        ];
        let mut dc = DateCache::new();
        let mut x: u64 = 88172645463325252;
        let mut rnd = || {
            x ^= x << 13;
            x ^= x >> 7;
            x ^= x << 17;
            x
        };
        let alphabet = b"0123456789-:TZz+ ,\"\r./";
        for _ in 0..2_000_000 {
            let mut v = seeds[(rnd() % 8) as usize].to_vec();
            for _ in 0..(rnd() % 3) {
                let i = (rnd() % v.len() as u64) as usize;
                v[i] = alphabet[(rnd() % alphabet.len() as u64) as usize];
            }
            assert_eq!(parse_ts(&v), parse_ts_c(&v, &mut dc), "{:?}", String::from_utf8_lossy(&v));
        }
    }

    #[test]
    fn f3_swar_matches_scalar() {
        let mut x: u64 = 0x9E3779B97F4A7C15;
        let mut rnd = || {
            x ^= x << 13;
            x ^= x >> 7;
            x ^= x << 17;
            x
        };
        for _ in 0..3_000_000 {
            let n = 1 + (rnd() % 24) as usize;
            let mut v: Vec<u8> = (0..n).map(|_| b'0' + (rnd() % 10) as u8).collect();
            if rnd() % 4 == 0 {
                let i = (rnd() % n as u64) as usize;
                v[i] = b"-.+eE x\x80"[(rnd() % 8) as usize];
            }
            if rnd() % 3 == 0 {
                for c in v.iter_mut().take((rnd() % 8) as usize) {
                    if c.is_ascii_digit() {
                        *c = b'0';
                    }
                }
            }
            assert_eq!(parse_ts(&v), parse_f3_scalar(&v), "{:?}", String::from_utf8_lossy(&v));
        }
    }

    #[test]
    fn padded_f3_of_f1_length() {
        let mut dc = DateCache::new();
        for t in [&b"00000001791071890000"[..], b"0000000000001791071890000"] {
            assert_eq!(parse_ts(t), Some(1791071890000));
            assert_eq!(parse_ts_c(t, &mut dc), Some(1791071890000));
        }
    }

    #[test]
    fn short_hashes_distinct() {
        let mut seen = std::collections::HashSet::new();
        for s in ["p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9", "p10", "p11"] {
            assert!(seen.insert(hash(s.as_bytes())), "collision {s}");
        }
    }
}

/// An input file that is either memory-mapped or read with pread (see `read_io`).
pub struct Input {
    pub fd: i32,
    pub len: usize,
    pub map: &'static [u8], // empty when not mapped
}

impl Input {
    /// Up to 1 MB from the start of the file, for line-length sampling.
    pub fn sample(&self) -> Vec<u8> {
        if !self.map.is_empty() {
            return self.map[..self.map.len().min(1 << 20)].to_vec();
        }
        let n = self.len.min(1 << 20);
        let mut v = vec![0u8; n];
        let got = unsafe { libc::pread(self.fd, v.as_mut_ptr() as *mut _, n, 0) };
        v.truncate(got.max(0) as usize);
        v
    }
}

/// Default input method. pread avoids page-table walks on VMs but costs a copy; measured slower on bare metal.
const DEFAULT_READ_IO: bool = true;

/// True when events and clicks should be read with pread into per-thread buffers instead of mmap.
/// `SHOP_IO=read|mmap` overrides DEFAULT_READ_IO.
pub fn read_io() -> bool {
    match std::env::var("SHOP_IO").as_deref() {
        Ok("read") => true,
        Ok("mmap") => false,
        _ => DEFAULT_READ_IO,
    }
}

pub fn csv_inputs(dir: &std::path::Path, map: bool) -> Vec<Input> {
    let mut v: Vec<_> = match std::fs::read_dir(dir) {
        Ok(rd) => rd
            .filter_map(|e| e.ok())
            .map(|e| e.path())
            .filter(|p| p.extension().map_or(false, |x| x == "csv") && p.is_file())
            .collect(),
        Err(_) => vec![],
    };
    v.sort();
    v.iter()
        .map(|p| {
            let f = File::open(p).unwrap_or_else(|e| panic!("open {p:?}: {e}"));
            let len = f.metadata().unwrap().len() as usize;
            let fd = std::os::unix::io::IntoRawFd::into_raw_fd(f);
            let map = if map { mmap(p) } else { &[] };
            Input { fd, len, map }
        })
        .collect()
}

/// (file index, start, end) pieces of about `size` bytes.
pub fn input_chunks(files: &[Input], size: usize) -> Vec<(usize, usize, usize)> {
    let mut out = vec![];
    for (i, f) in files.iter().enumerate() {
        let mut a = 0;
        loop {
            let b = (a + size).min(f.len);
            out.push((i, a, b));
            if b >= f.len {
                break;
            }
            a = b;
        }
    }
    out
}

fn pread_all(fd: i32, buf: &mut Vec<u8>, off: usize, want: usize) -> usize {
    let start = buf.len();
    buf.reserve(want);
    let mut got = 0;
    while got < want {
        let n = unsafe { libc::pread(fd, buf.as_mut_ptr().add(start + got) as *mut _, want - got, (off + got) as i64) };
        if n <= 0 {
            break;
        }
        got += n as usize;
    }
    unsafe { buf.set_len(start + got) };
    got
}

/// Same lines as `scan::region`, read with pread into `buf`; the slice borrows `buf` and is valid until the next call.
/// The read starts on a page boundary of the file and lands on a page boundary of `buf` (data starts at index `pad`):
/// the kernel's copy is 3x slower when source and destination are misaligned to each other.
pub fn read_region(f: &Input, a: usize, b: usize, buf: &mut Vec<u8>) -> &'static [u8] {
    const EXTRA: usize = 4096;
    buf.clear();
    let first = a.saturating_sub(1); // the byte before the chunk tells whether it starts on a line
    let off0 = first & !4095;
    let want = (b - off0 + EXTRA).min(f.len - off0);
    buf.reserve(want + 4096);
    let pad = (buf.as_ptr() as usize).wrapping_neg() & 4095;
    unsafe { buf.set_len(pad) };
    pread_all(f.fd, buf, off0, want);
    let rel = |x: usize| x - off0 + pad; // file offset -> buffer index
    // start of the first line owned by this chunk
    let p = if a == 0 {
        match memchr::memchr(b'\n', &buf[pad..]) {
            Some(i) => pad + i + 1,
            None => return &[],
        }
    } else if buf[rel(first)] == b'\n' {
        rel(first) + 1
    } else {
        match memchr::memchr(b'\n', &buf[rel(first) + 1..]) {
            Some(i) => rel(first) + i + 2,
            None => return &[],
        }
    };
    // end: after the newline that ends the last line starting before b
    let e = if b >= f.len {
        buf.len()
    } else if buf[rel(b) - 1] == b'\n' {
        rel(b)
    } else {
        let mut from = rel(b);
        loop {
            if let Some(i) = memchr::memchr(b'\n', &buf[from..]) {
                break from + i + 1;
            }
            from = buf.len();
            if pread_all(f.fd, buf, off0 + from - pad, 65536) == 0 {
                break buf.len();
            }
        }
    };
    if p >= e {
        return &[];
    }
    unsafe { std::slice::from_raw_parts(buf.as_ptr().add(p), e - p) }
}
