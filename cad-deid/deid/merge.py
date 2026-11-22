"""Merge pass-1 jsonl files + manifest path identities into idents.json (the global dictionary).
usage: python -m deid.merge manifest.tsv out.json pass1.jsonl [pass1b.jsonl ...]"""
import collections, json, re, sys
from . import discover, denylist


def main(manifest, out, *jsonls):
    seen = collections.defaultdict(set); kinds = {}
    for fn in jsonls:
        for l in open(fn):
            try: d = json.loads(l)
            except Exception: continue
            rel = d['r']; repo = '/'.join(rel.split('/')[:3])
            for kind, s in d.get('i', ()):
                s = s.split('\x00')[0]   # OLE property strings can carry trailing binary
                if '\\' in s and kind in ('person', 'user', 'host'):   # DOMAIN\\user (HOST) -> three candidates
                    pieces = [x.strip() for x in re.split(r'[\\()]', s) if x.strip()]
                else: pieces = s.split(';') if kind == 'person' else [s]
                for piece in pieces:
                    piece = re.sub(r'\.?\s*all rights reserved.*$', '', re.sub(r'^(?:\(c\)|©)?\s*\d{4}\s+', '', piece.strip().strip(',')), flags=re.I).strip(' .,')
                    if piece: seen[piece].add(repo); kinds.setdefault(piece, kind)
                    if kind == 'email':
                        seen[piece.split('@')[0]].add(repo); kinds.setdefault(piece.split('@')[0], 'user')
                    if kind == 'domain' and piece.lower().startswith('www.'):
                        seen[piece[4:]].add(repo); kinds.setdefault(piece[4:], 'domain')
    keep = set(); stat = collections.Counter(); removed = collections.defaultdict(list)
    for s, repos in seen.items():
        k = kinds[s]
        if len(repos) >= 6: removed['seen in 6+ repos (generic)'].append(s); continue
        r = denylist.reason(s, k)
        if r: removed[r].append(s); continue
        if k in ('email', 'domain'): ok = s.lower() not in discover.PUBLIC_DOMAINS and len(s) >= 5
        elif k == 'phone': ok = sum(c.isdigit() for c in s) >= 8
        else: ok = discover.ok_ident(s)
        if ok: keep.add(s); stat[k] += 1
    rels = [l.split('\t')[0] for l in open(manifest) if not l.startswith('#')]
    path_ids = discover.manifest_identities(rels)
    keep |= path_ids; stat['path'] = len(path_ids)
    with open(out.rsplit('/', 1)[0] + '/dict_removed.txt', 'w') as f:
        f.write('removed from dictionary (content-derived candidates), by rule: count, then all strings\n')
        for r, lst in sorted(removed.items(), key=lambda x: -len(x[1])):
            f.write(f'\n## {r}: {len(lst)}\n' + '\n'.join(sorted(lst)) + '\n')
    print({r: len(l) for r, l in removed.items()}, file=sys.stderr)
    json.dump(sorted(keep), open(out, 'w'), ensure_ascii=False)
    print(len(keep), dict(stat), file=sys.stderr)


if __name__ == '__main__':
    main(*sys.argv[1:])
