"""PDF rewrite v3 (attempt 3). Differences from handlers.pdf (attempt 2):
 - matching and redaction use the characters of page.get_text('rawdict') (exact glyph boxes, lines of one block joined) instead of
   page.search_for, whose boxes are wrong on some pages (the identity then stayed in the text layer);
 - per-PDF ("scoped") identities are added to the global dictionary: author/account tokens from /Info + XMP, the owner/repo/catalog/web
   scope names of the file's path (camelCase split), e-mails, non-public domains and phones found in the text;
 - a PDF-only dictionary of person/organisation names harvested from PDF body text (MACHINA_PDF_EXTRA json list);
 - the replacement is verified (text re-extracted and matched again) and the residue count is reported in the handler log.
Pseudonyms: dictionary identities keep the attempt-1/2 function (pseudo_v1 of the matched surface) so they stay consistent with every
unchanged file; identities that are new here use pseudo_v1 keyed by the canonical identity, so every spelling/case/separator gets the same letters."""
import json, os, re, unicodedata
import ahocorasick

FOLD = str.maketrans({'µ': 'u', 'μ': 'u', ' ': ' ', '‐': '-', '‑': '-', '‒': '-', '–': '-', '—': '-', '’': "'", '‘': "'"})
SEPS = ('-', '_', ' ', '', '.')
ZW = set('\u200b\u200c\u200d\u2060\ufeff\u00ad')
PUBLIC_HOSTS = {'github.com', 'gitlab.com', 'gmail.com', 'outlook.com', 'hotmail.com', 'yahoo.com', 'live.com', 'qq.com', '163.com', '126.com', 'google.com',
                'microsoft.com', 'autodesk.com', 'solidworks.com', 'example.com', 'w3.org', 'adobe.com', 'inkscape.org', 'purl.org', 'creativecommons.org',
                'iso.org', 'apple.com', 'icloud.com', 'protonmail.com', 'mail.com', 'web.de', 'wikipedia.org', 'youtube.com', 'facebook.com', 'twitter.com',
                'linkedin.com', 'instagram.com', 'amazon.com', 'gnu.org', 'apache.org', 'python.org', 'sourceforge.net', 'wordpress.com', 'dropbox.com',
                'bit.ly', 'doi.org', 'arxiv.org', 'ieee.org', 'asme.org', 'mcmaster.com', 'grabcad.com', 'thingiverse.com', 'kicad.org', 'freecad.org'}
