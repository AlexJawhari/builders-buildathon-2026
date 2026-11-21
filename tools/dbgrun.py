"""Run all non-heavy tests on one patch with the selector's runner; print failing ids. usage: dbgrun.py PATCH"""
import json, sys, time
sys.path.insert(0, "/blast")
import patchlib, runner
tests = json.load(open("/tests.json"))["tests"]
fds = patchlib.parse(open(sys.argv[1]).read())
patched = {fd.path: patchlib.apply(fd, open("/opt/sqlglot/" + fd.old).read()) for fd in fds}
nums = [t["number"] for t in tests if t["seconds"] < 3]
j = runner.Job("dbg", "/tmp/ov_dbg", "/opt/sqlglot", patched, tests, nums, {}, time.time() + 300, check=[])
while True:
    r = j.poll()
    if r is not None:
        break
    time.sleep(0.05)
print({k: v for k, v in r.items() if k != "failed"})
for n in r["failed"]:
    print(n, tests[n]["id"])
