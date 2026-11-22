//! Linked IDs: a lock-free hash table of every ID in a valid link line, joined by a
//! lock-free union-find whose root is always the byte-wise smallest string. After `assign`, every slot holds a
//! dense person id (+1); IDs first seen in events/clicks are added the same way, so one probe maps any user id to a person.
//! `names[pid]` is the packed (ptr,len) of the person's name.

use crate::util::*;
use std::sync::atomic::{AtomicU32, AtomicU64, Ordering::*};

const SPILL: usize = 64;

#[repr(C, align(16))]
struct Slot {
    k: AtomicU64,
    v: AtomicU64,
}

pub struct Links {
    slots: Box<[Slot]>,
    parent: Box<[AtomicU32]>, // 0 = root, else parent index + 1
    mask: usize,  // primary table: slots [0, mask]
    cap1: usize,
    mask2: usize, // overflow table: slots [cap1, cap1 + mask2], only touched when primary probes get long
    names: Box<[AtomicU64]>,
    npid: AtomicU32,
}

fn zeroed<T>(n: usize) -> Box<[T]> {
    unsafe {
        let layout = std::alloc::Layout::array::<T>(n).unwrap();
        let p = std::alloc::alloc_zeroed(layout) as *mut T;
        assert!(!p.is_null());
        Box::from_raw(std::slice::from_raw_parts_mut(p, n))
    }
}

impl Links {
    /// `expect`: likely number of distinct user IDs (sizes the primary table); `max_ids`: upper bound (sizes the
    /// overflow table, whose pages stay untouched unless the primary gets crowded).
    pub fn new(expect: usize, max_ids: usize) -> Links {
        let cap1 = (expect * 7 / 5 + 16).next_power_of_two();
        let cap2 = (max_ids * 23 / 20 + 16).next_power_of_two();
        let cap = cap1 + cap2;
        Links { slots: zeroed(cap), parent: zeroed(cap), mask: cap1 - 1, cap1, mask2: cap2 - 1, names: zeroed(cap), npid: AtomicU32::new(0) }
    }

    pub fn npid(&self) -> usize {
        self.npid.load(Relaxed) as usize
    }

    pub fn name(&self, pid: usize) -> u64 {
        self.names[pid].load(Relaxed)
    }

    /// Slot of `h`, inserting it if absent; newly inserted slots are appended to `fresh`.
    fn insert(&self, h: u64, v: u64, fresh: &mut Vec<u32>) -> usize {
        let (mut i, mut base, mut mask, mut probes) = ((h as usize) & self.mask, 0, self.mask, 0usize);
        loop {
            let s = &self.slots[base + i];
            let k = s.k.load(Acquire);
            if k == h {
                return base + i;
            }
            if k == 0 {
                match s.k.compare_exchange(0, h, AcqRel, Acquire) {
                    Ok(_) => {
                        s.v.store(v, Release);
                        fresh.push((base + i) as u32);
                        return base + i;
                    }
                    Err(cur) if cur == h => return base + i,
                    Err(_) => {}
                }
            }
            (i, base, mask, probes) = self.next(h, i, base, mask, probes);
        }
    }

    /// Next probe position; after `SPILL` probes in the primary table the key moves on to the overflow table
    /// (slots are never freed, so a key always takes the same path).
    #[inline(always)]
    fn next(&self, h: u64, i: usize, base: usize, mask: usize, probes: usize) -> (usize, usize, usize, usize) {
        if base == 0 && probes + 1 >= SPILL.min(mask + 1) {
            (h as usize & self.mask2, self.cap1, self.mask2, 0)
        } else {
            assert!(probes < mask, "user id table full");
            ((i + 1) & mask, base, mask, probes + 1)
        }
    }

    fn val(&self, i: usize) -> u64 {
        loop {
            let v = self.slots[i].v.load(Acquire);
            if v != 0 {
                return v;
            }
            std::hint::spin_loop();
        }
    }

    fn find(&self, mut i: usize) -> usize {
        loop {
            let p = self.parent[i].load(Relaxed);
            if p == 0 {
                return i;
            }
            let pp = self.parent[p as usize - 1].load(Relaxed);
            if pp != 0 {
                self.parent[i].store(pp, Relaxed);
                i = pp as usize - 1;
            } else {
                i = p as usize - 1;
            }
        }
    }

