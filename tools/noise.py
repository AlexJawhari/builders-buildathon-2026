"""Tests that fail in our in-process runner on the PRISTINE tree (state leakage); they carry no evidence.
Usage (image, cwd /app): python tools/noise.py OUT.json"""
import json, sys, time
sys.path.insert(0, "/app")
import runner
tests = json.load(open("/data/tests.json"))["tests"]
nums = [t["number"] for t in tests if t["seconds"] < 3]
out = {}
for mode in range(2):  # full list, and each class alone (the selector runs mixed subsets)
    job = runner.Job("noise", "/tmp/ov_noise%d" % mode, "/opt/sqlglot", {}, tests, nums, {n: 30 for n in nums}, time.time() + 600)
    r = None
    while r is None:
        r = job.poll(); time.sleep(0.05)
    out[mode] = r["failed"]
    print(mode, [tests[n]["id"] for n in r["failed"]], r.get("cpu"))
print("NOISE", json.dumps(sorted(set(out[0]) | set(out[1]))))
