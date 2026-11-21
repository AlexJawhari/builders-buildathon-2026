"""Per-test CPU under the selector's runner regime (inline pool, one suite per class); skips heavy tests.
Usage (dev image): python cpuprof.py TESTS_JSON OUT_JSON"""
import json, sys, time, unittest, importlib
sys.path.insert(0, "/opt/sqlglot"); sys.path.insert(0, "/blast")
import concurrent.futures as cf, runner
cf.ProcessPoolExecutor = runner._Inline
tests = json.load(open(sys.argv[1]))["tests"]
by = {}
for t in tests:
    if t["seconds"] < 3:
        f, c, m = t["id"].split("::")
        by.setdefault((f, c), []).append((m, t["number"]))
out = {}
for (f, c), items in by.items():
    cls = getattr(importlib.import_module(f[:-3].replace("/", ".")), c)
    byname = dict(items)
    class R(unittest.TestResult):
        def startTest(self, test):
            super().startTest(test); self.t0 = time.process_time()
        def stopTest(self, test):
            out[byname[test._testMethodName]] = round(time.process_time() - self.t0, 4); super().stopTest(test)
    t0 = time.process_time()
    try:
        cls.setUpClass()
        out["class:%s::%s" % (f, c)] = round(time.process_time() - t0, 4)
        cls.tearDownClass()
    except Exception:
        pass
    unittest.TestSuite([cls(m) for m, _ in items]).run(R())
json.dump(out, open(sys.argv[2], "w"))
print(len(out), sum(v for k, v in out.items() if not k.startswith('class:')))
