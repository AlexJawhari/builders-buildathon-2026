"""Offline map collector: run each test in its own forked child (clean sqlglot state),
record executed sqlglot lines, touched classes, opened files, pass/fail and CPU.
Usage (inside the dev image, cwd /opt/sqlglot): python collect.py tests.json OUTDIR [NPROC] [filter-substring]
"""
import json, os, pickle, sys, time, unittest, importlib, io
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SRC = os.getcwd()
PKG = os.path.join(SRC, "sqlglot") + os.sep
mon = sys.monitoring
E = mon.events


def start_tool(tool_id, name, line_cb, start_cb, disable):
    mon.use_tool_id(tool_id, name)
    def on_line(code, line):
        fn = code.co_filename
        if fn.startswith(PKG):
            line_cb(fn, line)
        return mon.DISABLE if disable else None
    mon.register_callback(tool_id, E.LINE, on_line)
    ev = E.LINE
    if start_cb:
        mon.register_callback(tool_id, E.PY_START, start_cb)
        ev |= E.PY_START
    mon.set_events(tool_id, ev)


def main():
    tests_json, outdir = sys.argv[1], sys.argv[2]
    nproc = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    filt = sys.argv[4] if len(sys.argv) > 4 else ""
    os.makedirs(outdir, exist_ok=True)
    tests = json.load(open(tests_json))["tests"]
    sys.path.insert(0, SRC)
    # import-time lines
    imp = {}
    def il(fn, line): imp.setdefault(fn[len(SRC) + 1:], set()).add(line)
    start_tool(1, "imp", il, None, False)
    import sqlglot  # noqa
    files = sorted({t["file"] for t in tests})
    for f in files:
        try:
            importlib.import_module(f[:-3].replace("/", "."))
        except Exception as e:
            print("import fail", f, e, file=sys.stderr)
    mon.set_events(1, 0); mon.free_tool_id(1)
    pickle.dump(imp, open(os.path.join(outdir, "_import.pkl"), "wb"))

    def child(t):
        from recorder import Recorder
        opens = set()
        def audit(ev, args):
            if ev == "open" and isinstance(args[0], str):
                opens.add(args[0])
        sys.addaudithook(audit)
        os.environ["COLLECT_TAG"] = str(t["number"]); os.environ["COLLECT_SRC"] = SRC; os.environ["COLLECT_OUT"] = outdir
        os.environ["PYTHONPATH"] = os.path.dirname(os.path.abspath(__file__)) + "/sc:" + SRC
        rec = Recorder(SRC, 2)
        rec.start()
        mod, cls, name = t["id"].split("::")
        suite = unittest.defaultTestLoader.loadTestsFromName(
            f"{mod[:-3].replace('/', '.')}.{cls}.{name}")
        res = unittest.TestResult()
        c0 = time.process_time()
        suite.run(res)
        cpu = time.process_time() - c0
        lines, ilines, cn = rec.stop()
        ok = res.wasSuccessful() and res.testsRun >= 1 and not res.skipped
        return dict(lines=lines, ilines=ilines, classes=cn, opens=sorted(o for o in opens if "/tests/" in o or "fixtures" in o),
                    ok=ok, cpu=cpu, skipped=len(res.skipped), err=[str(x[1])[-300:] for x in res.failures + res.errors][:2])

    only = set(map(int, os.environ["ONLY"].split(","))) if os.environ.get("ONLY") else None
    todo = [t for t in tests if filt in t["id"] and (only is None or t["number"] in only)]
    running = {}
    def reap(block):
        while running:
            try:
                pid, st = os.waitpid(-1, 0 if block else os.WNOHANG)
            except ChildProcessError:
                running.clear(); return
            if pid == 0: return
            running.pop(pid, None)
            if block: return
    t0 = time.time()
    for i, t in enumerate(todo):
        while len(running) >= nproc: reap(True)
        pid = os.fork()
        if pid == 0:
            try:
                r = child(t)
                pickle.dump(r, open(os.path.join(outdir, f"{t['number']}.pkl"), "wb"))
            except BaseException as e:
                print("child fail", t["id"], repr(e), file=sys.stderr)
                os._exit(1)
            os._exit(0)
        running[pid] = t
        if i % 200 == 0: print(i, time.time() - t0, file=sys.stderr)
    while running: reap(True)

if __name__ == "__main__":
    main()
