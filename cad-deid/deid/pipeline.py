"""Pass 2 driver: download -> rewrite -> content-addressed zstd store (+ sqlite state). Uploading is a separate process (upload_store.py).
usage: python -m deid.pipeline manifest.tsv idents.json [--workers 4] [--store-limit-gb 45] [--limit N] [--prefix sources/github/x]"""
import argparse, hashlib, os, sqlite3, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import zstandard
from . import handlers, net
from .ident import IdMap, load_secret

W = os.environ.get('MACHINA_WORK', '/home/alex/projects/buildathon/machina/work')
STORE = os.path.join(W, 'store'); DL = os.path.join(W, 'dl'); DB = os.path.join(W, 'state.db')
BIG = 200 << 20
IDM = None


def db():
    c = sqlite3.connect(DB, timeout=120, check_same_thread=False)
    c.execute('pragma journal_mode=wal')
    c.executescript('''create table if not exists files(rel text primary key, out text, md5 text, size int, status text, note text, up int default 0);
    create table if not exists blobs(md5 text primary key, size int, local int default 1, key text);
    create index if not exists files_up on files(up, status);''')
    try: c.execute('alter table files add column prev_out text')
    except sqlite3.OperationalError: pass
    return c


def blob_path(md5, ext): return os.path.join(STORE, md5[:2], md5 + ext)


def put_blob(md5, data=None, src=None):
    """store bytes (or a file) under its md5, zstd level 1 when it compresses, else raw. returns stored size."""
    for ext in ('.z', '.r'):
        if os.path.exists(blob_path(md5, ext)): return 0
    os.makedirs(os.path.join(STORE, md5[:2]), exist_ok=True)
    tmp = blob_path(md5, '.tmp%d' % os.getpid())
    if data is None: data = open(src, 'rb').read() if os.path.getsize(src) < (512 << 20) else None
    if data is not None:
        z = zstandard.ZstdCompressor(level=1).compress(data)
        ext, body = ('.z', z) if len(z) < 0.95 * len(data) else ('.r', data)
        with open(tmp, 'wb') as f: f.write(body)
    else:  # huge: stream-compress, keep raw if it does not help
        with open(src, 'rb') as i, open(tmp, 'wb') as o: zstandard.ZstdCompressor(level=1).copy_stream(i, o)
        ext = '.z'
    os.replace(tmp, blob_path(md5, ext))
    return os.path.getsize(blob_path(md5, ext))


def init_worker(idents_path, secret_path):
    global IDM
    import json
    IDM = IdMap(load_secret(secret_path))
    for s in json.load(open(idents_path)): IDM.add(s)


