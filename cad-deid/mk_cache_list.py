"""List of files to cache locally for attempt-2: all PDFs, OLE-container formats, and files whose path changes with the fixed matching."""
import json, os, sqlite3, sys, concurrent.futures as cf
sys.path.insert(0, '.')
os.environ['MACHINA_PSEUDO'] = 'v1'
from deid import net
from deid.ident import IdMap, load_secret
rows = [l.rstrip('\n').split('\t') for l in open('../../_downloads/manifest.tsv') if not l.startswith('#')]
rows = [(r[0], int(r[1]), r[3]) for r in rows]
OLE_EXT = ('.ipt', '.iam', '.idw', '.ipn', '.ipj', '.par', '.dft', '.psm', '.pwd', '.asm.', )
SW = ('.sldprt', '.sldasm', '.slddrw', '.sldlfp', '.slddrt', '.sldprt', '.drwdot', '.prtdot', '.asmdot')
sel = {}
for r in rows:
    e = r[0].lower()
    if e.endswith('.pdf') or e.endswith(OLE_EXT[:-1]): sel[r[0]] = r
swc = [r for r in rows if r[0].lower().endswith(SW) and r[1] > 512]
def probe(r):
    try: return r, net.fetch(r[2], (0, 7)) == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
    except Exception: return r, False
with cf.ThreadPoolExecutor(32) as ex:
    for r, ole in ex.map(probe, swc):
        if ole: sel[r[0]] = r
idm = IdMap(load_secret('../work/secret.key'))
for s in json.load(open('../work/idents.json')): idm.add(s)
db = sqlite3.connect('../work/state.db'); prev = dict(db.execute("select rel,out from files"))
chg = 0
for r in rows:
    if r[0] not in sel and idm.replace_path(r[0]) != prev.get(r[0]): sel[r[0]] = r; chg += 1
tot = sum(r[1] for r in sel.values())
print(len(sel), 'files', round(tot / 1e9, 1), 'GB; path-changed extra:', chg, file=sys.stderr)
with open('../work/cache.tsv', 'w') as f:
    for r in sel.values(): f.write(f'{r[0]}\t{r[1]}\t{r[2]}\n')
