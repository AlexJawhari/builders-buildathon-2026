"""Stage attempt N by server-side copy of attempt-1 objects (no re-upload). Files listed in --skip (changed, to be re-uploaded)
are not copied. usage: python -m deid.attempt --from-prefix teams/<id>/attempt-1/corpus/ --to-prefix teams/<id>/attempt-2/corpus/ [--skip rels.txt]"""
import argparse, os, sys, threading
from concurrent.futures import ThreadPoolExecutor
from .pipeline import W, db
from .upload_store import Tok, req, enc, BUCKET


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--from-prefix', required=True); ap.add_argument('--to-prefix', required=True)
    ap.add_argument('--skip'); ap.add_argument('--pending', action='store_true', help='copy rows marked up=2 (identical to previous attempt) using prev_out'); ap.add_argument('--loop', action='store_true'); ap.add_argument('--token-file', default=os.path.join(W, 'token.txt')); ap.add_argument('--threads', type=int, default=16)
    a = ap.parse_args(); tok = Tok(a.token_file); c = db()
    skip = set(open(a.skip).read().split('\n')) if a.skip else set()
    rows = c.execute("select rel,out,prev_out from files where status='ok' and up=2").fetchall() if a.pending else [(r, o, o) for r, o in c.execute("select rel,out from files where status='ok' and up=1")]
    rows = [x for x in rows if x[0] not in skip]
    n = [0]; lock = threading.Lock()
    def cp(ro):
        r, o, po = ro
        req(tok, 'PUT', a.to_prefix + o, None, {'x-goog-copy-source': f'/{BUCKET}/{enc(a.from_prefix + po)}', 'Content-Length': '0'})
        with lock:
            c.execute('update files set up=1 where rel=?', (r,)); c.commit()
            n[0] += 1
            if n[0] % 5000 == 0: print(n[0], 'copied', file=sys.stderr, flush=True)
    with ThreadPoolExecutor(a.threads) as ex: list(ex.map(cp, rows))
    print('copied', n[0], 'skipped', len(skip), file=sys.stderr)


if __name__ == '__main__':
    main()