def process_one(rel, dl, size, keep=False):
    try:
        out_rel = IDM.replace_path(rel); IDM.errors.clear(); note = ''
        if size == 0:
            md5 = hashlib.md5(b'').hexdigest(); n = 0; put_blob(md5, b'')
        elif size > BIG and rel.lower().endswith(handlers.RAW_STREAM_EXT):
            tmp = dl + '.out'; md5, n = handlers.stream_replace(dl, tmp, IDM); put_blob(md5, src=tmp); os.remove(tmp)
        elif size > (1500 << 20):
            md5 = hashlib.md5(); 
            with open(dl, 'rb') as f:
                for b in iter(lambda: f.read(1 << 24), b''): md5.update(b)
            md5 = md5.hexdigest(); n = size; put_blob(md5, src=dl); note = 'unchanged (too large to process)'
        else:
            data = open(dl, 'rb').read()
            out = handlers.process(data, rel, IDM)
            md5 = hashlib.md5(out).hexdigest(); n = len(out); put_blob(md5, out)
            if IDM.errors: note = ';'.join(f'{k}:{e}' for _, k, e in IDM.errors)[:300]
        return rel, out_rel, md5, n, 'ok', note
    except Exception as e:
        return rel, None, None, 0, 'error', repr(e)[:300]
    finally:
        if not keep:
            try: os.remove(dl)
            except OSError: pass


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('manifest'); ap.add_argument('idents')
    ap.add_argument('--secret', default=os.path.join(W, 'secret.key')); ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--store-limit-gb', type=float, default=45); ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--prefix', default=''); ap.add_argument('--dl-limit-gb', type=float, default=4)
    ap.add_argument('--redo', action='store_true', help='ignore previous status (new dictionary)')
    ap.add_argument('--reuse-ext', default='', help='comma list (e.g. pdf): rows with these extensions are not re-downloaded; their previous output is copied (renamed if the path changed)')
    ap.add_argument('--local-root', help='read inputs from this cache dir instead of downloading (never deleted)'); ap.add_argument('--only-from', help='tsv (path first column): process only these rows')
    ap.add_argument('--prev-db', help='state.db of the previous attempt: outputs identical to it are not stored/uploaded, only server-side copied (up=2)')
    a = ap.parse_args()
    os.makedirs(DL, exist_ok=True); os.makedirs(STORE, exist_ok=True)
    conn = db(); lock = threading.Lock()
    rows = [l.rstrip('\n').split('\t') for l in open(a.manifest) if not l.startswith('#')]
    rows = [(r[0], int(r[1]), r[3]) for r in rows if r[0].startswith(a.prefix)]
    done = set() if a.redo else {r[0] for r in conn.execute("select rel from files where status in ('ok')")}
    todo = [r for r in rows if r[0] not in done]
    if a.limit: todo = todo[:a.limit]
    print(f'{len(todo)} files to process ({sum(r[1] for r in todo)/1e9:.1f} GB)', file=sys.stderr, flush=True)
    prev = {}
    if a.prev_db:
        pc = sqlite3.connect(a.prev_db); prev = {r: (o, m) for r, o, m in pc.execute("select rel,out,md5 from files where status='ok'")}; pc.close()
    if a.only_from:
        only = {l.split('\t')[0] for l in open(a.only_from)}; todo = [r for r in rows if r[0] in only]   # explicit list: ignore previous status
    reuse = tuple('.' + e for e in a.reuse_ext.split(',') if e)
    if reuse and prev:
        init_worker(a.idents, a.secret); keep = []
        for r in todo:
            if r[0].lower().endswith(reuse) and r[0] in prev:
                conn.execute('insert or replace into files(rel,out,md5,size,status,note,up,prev_out) values(?,?,?,?,?,?,2,?)',
                             (r[0], IDM.replace_path(r[0]), prev[r[0]][1], -1, 'ok', 'reused from previous attempt', prev[r[0]][0]))
            else: keep.append(r)
        conn.commit(); print(f'{len(todo) - len(keep)} rows reused from previous attempt', file=sys.stderr); todo = keep
    state = {'inflight': 0, 'big': 0, 'n': 0, 'bytes': 0, 'err': 0}
    cond = threading.Condition()
    pool = ProcessPoolExecutor(a.workers, initializer=init_worker, initargs=(a.idents, a.secret))
    dls = ThreadPoolExecutor(24)
    t0 = time.time()

    def finish(fut, size):
        rel, out_rel, md5, n, st, note = fut.result()
        same = False
        with lock:
            same = st == 'ok' and rel in prev and prev[rel][1] == md5
            conn.execute('insert or replace into files(rel,out,md5,size,status,note,up,prev_out) values(?,?,?,?,?,?,?,?)', (rel, out_rel, md5, n, st, note, 2 if same else 0, prev[rel][0] if same else None))
            if md5 and not same: conn.execute('insert or ignore into blobs(md5,size,local) values(?,?,1)', (md5, n))
            conn.commit()
        with cond:
            state['inflight'] -= size; state['n'] += 1; state['bytes'] += size; state['err'] += st != 'ok'
            if size > BIG: state['big'] -= 1
            cond.notify_all()
        if same:
            for e in ('.z', '.r'):
                try: os.remove(blob_path(md5, e))
                except OSError: pass
        if state['n'] % 500 == 0:
            el = time.time() - t0; print(f"{state['n']} files, {state['bytes']/1e9:.1f} GB in, {state['bytes']/el/1e6:.1f} MB/s, errors {state['err']}", file=sys.stderr, flush=True)

    def job(rel, size, url):
        dl = os.path.join(DL, hashlib.md5(rel.encode()).hexdigest())
        try:
            if a.local_root: dl = os.path.join(a.local_root, rel)
            elif size: net.fetch(url, dest=dl)
            else: open(dl, 'wb').close()
        except Exception as e:
            with lock:
                conn.execute('insert or replace into files(rel,status,note) values(?,?,?)', (rel, 'dlfail', repr(e)[:200])); conn.commit()
            with cond:
                state['inflight'] -= size; state['big'] -= size > BIG; cond.notify_all()
            return
        f = pool.submit(process_one, rel, dl, size, bool(a.local_root))
        f.add_done_callback(lambda fu, s=size: finish(fu, s))

    for i, (rel, size, url) in enumerate(todo):
        with cond:
            while True:
                store_bytes = None
                if i % 50 == 0 or True:
                    with lock: store_bytes = conn.execute('select coalesce(sum(size),0) from blobs where local=1').fetchone()[0]
                ok = state['inflight'] + size <= a.dl_limit_gb * 1e9 or state['inflight'] == 0
                ok = ok and store_bytes < a.store_limit_gb * 1e9 and not (size > BIG and state['big'] > 0)
                if ok: break
                cond.wait(timeout=10)
            state['inflight'] += size; state['big'] += size > BIG
        dls.submit(job, rel, size, url)
    dls.shutdown(wait=True); pool.shutdown(wait=True)
    print('PIPELINE FINISHED', state, time.time() - t0, file=sys.stderr, flush=True)


if __name__ == '__main__':
    main()
