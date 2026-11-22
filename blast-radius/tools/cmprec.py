"""Record comparison-site outcomes per test (see cmpmap.py).
Usage (dev image, cwd /opt/sqlglot): python cmprec.py run TESTS_JSON RAWDIR [NPROC]
                                     python cmprec.py build TESTS_JSON RAWDIR OUT_Z"""
import ast, builtins, importlib, json, marshal, os, pickle, sys, time, unittest, zlib
sys.path.insert(0, "/blast")
import cmpmap

SRC = os.getcwd()
OPN = len(cmpmap.OPF)


def all_sites():
    sites, ids, n = {}, [], 0
    for dp, _, fs in sorted(os.walk(f"{SRC}/sqlglot")):
        for f in sorted(fs):
            if f.endswith(".py"):
                rel = os.path.relpath(f"{dp}/{f}", SRC)
                m = {}
                for line, col, oi, cs in cmpmap.site_list(ast.parse(open(f"{dp}/{f}", encoding="utf-8").read())):
                    m[(line, col)] = n
                    ids.append((oi, cs))
                    n += 1
                sites[rel] = m
    return sites, ids


class Wrap(ast.NodeTransformer):
    def __init__(self, smap):
        self.smap = smap

    def visit_Compare(self, node):
        self.generic_visit(node)
        sid = self.smap.get((node.lineno, node.col_offset)) if len(node.ops) == 1 else None
        if sid is None:
            return node
        new = ast.Call(ast.Name("__cmp__", ast.Load()), [ast.Constant(sid), node.left, node.comparators[0]], [])
        return ast.copy_location(new, node)


def install(sites, ids):
    acc, cnt = set(), {}
    f_op = cmpmap.OPF

    busy = [False]

    def cmpf(sid, a, b):
        oi, cs = ids[sid]
        r = f_op[oi](a, b)
        if busy[0]:  # comparison reached from __eq__ & co. while alternatives are evaluated: not recorded
            return r
        busy[0] = True
        try:
            rec(sid, oi, cs, a, b, r)
        finally:
            busy[0] = False
        return r

    plain = (int, str, float, bool, tuple, list, dict, set, frozenset, type(None), type, bytes)

    def heavy(x):  # user-defined __eq__ (Expression trees, Dialect): alternatives are slow or recurse
        return type(x) not in plain and hasattr(type(x).__eq__, "__code__")  # a Python-level __eq__

    def rec(sid, oi, cs, a, b, r):
        if oi >= 8 and hasattr(b, "__next__"):
            sig = -1
        else:
            sig, m = 0, 1
            hv = heavy(a) or heavy(b)
            for i in range(OPN):
                if i == oi:
                    v = (1 if r else 0) if type(r) is bool else 2
                elif hv and i > 1:
                    v = 2  # unknown: treated as different from anything
                else:
                    try:
                        x = f_op[i](a, b)
                        v = (1 if x else 0) if type(x) is bool else 2  # operator overloads (Expression > 0) build nodes
                    except Exception:
                        v = 2
                sig += v * m
                m *= 3
        t = None
        if cs:
            t = cmpmap.tok(b if cs == 1 else a)
            if t != "?":
                k = cnt.get(sid, 0)
                if k > 40:
                    t = "?"
                else:
                    cnt[sid] = k + 1
        acc.add((sid, sig, t))

    builtins.__cmp__ = cmpf
    builtins.__cmp_acc__ = acc
    from importlib.machinery import SourceFileLoader
    orig = SourceFileLoader.source_to_code
    pkg = SRC + "/sqlglot/"

    def source_to_code(self, data, path, *a, **k):
        p = str(path)
        if p.startswith(pkg) and p.endswith(".py"):
            tree = Wrap(sites[os.path.relpath(p, SRC)]).visit(ast.parse(data))
            return compile(ast.fix_missing_locations(tree), p, "exec", dont_inherit=True)
        return orig(self, data, path, *a, **k)

    SourceFileLoader.source_to_code = source_to_code
    sys.dont_write_bytecode = True
    sys.pycache_prefix = "/tmp/nopyc_cmp"
    return acc, cnt


