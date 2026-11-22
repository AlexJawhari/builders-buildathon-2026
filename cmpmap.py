"""Comparison-site outcome map: proves that swapping one comparison operator (or the constant
operand of a comparison) cannot change the outcome of any recorded evaluation, so no test can
behave differently.

Build time (tools/cmprec.py): every single-operator comparison in sqlglot is wrapped; per test we
record, for each evaluation, a signature = result of all ten operators on the actual operands, plus
a token for the other operand when one side is a literal. Selection time: `affected(...)` diffs the
patched and pristine ASTs; if the only differences are comparison operators / literal operands it
returns the set of tests with an evaluation that would change, else None (patch not eligible).
"""
import ast
import json
import marshal
import operator
import os
import re
import zlib

OPS = (ast.Is, ast.IsNot, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn)
OPF = (operator.is_, operator.is_not, operator.eq, operator.ne, operator.lt, operator.le,
       operator.gt, operator.ge, lambda a, b: a in b, lambda a, b: a not in b)
PRIM = (int, float, str, bool, type(None))


def op_index(node):
    for i, t in enumerate(OPS):
        if type(node) is t:
            return i
    return None


def const_side(node):
    """0: no literal operand, 1: literal on the left, 2: literal on the right."""
    if isinstance(node.left, ast.Constant) and type(node.left.value) in PRIM:
        return 1
    if isinstance(node.comparators[0], ast.Constant) and type(node.comparators[0].value) in PRIM:
        return 2
    return 0


