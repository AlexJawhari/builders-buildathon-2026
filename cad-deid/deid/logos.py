"""Attempt 4: logos inside PDFs. A logo is a raster image that recurs across PDFs (company/institute marks in title blocks). Clusters of
visually identical images (64-bit difference hash, Hamming <= 5, so re-encoded or resized copies join) are kept as logos unless they look
like drawing content (single source folder away from the page corners, or large mostly-white line art). Every image matching a kept
cluster is replaced by one deterministic plate per cluster (same bytes in every PDF, different plate for different clusters).
  python -m deid.logos build cache imgs.json out_clusters.json      (imgs.json: md5 -> pdf count, from imgscan; see below)
Runtime: MACHINA_LOGOS=<clusters json>; handlers call replace_logos(doc, idm) from the PDF rewrite."""
import collections, hashlib, io, json, os, re, sys

MINPX, MAXPX, MAXRAW = 24, 1500, 600_000
HAM = 5


def dhash(pm):
    """64-bit difference hash of a fitz.Pixmap (alpha dropped, CMYK converted)"""
    import fitz
    from PIL import Image
    if pm.alpha: pm = fitz.Pixmap(pm, 0)
    if pm.colorspace is None or pm.colorspace.n != 1:
        pm = fitz.Pixmap(fitz.csGRAY, fitz.Pixmap(fitz.csRGB, pm) if pm.colorspace and pm.colorspace.n == 4 else pm)
    im = Image.frombytes('L', (pm.width, pm.height), pm.samples).resize((9, 8), Image.BOX)
    p = list(im.getdata()); v = 0
    for r in range(8):
        for c in range(8): v = (v << 1) | (p[r * 9 + c] > p[r * 9 + c + 1])
    return v, sum(1 for x in pm.samples[::max(1, len(pm.samples) // 4000)] if x > 235) / max(1, len(pm.samples[::max(1, len(pm.samples) // 4000)]))


def image_xrefs(doc):
    """(xref, w, h) of every raster image object in the file (nested form XObjects included) within the logo size window"""
    out = []
    for x in range(1, doc.xref_length()):
        try:
            if doc.xref_get_key(x, 'Subtype')[1] != '/Image': continue
            w = int(doc.xref_get_key(x, 'Width')[1]); h = int(doc.xref_get_key(x, 'Height')[1])
            ln = doc.xref_get_key(x, 'Length')[1]
            if MINPX <= w <= MAXPX and MINPX <= h <= MAXPX and (not ln.isdigit() or int(ln) <= MAXRAW): out.append((x, w, h))
        except Exception: continue
    return out


# ---------------------------------------------------------------- build
def scan_pdf(args):
    import fitz
    path, rel, targets = args
    out = []
    try:
        d = fitz.open(path); seen = {}
        for pg in d:
            pa = pg.rect.get_area() or 1
            for im in pg.get_images(full=True):
                x = im[0]
                if x in seen: continue
                try: raw = d.xref_stream_raw(x)
                except Exception: continue
                h = hashlib.md5(raw).hexdigest()
                if h not in targets or len(raw) > MAXRAW: continue
                try:
                    r = pg.get_image_rects(x)[0]; pm = fitz.Pixmap(d, x); v, white = dhash(pm)
                except Exception: continue
                seen[x] = 1
                out.append((h, v, pm.width, pm.height, white, r.get_area() / pa, (r.x0 + r.x1) / 2 / pg.rect.width, (r.y0 + r.y1) / 2 / pg.rect.height, rel))
    except Exception: pass
    return out


def scope_of(rel):
    p = rel.split('/')
    return '/'.join(p[1:5]) if p[1] == 'existing' else '/'.join(p[1:3])


def build(cache, imgs_json, out, mindf=3):
    from concurrent.futures import ProcessPoolExecutor
    J = json.load(open(imgs_json)); per = J['per']
    df = collections.Counter(h for hs in per.values() for h in set(hs))
    targets = {h for h, c in df.items() if c >= mindf}
    todo = [(os.path.join(cache, rel), rel, targets) for rel, hs in per.items() if any(h in targets for h in hs)]
    occ = collections.defaultdict(list)
    with ProcessPoolExecutor(10) as ex:
        for res in ex.map(scan_pdf, todo, chunksize=8):
            for o in res: occ[o[0]].append(o)
    # one record per md5
    recs = {}
    for h, os_ in occ.items():
        o = os_[0]; recs[h] = dict(v=o[1], w=o[2], h=o[3], white=o[4], area=sorted(x[5] for x in os_)[len(os_) // 2],
                                  cx=sorted(x[6] for x in os_)[len(os_) // 2], cy=sorted(x[7] for x in os_)[len(os_) // 2], pdfs={x[8] for x in os_})
    hs = list(recs); par = {h: h for h in hs}
    def find(a):
        while par[a] != a: par[a] = par[par[a]]; a = par[a]
        return a
    import numpy as np
    vs = np.array([recs[h]['v'] for h in hs], dtype=np.uint64)
    for i, h in enumerate(hs):
        x = vs ^ vs[i]
        pc = np.zeros(len(x), dtype=np.int64)
        for b in range(64): pc += ((x >> np.uint64(b)) & np.uint64(1)).astype(np.int64)
        for j in np.nonzero(pc <= HAM)[0]:
            a, b = find(h), find(hs[j])
            if a != b: par[a] = b
    cl = collections.defaultdict(list)
    for h in hs: cl[find(h)].append(h)
    kept = []; drop = collections.Counter()
    for root, mem in cl.items():
        R = [recs[h] for h in mem]
        pdfs = set().union(*(r['pdfs'] for r in R)); scopes = {scope_of(p) for p in pdfs}
        area = sorted(r['area'] for r in R)[len(R) // 2]; white = sorted(r['white'] for r in R)[len(R) // 2]
        cx = sorted(r['cx'] for r in R)[len(R) // 2]; cy = sorted(r['cy'] for r in R)[len(R) // 2]
        big = max(max(r['w'], r['h']) for r in R)
        corner = (cy < 0.18 or cy > 0.75) and area < 0.04        # header / title-block band
        lineart = white > 0.85 and area > 0.08
        if len(pdfs) < 3: drop['few pdfs'] += 1; continue
        small = min(min(r['w'], r['h']) for r in R)
        if white > 0.95 or big < 40 or big / max(1, small) > 8: drop['blank strip / icon'] += 1; continue
        if lineart or area > 0.1: drop['line art / large'] += 1; continue
        if not (corner or (len(scopes) >= 3 and area < 0.02)): drop['not title block'] += 1; continue
        if len(scopes) < 2 and not (len(pdfs) >= 8 and area < 0.02): drop['single source'] += 1; continue
        main = max(R, key=lambda r: len(r['pdfs'])); mh = [h for h in mem if recs[h] is main][0]
        kept.append(dict(id=hashlib.md5(('%016x' % min(r['v'] for r in R)).encode()).hexdigest()[:10], hashes=sorted({'%016x' % r['v'] for r in R}),
                         md5=mh, rel=sorted(main['pdfs'])[0], w=main['w'], h=main['h'], pdfs=len(pdfs), scopes=len(scopes), area=round(area, 3), white=round(white, 2)))
    json.dump(kept, open(out, 'w'))
    print('images', len(hs), 'clusters', len(cl), 'kept', len(kept), 'kept pdfs (sum)', sum(c['pdfs'] for c in kept), dict(drop))


# ---------------------------------------------------------------- runtime
class Index:
    def __init__(self, path):
        import numpy as np
        self.cl = json.load(open(path)); hs = []; ids = []
        for i, c in enumerate(self.cl):
            for h in c['hashes']: hs.append(int(h, 16)); ids.append(i)
        self.hs = np.array(hs, dtype=np.uint64); self.ids = ids; self.cache = {}

    def match(self, v):
        import numpy as np
        x = self.hs ^ np.uint64(v); pc = np.zeros(len(x), dtype=np.int64)
        for b in range(64): pc += ((x >> np.uint64(b)) & np.uint64(1)).astype(np.int64)
        j = int(pc.argmin())
        return self.ids[j] if pc[j] <= HAM - 1 else None


def plate(idm, c):
    """deterministic PNG pixmap for one cluster: coloured plate carrying a pseudonym name, aspect of the cluster's main image"""
    import fitz
    from PIL import Image, ImageDraw, ImageFont
    h = hashlib.sha256(idm.secret + b'logo:' + c['id'].encode()).digest()
    W = min(c['w'], 480); H = max(8, round(W * c['h'] / c['w']))
    bg = tuple(170 + h[i] % 70 for i in range(3)); fg = tuple(h[3 + i] % 90 for i in range(3))
    im = Image.new('RGB', (W, H), bg); d = ImageDraw.Draw(im)
    name = idm.pseudo_v1('Xxxxxxxx Xxxxxxxxx', key='logo ' + c['id'])
    fs = max(6, min(H // 3, W // 9))
    try: f = ImageFont.load_default(size=fs)
    except TypeError: f = ImageFont.load_default()
    d.rectangle([1, 1, W - 2, H - 2], outline=fg)
    d.text((W // 2, H // 2), name, fill=fg, font=f, anchor='mm')
    out = io.BytesIO(); im.save(out, 'PNG', optimize=False)
    return fitz.Pixmap(out.getvalue())


def replace_logos(doc, idm):
    """replace every matching image object; returns number replaced"""
    import fitz
    path = os.environ.get('MACHINA_LOGOS')
    if not path or not os.path.exists(path) or doc.page_count == 0: return 0
    ix = getattr(idm, '_logoindex', None)
    if ix is None: ix = idm._logoindex = Index(path)
    n = 0
    for x, w, h in image_xrefs(doc):
        try:
            pm = fitz.Pixmap(doc, x); v, _ = dhash(pm)
            ci = ix.match(v)
            if ci is None: continue
            c = ix.cl[ci]
            if c['id'] not in ix.cache: ix.cache[c['id']] = plate(idm, c)
            doc[0].replace_image(x, pixmap=ix.cache[c['id']])
            for k in ('SMask', 'Mask'):
                try:
                    if doc.xref_get_key(x, k)[0] != 'null': doc.xref_set_key(x, k, 'null')
                except Exception: pass
            n += 1
        except Exception: continue
    return n


if __name__ == '__main__':
    if sys.argv[1] == 'build': build(*sys.argv[2:5])
