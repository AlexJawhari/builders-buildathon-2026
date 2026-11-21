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


MIN_EVIDENCE = 6
CPU_MUL = 1.6  # scorer CPU per local CPU second (measured on practice)
CPU_FIX = 0.6  # per-job fixed CPU on the scorer
P_PASS = 0.55  # share of candidate sets that turn out to fail nothing
WIDE_COUNT = 0.5  # ... or when it already names this fraction of all tests
WIDE = 0.8  # print everything when the chosen set costs this fraction of printing everything
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
    try:
        canary_all = json.loads((Path(MAP) / "canary.json").read_text())
    except Exception:
        canary_all = []
    cpu_est = [cpu_map.get(str(t["number"]), t["seconds"]) for t in tests]
    patches = sorted((DATA / "commits").glob("*.patch"))
    out = {}

    def emit(cid, workers):
        out[cid] = workers

    all_cost = pack(list(range(len(tests))), ms)[1]

    def emit_set(cid, nums):
        """Print `nums` packed over workers, or every test when that is nearly as cheap: a wide
        change can fail tests our per-test maps do not tie to it (shared-process contamination)."""
        w, c = pack(nums, ms)
        out[cid] = pack(list(range(len(tests))), ms)[0] if c >= WIDE * all_cost or len(set(nums)) >= WIDE_COUNT * len(tests) else w

    all_workers = pack(list(range(len(tests))), ms)[0]
    try:
        import hashlib
        import mapper
        import patchlib
        import runner
        want = (Path(MAP) / "ids.sha1").read_text().strip()
        if hashlib.sha1("\n".join(t["id"] for t in tests).encode()).hexdigest() != want:
            raise RuntimeError("tests.json differs from the map's test list")
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
            s = sum(cpu_est[n] for n in rest) + sum(
                cpu_map.get("class:%s::%s" % (f, c), 0.0)
                for f, c in {tuple(tests[n]["id"].split("::")[:2]) for n in rest})
            printed = pack(rest, ms)[1] / 1000
            cost_run = CPU_MUL * s + CPU_FIX
            full = pack(rest + heavy, ms)[1] / 1000
            run = bool(rest) and s <= RUN_MAX and cost_run < P_PASS * full
            direct = nums + assumed if not run else None
            if run or check:
                patched = {}
                for fd in fds:
                    path = fd.old or fd.path
                    if fd.new is None:
                        patched[path] = None
                    elif path.endswith(".py") or path.startswith("tests/"):
                        patched[fd.path] = patchlib.apply(fd, orig(path))
                canary = [n for n in canary_all if n not in set(nums)] if check else []
                jobs_todo.append(dict(cid=cid, patched=patched, run=rest if run else [], heavy=heavy, assumed=assumed, canary=canary,
                                      nums=nums, check=check, direct=direct,
                                      extra={f: mp.import_fail_bits(f) for f in mp.import_files if f.startswith("sqlglot/")}))
            else:
                emit_set(cid, direct if direct is not None else assumed)
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
        extra = 0
        for v in j["extra"].values():
            extra |= v
        return j["nums"] + j["assumed"] + bits_to_nums(extra)

    while jobs_todo or running:
        while jobs_todo and len(running) < nproc:
            j = jobs_todo.pop(0)
            if time.time() - T0 > budget:
                emit_set(j["cid"], fallback(j))
                continue
            to_run = j["run"] + j["canary"]
            limits = {n: max(4.0, 3 * tests[n]["seconds"]) for n in to_run}
            try:
                job = runner.Job(j["cid"], f"/tmp/ov_{j['cid']}", SRC, j["patched"], tests, to_run, limits,
                                 time.time() + 40, failfast=bool(j["heavy"] and j["run"]), check=j["check"],
                                 smoke=bool(j["check"]))
                running.append((j, job))
            except Exception:
                log(traceback.format_exc())
                emit_set(j["cid"], fallback(j))
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
                emit_set(cid, fallback(j))
            elif r.get("importerr"):
                extra = 0
                for f in j["extra"]:
                    if mp.modname(f) in r["importerr"]:
                        extra |= j["extra"][f]
                emit_set(cid, j["nums"] + j["assumed"] + bits_to_nums(extra))
            elif r.get("smoke_fail") or (set(r.get("failed", [])) & set(j["canary"])):
                log(cid, "canary alarm", r.get("smoke_fail"), sorted(set(r.get("failed", [])) & set(j["canary"]))[:5])
                emit(cid, all_workers)  # wide blast radius: print everything
            elif not j["run"]:
                emit_set(cid, j["direct"])
            elif r.get("stopped"):  # heavy kept anyway: the cheap tests ride along for free
                emit_set(cid, j["run"] + j["assumed"] + j["heavy"])
            else:
                failed = [n for n in r["failed"] if n not in set(j["canary"])]
                final = failed + j["assumed"] + (j["heavy"] if keep_heavy(j["run"], failed) else [])
                emit_set(cid, final)
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
