"""Compile raw per-test pickles into data/: lines/<file>.z (line->test bitset), classes.z, opens.z, imp.z."""
import glob, os, pickle, sys, zlib, collections, marshal

raw, out = sys.argv[1], sys.argv[2]
os.makedirs(out + "/lines", exist_ok=True)
lines = collections.defaultdict(lambda: collections.defaultdict(int))
classes = collections.defaultdict(int)
opens = collections.defaultdict(int)
bad = []
for f in glob.glob(raw + "/[0-9]*.pkl"):
    n = int(os.path.basename(f)[:-4])
    r = pickle.load(open(f, "rb"))
    if not r["ok"]:
        bad.append(n)
    bit = 1 << n
    for fn, ls in r["lines"].items():
        d = lines[fn]
        for l in ls:
            d[l] |= bit
    for c in r["classes"]:
        classes[c] |= bit
    for o in r["opens"]:
        opens[o.replace("/opt/sqlglot/", "")] |= bit
for f in glob.glob(raw + "/w_*.pkl"):
    n = int(os.path.basename(f).split("_")[1])
    r = pickle.load(open(f, "rb"))
    bit = 1 << n
    for fn, ls in r["lines"].items():
        d = lines[fn]
        for l in ls:
            d[l] |= bit
    for c in r["classes"]:
        classes[c] |= bit
print("not ok:", sorted(bad))
tot = 0
for fn, d in lines.items():
    b = zlib.compress(marshal.dumps({l: v.to_bytes((v.bit_length() + 7) // 8, "little") for l, v in d.items()}), 9)
    open(out + "/lines/" + fn.replace("/", "__") + ".z", "wb").write(b)
    tot += len(b)
print("lines bytes", tot, "files", len(lines))
imp = pickle.load(open(raw + "/_import.pkl", "rb"))
def dump(name, obj):
    b = zlib.compress(marshal.dumps(obj), 9)
    open(f"{out}/{name}.z", "wb").write(b)
    print(name, len(b))
dump("imp", {k: sorted(v) for k, v in imp.items()})
dump("classes", {k: v.to_bytes((v.bit_length() + 7) // 8, "little") for k, v in classes.items()})
dump("opens", {k: v.to_bytes((v.bit_length() + 7) // 8, "little") for k, v in opens.items()})
