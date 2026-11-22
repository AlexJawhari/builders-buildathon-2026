//! Parquet value encodings: RLE/bit-packed hybrid for dictionary indices, and a small dictionary builder.

/// Bits needed to store indices below `n` (at least 1).
pub fn bit_width(n: usize) -> u32 {
    (usize::BITS - n.saturating_sub(1).leading_zeros()).max(1)
}

fn varint(out: &mut Vec<u8>, mut v: u64) {
    while v >= 0x80 {
        out.push((v as u8) | 0x80);
        v >>= 7;
    }
    out.push(v as u8);
}

fn flush_packed(out: &mut Vec<u8>, pend: &mut Vec<u32>, bw: u32) {
    if pend.is_empty() {
        return;
    }
    let groups = pend.len().div_ceil(8);
    varint(out, ((groups as u64) << 1) | 1);
    let (mut acc, mut nb) = (0u64, 0u32);
    for i in 0..groups * 8 {
        let v = pend.get(i).copied().unwrap_or(0) as u64;
        acc |= v << nb;
        nb += bw;
        while nb >= 8 {
            out.push(acc as u8);
            acc >>= 8;
            nb -= 8;
        }
    }
    pend.clear();
}

/// Encode `vals` (each below 2^bw) as the Parquet RLE/bit-packed hybrid: runs of 8 or more equal values are
/// RLE runs, everything else is bit-packed in groups of 8 (only the final group may be zero-padded).
pub fn hybrid(vals: &[u32], bw: u32, out: &mut Vec<u8>) {
    let vbytes = bw.div_ceil(8) as usize;
    let mut pend: Vec<u32> = Vec::with_capacity(64);
    let n = vals.len();
    let mut i = 0;
    while i < n {
        let v = vals[i];
        let mut r = 1;
        while r < 8 && i + r < n && vals[i + r] == v {
            r += 1;
        }
        if r < 8 {
            pend.push(v);
            i += 1;
            continue;
        }
        while i + r < n && vals[i + r] == v {
            r += 1;
        }
        // complete the pending group with values of this run, then emit the rest as RLE
        let need = (8 - pend.len() % 8) % 8;
        let take = need.min(r);
        for _ in 0..take {
            pend.push(v);
        }
        i += take;
        r -= take;
        if pend.len() % 8 != 0 {
            continue;
        }
        if r >= 8 {
            flush_packed(out, &mut pend, bw);
            varint(out, (r as u64) << 1);
            out.extend_from_slice(&v.to_le_bytes()[..vbytes]);
            i += r;
        }
    }
    flush_packed(out, &mut pend, bw);
}

/// Dictionary of u64 values in order of first appearance.
pub struct Dict {
    small: Vec<u32>, // direct table for values below 4096
    keys: Vec<u64>,
    idx: Vec<u32>, // u32::MAX = empty
    mask: usize,
    pub values: Vec<u64>,
}

impl Dict {
    pub fn new() -> Dict {
        Dict { small: vec![u32::MAX; 4096], keys: vec![0; 1024], idx: vec![u32::MAX; 1024], mask: 1023, values: vec![] }
    }

    #[inline(always)]
    pub fn id(&mut self, v: u64) -> u32 {
        if v < 4096 {
            let i = self.small[v as usize];
            if i != u32::MAX {
                return i;
            }
            let id = self.values.len() as u32;
            self.small[v as usize] = id;
            self.values.push(v);
            return id;
        }
        let mut s = (v.wrapping_mul(0x9E37_79B9_7F4A_7C15) >> 40) as usize & self.mask;
        loop {
            let i = self.idx[s];
            if i == u32::MAX {
                break;
            }
            if self.keys[s] == v {
                return i;
            }
            s = (s + 1) & self.mask;
        }
        let id = self.values.len() as u32;
        self.keys[s] = v;
        self.idx[s] = id;
        self.values.push(v);
        if self.values.len() * 2 > self.keys.len() {
            self.grow();
        }
        id
    }

    fn grow(&mut self) {
        let cap = self.keys.len() * 2;
        self.keys = vec![0; cap];
        self.idx = vec![u32::MAX; cap];
        self.mask = cap - 1;
        for (id, &v) in self.values.iter().enumerate().filter(|(_, &v)| v >= 4096) {
            let mut s = (v.wrapping_mul(0x9E37_79B9_7F4A_7C15) >> 40) as usize & self.mask;
            while self.idx[s] != u32::MAX {
                s = (s + 1) & self.mask;
            }
            self.keys[s] = v;
            self.idx[s] = id as u32;
        }
    }
}
