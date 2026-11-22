"""Pass 1 at scale: read the manifest, fetch only what carries identities (header bytes via HTTP Range for bulk geometry,
whole file otherwise), append discovered identities to a jsonl. Resumable (rows already in the jsonl are skipped).
usage: python -m deid.pass1 manifest.tsv out.jsonl [--workers 4] [--limit N]"""
import argparse, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from . import discover, net

HEAD = {'step': 65536, 'stp': 65536, 'p21': 65536, 'igs': 65536, 'iges': 65536, 'sat': 32768, 'stl': 1024, 'obj': 16384,
        'wrl': 16384, 'x_b': 8192, 'x_t': 8192, 'xmt_txt': 8192, 'jt': 4096, 'unv': 8192, 'neu': 8192, 'brep': 8192,
        'u3d': 4096, 'ps': 32768, 'eps': 32768, 'dxf': 1 << 20, 'catpart': 8192, 'catproduct': 8192, '1': 16384, 'prt': 16384,
        'asm': 16384, 'drw': 16384}
FULL_MAX = 300_000_000   # larger non-geometry files: first 4 MB only in pass 1


def plan(rel, size):
    ext = rel.lower().rsplit('.', 1)[-1] if '.' in rel.rsplit('/', 1)[-1] else ''
    if ext in HEAD and size > HEAD[ext]: return HEAD[ext]
    if size > FULL_MAX: return 4 << 20
    return None


def work(batch):
    """batch: list of (rel, size, urlpath). Returns list of (rel, [(kind, str)...]) """
    def get(row):
        rel, size, url = row
        n = plan(rel, size)
        try:
            if size == 0: return rel, b''
            return rel, net.fetch(url, (0, n - 1) if n else None)
        except Exception as e:
            return rel, None
    out = []
    with ThreadPoolExecutor(24) as ex:
        for rel, data in ex.map(get, batch):
            if data is None: out.append((rel, None)); continue
            try: ids = discover.content_identities(rel, data)
            except Exception: ids = []
            out.append((rel, [list(t) for t in dict.fromkeys(tuple(x) for x in ids)]))
    return out


def batches(rows, max_bytes=24e6, max_n=120):
    cur, b = [], 0
    for rel, size, url in rows:
        n = plan(rel, size) or size
        cur.append((rel, size, url)); b += n
        if b >= max_bytes or len(cur) >= max_n:
            yield cur; cur, b = [], 0
    if cur: yield cur


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('manifest'); ap.add_argument('out')
    ap.add_argument('--workers', type=int, default=4); ap.add_argument('--limit', type=int, default=0); ap.add_argument('--only-from', help='jsonl whose pdf/dxf rows are re-scanned (title-block pass)')
    a = ap.parse_args()
    rows = [l.rstrip('\n').split('\t') for l in open(a.manifest) if not l.startswith('#')]
    rows = [(r[0], int(r[1]), r[3]) for r in rows]
    done = set()
    if os.path.exists(a.out):
        for l in open(a.out):
            try: done.add(json.loads(l)['r'])
            except Exception: pass
    todo = [r for r in rows if r[0] not in done]
    if a.only_from:
        redo = set()
        for l in open(a.only_from):
            try: redo.add(json.loads(l)['r'])
            except Exception: pass
        todo = [r for r in rows if r[0] in redo and r[0].lower().endswith(('.pdf', '.dxf')) and r[0] not in done]
    if a.limit: todo = todo[:a.limit]
    # biggest ranged/full downloads interleaved with small ones: simple order by path keeps repos together
    tot = sum(plan(r[0], r[1]) or r[1] for r in todo)
    print(f'{len(todo)} files to scan, ~{tot/1e9:.1f} GB to fetch', file=sys.stderr, flush=True)
    t0 = time.time(); n = 0; failed = 0
    if os.path.exists(a.out): open(a.out, 'a').write('\n')
    with open(a.out, 'a') as fo, ProcessPoolExecutor(a.workers) as pool:
        futs = set(); it = batches(todo)
        def feed():
            for b in it:
                futs.add(pool.submit(work, b))
                if len(futs) >= a.workers * 2: return
        feed()
        while futs:
            for f in as_completed(list(futs)):
                futs.discard(f)
                for rel, ids in f.result():
                    if ids is None: failed += 1; fo.write(json.dumps({'r': rel, 'fail': 1}) + '\n'); continue
                    fo.write(json.dumps({'r': rel, 'i': ids}, ensure_ascii=False) + '\n'); n += 1
                fo.flush(); feed()
                print(f'{n} done, {failed} failed, {time.time()-t0:.0f}s', file=sys.stderr, flush=True) if n % 2000 < 200 else None
                break
    print('PASS1 FINISHED', n, failed, time.time() - t0, file=sys.stderr)
