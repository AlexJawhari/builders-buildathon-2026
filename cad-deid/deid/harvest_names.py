"""Part / drawing codes carried by file and folder names below the repository level (counted over files). The same codes are printed in
the drawings' title blocks and bills of material, so they are applied inside PDF text (never in names or native CAD content)."""
import collections, re
from .pdfharv2 import STEMCODE


def codes_in(name):
    out = set()
    for m in STEMCODE.finditer(name):
        v = m.group(1).strip('-_.')
        segs = re.split(r'([-_.])', v)
        last = max((i for i, s in enumerate(segs) if len(re.findall(r'\d', s)) >= 3), default=-1)
        out.add(v)
        if last >= 0:
            out.add(''.join(segs[:last + 1]).strip('-_.'))
            out |= {s for s in segs[::2] if len(s) >= 5}
    return out


def path_codes(manifest):
    c = collections.Counter(); seen = set()
    for l in open(manifest):
        if l.startswith('#'): continue
        p = l.split('\t', 1)[0].split('/')
        if len(p) < 5: continue
        segs = p[3:-1] + [re.sub(r'\.[A-Za-z0-9]{1,5}$', '', p[-1])]
        for s in segs:
            s = re.sub(r'\.extracted$', '', s)
            if (s, p[2]) in seen: continue
            seen.add((s, p[2]))
            for v in codes_in(s): c[v] += 1
    return c