    fn union(&self, a: usize, b: usize) {
        loop {
            let ra = self.find(a);
            let rb = self.find(b);
            if ra == rb {
                return;
            }
            let (child, root) = if unpack(self.val(ra)) < unpack(self.val(rb)) { (rb, ra) } else { (ra, rb) };
            if self.parent[child].compare_exchange(0, root as u32 + 1, AcqRel, Relaxed).is_ok() {
                return;
            }
        }
    }

    /// Add one link line (both sides non-empty, already validated).
    pub fn add(&self, a: &'static [u8], b: &'static [u8], fresh: &mut Vec<u32>) {
        let ia = self.insert(hash(a), pack(a), fresh);
        let ib = self.insert(hash(b), pack(b), fresh);
        if ia != ib {
            self.union(ia, ib);
        }
    }

    /// Step 1 (parallel over the slots inserted by link lines): give every union-find root a person id.
    pub fn assign_roots(&self, slots: &[u32], tmp: &[AtomicU32]) {
        for (j, &i) in slots.iter().enumerate() {
            if let Some(&n) = slots.get(j + 12) {
                self.prefetch_slot(n as usize);
            }
            let i = i as usize;
            if self.parent[i].load(Relaxed) == 0 {
                let pid = self.npid.fetch_add(1, Relaxed);
                self.names[pid as usize].store(self.slots[i].v.load(Relaxed), Relaxed);
                tmp[i].store(pid + 1, Relaxed);
            }
        }
    }
    /// Step 2 (after step 1 finished everywhere): every slot takes its root's person id.
    pub fn assign_all(&self, slots: &[u32], tmp: &[AtomicU32]) {
        for (j, &i) in slots.iter().enumerate() {
            if let Some(&n) = slots.get(j + 12) {
                self.prefetch_slot(n as usize);
            }
            if let Some(&n) = slots.get(j + 6) {
                // the parent entry was prefetched 6 iterations ago: pull in the tmp entry of the likely root
                let p = self.parent[n as usize].load(Relaxed);
                Self::pf(&tmp[if p == 0 { n as usize } else { p as usize - 1 }]);
            }
            let i = i as usize;
            let r = self.find(i);
            self.slots[i].v.store(tmp[r].load(Relaxed) as u64, Relaxed);
        }
    }
    pub fn tmp(&self) -> Box<[AtomicU32]> {
        zeroed(self.slots.len())
    }

    #[inline(always)]
    fn pf<T>(r: &T) {
        #[cfg(target_arch = "x86_64")]
        unsafe {
            std::arch::x86_64::_mm_prefetch(r as *const T as *const i8, std::arch::x86_64::_MM_HINT_T0);
        }
    }

    #[inline(always)]
    fn prefetch_slot(&self, i: usize) {
        Self::pf(&self.parent[i]);
        Self::pf(&self.slots[i]);
    }

    #[inline(always)]
    pub fn prefetch(&self, h: u64) {
        #[cfg(target_arch = "x86_64")]
        unsafe {
            use std::arch::x86_64::*;
            _mm_prefetch(self.slots.as_ptr().add((h as usize) & self.mask) as *const i8, _MM_HINT_T0);
        }
    }

    /// Person id of a user id (hash `h`, bytes `name`), adding the ID as a new person when unseen.
    #[inline(always)]
    pub fn pid(&self, h: u64, name: &[u8], arena: &mut crate::bucket::Arena) -> u32 {
        let (mut i, mut base, mut mask, mut probes) = ((h as usize) & self.mask, 0, self.mask, 0usize);
        loop {
            let s = &self.slots[base + i];
            let k = s.k.load(Relaxed);
            if k == h {
                return Self::wait(s);
            }
            if k == 0 {
                match s.k.compare_exchange(0, h, AcqRel, Acquire) {
                    Ok(_) => {
                        let pid = self.npid.fetch_add(1, Relaxed);
                        self.names[pid as usize].store(pack(arena.copy_bytes(name)), Relaxed);
                        s.v.store(pid as u64 + 1, Release);
                        return pid;
                    }
                    Err(cur) if cur == h => return Self::wait(s),
                    Err(_) => {}
                }
            }
            (i, base, mask, probes) = self.next(h, i, base, mask, probes);
        }
    }

    #[inline(always)]
    fn wait(s: &Slot) -> u32 {
        loop {
            let v = s.v.load(Acquire);
            if v != 0 {
                return (v - 1) as u32;
            }
            std::hint::spin_loop();
        }
    }
}