EMAIL = re.compile(r'[A-Za-z0-9._%+-]{2,40}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,6}')
HOST = re.compile(r'(?:(?:https?|ftp)://)?((?:www\.)?[A-Za-z0-9-]{2,40}(?:\.[A-Za-z0-9-]{2,40})*\.(?:com|org|net|edu|gov|io|de|fr|it|es|nl|ch|se|uk|jp|cn|ru|ca|au|eu|info|co|tw|kr|at|dk|fi|no|pl|cz|br|in|us|sg|hk|be|pt|gr|il|tr|me|dev|ai|xyz)\b)(?![\w-])', re.I)
PHONE = re.compile(r'(?<![\w.])(\+\d{1,3}[ .-]?\(?\d{1,4}\)?[ .-]?\d{2,4}[ .-]?\d{2,4}(?:[ .-]?\d{2,4})?|\(\d{3}\) ?\d{3}[-. ]\d{4}|\b\d{3}[-.]\d{3}[-.]\d{4})\b')
TOKEN = re.compile(r"[^\W_](?:[\w'.-]*[^\W_])?")
CAMEL = re.compile(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')
GENERIC_ACCT = set('administrator admin user users owner operator guest default pc desktop laptop computer unknown anonymous author none root student teacher engineer designer'.split())
HEX12 = re.compile(r'^[0-9a-f]{12}$')


def lower1(s):
    return ''.join(c.lower() if len(c.lower()) == 1 else c for c in s)


def fold(s):
    return lower1(s.translate(FOLD))


def norm(s):
    return re.sub(r'[-_ .]+', ' ', fold(s).strip())


def boundary_ok(hay, s, e, edge_l, edge_r):
    """no ASCII/Latin alphanumeric glued to an edge that is itself Latin alphanumeric; CJK edges need no boundary"""
    def latin(c): return c.isalnum() and c < '⺀'
    if s > 0 and latin(edge_l) and latin(hay[s - 1]): return False
    if e < len(hay) and latin(edge_r) and latin(hay[e]): return False
    return True


class Matcher:
    """case/separator/ligature-insensitive dictionary matcher on text. entries: {surface or norm key: source tag}; the surface spelling
    itself is also matched (mixed separators such as 'J.A.M. DE WAAL')"""
    def __init__(self, entries):
        self.a = ahocorasick.Automaton(); seen = set()
        for surf, src in entries.items():
            k = norm(surf)
            if not k: continue
            vs = ({k.replace(' ', s) for s in SEPS} if ' ' in k else {k}) | {fold(surf.strip())}
            if src in ('c', 'x') and len(k.split()) == 2:       # 'Smith, John' / 'Smith John' for 'John Smith'
                a, b = k.split(); vs |= {f'{b}, {a}', f'{b},{a}', f'{b} {a}'}
            for v in set(vs) | {lower1(unicodedata.normalize('NFKC', x)) for x in vs}:
                if v in seen: continue
                seen.add(v); self.a.add_word(v, (v, k, src))
        self.n = len(seen)
        if self.n: self.a.make_automaton()

    def find(self, low, hay_len=None):
        """non-overlapping matches (start, end, key, src), longest first at each start; low = fold()ed text"""
        if not self.n: return []
        hits = []
        for end, (v, k, src) in self.a.iter(low):
            s = end - len(v) + 1; e = end + 1
            if boundary_ok(low, s, e, v[0], v[-1]): hits.append((s, e, k, src))
        hits.sort(key=lambda t: (t[0], -(t[1] - t[0])))
        res = []; last = 0
        for h in hits:
            if h[0] >= last: res.append(h); last = h[1]
        return res


_PROT = ' '    # normalised file-name stem of the PDF being rewritten: words of the drawing's own name are part names that the model files carry too


def merge_hits(*lists):
    hits = sorted((h for l in lists for h in l if h[3] == 'g' or f' {h[2]} ' not in _PROT), key=lambda t: (t[0], -(t[1] - t[0])))
    res = []; last = 0
    for h in hits:
        if h[0] >= last: res.append(h); last = h[1]
    return res


class Ctx:
    """per-process state: global matcher (attempt-2 dictionary + PDF-only extras)"""
    def __init__(self, idm):
        self.idm = idm
        ents = {}; drop = os.environ.get('MACHINA_PDF_DROP')
        drop = {norm(x) for x in json.load(open(drop))} if drop and os.path.exists(drop) else set()
        for k in idm.ids:
            if len(k.replace(' ', '')) >= 3 and norm(idm.ids[k]) not in drop: ents[idm.ids[k]] = 'g'
        extra = os.environ.get('MACHINA_PDF_EXTRA')
        if extra and os.path.exists(extra):
            for s in json.load(open(extra)):
                if len(norm(s).replace(' ', '')) >= 3: ents.setdefault(s, 'x')
        self.M = Matcher(ents)

    def pseudo(self, surface, k, src):
        idm = self.idm
        if src == 'g': return idm.pseudo_v1(surface)
        if src == 'e': return pseudo_email(idm, surface)
        if src == 'h': return pseudo_host(idm, surface)
        if src in ('c', 'x'): return idm.pseudo_v2(surface)    # person/org names: per word, so 'Jane Doe', 'JANE DOE', 'J. Doe' share 'doe'
        if src == 's' and k not in idm.ids and k.replace(' ', '') in idm.ids: k = k.replace(' ', '')   # 'Acme Robotics' = path name 'AcmeRobotics'
        if re.fullmatch(r'[A-Za-z]{1,3}', surface): return idm._tok(surface)   # short words: collision-free bijection
        return idm.pseudo_v1(surface, key=k)


def _tok(idm, t): return idm._tok(t)


def pseudo_email(idm, s):
    loc, dom = s.rsplit('@', 1)
    return idm.pseudo_v1(loc, key=norm(loc)) + '@' + (dom if dom.lower() in PUBLIC_HOSTS else pseudo_host(idm, dom))


def pseudo_host(idm, s):
    """component-wise: every label except www and the TLD (and co/com/ac... second level) gets its own pseudonym, same length"""
    parts = s.split('.')
    keep = {len(parts) - 1} | {i for i, p in enumerate(parts) if p.lower() == 'www'}
    if len(parts) > 2 and parts[-2].lower() in ('co', 'com', 'ac', 'org', 'edu', 'gov', 'net'): keep.add(len(parts) - 2)
    return '.'.join(p if i in keep else idm.pseudo_v1(p, key=norm(p)) for i, p in enumerate(parts))


def scope_entries(rel):
    """identities implied by the source folder of a corpus path (owner, repo, catalog title, web domain): the camelCase-split phrase and
    each distinctive word, so 'AcmeRobotics' also matches 'Acme Robotics' and 'example' matches in 'docs.example.org' PDFs"""
    from .discover import english, GENERIC
    p = rel.split('/')
    if len(p) < 4 or p[0] != 'sources': return {}
    seg = p[2]
    if p[1] in ('github', 'gitlab'):
        parts = seg.split('__')
        if len(parts) > 1 and HEX12.match(parts[-1]): parts = parts[:-1]
        if p[1] == 'gitlab' and parts and parts[0] == 'gitlab.com': parts = parts[1:]
    elif p[1] == 'web': parts = [seg[4:] if seg.startswith('www.') else seg]
    else: parts = [seg]
    out = {}; skip = ('github', 'gitlab', 'extracted', 'original', 'manufacturing', 'models', 'parts', 'drawings', 'com', 'org', 'net', 'edu', 'www', 'gov')
    for pi, part in enumerate(parts):
        if p[1] == 'catalog': break
        words = [w for w in re.split(r'[^\w]+|_', CAMEL.sub(' ', part)) if w and w.lower() not in skip]
        chunks = [c for c in re.split(r'[-_. ]+', part) if c]
        is_repo = p[1] in ('github', 'gitlab') and pi == len(parts) - 1 and len(parts) > 1
        if len(words) >= 2 and sum(len(w) for w in words) >= 6: out[' '.join(words)] = 's'
        if is_repo and len(chunks) > 1: continue          # multi-word repo names: the phrase only (the words are ordinary vocabulary in other languages too)
        for w in (words if not is_repo else [''.join(words)]):
            w2 = re.sub(r'\d+$', '', w)
            lw = w2.lower()
            if len(w2) >= 4 and lw not in english() and lw not in GENERIC: out[w2] = 's'
    return out


def meta_tokens(vals):
    """account/person tokens of Author-like metadata: 'DOMAIN\\user (HOST)' -> domain, user, host"""
    out = {}
    for v in vals:
        for t in TOKEN.findall(v or ''):
            if (len(t) >= 2 and re.search(r'[^\W\d_]', t)) or (t.isdigit() and len(t) >= 4): out[t] = 't'     # digit-only account/employee numbers too (metadata only)
    return out


LABELS = set("""drawn designed checked approved reviewed author created engineer designer drafter drafted checker approver issued released
gezeichnet kontrolliert gez gepr geprüft geprueft genehmigt freigegeben bearbeiter konstrukteur ersteller entwickelt bearb norm gezeichnet
dibujado dibujó revisado aprobado diseñado comprobado realizado dessiné dessine vérifié verifie approuvé dessinateur contrôlé disegnato controllato approvato
desenhado aprovado verificado getekend gecontroleerd goedgekeurd 制图 设计 校对 审核 批准 绘图 工艺 标准化 設計 製図 承認 作成 chk'd appv'd chkd appvd""".split())
PREP = set('by par von door por'.split())
STOPW = set('name date datum signature sign scale size sheet title rev revision material weight mass drawing part number no none na tbd mm kg'.split())
_DF = None


def _df():
    global _DF
    if _DF is None:
        f = os.environ.get('MACHINA_DF'); _DF = json.load(open(f)) if f and os.path.exists(f) else {}
    return _DF


def titleblock_names(pg):
    """names printed next to / under a title-block label (drawn, checked, approved ... in several languages): returns {surface: 'c'}"""
    from .discover import english
    try: words = pg.get_text('words')
    except Exception: return {}
    if len(words) > 700: return {}      # prose page, not a drawing sheet
    df = _df(); out = {}
    def lab(w, S=LABELS): return re.sub(r"[^\w']", '', w[4].lower()).strip("'") in S
    def okt(t):
        tl = t.lower().strip('.')
        return (len(t) >= 2 and t[0].isupper() and len(t) <= 14 and re.match(r"^[^\W\d_][\w.'’-]*$", t) and tl not in LABELS and tl not in STOPW and tl not in english()
                and df.get(tl, 0) < 80 and not re.search(r'\d', t))
    for L in words:
        if not lab(L): continue
        h = max(L[3] - L[1], 4); cands = []
        for W in words:
            if W is L or lab(W, LABELS | PREP): continue
            right = abs((W[1] + W[3]) / 2 - (L[1] + L[3]) / 2) < 0.6 * h and L[2] - 1 <= W[0] <= L[2] + 150
            below = L[3] - 1 <= W[1] <= L[3] + 3.5 * h and L[0] - 25 <= W[0] <= L[2] + 70
            if right or below: cands.append(W)
        cands.sort(key=lambda w: (round(w[1] / (h * 0.8)), w[0]))
        run = []
        for W in cands + [None]:
            if W is not None and okt(W[4]): run.append(W[4]); continue
            toks = [t for t in run if t]
            if toks and len(toks) <= 4:
                if len(toks) >= 2: out[' '.join(toks)] = 'c'
                for t in toks:
                    for part in {t, *[x for x in t.split('.') if len(x) >= 3]}:
                        if len(part.strip('.')) >= 3 or ('.' in part and len(part) >= 3): out[part] = 'c'
            run = []
    return out


def account_surnames(tok, page_text):
    """'sgarin' (initial + surname account) -> 'doe' when that word is in the text; also 'doe' alone and the matching first-name word"""
    out = {}
    words = {w.lower() for w in re.findall(r"[^\W\d_]{4,}", page_text)}
    for t in tok:
        t = t.lower()
        if re.fullmatch(r'[a-z]{5,14}', t) and t not in words and t[1:] in words: out[t[1:]] = 'c'
    return out


def local_entries(text, extra_tokens):
    ents = {}
    for m in EMAIL.finditer(text):
        e = m.group(0); ents[e] = 'e'
        parts = [x for x in re.split(r'[._-]+', e.split('@')[0]) if x.isalpha() and len(x) >= 3]
        if len(parts) == 2: ents[' '.join(parts)] = 'c'
    for m in HOST.finditer(text):
        h = m.group(1)
        if h.lower().removeprefix('www.') in PUBLIC_HOSTS or h.lower() in PUBLIC_HOSTS: continue
        ents.setdefault(h, 'h')
    for m in PHONE.finditer(text):
        n = m.group(1)
        if 8 <= sum(c.isdigit() for c in n) <= 15 and not re.search(r'\d\.\d{3}\)', n): ents.setdefault(n, 'l')
    ents.update(extra_tokens)
    return {k: v for k, v in ents.items() if k and len(norm(k).replace(' ', '')) >= 2}


def replace_str(s, ctx, loc):
    """plain-string replacement (metadata, links, outline, annotations): case/ligature-insensitive, same length"""
    if not s: return s
    low = fold(s); hits = merge_hits(ctx.M.find(low), loc.find(low) if loc else [])
    if not hits: return s
    out = []; last = 0
    for st, e, k, src in hits:
        out.append(s[last:st]); out.append(ctx.pseudo(s[st:e], k, src)); last = e
    out.append(s[last:])
    return ''.join(out)


def page_edits(pg, ctx, loc):
    """[(rect, replacement text, fontsize)] for every dictionary match in the page's text, from the exact glyph boxes"""
    import fitz
    rd = pg.get_text('rawdict'); edits = []
    orig = []; idx = []; boxes = []; sizes = []; lines = []; origs = []; li = 0
    for b in rd['blocks']:                  # whole page as one text (names split over lines/blocks still match); ' ' between lines
        if b.get('type') != 0: continue
        for l in b['lines']:
            li += 1
            for sp in l['spans']:
                for c in sp['chars']:
                    ch = unicodedata.normalize('NFKC', c['c']) if len(c['c']) == 1 else c['c']
                    for x in (ch or ' '):
                        if x in ZW or (x.isspace() and orig and orig[-1] == ' '): continue   # zero-width chars stay in place; runs of blanks count as one
                        orig.append(' ' if x.isspace() else x.translate(FOLD)); idx.append(len(boxes))
                    boxes.append(c['bbox']); sizes.append(sp['size']); lines.append(li); origs.append((c['origin'], sp.get('color', 0), l.get('dir', (1, 0))))
            if not orig or orig[-1] != ' ': orig.append(' '); idx.append(-1)
    if boxes:
        text = ''.join(orig); low = lower1(text)
        if len(low) == len(text):
            for st, e, k, src in merge_hits(ctx.M.find(low), loc.find(low) if loc else []):
                ps = ctx.pseudo(text[st:e], k, src)
                if len(ps) != e - st: continue
                runs = {}
                for i in range(st, e):
                    ci = idx[i]
                    if ci >= 0: runs.setdefault(lines[ci], []).append((ci, ps[i - st]))
                for lk, items in runs.items():
                    r = fitz.Rect(boxes[items[0][0]])
                    for ci, _ in items: r |= fitz.Rect(boxes[ci])
                    edits.append((r, ''.join(c for _, c in items), min(sizes[ci] for ci, _ in items), origs[items[0][0]]))
    return edits


def apply_page(pg, ctx, loc):
    """redact every match (text removed from the content stream, no fill so the drawing is untouched), then write the pseudonym at the
    original baseline with insert_text (a redaction annotation's own text is silently dropped below about 6 pt)"""
    import fitz
    edits = page_edits(pg, ctx, loc)
    for r, txt, size, org in edits:
        pg.add_redact_annot(fitz.Rect(r.x0 + 0.2, r.y0 + 0.15, r.x1 - 0.2, r.y1 - 0.15), fill=False, cross_out=False)
    if not edits: return 0, 0
    pg.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
    for r, txt, size, ((ox, oy), col, (dx, dy)) in edits:
        rot = 90 if dy < -0.7 else 270 if dy > 0.7 else 180 if dx < -0.7 else 0     # text direction of the line (vertical title-block text)
        room = r.height if rot in (90, 270) else r.width
        fs = max(2.5, size * 0.95); w = fitz.get_text_length(txt, 'helv', fs)
        if w > room * 1.05 and w: fs = max(2.5, fs * room * 1.05 / w)
        rgb = ((col >> 16 & 255) / 255, (col >> 8 & 255) / 255, (col & 255) / 255)
        try: pg.insert_text(fitz.Point(ox, oy), txt, fontsize=fs, fontname='helv', color=rgb, rotate=rot)
        except Exception: pass
    flat = re.sub(r'\s+', '', pg.get_text('text')).lower()
    return len(edits), sum(1 for _, txt, _, _ in edits if re.sub(r'\s+', '', txt).lower() not in flat)


def page_residue(pg, ctx, loc):
    low = fold(pg.get_text('text'))
    return len(merge_hits(ctx.M.find(low), loc.find(low) if loc else []))


def pdf3(data, idm, depth, name, process):
    import fitz, time
    t0 = time.time()
    global _PROT
    _PROT = ' '      # attempt 5: the drawing's own part number is an identifier in the PDF checks (file names keep their stems)
    fitz.TOOLS.set_small_glyph_heights(True)
    ctx = getattr(idm, '_pdfctx', None)
    if ctx is None: ctx = idm._pdfctx = Ctx(idm)
    doc = fitz.open(stream=data, filetype='pdf')
    if doc.needs_pass: raise ValueError('encrypted')
    md = {k: v for k, v in doc.metadata.items() if k not in ('format', 'encryption')}
    xmp = ''
    try: xmp = doc.get_xml_metadata() or ''
    except Exception: pass
    # custom /Info string keys
    info = {}; ix = None
    try:
        t, v = doc.xref_get_key(-1, 'Info')
        if t == 'xref':
            ix = int(v.split()[0])
            for k in doc.xref_get_keys(ix):
                kt, kv = doc.xref_get_key(ix, k)
                if kt == 'string': info[k] = kv
    except Exception: ix = None
    acct = [md.get('author'), info.get('Company'), info.get('Manager'), info.get('LastModifiedBy'), info.get('Author')]
    for m in re.finditer(r'<(?:dc:creator|pdf:Author|xmp:Creator|xmpMM:LastModifiedBy|pdfx:Company|pdfx:Manager|dc:publisher)[^>]*>(?:\s*<[^>]+>)*\s*([^<]+?)\s*<', xmp): acct.append(m.group(1))
    tok = meta_tokens(acct)
    body_tok = {k: v for k, v in tok.items() if len(k) >= 3 and k not in GENERIC_ACCT and re.search(r'[a-z]', k)}   # tokens also searched in the page text
    page_text = ''
    for pg in doc:
        try: page_text += pg.get_text('text') + '\n'
        except Exception: pass
    page_text = page_text[:2_000_000]
    for pg in doc:
        try:
            for lk in pg.get_links():
                if lk.get('uri'): page_text += '\n' + lk['uri']
        except Exception: pass
    sc = scope_entries(name) if depth == 0 else {}
    tbn = {}
    for pg in doc:
        try: tbn.update(titleblock_names(pg))
        except Exception: pass
    loc = Matcher({**local_entries(page_text, body_tok), **sc, **tbn, **account_surnames(tok, page_text)})
    meta_loc = Matcher({**loc_entries_meta(tok), **local_entries(' '.join(str(v) for v in md.values() if v) + ' ' + xmp, {}), **sc})
    nm = lambda s: replace_str(s, ctx, meta_loc)
    # page content
    n_edit = 0; resid = 0; lost = 0
    for pg in doc:
        try:
            ne, miss = apply_page(pg, ctx, loc); n_edit += ne; lost += miss
            resid += page_residue(pg, ctx, loc)
        except Exception as e:
            idm.errors.append((name, 'pdf3', 'page: ' + repr(e)[:80]))
        try:
            for an in pg.annots() or ():
                inf = an.info; new = {k: nm(v) for k, v in inf.items() if isinstance(v, str)}
                if new != {k: v for k, v in inf.items() if isinstance(v, str)}:
                    an.set_info(**{k: v for k, v in new.items() if k in ('title', 'content', 'subject', 'name')}); an.update()
            for w in pg.widgets() or ():
                if isinstance(w.field_value, str) and nm(w.field_value) != w.field_value:
                    w.field_value = nm(w.field_value); w.update()
        except Exception: pass
        for lk in pg.get_links():
            if lk.get('uri') and nm(lk['uri']) != lk['uri']:
                try: lk['uri'] = nm(lk['uri']); pg.update_link(lk)
                except Exception: pg.delete_link(lk)
    rewrite_objects(doc, nm)
    try:
        from . import logos
        logos.replace_logos(doc, idm)
    except Exception as e: idm.errors.append((name, 'pdf3', 'logos: ' + repr(e)[:60]))
    # metadata: author-like fields are always pseudonymised
    def auth(s):
        global _PROT
        keep, _PROT = _PROT, ' '          # author fields are always pseudonymised, even when the word is also in the file name
        try: return replace_str(s, ctx, Matcher(tok_keys(tok)))
        finally: _PROT = keep
    newmd = {}
    for k, v in md.items():
        if not isinstance(v, str): newmd[k] = v; continue
        nv = nm(v)
        if k in ('author', 'subject', 'keywords') and v: nv = auth(nv)
        newmd[k] = nv
    try: doc.set_metadata(newmd)
    except Exception: pass     # no /Info dictionary to write into
    if ix is not None:
        for k, kv in info.items():
            if k in md or k in ('Title', 'Author', 'Subject', 'Keywords', 'Creator', 'Producer', 'CreationDate', 'ModDate'): continue
            nv = nm(kv)
            if k in ('Company', 'Manager', 'LastModifiedBy'): nv = auth(nv)
            if nv != kv:
                try: doc.xref_set_key(ix, k, fitz.get_pdf_str(nv))
                except Exception: pass
    if xmp:
        try: doc.set_xml_metadata(auth(nm(xmp)))
        except Exception: pass
    try: doc.set_toc([[l, nm(t), p] for l, t, p in doc.get_toc(simple=True)])
    except Exception: pass
    for n in list(doc.embfile_names()):
        b = doc.embfile_get(n); nb = process(b, n, idm, depth + 1); desc = doc.embfile_info(n).get('desc') or ''
        doc.embfile_del(n)   # embfile_upd with a buffer raises in this PyMuPDF (attempt 2 silently fell back to a plain replace for such PDFs)
        doc.embfile_add(nm(n), nb, filename=nm(n), ufilename=nm(n), desc=nm(desc))
    if resid: idm.errors.append((name, 'pdf3', f'residue {resid}'))
    if time.time() - t0 > 60: idm.errors.append((name, 'pdf3', f'slow {time.time() - t0:.0f}s'))
    if lost: idm.errors.append((name, 'pdf3', f'insert missing {lost}/{n_edit}'))
    try: out = doc.tobytes(garbage=3, deflate=True)
    except Exception: out = doc.tobytes(garbage=1, deflate=True, clean=True)
    from .handlers import stable_pdf_id
    return stable_pdf_id(out)      # MuPDF writes a random trailer /ID per save; make the output reproducible


LIT = re.compile(rb'\((?:\\.|[^()\\])*\)')
OBJKEYS = re.compile(r'/(?:URI|Contents|Subj|TU|T|Author|Company|Manager|Subject|Keywords|Title|NM|F|UF|Desc|Name)\b')   # Name: optional content group (layer) names


def rewrite_objects(doc, nm):
    """literal strings of non-stream objects whose dictionaries carry text keys (URI actions, annotation /T /Contents, ...): link
    annotations stored as direct dictionaries inside an /Annots array have no xref, so Page.update_link cannot reach them"""
    n = 0
    for x in range(1, doc.xref_length()):
        try:
            if doc.xref_is_stream(x): continue
            o = doc.xref_object(x, compressed=False)
        except Exception: continue
        if '(' not in o or not OBJKEYS.search(o): continue
        ob = o.encode('latin1', 'ignore')
        def fix(m):
            t = m.group(0)
            try: new = nm(t.decode('latin1')).encode('latin1')
            except Exception: return t
            return new if len(new) == len(t) else t
        nb = LIT.sub(fix, ob)
        if nb != ob:
            try: doc.update_object(x, nb.decode('latin1')); n += 1
            except Exception: pass
    return n


def tok_keys(tok): return {k: 't' for k in tok}


def loc_entries_meta(tok): return dict(tok)
