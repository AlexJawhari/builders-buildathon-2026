"""Generate synthetic one-token mutants of the pinned sqlglot, as unified diffs (evaluation only).
Usage: python mutate.py SRC OUTDIR N SEED"""
import ast, difflib, io, os, random, sys, tokenize

src, out, n, seed = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
rnd = random.Random(seed)
files = []
for dp, _, fs in os.walk(src + "/sqlglot"):
    for f in fs:
        if f.endswith(".py") and "_version" not in f:
            files.append(os.path.relpath(dp + "/" + f, src))
fixtures = [os.path.relpath(dp + "/" + f, src) for dp, _, fs in os.walk(src + "/tests/fixtures") for f in fs if f.endswith(".sql") and "tpc" not in dp]
SWAP = {"and": "or", "or": "and", "True": "False", "False": "True", "is": "==", "==": "!=", "!=": "==",
        "<": "<=", "<=": "<", ">": ">=", ">=": ">", "in": "not in", "+": "-", "-": "+"}
os.makedirs(out, exist_ok=True)


def mutate_py(rel):
    text = open(f"{src}/{rel}", encoding="utf-8").read()
    toks = [t for t in tokenize.generate_tokens(io.StringIO(text).readline)]
    cand = []
    for i, t in enumerate(toks):
        if t.type == tokenize.NAME and t.string in SWAP or t.type == tokenize.OP and t.string in SWAP or t.type == tokenize.NUMBER and t.string.isdigit():
            cand.append(i)
        if t.type == tokenize.NAME and t.string == "not" and toks[i - 1].string in ("if", "and", "or", "elif", "while", "assert", "("):
            cand.append(i)
    if not cand:
        return None
    for _ in range(20):
        i = rnd.choice(cand)
        t = toks[i]
        if t.type == tokenize.NUMBER:
            new = str(int(t.string) + rnd.choice([1, -1])) if int(t.string) else "1"
        elif t.string == "not":
            new = ""
        else:
            new = SWAP[t.string]
        lines = text.split("\n")
        (r, c0), (_, c1) = t.start, t.end
        ln = lines[r - 1]
        lines[r - 1] = ln[:c0] + new + ln[c1:]
        res = "\n".join(lines)
        try:
            ast.parse(res)
        except SyntaxError:
            continue
        return text, res
    return None


def mutate_fixture(rel):
    text = open(f"{src}/{rel}", encoding="utf-8").read()
    lines = text.split("\n")
    idx = [i for i, l in enumerate(lines) if l.strip() and not l.startswith("--") and not l.startswith("#") and len(l) > 8]
    if not idx:
        return None
    i = rnd.choice(idx)
    l = lines[i]
    p = rnd.randrange(len(l))
    lines[i] = l[:p] + rnd.choice(["x", "1", " ", ""]) + l[p + 1:]
    return text, "\n".join(lines)


made = 0
while made < n:
    if rnd.random() < 0.15:
        rel = rnd.choice(fixtures)
        r = mutate_fixture(rel)
    else:
        rel = rnd.choice(files)
        r = mutate_py(rel)
    if not r or r[0] == r[1]:
        continue
    d = f"{out}/m{made:03d}"
    os.makedirs(d, exist_ok=True)
    diff = "".join(difflib.unified_diff(r[0].splitlines(True), r[1].splitlines(True), f"a/{rel}", f"b/{rel}", n=3))
    open(f"{d}/m{made:03d}.patch", "w").write("diff --git a/%s b/%s\n" % (rel, rel) + diff)
    made += 1
print("made", made)
