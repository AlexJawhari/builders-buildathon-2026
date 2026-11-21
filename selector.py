"""Blast Radius selector.

For every patch: map it to a conservative candidate test set (coverage + class + fixture maps built
from the pinned sqlglot commit), then either run the cheap candidates against the patched source and
print exactly the failing ones, or print the candidates split over workers when that is cheaper.
Never crashes: any internal error falls back to printing every test.
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DATA = Path(os.environ.get("BLAST_DATA", "/data"))
SRC = os.environ.get("BLAST_SRC", "/opt/sqlglot")
MAP = os.environ.get("BLAST_MAP", str(HERE / "data"))
HEAVY = 3.0  # tests at least this long (recorded seconds) are printed, never run locally
RUN_MAX = 30.0  # never run more than this many recorded seconds of candidates


def keep_heavy(rest, failed):
    """Heavy candidates are kept when the cheap tests give evidence the change matters."""
    return bool(failed) or len(rest) < MIN_EVIDENCE


MIN_EVIDENCE = 3
T0 = time.time()


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def pack(nums, ms):
    """Best split of nums over 1..8 workers (LPT per k), minimising max load + 2 s per worker."""
    if not nums:
        return [], 0
    items = sorted(set(nums), key=lambda n: -ms[n])
    best = None
    for k in range(1, min(8, len(items)) + 1):
        loads, ws = [0] * k, [[] for _ in range(k)]
        for n in items:
            i = loads.index(min(loads))
            ws[i].append(n)
            loads[i] += ms[n]
        cost = max(loads) + 2000 * k
        if best is None or cost < best[1]:
            best = (ws, cost)
    return best


def bits_to_nums(b):
    out, i = [], 0
    while b:
        low = b & -b
        out.append(low.bit_length() - 1)
        b ^= low
    return out


def main():
    tests = json.loads((DATA / "tests.json").read_text())["tests"]
    ms = [round(t["seconds"] * 1000) for t in tests]
    patches = sorted((DATA / "commits").glob("*.patch"))
    out = {}

    def emit(cid, workers):
        out[cid] = workers

    all_workers = pack(list(range(len(tests))), ms)[0]
    try:
        import mapper
        import patchlib
        import runner
        mp = mapper.Mapper(MAP, SRC, tests)
    except Exception:
        log(traceback.format_exc())
        for p in patches:
            print(json.dumps({"commit": p.stem, "workers": all_workers}))
        return

    def orig(path):
        try:
            with open(f"{SRC}/{path}", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None

    jobs_todo, cands = [], {}
    for p in patches:
        cid = p.stem
        try:
            fds = patchlib.parse(p.read_text(encoding="utf-8", errors="replace"))
            bits, notes = mp.candidates(fds, orig)
            assumed = bits_to_nums(mp.assumed & mp.all_bits)
            bits &= ~mp.assumed
            nums = bits_to_nums(bits)
            cands[cid] = nums
            heavy = [n for n in nums if tests[n]["seconds"] >= HEAVY]
            rest = [n for n in nums if tests[n]["seconds"] < HEAVY]
            s = sum(ms[n] for n in rest) / 1000
            printed = pack(rest, ms)[1] / 1000
            if not nums:
                emit(cid, pack(assumed, ms)[0])
            elif not rest or s > RUN_MAX or (not heavy and s * 1.3 + 0.3 > printed):
                emit(cid, pack(nums + assumed, ms)[0])
            else:
                patched = {}
                for fd in fds:
                    path = fd.old or fd.path
                    if fd.new is None:
                        patched[path] = None
                    elif path.endswith(".py") or path.startswith("tests/"):
                        patched[fd.path] = patchlib.apply(fd, orig(path))
                jobs_todo.append((cid, patched, rest, heavy, assumed))
            log(cid, len(nums), round(s, 2), "heavy" if heavy else "", "->", "run" if jobs_todo and jobs_todo[-1][0] == cid else "print")
        except Exception:
            log(cid, traceback.format_exc())
            emit(cid, all_workers)

    if jobs_todo:
        for m in ("duckdb", "pandas", "numpy", "unittest", "pickle", "concurrent.futures"):
            try:  # preload once; forked children inherit the imports
                __import__(m)
            except Exception:
                pass
    budget = 4.5 * len(patches) * 0.7
    running = []
    nproc = 2
    while jobs_todo or running:
        while jobs_todo and len(running) < nproc:
            cid, patched, nums, heavy, assumed = jobs_todo.pop(0)
            if time.time() - T0 > budget:
                emit(cid, pack(nums + heavy + assumed, ms)[0])
                continue
            limits = {n: max(4.0, 3 * tests[n]["seconds"]) for n in nums}
            try:
                j = runner.Job(cid, f"/tmp/ov_{cid}", SRC, patched, tests, nums, limits, time.time() + 40)
                running.append((cid, nums, heavy, assumed, j))
            except Exception:
                log(traceback.format_exc())
                emit(cid, pack(nums + heavy + assumed, ms)[0])
        for item in list(running):
            cid, nums, heavy, assumed, j = item
            r = j.poll()
            if r is not None:
                running.remove(item)
                if r.get("ok"):
                    final = list(r["failed"]) + assumed + (heavy if keep_heavy(nums, r["failed"]) else [])
                    emit(cid, pack(final, ms)[0])
                else:
                    log(cid, "run failed", r.get("err"))
                    emit(cid, pack(nums + heavy + assumed, ms)[0])
        time.sleep(0.005)
    for p in patches:
        print(json.dumps({"commit": p.stem, "workers": out.get(p.stem, all_workers)}))
    log("elapsed", round(time.time() - T0, 2))
    try:
        for ln in open("/sys/fs/cgroup/cpu.stat"):
            if ln.startswith("usage_usec"):
                log("cpu_seconds", int(ln.split()[1]) / 1e6)
    except OSError:
        pass


if __name__ == "__main__":
    main()
