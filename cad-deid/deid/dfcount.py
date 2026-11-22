"""Document frequency of every word (case-folded) over the PDF text (first 4 pages + last): separates ordinary vocabulary in any language
from names. usage: python -m deid.dfcount cache_dir out.json"""
import collections, json, os, re, sys
from concurrent.futures import ProcessPoolExecutor
W = re.compile(r"[^\W\d_][\w'’-]*")


def one(path):
    import fitz
    try:
        d = fitz.open(path)
        if d.needs_pass: return set()
        pages = list(range(min(4, d.page_count))) + ([d.page_count - 1] if d.page_count > 4 else [])
        t = '\n'.join(d[i].get_text() for i in pages)[:80000]
        return {w.lower() for w in W.findall(t)}
    except Exception: return set()


if __name__ == '__main__':
    root, out = sys.argv[1:3]
    files = [os.path.join(dp, f) for dp, _, fn in os.walk(root) for f in fn if f.lower().endswith('.pdf')]
    c = collections.Counter()
    with ProcessPoolExecutor(8) as ex:
        for s in ex.map(one, files, chunksize=16): c.update(s)
    json.dump({k: v for k, v in c.items() if v >= 2}, open(out, 'w'))
    print('done', len(files), len(c))