def site_list(tree):
    """[(line, col, op index, const side)] for every single-operator comparison, in walk order."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Compare) and len(n.ops) == 1:
            i = op_index(n.ops[0])
            if i is not None:
                out.append((n.lineno, n.col_offset, i, const_side(n)))
    return out


def tok(v):
    return (type(v).__name__, v) if type(v) in PRIM and not (type(v) is str and len(v) > 40) else "?"


def digit(sig, i):
    return (sig // 3 ** i) % 3


def strip_docstrings(tree):
    """Docstrings are inert here (only core.py reads __doc__, and only to default it to "")."""
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body:
            f = n.body[0]
            if isinstance(f, ast.Expr) and isinstance(f.value, ast.Constant) and isinstance(f.value.value, str):
                n.body = n.body[1:] or [ast.Pass(lineno=f.lineno, col_offset=0)]
    return tree


_ANN = {"int", "str", "bool", "float", "bytes", "list", "dict", "tuple", "set", "object"}


def _plain_def(n):
    """A def whose creation at import cannot fail or have effects: no decorators, constant defaults, trivial annotations."""
    if not isinstance(n, ast.FunctionDef) or n.decorator_list or n.name.startswith("__"):
        return False
    a = n.args
    ann = [x.annotation for x in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg] if x is not None] + [n.returns]
    return (all(isinstance(d, ast.Constant) for d in a.defaults + [d for d in a.kw_defaults if d is not None])
            and all(x is None or (isinstance(x, ast.Name) and x.id in _ANN) or (isinstance(x, ast.Constant) and x.value is None)
                    for x in ann))


def drop_unused_defs(old_tree, new_tree, new_text, mentions):
    """Remove new top-level plain functions that nothing mentions anywhere (never called, never looked up by name)."""
    have = {n.name for n in old_tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    keep = []
    for n in new_tree.body:
        if n.name in have if isinstance(n, ast.FunctionDef) else True:
            keep.append(n)
            continue
        if _plain_def(n) and not mentions(n.name):
            seg = ast.get_source_segment(new_text, n) or ""
            if len(re.findall(r"\b%s\b" % re.escape(n.name), new_text)) == len(re.findall(r"\b%s\b" % re.escape(n.name), seg)):
                continue
        keep.append(n)
    new_tree.body = keep


class _Diff:
    def __init__(self):
        self.edits = []  # (line, col, old op idx, new op idx, const side, (old, new) literal or None)
        self.flags = []  # (class qualname, key) whose required flag in arg_types flipped
        self.stack = []

    def walk(self, a, b):
        if type(a) is not type(b):
            return False
        if isinstance(a, ast.Compare) and len(a.ops) == 1 and len(b.ops) == 1:
            oi, ni = op_index(a.ops[0]), op_index(b.ops[0])
            if oi is None or ni is None:
                return False
            side = const_side(a)
            cc = None
            ok = True
            pairs = [(a.left, b.left), (a.comparators[0], b.comparators[0])]
            for k, (x, y) in enumerate(pairs, 1):
                if (side == k and isinstance(y, ast.Constant) and isinstance(x, ast.Constant)
                        and (type(x.value), x.value) != (type(y.value), y.value) and type(y.value) in PRIM):
                    cc = (x.value, y.value)
                else:
                    ok = ok and self.walk(x, y)
            if not ok:
                return False
            if oi != ni or cc is not None:
                self.edits.append((a.lineno, a.col_offset, oi, ni, side, cc))
            return True
        if isinstance(a, ast.ClassDef):
            self.stack.append(a.name)
            try:
                return self.fields(a, b)
            finally:
                self.stack.pop()
        if (isinstance(a, ast.Assign) and self.stack and len(a.targets) == 1
                and isinstance(a.targets[0], ast.Name) and a.targets[0].id == "arg_types"
                and isinstance(a.value, ast.Dict) and isinstance(b.value, ast.Dict)
                and len(a.value.keys) == len(b.value.keys) and self.walk(a.targets[0], b.targets[0])):
            for k1, k2, v1, v2 in zip(a.value.keys, b.value.keys, a.value.values, b.value.values):
                if k1 is None:  # ** unpacking
                    if k2 is not None or not self.walk(v1, v2):
                        return False
                    continue
                if not self.walk(k1, k2):
                    return False
                if not isinstance(k1, ast.Constant):
                    if not self.walk(v1, v2):
                        return False
                    continue
                if (isinstance(v1, ast.Constant) and isinstance(v2, ast.Constant) and type(v1.value) is bool
                        and type(v2.value) is bool and v1.value != v2.value):
                    self.flags.append((".".join(self.stack), k1.value))
                elif not self.walk(v1, v2):
                    return False
            return True
        return self.fields(a, b)

    def fields(self, a, b):
        for f in a._fields:
            x, y = getattr(a, f, None), getattr(b, f, None)
            if isinstance(x, list):
                if not isinstance(y, list) or len(x) != len(y):
                    return False
                for p, q in zip(x, y):
                    if isinstance(p, ast.AST):
                        if not self.walk(p, q):
                            return False
                    elif p != q:
                        return False
            elif isinstance(x, ast.AST):
                if not isinstance(y, ast.AST) or not self.walk(x, y):
                    return False
            elif (type(x), x) != (type(y), y):
                return False
        return True


class CmpMap:
    def __init__(self, data_dir, ntests):
        with open(f"{data_dir}/cmp.z", "rb") as f:
            d = marshal.loads(zlib.decompress(f.read()))
        self.sites = d["sites"]  # file -> {(line, col): site id}
        self.ev = d["ev"]  # site id -> {(sig, tok): bitset bytes}
        self.n = ntests
        self.flags = {k: int.from_bytes(v, "little") for k, v in d.get("flags", {}).items()}
        self.unknown = int.from_bytes(d.get("unknown", b""), "little")  # tests with no recording

    def affected(self, fds, orig, parse_new, mentions=lambda name: True):
        """Bitset of tests that may behave differently, or None when the patch is not eligible.
        Tests with no recording (self.unknown) are left to the caller."""
        edits, flags = [], []
        for fd in fds:
            if not fd.path.endswith(".py"):
                return None
            if fd.old != fd.new or fd.old is None:
                return None
            if not fd.path.startswith("sqlglot/"):
                return None
            old = orig(fd.old)
            new = parse_new(fd, old)
            if old is None or new is None:
                return None
            try:
                ta, tb = strip_docstrings(ast.parse(old)), strip_docstrings(ast.parse(new))
            except SyntaxError:
                return None
            if len(tb.body) > len(ta.body):
                drop_unused_defs(ta, tb, new, mentions)
            d = _Diff()
            if not d.walk(ta, tb):
                return None
            mod = fd.path[:-3].replace("/", ".")
            flags += [(f"{mod}.{q}", k) for q, k in d.flags]
            smap = self.sites.get(fd.path)
            for e in d.edits:
                sid = (smap or {}).get((e[0], e[1]))
                if sid is None:
                    return None
                edits.append((sid,) + e[2:])
        self.had_edits = bool(edits or flags)
        bits = 0
        for fk in flags:
            bits |= self.flags.get(fk, 0)
        for sid, oi, ni, side, cc in edits:
            for (sig, tk), b in self.ev.get(sid, {}).items():
                if cc is not None:
                    if tk == "?" or sig == -1:
                        hit = True
                    else:  # literal changed: recompute both outcomes on the recorded other operand
                        v = tk[1]
                        a0, a1 = (cc[0], v) if side == 1 else (v, cc[0])
                        b0, b1 = (cc[1], v) if side == 1 else (v, cc[1])
                        try:
                            hit = bool(OPF[oi](a0, a1)) != bool(OPF[ni](b0, b1))
                        except Exception:
                            hit = True
                else:
                    hit = sig == -1 or digit(sig, oi) != digit(sig, ni) or digit(sig, oi) == 2
                if hit:
                    bits |= int.from_bytes(b, "little")
        if bits >> self.n:
            return None  # an evaluation during import (bit n) would change
        return bits
