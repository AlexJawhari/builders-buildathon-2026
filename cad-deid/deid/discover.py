"""Pass 1: find identity strings. From paths (manifest only, no download) and from file metadata/text."""
import io, os, re, zipfile, collections
from . import swfmt

WORDS = None
def english():
    global WORDS
    if WORDS is None:
        try: WORDS = {w.strip() for w in open('/usr/share/dict/words') if w[:1].islower()}
        except OSError: WORDS = set()
    return WORDS

GENERIC = set('''utilisateur benutzer usuario utente open cascade author unknown user administrator admin owner none anonymous root n/a na nobody guest default test demo
open cascade spatial corp freecad kicad solidworks dassault autodesk inventor ptc siemens nx catia sdrc acis step parts
step.parts opencascade occt autocad microsoft windows linux ubuntu github gitlab gmail outlook hotmail yahoo example
company organization personal home pc computer desktop documents downloads temp tmp users data public mechanical
design designer engineer student teacher school university institute department lab laboratory group team project'''.split())
PUBLIC_DOMAINS = {'github.com', 'gitlab.com', 'gmail.com', 'outlook.com', 'hotmail.com', 'yahoo.com', 'live.com', 'qq.com', '163.com',
                  'google.com', 'microsoft.com', 'autodesk.com', 'solidworks.com', 'example.com', 'w3.org', 'adobe.com', 'inkscape.org',
                  'purl.org', 'creativecommons.org', 'iso.org', 'apple.com', 'icloud.com', 'protonmail.com', 'proton.me', 'mail.com', 'web.de'}
HEX12 = re.compile(r'^[0-9a-f]{12}$')


TLDS = set('com org net edu gov io de fr cn ru uk it es jp nl ch at se no dk fi pl cz br ca au in kr tw hk eu us be pt gr hu ro ua il za ar mx nz sg tr info xyz co me dev ai app'.split())
NICE = re.compile(r"^[\w .,'&()/@-]+$", re.U)


def dec(b):
    try: return b.decode('utf-8')
    except UnicodeDecodeError: return b.decode('latin1')


def ok_email(e):
    loc, dom = e.rsplit('@', 1); labels = dom.lower().split('.')
    return labels[-1] in TLDS and all(re.fullmatch(r'[a-z0-9-]{2,}', x) for x in labels) and len(loc) >= 3 and not re.search(r'\d{5,}', loc) and sum(c.isupper() for c in loc) <= len(loc) // 2 + 1


BAD_PHRASE = re.compile(r'\b(?:all users|technical reference|document type|your company|autodesk|sheet|aprovado|approved|checked|drawn|not applicable|default user|public)\b|&\w*;?$|^[A-Z0-9]+_[A-Z0-9_]+$', re.I)


def ok_ident(s):
    s = s.strip()
    if BAD_PHRASE.search(s): return False
    if not NICE.match(s) or s.count('.') > 3: return False
    if re.search(r'[A-Z][a-z]*[A-Z][a-z]*[A-Z]', s) and ' ' not in s and len(s) > 12 and not re.search(r'[-_]', s): return False
    if len(s) < 3 or len(s) > 80 or not re.search(r'[A-Za-z\u0080-￿]', s): return False
    k = s.lower()
    if k in GENERIC or k in ('open cascade', 'spatial corp') or any(w in ('cascade', 'spatial', 'solidworks', 'freecad', 'autodesk', 'dassault') for w in k.split()): return False
    if ' ' not in k and k in english(): return False
    return True


def generic_ok(x):
    k = x.lower()
    return not (k in GENERIC or k in english() or k in ('resources', 'project', 'projects', 'solidworks', 'cad', 'models', 'hardware', 'mechanical'))


