"""Assemble an eval dir (commits/, tests.json, answers.json) from labeled mutants.
Usage: python mkeval.py MUTDIR TESTS_JSON EVALDIR"""
import json, os, shutil, sys
mutdir, tj, ev = sys.argv[1:4]
os.makedirs(ev + "/commits", exist_ok=True)
shutil.copy(tj, ev + "/tests.json")
ans = {}
for m in sorted(os.listdir(mutdir)):
    lab = f"{mutdir}/{m}/labels.json"
    if not os.path.exists(lab):
        continue
    shutil.copy(f"{mutdir}/{m}/{m}.patch", f"{ev}/commits/{m}.patch")
    ans[m] = {"failing": [x for x in json.load(open(lab))["failing"] if x >= 0], "kind": "mut"}
json.dump({"commits": ans}, open(ev + "/answers.json", "w"))
print(len(ans), "mutants,", sum(1 for v in ans.values() if v["failing"]), "with failures")
