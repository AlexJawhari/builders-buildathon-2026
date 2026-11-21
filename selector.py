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
    try:  # measured CPU per test under our runner (sequential, no worker pool); recorded seconds otherwise
        cpu_map = json.loads((Path(MAP) / "cpu.json").read_text())
    except Exception:
        cpu_map = {}
    cpu_est = [cpu_map.get(str(t["number"]), t["seconds"]) for t in tests]
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

    jobs_todo = []
    for p in patches:
        cid = p.stem
        try:
            fds = patchlib.parse(p.read_text(encoding="utf-8", errors="replace"))
            bits, notes = mp.candidates(fds, orig)
            assumed = bits_to_nums(mp.assumed & mp.all_bits)
            bits &= ~mp.assumed
            nums = bits_to_nums(bits)
            check = sorted({mp.modname(f) for f in mp.import_files if f.startswith("sqlglot/")})
            heavy = [n for n in nums if tests[n]["seconds"] >= HEAVY]
            rest = [n for n in nums if tests[n]["seconds"] < HEAVY]
            s = sum(cpu_est[n] for n in rest)
            printed = pack(rest, ms)[1] / 1000
            run = bool(rest) and s <= RUN_MAX and (heavy or s * 1.1 + 0.4 <= printed)
            direct = pack(nums + assumed, ms)[0] if not run else None
            if run or check:
                patched = {}
                for fd in fds:
                    path = fd.old or fd.path
                    if fd.new is None:
                        patched[path] = None
                    elif path.endswith(".py") or path.startswith("tests/"):
                        patched[fd.path] = patchlib.apply(fd, orig(path))
                jobs_todo.append(dict(cid=cid, patched=patched, run=rest if run else [], heavy=heavy, assumed=assumed,
                                      nums=nums, check=check, direct=direct,
                                      extra={f: mp.import_fail_bits(f) for f in mp.import_files if f.startswith("sqlglot/")}))
            else:
                emit(cid, direct if direct is not None else pack(assumed, ms)[0])
            log(cid, len(nums), round(s, 2), "heavy" if heavy else "", "->", "run" if run else ("check" if check else "print"))
        except Exception:
            log(cid, traceback.format_exc())
            emit(cid, all_workers)

    if any(j["run"] for j in jobs_todo):
        for m in ("duckdb", "pandas", "numpy", "unittest", "pickle", "concurrent.futures"):
            try:  # preload once; forked children inherit the imports
                __import__(m)
            except Exception:
                pass
    budget = 4.5 * len(patches) * 0.7
    running = []
    nproc = 2

    def fallback(j):
        return pack(j["nums"] + j["assumed"] + (bits_to_nums(0) if not j["extra"] else
                    bits_to_nums(__import__("functools").reduce(lambda x, y: x | y, j["extra"].values()))), ms)[0]

    while jobs_todo or running:
        while jobs_todo and len(running) < nproc:
            j = jobs_todo.pop(0)
            if time.time() - T0 > budget:
                emit(j["cid"], fallback(j))
                continue
            limits = {n: max(4.0, 3 * tests[n]["seconds"]) for n in j["run"]}
            try:
                job = runner.Job(j["cid"], f"/tmp/ov_{j['cid']}", SRC, j["patched"], tests, j["run"], limits,
                                 time.time() + 40, failfast=bool(j["heavy"]), check=j["check"])
                running.append((j, job))
            except Exception:
                log(traceback.format_exc())
                emit(j["cid"], fallback(j))
        for item in list(running):
            j, job = item
            r = job.poll()
            if r is None:
                continue
            running.remove(item)
            cid = j["cid"]
            log(cid, "ran", len(j["run"]), "failed", len(r.get("failed", [])), "cpu", r.get("cpu"), r.get("importerr", ""))
            if not r.get("ok"):
                log(cid, "run failed", r.get("err"))
                emit(cid, fallback(j))
            elif r.get("importerr"):
                extra = 0
                for f in j["extra"]:
                    if mp.modname(f) in r["importerr"]:
                        extra |= j["extra"][f]
                emit(cid, pack(j["nums"] + j["assumed"] + bits_to_nums(extra), ms)[0])
            elif not j["run"]:
                emit(cid, j["direct"])
            elif r.get("stopped"):  # heavy kept anyway: the cheap tests ride along for free
                emit(cid, pack(j["run"] + j["assumed"] + j["heavy"], ms)[0])
            else:
                final = list(r["failed"]) + j["assumed"] + (j["heavy"] if keep_heavy(j["run"], r["failed"]) else [])
                emit(cid, pack(final, ms)[0])
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
