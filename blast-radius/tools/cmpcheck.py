"""Check the comparison map against labeled patches: affected set must contain every failing test.
Usage: python cmpcheck.py  (sample + blast_work mutants)"""
import glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cmpmap, patchlib, mapper
W = "/home/alex/projects/buildathon"
SRC = W + "/blast_work/src"
tests = json.load(open(W + "/blast/sample/tests.json"))["tests"]
cm = cmpmap.CmpMap(W + "/blast/data", len(tests))
mp = mapper.Mapper(W + '/blast/data', SRC, tests)
orig = lambda p: open(SRC + "/" + p).read() if os.path.exists(SRC + "/" + p) else None
rows = []
for k, v in json.load(open(W + "/blast/sample/answers.json"))["commits"].items():
    rows.append((k, v["failing"], f"{W}/blast/sample/commits/{k}.patch"))
for lab in sorted(glob.glob(W + "/blast_work/mut*/*/labels.json")):
    d = os.path.dirname(lab)
    fl = [x for x in json.load(open(lab))["failing"] if x >= 0]
    rows.append((d.split("/")[-2] + "/" + d.split("/")[-1], fl, f"{d}/{os.path.basename(d)}.patch"))
elig = bad = 0
for cid, fl, pf in rows:
    b = cm.affected(patchlib.parse(open(pf).read()), orig, lambda fd, old: patchlib.apply(fd, old))
    if b is None:
        continue
    elig += 1
    bb, _ = mp.candidates(patchlib.parse(open(pf).read()), orig)
    b |= cm.unknown & bb
    n = bin(b).count("1")
    miss = [f for f in fl if not b >> f & 1]
    if miss:
        bad += 1
    print(cid, "affected", n, "failing", len(fl), "MISS" if miss else "", miss[:5])
print("eligible", elig, "of", len(rows), "misses", bad)
