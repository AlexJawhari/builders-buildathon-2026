"""Label mutants by running the full suite with pytest on a patched overlay (dev image).
Usage: python label.py MUTDIR TESTS_JSON [names...]   -> writes MUTDIR/<m>/labels.json"""
import json, os, re, shutil, subprocess, sys
sys.path.insert(0, "/blast")
import patchlib, runner

mutdir, tj = sys.argv[1], sys.argv[2]
tests = json.load(open(tj))["tests"]
num = {t["id"]: t["number"] for t in tests}
names = sys.argv[3:] or sorted(os.listdir(mutdir))
SRC = "/opt/sqlglot"
pat = re.compile(r"(tests/[\w/]+\.py::\w+::\w+)")
filepat = re.compile(r"^(?:ERROR|FAILED) (tests/[\w/]+\.py)(?: - |$)")
by_file = {}
for t in tests:
    by_file.setdefault(t["file"], []).append(t["number"])
for m in names:
    lab = f"{mutdir}/{m}/labels.json"
    if os.path.exists(lab):
        continue
    fds = patchlib.parse(open(f"{mutdir}/{m}/{m}.patch").read())
    patched = {}
    for fd in fds:
        patched[fd.path] = patchlib.apply(fd, open(f"{SRC}/{fd.old}").read())
    root = f"/tmp/lab_{m}"
    shutil.rmtree(root, ignore_errors=True)
    runner.build_overlay(root, SRC, patched)
    env = dict(os.environ, PYTHONPATH=root, SKIP_INTEGRATION="0")
    r = subprocess.run([sys.executable, "-m", "pytest", "tests", "-n", "4", "-q", "-p", "no:cacheprovider",
                        "--timeout=120", "-rfE", "--tb=no"], cwd=root, env=env, capture_output=True, text=True)
    failed = set()
    unmatched = []
    for line in r.stdout.splitlines():
        if line.startswith(("FAILED", "ERROR", "SUBFAIL")):
            mm = pat.search(line)
            fm = filepat.match(line)
            if fm and fm.group(1) in by_file:
                failed.update(by_file[fm.group(1)])  # collection error: every test of the file fails
            elif mm and mm.group(1) in num:
                failed.add(num[mm.group(1)])
            elif not mm:
                failed.add(-1)
                unmatched.append(line[:200])
    tail = r.stdout.strip().splitlines()[-1:] 
    json.dump({"failing": sorted(failed), "tail": tail, "unmatched": unmatched[:5]}, open(lab, "w"))
    print(m, sorted(failed)[:10], len(failed), tail, flush=True)
    shutil.rmtree(root, ignore_errors=True)
