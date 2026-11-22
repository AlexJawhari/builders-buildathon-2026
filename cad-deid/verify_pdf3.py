"""Check attempt-3 PDF outputs from the local store: opens, metadata author changed, known dictionary identities gone from text.
usage: MACHINA_WORK=../work3 python verify_pdf3.py N"""
import json, os, random, re, sqlite3, sys, zstandard
sys.path.insert(0, '.')
os.environ['MACHINA_PSEUDO'] = 'v1'
import fitz
from deid.ident import IdMap, load_secret
from deid.pipeline import W, blob_path
ids = json.load(open('../work/idents2.json')); m = IdMap(load_secret(W + '/secret.key'))
for s in ids: m.add(s)
c = sqlite3.connect(W + '/state.db')
rows = c.execute("select rel,md5,up from files where (rel like '%.pdf' or rel like '%.PDF') and status='ok' and up=0").fetchall()
print('changed pdfs (up=0):', len(rows), 'of', c.execute("select count(*) from files where (rel like '%.pdf' or rel like '%.PDF')").fetchone()[0])
random.seed(5); random.shuffle(rows); bad = auth = n = 0
for rel, md5, up in rows[:int(sys.argv[1])]:
    p = blob_path(md5, '.z')
    if os.path.exists(p): b = zstandard.ZstdDecompressor().decompress(open(p, 'rb').read(), max_output_size=1 << 31)
    elif os.path.exists(blob_path(md5, '.r')): b = open(blob_path(md5, '.r'), 'rb').read()
    else: continue
    d = fitz.open(stream=b, filetype='pdf'); di = fitz.open('../cache/' + rel); n += 1
    t = ' '.join(pg.get_text() for pg in d)
    sv = m.surfaces(re.sub(r'\s+', ' ', t))
    if sv: bad += 1; print(rel[-50:], sv[:4])
    a0 = di.metadata.get('author'); 
    if a0 and d.metadata.get('author') == a0 and len(a0) > 2: auth += 1
print('checked', n, 'with known identity left', bad, 'author unchanged', auth)
