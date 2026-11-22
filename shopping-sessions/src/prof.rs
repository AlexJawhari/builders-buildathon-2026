//! Tiny sampling profiler (SHOP_PROF=1): SIGPROF records the interrupted RIP; dump() prints a histogram.
use std::sync::atomic::{AtomicUsize, Ordering::Relaxed};

const CAP: usize = 1 << 20;
static BUF: [AtomicUsize; CAP] = [const { AtomicUsize::new(0) }; CAP];
static N: AtomicUsize = AtomicUsize::new(0);

extern "C" fn handler(_: i32, _: *mut libc::siginfo_t, uc: *mut libc::c_void) {
    let rip = unsafe { (*(uc as *mut libc::ucontext_t)).uc_mcontext.gregs[libc::REG_RIP as usize] } as usize;
    let i = N.fetch_add(1, Relaxed);
    if i < CAP {
        BUF[i].store(rip, Relaxed);
    }
}

pub fn start() {
    unsafe {
        let mut sa: libc::sigaction = std::mem::zeroed();
        sa.sa_sigaction = handler as *const () as usize;
        sa.sa_flags = libc::SA_SIGINFO | libc::SA_RESTART;
        libc::sigaction(libc::SIGPROF, &sa, std::ptr::null_mut());
        let it = libc::itimerval {
            it_interval: libc::timeval { tv_sec: 0, tv_usec: 1000 },
            it_value: libc::timeval { tv_sec: 0, tv_usec: 1000 },
        };
        libc::setitimer(libc::ITIMER_PROF, &it, std::ptr::null_mut());
    }
}

pub fn dump() {
    let exe = std::fs::read_link("/proc/self/exe").unwrap();
    let maps = std::fs::read_to_string("/proc/self/maps").unwrap();
    let base = maps.lines().find(|l| l.ends_with(exe.to_str().unwrap())).map_or(0, |l| usize::from_str_radix(l.split('-').next().unwrap(), 16).unwrap());
    let n = N.load(Relaxed).min(CAP);
    let mut v: Vec<usize> = (0..n).map(|i| BUF[i].load(Relaxed).wrapping_sub(base)).collect();
    v.sort_unstable();
    let mut out = String::new();
    for r in v.chunk_by(|a, b| a == b) {
        out.push_str(&format!("{:x} {}\n", r[0], r.len()));
    }
    std::fs::write("/tmp/shop_prof.txt", out).unwrap();
}
