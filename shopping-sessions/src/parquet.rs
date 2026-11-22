//! Minimal Parquet writer: one row group, one uncompressed PLAIN data page per column.

use std::io::Write;

pub const T_I32: i32 = 1;
pub const T_I64: i32 = 2;
pub const T_BYTES: i32 = 6;
pub const CONV_UTF8: i32 = 0;
pub const CONV_DATE: i32 = 6;

pub struct Col<'a> {
    pub name: &'static str,
    pub ptype: i32,
    pub conv: Option<i32>,
    pub optional: bool,
    pub data: &'a [u8], // data page body: levels (if optional) then PLAIN values, or bit width + hybrid indices if `dict`
    pub dict: Option<(&'a [u8], usize)>, // dictionary page body (PLAIN) and its entry count
    pub nvalues: usize, // rows including nulls
}

struct Th {
    b: Vec<u8>,
    last: Vec<i16>,
}
impl Th {
    fn new() -> Th {
        Th { b: vec![], last: vec![0] }
    }
    fn varint(&mut self, mut v: u64) {
        while v >= 0x80 {
            self.b.push((v as u8) | 0x80);
            v >>= 7;
        }
        self.b.push(v as u8);
    }
    fn zig(v: i64) -> u64 {
        ((v << 1) ^ (v >> 63)) as u64
    }
    fn field(&mut self, id: i16, ty: u8) {
        let d = id - *self.last.last().unwrap();
        if (1..=15).contains(&d) {
            self.b.push(((d as u8) << 4) | ty);
        } else {
            self.b.push(ty);
            self.varint(Self::zig(id as i64));
        }
        *self.last.last_mut().unwrap() = id;
    }
    fn i32(&mut self, id: i16, v: i32) {
        self.field(id, 5);
        self.varint(Self::zig(v as i64));
    }
    fn i64(&mut self, id: i16, v: i64) {
        self.field(id, 6);
        self.varint(Self::zig(v));
    }
    fn bin(&mut self, id: i16, s: &str) {
        self.field(id, 8);
        self.bin_val(s);
    }
    fn bin_val(&mut self, s: &str) {
        self.varint(s.len() as u64);
        self.b.extend_from_slice(s.as_bytes());
    }
    fn list(&mut self, id: i16, elem: u8, n: usize) {
        self.field(id, 9);
        self.list_hdr(elem, n);
    }
    fn list_hdr(&mut self, elem: u8, n: usize) {
        if n < 15 {
            self.b.push(((n as u8) << 4) | elem);
        } else {
            self.b.push(0xF0 | elem);
            self.varint(n as u64);
        }
    }
    fn begin_field(&mut self, id: i16) {
        self.field(id, 12);
        self.last.push(0);
    }
    fn begin(&mut self) {
        self.last.push(0);
    }
    fn end(&mut self) {
        self.b.push(0);
        self.last.pop();
    }
}

/// Def levels for `n` values, bit width 1, as one bit-packed RLE-hybrid run, 4-byte length prefixed.
pub fn def_levels(valid: &[bool]) -> Vec<u8> {
    let n = valid.len();
    let groups = (n + 7) / 8;
    let mut body = Vec::with_capacity(groups + 6);
    let mut hdr = Th::new();
    hdr.varint(((groups as u64) << 1) | 1);
    body.extend_from_slice(&hdr.b);
    for g in 0..groups {
        let mut byte = 0u8;
        for k in 0..8 {
            if g * 8 + k < n && valid[g * 8 + k] {
                byte |= 1 << k;
            }
        }
        body.push(byte);
    }
    let mut out = (body.len() as u32).to_le_bytes().to_vec();
    out.extend_from_slice(&body);
    out
}

pub fn write(path: &std::path::Path, nrows: usize, cols: &[Col]) {
    let mut file = std::io::BufWriter::with_capacity(1 << 16, std::fs::File::create(path).unwrap());
    file.write_all(b"PAR1").unwrap();
    let mut pos: i64 = 4;
    let mut chunk_meta: Vec<(i64, i64, i64, i64, i64)> = vec![]; // first page offset, total size, num values, dict page offset (0 = none), data page offset
    if nrows > 0 {
        for c in cols {
            let off = pos;
            let mut dict_off = None;
            if let Some((d, entries)) = c.dict {
                let mut h = Th::new();
                h.i32(1, 2); // DICTIONARY_PAGE
                h.i32(2, d.len() as i32);
                h.i32(3, d.len() as i32);
                h.begin_field(7);
                h.i32(1, entries as i32);
                h.i32(2, 0); // PLAIN
                h.end();
                h.end();
                file.write_all(&h.b).unwrap();
                file.write_all(d).unwrap();
                dict_off = Some(pos);
                pos += (h.b.len() + d.len()) as i64;
            }
            let data_off = pos;
            let mut h = Th::new();
            h.i32(1, 0);
            h.i32(2, c.data.len() as i32);
            h.i32(3, c.data.len() as i32);
            h.begin_field(5);
            h.i32(1, c.nvalues as i32);
            h.i32(2, if c.dict.is_some() { 8 } else { 0 }); // RLE_DICTIONARY or PLAIN
            h.i32(3, 3);
            h.i32(4, 3);
            h.end();
            h.end();
            file.write_all(&h.b).unwrap();
            file.write_all(c.data).unwrap();
            pos += (h.b.len() + c.data.len()) as i64;
            chunk_meta.push((off, pos - off, c.nvalues as i64, dict_off.unwrap_or(0), data_off));
        }
    }
    let mut m = Th::new();
    m.i32(1, 1);
    m.list(2, 12, cols.len() + 1);
    m.begin();
    m.bin(4, "schema");
    m.i32(5, cols.len() as i32);
    m.end();
    for c in cols {
        m.begin();
        m.i32(1, c.ptype);
        m.i32(3, c.optional as i32);
        m.bin(4, c.name);
        if let Some(cv) = c.conv {
            m.i32(6, cv);
        }
        m.end();
    }
    m.i64(3, nrows as i64);
    if nrows > 0 {
        m.list(4, 12, 1);
        m.begin();
        m.list(1, 12, cols.len());
        let mut total = 0;
        for (c, &(off, size, nv, dict_off, data_off)) in cols.iter().zip(&chunk_meta) {
            total += size;
            m.begin();
            m.i64(2, off);
            m.begin_field(3);
            m.i32(1, c.ptype);
            let mut encs = vec![0i64];
            if c.optional || c.dict.is_some() {
                encs.push(3);
            }
            if c.dict.is_some() {
                encs.push(8);
            }
            m.list(2, 5, encs.len());
            for e in encs {
                m.varint(Th::zig(e));
            }
            m.list(3, 8, 1);
            m.bin_val(c.name);
            m.i32(4, 0);
            m.i64(5, nv);
            m.i64(6, size);
            m.i64(7, size);
            m.i64(9, data_off);
            if c.dict.is_some() {
                m.i64(11, dict_off);
            }
            m.end();
            m.end();
        }
        m.i64(2, total);
        m.i64(3, nrows as i64);
        m.end();
    } else {
        m.list(4, 12, 0);
    }
    m.bin(6, "shop");
    m.end();
    file.write_all(&m.b).unwrap();
    file.write_all(&(m.b.len() as u32).to_le_bytes()).unwrap();
    file.write_all(b"PAR1").unwrap();
    file.flush().unwrap();
}
