"""CLI. scan: pass 1 (idents from manifest paths + file contents) -> idents.json.  apply: pass 2 rewrite a tree."""
import argparse, hashlib, json, os, sys, time, collections, traceback
from . import discover, handlers
from .ident import IdMap, load_secret


def repo_of(rel):
    p = rel.split('/'); return '/'.join(p[:3])


def scan(args):
    items = collections.defaultdict(set); t = time.time(); n = 0; nb = 0
    for dp, dn, fn in os.walk(args.root):
        for f in fn:
            ap = os.path.join(dp, f); rel = os.path.relpath(ap, args.root)
            if os.path.getsize(ap) > args.maxsize: continue
            try: data = open(ap, 'rb').read()
            except OSError: continue
            n += 1; nb += len(data)
            for r in discover.path_identities(rel): items[r.lstrip('~')].add('PATH')
            try:
                for kind, s in discover.content_identities(rel, data):
                    for piece in (s.split(';') if kind == 'person' else [s]):
                        piece = piece.strip().strip(',')
                        if piece and (kind in ('email', 'domain') or discover.ok_ident(piece)): items[piece].add(repo_of(rel))
                        if kind == 'email':
                            loc = piece.split('@')[0]; items[loc].add(repo_of(rel))
            except Exception as e: print('scan err', rel, repr(e)[:100], file=sys.stderr)
    # path identities are always kept (authoritative list from the manifest when given), content ones must pass the generic filter
    if args.manifest:
        rels = [l.split('\t')[0] for l in open(args.manifest) if not l.startswith('#')]
        keep = discover.manifest_identities(rels)
    else:
        keep = {s for s, r in items.items() if 'PATH' in r}
    keep |= set(discover.merge({s: r for s, r in items.items() if 'PATH' not in r}, None))
    json.dump(sorted(keep), open(args.out, 'w'), indent=0, ensure_ascii=False)
    print(f'scanned {n} files {nb/1e6:.0f} MB in {time.time()-t:.1f}s; {len(keep)} identities', file=sys.stderr)


def load_idm(args):
    idm = IdMap(load_secret(args.secret))
    for s in json.load(open(args.idents)): idm.add(s)
    return idm


def apply(args):
    idm = load_idm(args); t = time.time(); n = nb = 0; err = []
    man = open(args.out.rstrip('/') + '.manifest.tsv', 'w')  # in_path, out_path, out_bytes, out_md5: diff between attempts
    for dp, dn, fn in os.walk(args.root):
        for f in sorted(fn):
            ap = os.path.join(dp, f); rel = os.path.relpath(ap, args.root)
            if os.path.getsize(ap) > args.maxsize: continue
            data = open(ap, 'rb').read(); n += 1; nb += len(data)
            nrel = idm.replace_path(rel)
            try: out = handlers.process(data, rel, idm)
            except Exception as e: err.append((rel, repr(e)[:100])); out = data
            op = os.path.join(args.out, nrel); os.makedirs(os.path.dirname(op), exist_ok=True)
            open(op, 'wb').write(out)
            man.write(f'{rel}\t{nrel}\t{len(out)}\t{hashlib.md5(out).hexdigest()}\n')
    print(f'applied {n} files {nb/1e6:.0f} MB in {time.time()-t:.1f}s; handler fallbacks {len(idm.errors)}, hard errors {len(err)}', file=sys.stderr)
    json.dump({'fallbacks': idm.errors, 'errors': err}, open(args.out.rstrip('/') + '.errors.json', 'w'))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest='cmd', required=True)
    s = sp.add_parser('scan'); s.add_argument('root'); s.add_argument('out'); s.add_argument('--maxsize', type=int, default=10**12); s.add_argument('--manifest'); s.set_defaults(fn=scan)
    a = sp.add_parser('apply'); a.add_argument('root'); a.add_argument('out'); a.add_argument('--idents', required=True)
    a.add_argument('--secret', default='work/secret.key'); a.add_argument('--maxsize', type=int, default=10**12); a.set_defaults(fn=apply)
    args = ap.parse_args(); args.fn(args)
