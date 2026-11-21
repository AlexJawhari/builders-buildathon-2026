"""Unified-diff parsing and in-memory application, plus the comment-only token filter."""
import io
import re
import tokenize

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class FileDiff:
    def __init__(self, old, new):
        self.old, self.new = old, new  # paths without a/ b/ ; None for /dev/null
        self.hunks = []  # (old_start, [(op, text)])

    @property
    def path(self):
        return self.new or self.old


def _path(s):
    s = s.split("\t")[0].strip()
    if s == "/dev/null":
        return None
    return s[2:] if s[:2] in ("a/", "b/") else s


def parse(text):
    """Return a list of FileDiff. Tolerant: unknown header lines are skipped."""
    files, cur, old = [], None, None
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            cur = FileDiff(_path(ln[4:]), _path(lines[i + 1][4:]))
            files.append(cur)
            i += 2
            continue
        m = HUNK.match(ln)
        if m and cur is not None:
            os_, ol = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
            ns_, nl = int(m.group(3)), int(m.group(4) if m.group(4) is not None else 1)
            body, i = [], i + 1
            while i < len(lines) and (ol > 0 or nl > 0 or (lines[i].startswith("\\"))):
                l = lines[i]
                if l.startswith("\\"):
                    i += 1
                    continue
                op, t = (l[:1] or " "), l[1:]
                if op == "-":
                    ol -= 1
                elif op == "+":
                    nl -= 1
                else:
                    ol -= 1
                    nl -= 1
                    op = " "
                body.append((op, t))
                i += 1
            cur.hunks.append((os_, body))
            continue
        i += 1
    return files


def apply(fd, old_text):
    """Apply a FileDiff to old_text (str or None). Returns new text or None for deletion."""
    if fd.new is None:
        return None
    src = old_text.split("\n") if old_text is not None else []
    out, pos = [], 0
    for start, body in fd.hunks:
        s = max(start - 1, 0) if (start or any(op != "+" for op, _ in body)) else 0
        out.extend(src[pos:s])
        pos = s
        for op, t in body:
            if op == " ":
                out.append(t)
                pos += 1
            elif op == "-":
                pos += 1
            else:
                out.append(t)
    out.extend(src[pos:])
    return "\n".join(out)


def changed_old_lines(fd):
    """(removed_lines, insert_points): 1-based old line numbers removed; for pure insertions the
    (before, after) old line pair around the insertion."""
    removed, ins = set(), []
    for start, body in fd.hunks:
        o = start
        run = False
        for op, _ in body:
            if op == "-":
                removed.add(o)
                o += 1
                run = False
            elif op == "+":
                if not run:
                    ins.append((o - 1, o))
                    run = True
            else:
                o += 1
                run = False
    return removed, ins


_SKIP = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.ENCODING, tokenize.ENDMARKER,
         tokenize.INDENT, tokenize.DEDENT}


def tokens_equal(a, b):
    """True when two python sources have identical token streams ignoring comments and layout.
    Any tokenize failure counts as different."""
    def toks(s):
        return [(t.type, t.string) for t in tokenize.generate_tokens(io.StringIO(s).readline)
                if t.type not in _SKIP]
    try:
        return toks(a) == toks(b)
    except Exception:
        return False


def changed_spans(fd):
    """{old line: (start_col, end_col)} for lines replaced 1:1 by a new line: the differing
    middle after stripping the common prefix and suffix."""
    spans = {}
    for start, body in fd.hunks:
        o, i = start, 0
        while i < len(body):
            if body[i][0] == " ":
                o += 1
                i += 1
                continue
            minus, plus = [], []
            while i < len(body) and body[i][0] == "-":
                minus.append((o, body[i][1]))
                o += 1
                i += 1
            while i < len(body) and body[i][0] == "+":
                plus.append(body[i][1])
                i += 1
            if len(minus) == len(plus):
                for (ln, old), new in zip(minus, plus):
                    p = 0
                    while p < min(len(old), len(new)) and old[p] == new[p]:
                        p += 1
                    s = 0
                    while s < min(len(old), len(new)) - p and old[len(old) - 1 - s] == new[len(new) - 1 - s]:
                        s += 1
                    spans[ln] = (p, max(len(old) - s, p + 1))
    return spans