def path_identities(rel):
    """Identity strings implied by a corpus path (repo owner, repo, catalog title, site domain, gitlab groups)."""
    p = rel.split('/')
    out = []
    if len(p) < 3: return out
    if p[1] in ('github', 'gitlab'):
        parts = p[2].split('__')
        if parts and HEX12.match(parts[-1]) and len(parts) > 1: parts = parts[:-1]
        if p[1] == 'gitlab' and parts and parts[0] == 'gitlab.com': parts = parts[1:]
        parts = [x.strip('_') for x in parts if x.strip('_')]
        out += [x for x in parts if len(parts) == 1 or generic_ok(x)]
        if len(parts) > 1 and p[1] == 'github': out.append('__'.join(parts))
        # distinctive sub-tokens
        for x in parts:
            for t in re.split(r'[-_. ]+', x):
                t2 = re.sub(r'\d+$', '', t)
                if len(t2) >= 5 and t2.lower() not in english() and t2.lower() not in GENERIC: out.append('~' + t2)
    elif p[1] == 'catalog':
        out.append(p[2])
    elif p[1] == 'web':
        out.append(p[2])
    return [o for o in out if o]


EMAIL = re.compile(rb'(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{2,40}@[A-Za-z0-9.-]{1,80}\.[A-Za-z]{2,}')
USERPATH = re.compile(rb'(?:[A-Za-z]:[\\/]+Users[\\/]+|/home/|/Users/)([A-Za-z0-9._ -]{2,32})[\\/]')
GHURL = re.compile(rb'github\.com[/:]([A-Za-z0-9_.-]{2,39})(?:/([A-Za-z0-9_.-]{2,100}))?')
STEPNAME = re.compile(rb"FILE_NAME\s*\((.*?)\)\s*;", re.S)
QSTR = re.compile(rb"'((?:[^']|'')*)'")


FIELDS = [
    (re.compile(rb'name="(?:CreatedBy|LastModifiedBy|Company|Author)"[^>]*>\s*<String value="([^"]+)"'), 'person'),   # FCStd Document.xml
    (re.compile(rb'<metadata name="(?:Designer|Author|Copyright|Organization|Company)"[^>]*>([^<]+)<'), 'person'),    # 3MF
    (re.compile(rb'<(?:dc:creator|meta:initial-creator|cp:lastModifiedBy|Manager|Company)>([^<]+)<'), 'person'),    # OPC/ODF
    (re.compile(rb'MC=([A-Za-z0-9_.-]{3,40});'), 'host'),                                                           # Parasolid
    (re.compile(rb'\$LASTSAVEDBY\s+1\s+([^\r\n]+)'), 'person'),                                                        # DXF
]


def _text_ids(b, out):
    for rx, kind in FIELDS:
        for m in rx.finditer(b): out.append((kind, dec(m.group(1)).strip()))
    for m in (EMAIL.finditer(b) if b'@' in b else ()):
        e = dec(m.group(0)); loc, dom = e.split('@', 1)
        if not ok_email(e): continue
        out.append(('email', e))
        if dom.lower() not in PUBLIC_DOMAINS: out.append(('domain', dom))
    for m in USERPATH.finditer(b):
        out.append(('user', dec(m.group(1))))
    for m in GHURL.finditer(b):
        out.append(('ghuser', m.group(1).decode()))


