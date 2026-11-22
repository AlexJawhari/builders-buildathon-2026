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


def mutate_py2(rel):
    """Statement-level mutants: delete a simple statement, return None, tweak a string literal."""
    text = open(f"{src}/{rel}", encoding="utf-8").read()
    tree = ast.parse(text)
    lines = text.split("\n")
    cands = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.Assign, ast.AugAssign, ast.Expr, ast.Return)) and n.lineno == n.end_lineno:
            cands.append(n)
    if not cands:
        return None
    for _ in range(30):
        n = rnd.choice(cands)
        ln = lines[n.lineno - 1]
        kind = rnd.choice(["delete", "none", "str"])
        new = list(lines)
        if kind == "delete":
            new[n.lineno - 1:n.lineno] = []
        elif kind == "none" and isinstance(n, ast.Return) and n.value is not None:
            new[n.lineno - 1] = ln[:n.col_offset] + "return None"
        elif kind == "str":
            strs = [t for t in ast.walk(n) if isinstance(t, ast.Constant) and isinstance(t.value, str) and len(t.value) > 1 and t.lineno == t.end_lineno]
            if not strs:
                continue
            t = rnd.choice(strs)
            seg = ln[t.col_offset + 1:t.end_col_offset - 1]
            if not seg:
                continue
            p = rnd.randrange(len(seg))
            new[n.lineno - 1] = ln[:t.col_offset + 1] + seg[:p] + "X" + seg[p + 1:] + ln[t.end_col_offset - 1:]
        else:
            continue
        res = "\n".join(new)
        try:
            compile(res, rel, "exec")
        except Exception:
            continue
        return text, res
    return None


def mutate_class(rel):
    """Class-body edits: flip a constant, drop a dict entry, or edit a set/dict literal."""
    text = open(f"{src}/{rel}", encoding="utf-8").read()
    tree = ast.parse(text)
    lines = text.split("\n")
    cands = []
    for c in ast.walk(tree):
        if isinstance(c, ast.ClassDef):
            for n in c.body:
                if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
                    cands.append(n)
    if not cands:
        return None
    for _ in range(30):
        n = rnd.choice(cands)
        new = list(lines)
        v = n.value
        if isinstance(v, ast.Dict) and v.keys and rnd.random() < 0.6:
            i = rnd.randrange(len(v.keys))
            k = v.values[i]
            if v.keys[i] is not None and k.lineno == k.end_lineno and v.keys[i].lineno == k.lineno:
                new[k.lineno - 1:k.lineno] = []
            else:
                continue
        else:
            consts = [x for x in ast.walk(v) if isinstance(x, ast.Constant) and x.lineno == x.end_lineno
                      and (isinstance(x.value, (bool, int)) or isinstance(x.value, str) and len(x.value) > 1)]
            if not consts:
                continue
            x = rnd.choice(consts)
            ln = lines[x.lineno - 1]
            if isinstance(x.value, bool):
                rep = str(not x.value)
            elif isinstance(x.value, int):
                rep = str(x.value + 1)
            else:
                seg = ln[x.col_offset + 1:x.end_col_offset - 1]
                if not seg:
                    continue
                p = rnd.randrange(len(seg))
                rep = ln[x.col_offset] + seg[:p] + "Q" + seg[p + 1:] + ln[x.end_col_offset - 1]
            new[x.lineno - 1] = ln[:x.col_offset] + rep + ln[x.end_col_offset:]
        res = "\n".join(new)
        try:
            compile(res, rel, "exec")
        except Exception:
            continue
        return text, res
    return None


def mutate_misc(rel):
    """Rename a function (its callers keep the old name) or append a new helper."""
    text = open(f"{src}/{rel}", encoding="utf-8").read()
    if rnd.random() < 0.5:
        tree = ast.parse(text)
        defs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and not n.name.startswith("__")]
        if not defs:
            return None
        n = rnd.choice(defs)
        lines = text.split("\n")
        ln = lines[n.lineno - 1]
        lines[n.lineno - 1] = ln.replace("def " + n.name, "def " + n.name + "_renamed", 1)
        return text, "\n".join(lines)
    return text, text.rstrip("\n") + "\n\n\ndef _unused_helper():\n    return 1\n"


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


tests_py = [os.path.relpath(dp + "/" + f, src) for dp, _, fs in os.walk(src + "/tests") for f in fs if f.startswith("test_") and f.endswith(".py")]
MODE = os.environ.get("MODE", "")
made = 0
while made < n:
    if MODE == "div":
        r_ = rnd.random()
        if r_ < 0.4:
            rel = rnd.choice([f for f in files if any(k in f for k in ("dialects/", "parsers/", "generators/", "expressions/", "tokens", "parser.py", "generator.py", "typing/"))])
            r = mutate_class(rel)
        elif r_ < 0.55:
            rel = rnd.choice(tests_py)
            r = mutate_py(rel)
        elif r_ < 0.7:
            rel = rnd.choice(files)
            r = mutate_misc(rel)
        else:
            rel = rnd.choice(files)
            r = mutate_py(rel)
        if not r or r[0] == r[1]:
            continue
        d = f"{out}/m{made:03d}"
        os.makedirs(d, exist_ok=True)
        diff = "".join(difflib.unified_diff(r[0].splitlines(True), r[1].splitlines(True), f"a/{rel}", f"b/{rel}", n=3))
        if rnd.random() < 0.2:  # multi-file: add an independent second edit
            rel2 = rnd.choice(files)
            r2 = mutate_py(rel2)
            if r2 and r2[0] != r2[1] and rel2 != rel:
                diff += "diff --git a/%s b/%s\n" % (rel2, rel2) + "".join(difflib.unified_diff(r2[0].splitlines(True), r2[1].splitlines(True), f"a/{rel2}", f"b/{rel2}", n=3))
        open(f"{d}/m{made:03d}.patch", "w").write("diff --git a/%s b/%s\n" % (rel, rel) + diff)
        made += 1
        continue
    if rnd.random() < 0.15:
        rel = rnd.choice(fixtures)
        r = mutate_fixture(rel)
    else:
        rel = rnd.choice(files)
        r = mutate_py2(rel) if rnd.random() < float(os.environ.get("P2", "0")) else mutate_py(rel)
    if not r or r[0] == r[1]:
        continue
    d = f"{out}/m{made:03d}"
    os.makedirs(d, exist_ok=True)
    diff = "".join(difflib.unified_diff(r[0].splitlines(True), r[1].splitlines(True), f"a/{rel}", f"b/{rel}", n=3))
    open(f"{d}/m{made:03d}.patch", "w").write("diff --git a/%s b/%s\n" % (rel, rel) + diff)
    made += 1
print("made", made)
