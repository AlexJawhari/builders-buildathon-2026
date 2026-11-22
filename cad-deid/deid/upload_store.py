"""Resumable uploader: drains the local content-addressed store into gs://<bucket>/<prefix>attempt-N/corpus/<out path>.
Identical blobs are PUT once, later copies are server-side copies (x-goog-copy-source). Waits for a fresh token file on 401.
usage: python -m deid.upload_store --prefix teams/<id>/attempt-1/corpus/ [--token-file work/token.txt] [--threads 8]"""
import argparse, hashlib, http.client, os, sqlite3, sys, threading, time, urllib.parse
import zstandard
from .pipeline import W, STORE, blob_path, db

HOST = 'storage.googleapis.com'; BUCKET = 'builders-machina-submissions-use1'
tl = threading.local()


def enc(key): return '/'.join(urllib.parse.quote(p, safe='') for p in key.split('/'))


class Tok:
    def __init__(self, path): self.path = path; self.val = None; self.mt = 0; self.lock = threading.Lock()
    def get(self):
        with self.lock:
            mt = os.stat(self.path).st_mtime
            if mt != self.mt: self.val = open(self.path).read().strip(); self.mt = mt
            return self.val
    def renew(self, stale):
        """block until the token file holds a different token than the rejected one"""
        while True:
            with self.lock:
                try:
                    v = open(self.path).read().strip()
                    if v and v != stale: self.val = v; self.mt = os.stat(self.path).st_mtime; return
                except OSError: pass
            print('token rejected/expired; waiting for a new token in', self.path, file=sys.stderr, flush=True); time.sleep(30)


def expired(status, body): return status == 401 or (status in (400, 403) and b'xpired' in body)


def req(tok, method, key, body=None, headers=None, retries=6):
    for i in range(retries):
        t = tok.get()
        try:
            c = getattr(tl, 'c', None)
            if c is None: c = tl.c = http.client.HTTPSConnection(HOST, timeout=180)
            h = {'Authorization': f'Bearer {t}', **(headers or {})}
            c.request(method, f'/{BUCKET}/{enc(key)}', body=body, headers=h)
            r = c.getresponse(); rb = r.read()
            if expired(r.status, rb): tok.renew(t); continue
            if r.status < 300: return r
            if r.status in (429, 500, 502, 503, 504): raise IOError(r.status)
            raise RuntimeError(f'{method} {key[-80:]} -> {r.status} {rb[:200]!r}')
        except (IOError, http.client.HTTPException, OSError):
            tl.c = None; time.sleep(min(2 ** i, 30))
    raise RuntimeError('retries exhausted ' + key[-80:])


def read_blob(md5):
    p = blob_path(md5, '.z')
    if os.path.exists(p): return zstandard.ZstdDecompressor().stream_reader(open(p, 'rb')).read() if os.path.getsize(p) < (1 << 29) else None
    return open(blob_path(md5, '.r'), 'rb').read()


def put_blob(tok, key, md5, size):
    p = blob_path(md5, '.r'); z = blob_path(md5, '.z')
    if os.path.exists(p):
        with open(p, 'rb') as f: req(tok, 'PUT', key, f, {'Content-Length': str(size), 'Content-Type': 'application/octet-stream'})
    else:   # decompress to a temp file (big blobs) then stream
        tmp = os.path.join(W, 'up_%d_%d.tmp' % (os.getpid(), threading.get_ident()))
        with open(z, 'rb') as i, open(tmp, 'wb') as o: zstandard.ZstdDecompressor().copy_stream(i, o)
        with open(tmp, 'rb') as f: req(tok, 'PUT', key, f, {'Content-Length': str(os.path.getsize(tmp)), 'Content-Type': 'application/octet-stream'})
        os.remove(tmp)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--prefix', required=True); ap.add_argument('--token-file', default=os.path.join(W, 'token.txt'))
    ap.add_argument('--threads', type=int, default=8); ap.add_argument('--once', action='store_true', help='exit when nothing is pending')
    ap.add_argument('--col', default='up', help="files column marking upload state for this attempt")
    a = ap.parse_args()
    tok = Tok(a.token_file); lock = threading.Lock(); conn = db(); stats = {'put': 0, 'copy': 0, 'bytes': 0, 'err': 0}; t0 = time.time()
    try: conn.execute('alter table blobs add column key text')
    except sqlite3.OperationalError: pass

    def do_group(md5, items):
        """items: [(rel,out)] sharing one blob. first PUT (or copy from a key known server side), the rest copies"""
        try:
            with lock: row = conn.execute('select key,size from blobs where md5=?', (md5,)).fetchone()
            key0, size = row
            for rel, out in items:
                key = a.prefix + out
                if key0 and key0 != key: req(tok, 'PUT', key, None, {'x-goog-copy-source': f'/{BUCKET}/{enc(key0)}', 'Content-Length': '0'}); stats['copy'] += 1
                elif not key0 or key0 != key:
                    put_blob(tok, key, md5, size); key0 = key; stats['put'] += 1; stats['bytes'] += size
                    with lock: conn.execute('update blobs set key=? where md5=?', (key, md5)); conn.commit()
                with lock: conn.execute('update files set up=1 where rel=?', (rel,)); conn.commit()
            with lock:  # all files of this blob uploaded and no other pending reference: drop the local copy, keep key for server-side copies
                pend = conn.execute("select count(*) from files where md5=? and up=0 and status='ok'", (md5,)).fetchone()[0]
                if not pend:
                    for e in ('.z', '.r'):
                        try: os.remove(blob_path(md5, e)); conn.execute('update blobs set local=0 where md5=?', (md5,))
                        except OSError: pass
                    conn.commit()
        except Exception as e:
            stats['err'] += 1; print('upload error', md5, repr(e)[:200], file=sys.stderr, flush=True)

    from concurrent.futures import ThreadPoolExecutor
    ex = ThreadPoolExecutor(a.threads)
    while True:
        with lock: rows = conn.execute("select md5,rel,out from files where up=0 and status='ok' order by md5 limit 4000").fetchall()
        # a blob whose local copy is gone but with a known key can still be copied; one without either must be reprocessed
        groups = {}
        for md5, rel, out in rows: groups.setdefault(md5, []).append((rel, out))
        if not groups:
            if a.once: break
            time.sleep(30); continue
        list(ex.map(lambda kv: do_group(*kv), groups.items()))
        el = time.time() - t0
        print(f"put {stats['put']} copy {stats['copy']} err {stats['err']} {stats['bytes']/1e9:.2f} GB {stats['bytes']/el/1e6:.1f} MB/s", file=sys.stderr, flush=True)
        if stats['err'] and stats['err'] > 50 + stats['put']: time.sleep(60)
    print('UPLOAD DRAINED', stats, file=sys.stderr)


if __name__ == '__main__':
    main()