def content_identities(rel, data, depth=0):
    """List of (kind, string) found in the file; recurses into archives."""
    from .handlers import sniff
    out = []
    ext = rel.lower().rsplit('.', 1)[-1]
    kind = sniff(data, rel)
    if kind == 'zip' and depth < 4:
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
            for zi in z.infolist():
                if not zi.is_dir() and zi.file_size < 200e6:
                    out += content_identities(zi.filename, z.read(zi), depth + 1)
        except Exception: pass
        return out
    if kind == 'gz' and depth < 4:
        try:
            import gzip; return content_identities(rel[:-3], gzip.decompress(data), depth + 1)
        except Exception: return out
    blobs = [data[:20000] if ext in ('step', 'stp', 'p21') else data[:2_000_000]]
    if ext == 'dxf' and data[:200].lstrip()[:1] != b'\x00' and b'SECTION' in data[:400]:
        from . import titleblock
        out += titleblock.from_lines(titleblock.dxf_text_lines(data[:20_000_000]))
    if kind == 'sw':
        blobs = []
        for p, name, ps, comp, o in swfmt.chunks(data):
            if name.startswith('docProps/') and 'core' in name or name in ('docProps/custom.xml', 'docProps/app.xml'):
                t = o.decode('utf8', 'replace')
                for val in re.findall(r'<(?:dc:creator|cp:lastModifiedBy|Manager|Company)>([^<]+)', t):
                    out.append(('person', val))
                for n, v in re.findall(r'name="([^"]+)"[^>]*>\s*<vt:lpwstr>([^<]*)', t):
                    if re.search(r'author|company|customer|design|draw|check|approv|engineer|owner|client|vendor|supplier', n, re.I) and ok_ident(v):
                        out.append(('person', v))
            if name.endswith(('Biography', 'History')) or name in ('Header2', 'Contents/Definition', 'Contents/CusProps'):
                blobs.append(o)
    if kind == 'ole':
        try:
            import olefile
            with olefile.OleFileIO(io.BytesIO(data)) as f:
                md = f.get_metadata()
                for a in ('author', 'last_saved_by', 'company', 'manager'):
                    v = getattr(md, a, None)
                    if isinstance(v, bytes): v = v.decode('latin1', 'ignore')
                    if v: out.append(('person', v))
                for s in f.listdir(streams=True, storages=False):
                    if f.get_size(s) < 3_000_000: blobs.append(f.openstream(s).read())
        except Exception: pass
    if kind == 'pdf':
        try:
            import fitz
            d = fitz.open(stream=data, filetype='pdf')
            for k in ('author',):
                if d.metadata.get(k): out.append(('person', d.metadata[k]))
            blobs.append((d.metadata.get('subject') or '').encode() + (d.get_xml_metadata() or '').encode())
            from . import titleblock
            out += titleblock.from_text('\n'.join(pg.get_text() for pg in d[:6]))
        except Exception: pass
    for b in blobs:
        for enc_b in (b, b.replace(b'\x00', b'')):  # also catches UTF-16LE ASCII text
            _text_ids(enc_b, out)
        if ext in ('step', 'stp', 'p21'):
            m = STEPNAME.search(b)
            if m:
                strs = [dec(x).replace("''", "'") for x in QSTR.findall(re.sub(rb'/\*.*?\*/', b'', m.group(1), flags=re.S))]
                # fields: name, time, author(s), org(s), preproc, origsys, auth
                if len(strs) >= 4:
                    for v in strs[2:4]: out.append(('person', v))
    return out


def merge(items, repo_counts, min_repos_generic=6):
    """items: dict string -> set(repo). Drop strings seen in many repos (software/vendor/generic) and generic words."""
    res = {}
    for s, repos in items.items():
        if len(repos) >= min_repos_generic or not ok_ident(s): continue
        res[s] = len(repos)
    return res


def manifest_identities(rels, max_repos=2):
    """Pass 1a, manifest only (no download): all path identities. Repo-name sub-tokens ('~' prefix) are kept only if the word
    appears in the paths of at most `max_repos` repos (so a project word like 'spectrometer' that is also an ordinary folder
    name elsewhere is not renamed everywhere)."""
    import collections
    ids = collections.defaultdict(set); words = collections.defaultdict(set)
    for rel in rels:
        p = rel.split('/'); repo = '/'.join(p[:3])
        for w in re.split(r'[^A-Za-z0-9\u0080-\uffff]+', '/'.join(p[3:]).lower()): words[w].add(repo)
        for x in path_identities(rel): ids[x].add(repo)
    out = set()
    for x, repos in ids.items():
        if x.startswith('~'):
            t = x[1:]
            if len(words.get(t.lower(), ())) <= max_repos: out.add(t)
        else: out.add(x)
    return out
