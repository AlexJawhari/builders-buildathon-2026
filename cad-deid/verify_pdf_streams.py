"""Raw residue scan of attempt-3 PDFs: every object's inflated stream and every object's source text (Info, XMP, outlines, annotations, names)
is searched for dictionary identities as ASCII and UTF-16BE/LE, independent of text extraction. Compares with the input PDF.
usage: MACHINA_WORK=../work3 python verify_pdf_streams.py N"""
import json, os, random, re, sqlite3, sys, zstandard
sys.path.insert(0, '.')
os.environ['MACHINA_PSEUDO'] = 'v1'
import fitz
from deid.ident import IdMap, load_secret
from deid.pipeline import W, blob_path
from deid import pdfx
ids = json.load(open('../work/idents2.json')) + json.load(open(os.environ.get('MACHINA_PDF_EXTRA', '../work3/pdf_extra.json')))
drop = {pdfx.norm(x) for x in json.load(open('../work3/pdf_drop.json'))}
m = IdMap(load_secret(W + '/secret.key'))
for s in ids:
    if pdfx.norm(s) not in drop and len(pdfx.norm(s).replace(' ', '')) >= 5: m.add(s)
c = sqlite3.connect(W + '/state.db')
rows = c.execute("select rel,md5 from files where (rel like '%.pdf' or rel like '%.PDF') and status='ok' and up=0").fetchall()
random.seed(8); random.shuffle(rows)


def scan(b):
    d = fitz.open(stream=b, filetype='pdf'); hits = set()
    for x in range(1, d.xref_length()):
        try:
            parts = [d.xref_object(x, compressed=False).encode('latin1', 'ignore')]
            if d.xref_is_stream(x): parts.append(d.xref_stream(x) or b'')
        except Exception: continue
        for p in parts:
            for sfx in (p, p.replace(b'\x00', b'')):          # UTF-16 strings: drop the NULs
                hits |= set(m._surf_bytes(sfx))
    return hits


n = tin = tout = 0
for rel, md5 in rows[:int(sys.argv[1])]:
    p = blob_path(md5, '.z')
    b = zstandard.ZstdDecompressor().decompress(open(p, 'rb').read(), max_output_size=1 << 31) if os.path.exists(p) else open(blob_path(md5, '.r'), 'rb').read() if os.path.exists(blob_path(md5, '.r')) else None
    if b is None: continue
    try: hi = scan(open('../cache/' + rel, 'rb').read()); ho = scan(b)
    except Exception as e: continue
    n += 1; tin += bool(hi); tout += bool(ho)
    if ho: print(rel[-60:], sorted(ho)[:4])
print('scanned', n, 'input has raw identity', tin, 'output has raw identity', tout)
