"""Compile tools/importrec.py output into data/impfiles.z. Usage: python build_impmap.py RAWDIR DATADIR"""
import collections, json, marshal, pickle, sys, zlib
raw, out = sys.argv[1], sys.argv[2]
files = json.load(open(raw + "/files.json"))
lines = collections.defaultdict(lambda: collections.defaultdict(int))
classes = collections.defaultdict(int)
for i, f in enumerate(files):
    r = pickle.load(open(f"{raw}/{i}.pkl", "rb"))
    bit = 1 << i
    for key in ("lines", "ilines"):
        for fn, ls in r[key].items():
            d = lines[fn]
            for l in ls:
                d[l] |= bit
    for c in r["classes"]:
        classes[c] |= bit
nb = (len(files) + 7) // 8
obj = {"files": files,
       "lines": {fn: {l: v.to_bytes(nb, "little") for l, v in d.items()} for fn, d in lines.items()},
       "classes": {c: v.to_bytes(nb, "little") for c, v in classes.items()}}
b = zlib.compress(marshal.dumps(obj), 9)
open(out + "/impfiles.z", "wb").write(b)
print(len(b), "bytes;", len(files), "files;", sum(len(d) for d in lines.values()), "lines")
