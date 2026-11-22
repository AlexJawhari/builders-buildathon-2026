"""Attempt-3: attempt-2 dictionary entries that are not identities (materials, units, drawing/license terms, standards, plain words) and are
not implied by a corpus path. They stay in the dictionary for paths/other formats (the attempt-2 outputs are reused) but are not applied
to PDF text, where they destroyed engineering text (e.g. 'ALUMINUM', 'DWG. NO').  usage: python -m deid.pdfdrop idents.json state.db out.json"""
import json, re, sqlite3, sys
from . import denylist, discover
from .ident import norm


def main(idp, dbp, out):
    ids = json.load(open(idp)); prot = set()
    for (rel,) in sqlite3.connect(dbp).execute('select rel from files'):
        for s in discover.path_identities(rel): prot.add(norm(s.lstrip('~')))
        p = rel.split('/')
        if len(p) > 2: prot.add(norm(p[2]))
    drop = [s for s in ids if norm(s) not in prot and denylist.reason(s, '') and '@' not in s and not re.search(r'\.[A-Za-z]{2,6}$', s) and sum(c.isdigit() for c in s) < 8]
    json.dump(drop, open(out, 'w'), ensure_ascii=False); print(len(ids), 'drop', len(drop), drop[:40])


if __name__ == '__main__':
    main(*sys.argv[1:4])