def run(tests_json, raw, nproc):
    os.makedirs(raw, exist_ok=True)
    tests = json.load(open(tests_json))["tests"]
    sites, ids = all_sites()
    acc, cnt = install(sites, ids)
    sys.path.insert(0, SRC)
    import concurrent.futures as cf, runner
    cf.ProcessPoolExecutor = runner._Inline
    import sqlglot  # noqa
    from sqlglot import expressions as _e
    _orig_em = _e.Expression.error_messages

    def _em(self, args=None):  # which required-flag keys are absent, per defining class (see cmpmap flag edits)
        for c in type(self).__mro__:
            if "arg_types" in c.__dict__:
                break
        for k in c.arg_types:
            v = self.args.get(k)
            if v is None or (isinstance(v, list) and not v):
                acc.add((-1, f"{c.__module__}.{c.__qualname__}", k))
        return _orig_em(self, args)

    _e.Expression.error_messages = _em
    for f in sorted({t["file"] for t in tests}):
        importlib.import_module(f[:-3].replace("/", "."))
    pickle.dump(dict(sites=sites, ids=ids, glob=set(acc)), open(f"{raw}/_meta.pkl", "wb"))

    def child(t):
        import signal
        signal.alarm(600)  # a test that crawls under instrumentation is recorded as unknown
        acc.clear(); cnt.clear()
        mod, cls, name = t["id"].split("::")
        suite = unittest.defaultTestLoader.loadTestsFromName(f"{mod[:-3].replace('/', '.')}.{cls}.{name}")
        res = unittest.TestResult()
        suite.run(res)
        return dict(acc=set(acc), ok=res.wasSuccessful() and not res.skipped)

    running = {}
    for i, t in enumerate(tests):
        if os.path.exists(f"{raw}/{t['number']}.pkl"):
            continue
        while len(running) >= nproc:
            pid, _ = os.wait()
            running.pop(pid, None)
        pid = os.fork()
        if pid == 0:
            try:
                sys.stdout = sys.stderr = open(os.devnull, "w")
                pickle.dump(child(t), open(f"{raw}/{t['number']}.pkl", "wb"))
            finally:
                os._exit(0)
        running[pid] = t
        if i % 200 == 0:
            print(i, file=sys.__stderr__, flush=True)
    while running:
        pid, _ = os.wait()
        running.pop(pid, None)


def build(tests_json, raw, out):
    tests = json.load(open(tests_json))["tests"]
    n = len(tests)
    meta = pickle.load(open(f"{raw}/_meta.pkl", "rb"))
    ev, bad, unknown = {}, [], 0

    def add(sid, sig, t, bit):
        ev.setdefault(sid, {}).setdefault((sig, t), 0)
        ev[sid][(sig, t)] |= bit
    flags = {}
    for sid, sig, t in meta["glob"]:
        if sid == -1:
            flags[(sig, t)] = flags.get((sig, t), 0) | (1 << n)
        else:
            add(sid, sig, t, 1 << n)
    for tt in tests:
        try:
            r = pickle.load(open(f"{raw}/{tt['number']}.pkl", "rb"))
        except OSError:
            unknown |= 1 << tt["number"]  # no record: always a candidate
            continue
        if not r["ok"]:
            bad.append(tt["number"])
        for sid, sig, t in r["acc"]:
            if sid == -1:
                flags[(sig, t)] = flags.get((sig, t), 0) | (1 << tt["number"])
            else:
                add(sid, sig, t, 1 << tt["number"])
    ev = {s: {k: v.to_bytes((v.bit_length() + 7) // 8, "little") for k, v in d.items()} for s, d in ev.items()}
    open(out, "wb").write(zlib.compress(marshal.dumps(dict(sites=meta["sites"], ev=ev, flags={k: v.to_bytes((v.bit_length() + 7) // 8, "little") for k, v in flags.items()}, unknown=unknown.to_bytes((unknown.bit_length() + 7) // 8 or 1, "little"))), 9))
    print("not ok:", bad, "sites", len(meta["ids"]), "evaluated", len(ev), "bytes", os.path.getsize(out))


if __name__ == "__main__":
    if sys.argv[1] == "run":
        run(sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 4)
    else:
        build(*sys.argv[2:5])
