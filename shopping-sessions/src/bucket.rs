//! Append-only bucket storage carved from a per-thread, pre-faulted arena.
//! Fresh pages are populated in one madvise call instead of one page fault each.

use std::sync::atomic::{AtomicUsize, Ordering::Relaxed};

const SH: usize = 8;
const PF: usize = 192; // bytes ahead of the write cursor to prefetch
const BLK: usize = 1 << SH; // records per block

const SLAB: usize = 1 << 20;

/// One big anonymous mapping shared by all threads; threads claim 1 MB slabs from it.
pub struct Pool {
    base: usize,
    cap: usize,
    next: AtomicUsize,
}

impl Pool {
    pub fn new(cap: usize) -> &'static Pool {
        let cap = (cap + SLAB - 1) & !(SLAB - 1);
        let mut base = 0;
        if cap > 0 {
            unsafe {
                let p = libc::mmap(
                    std::ptr::null_mut(),
                    cap,
                    libc::PROT_READ | libc::PROT_WRITE,
                    libc::MAP_PRIVATE | libc::MAP_ANONYMOUS | libc::MAP_NORESERVE,
                    -1,
                    0,
                );
                if p != libc::MAP_FAILED {
                    base = p as usize;
                    libc::madvise(p, cap, libc::MADV_HUGEPAGE);
                }
            }
        }
        let cap = if base == 0 { 0 } else { cap };
        Box::leak(Box::new(Pool { base, cap, next: AtomicUsize::new(0) }))
    }

    pub fn stats(&self) -> (usize, usize) {
        (self.next.load(Relaxed), self.cap)
    }

    fn part(&self, i: usize, parts: usize) -> (usize, usize) {
        let slabs = self.cap / SLAB;
        let (a, b) = (slabs * i / parts, slabs * (i + 1) / parts);
        (self.base + a * SLAB, (b - a) * SLAB)
    }

    /// Fault in part `i` of `parts` with one call (parallel over threads).
    pub fn populate(&self, i: usize, parts: usize) {
        let (p, n) = self.part(i, parts);
        if n > 0 {
            unsafe { libc::madvise(p as *mut _, n, 23) }; // MADV_POPULATE_WRITE; older kernels ignore it
        }
    }
}

/// Per-thread bump allocator over slabs of a Pool; falls back to the heap when the pool is used up.
pub struct Arena {
    pool: &'static Pool,
    cur: usize,
    end: usize,
    scur: usize, // separate slab for copied strings (no alignment)
    send: usize,
}
unsafe impl Send for Arena {}
unsafe impl Sync for Arena {}

impl Arena {
    pub fn new(pool: &'static Pool) -> Arena {
        Arena { pool, cur: 0, end: 0, scur: 0, send: 0 }
    }

    /// Copy `b` into stable memory (the input buffer it came from is reused) and return the copy.
    pub fn copy_bytes(&mut self, b: &[u8]) -> &'static [u8] {
        if self.scur + b.len() > self.send {
            let off = self.pool.next.fetch_add(SLAB, Relaxed);
            if off + SLAB > self.pool.cap || b.len() > SLAB {
                return Box::leak(b.to_vec().into_boxed_slice());
            }
            self.scur = self.pool.base + off;
            self.send = self.scur + SLAB;
        }
        let p = self.scur as *mut u8;
        self.scur += b.len();
        unsafe {
            std::ptr::copy_nonoverlapping(b.as_ptr(), p, b.len());
            std::slice::from_raw_parts(p, b.len())
        }
    }

    fn alloc<T>(&mut self, n: usize) -> *mut T {
        let bytes = (n * std::mem::size_of::<T>() + 63) & !63;
        if self.cur + bytes > self.end {
            let off = self.pool.next.fetch_add(SLAB, Relaxed);
            if off + SLAB > self.pool.cap {
                return unsafe { std::alloc::alloc(std::alloc::Layout::from_size_align(bytes, 64).unwrap()) as *mut T };
            }
            self.cur = self.pool.base + off;
            self.end = self.cur + SLAB;
        }
        let p = self.cur;
        self.cur += bytes;
        p as *mut T
    }
}

/// One cache line of state per bucket: the write cursor lives here, so a push touches this line and the tail line only.
#[repr(align(64))]
pub struct Bucket<T> {
    cur: *mut T,
    left: usize,
    len: usize,
    blocks: Vec<*mut T>,
}
unsafe impl<T> Send for Bucket<T> {}
unsafe impl<T> Sync for Bucket<T> {}

impl<T: Copy> Bucket<T> {
    pub fn new() -> Self {
        Bucket { cur: std::ptr::null_mut(), left: 0, len: 0, blocks: Vec::new() }
    }
    pub fn len(&self) -> usize {
        self.len
    }
    #[inline(always)]
    pub fn push(&mut self, v: T, arena: &mut Arena) {
        if self.left == 0 {
            self.cur = arena.alloc::<T>(BLK);
            self.blocks.push(self.cur);
            self.left = BLK;
        }
        unsafe {
            self.cur.write(v);
            self.cur = self.cur.add(1);
            #[cfg(target_arch = "x86_64")]
            std::arch::x86_64::_mm_prefetch((self.cur as *const i8).add(PF), std::arch::x86_64::_MM_HINT_ET0);
        }
        self.left -= 1;
        self.len += 1;
    }
    #[inline(always)]
    pub fn ptr(&self, i: usize) -> *mut T {
        unsafe { self.blocks.get_unchecked(i >> SH).add(i & (BLK - 1)) }
    }
    pub fn get(&self, i: usize) -> &T {
        unsafe { &*self.ptr(i) }
    }
    /// The contents as a sequence of slices.
    pub fn slices(&self) -> impl Iterator<Item = &[T]> {
        let len = self.len;
        self.blocks.iter().enumerate().map(move |(b, &p)| {
            let n = (len - (b << SH)).min(BLK);
            unsafe { std::slice::from_raw_parts(p, n) }
        })
    }
}
