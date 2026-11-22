"""Per-format rewrite handlers. Every handler: (data: bytes, idm: IdMap) -> bytes. Replacement is same-length so binary
containers stay valid; containers (zip/tar/gz/OLE/PDF/SolidWorks chunks/PNG) are opened, rewritten and re-packed."""
import gzip, io, os, re, struct, tarfile, tempfile, zipfile, zlib
from . import swfmt

SW_EXT = ('.sldprt', '.sldasm', '.slddrw', '.sldlfp', '.slddrt')


def sniff(data: bytes, name: str) -> str:
    h = data[:8]
    if h[:5] == b'%PDF-': return 'pdf'
    if h[:4] == b'PK\x03\x04': return 'zip'
    if h[:2] == b'\x1f\x8b': return 'gz'
    if h[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1': return 'ole'
    if h[:8] == b'\x89PNG\r\n\x1a\n': return 'png'
    if name.lower().endswith(SW_EXT) and swfmt.M in data[:64]: return 'sw'
    if len(data) > 262 and data[257:262] == b'ustar': return 'tar'
    return 'raw'


PREVIEW_NAME = re.compile(r'(?:^|/)(?:thumbnails?|previews?)[^/]*\.(?:png|jpe?g|bmp)$|(?:^|/)Previews?/.+\.(?:png|jpe?g|bmp)$|thumbnail\.(?:png|jpe?g)$', re.I)


def blank_image(data: bytes) -> bytes:
    """Neutral gray image of the same pixel size and format: keeps the file valid, removes any title block / logo shown in a preview."""
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data)); fmt = im.format; sz = im.size
        out = io.BytesIO(); Image.new('RGB', sz, (214, 214, 214)).save(out, fmt)
        return out.getvalue()
    except Exception:
        return data


def process(data: bytes, name: str, idm, depth=0) -> bytes:
    if name.lower().endswith('.dwg'):
        return data   # compressed, CRC-protected sections: byte edits would corrupt it; left unchanged and documented
    if PREVIEW_NAME.search(name) and data[:8] in (b'\x89PNG\r\n\x1a\n',) or (PREVIEW_NAME.search(name) and data[:3] == b'\xff\xd8\xff'):
        return blank_image(data)
    kind = sniff(data, name)
    if depth > 6: kind = 'raw'
    fn = {'pdf': pdf, 'zip': zipf, 'gz': gz, 'ole': ole, 'png': png, 'sw': sw, 'tar': tar}.get(kind)
    if fn:
        try:
            return fn(data, idm, depth, name)
        except Exception as e:  # fall back to plain same-length replace, never drop the file
            idm.errors.append((name, kind, repr(e)[:120]))
    if kind == 'raw' and b'\\X2\\' in data and name.lower().endswith(('.step', '.stp', '.p21')): data = step_x2(data, idm)
    return idm.replace(data)


X2 = re.compile(rb'\\X2\\([0-9A-Fa-f]+)\\X0\\')


X2S = re.compile(r'\\X2\\([0-9A-Fa-f]+)\\X0\\')
LIT = re.compile(rb"'((?:[^']|'')*)'")


def _x2_decode(m): return bytes.fromhex(m.group(1)).decode('utf-16-be')


def _x2_encode(t):
    out = []; run = ''
    for ch in t + '\0':
        if ch != '\0' and ord(ch) > 126: run += ch; continue
        if run: out.append('\\X2\\' + run.encode('utf-16-be').hex().upper() + '\\X0\\'); run = ''
        if ch != '\0': out.append(ch)
    return ''.join(out)


def step_x2(data, idm):
    """STEP string literals with \\X2\\<utf-16-be hex>\\X0\\ escapes: decode the whole literal, replace identities, re-escape non-ASCII."""
    def fix(m):
        lit = m.group(1)
        if b'\\X2\\' not in lit: return m.group(0)
        try:
            t = X2S.sub(_x2_decode, lit.decode('latin1'))
        except Exception: return m.group(0)
        n = idm.replace_str(t, names=False)
        return m.group(0) if n == t else b"'" + _x2_encode(n).encode('latin1') + b"'"
    return LIT.sub(fix, data)


def raw(data, idm, depth, name): return idm.replace(data)


def _read_member(zin, zi):
    """zipfile.read, plus ZIP method 93 (Zstandard, used by Fusion .f3d) via the zstandard package."""
    try:
        return zin.read(zi)
    except NotImplementedError:
        if zi.compress_type != 93: raise
        import zstandard
        zin.fp.seek(zi.header_offset); h = zin.fp.read(30)
        n, m = struct.unpack('<HH', h[26:30]); zin.fp.seek(zi.header_offset + 30 + n + m)
        return zstandard.ZstdDecompressor().decompress(zin.fp.read(zi.compress_size), max_output_size=max(zi.file_size, 1))


def zipf(data, idm, depth, name):
    zin = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zout:
        zout.comment = idm.replace(zin.comment)
        for zi in zin.infolist():
            nn = idm.replace_str(zi.filename)
            if zi.is_dir():
                zout.writestr(nn, b''); continue
            body = process(_read_member(zin, zi), zi.filename, idm, depth + 1)
            ni = zipfile.ZipInfo(nn, zi.date_time[:6] if zi.date_time[0] >= 1980 else (1980, 1, 1, 0, 0, 0))
            ni.compress_type = zipfile.ZIP_STORED if zi.compress_type == zipfile.ZIP_STORED else zipfile.ZIP_DEFLATED  # method 93 members are rewritten as deflate
            ni.external_attr = zi.external_attr
            zout.writestr(ni, body)
    return out.getvalue()


def gz(data, idm, depth, name):
    body = process(gzip.decompress(data), re.sub(r'\.gz$', '', name, flags=re.I), idm, depth + 1)
    return gzip.compress(body, 6, mtime=0)


def tar(data, idm, depth, name):
    tin = tarfile.open(fileobj=io.BytesIO(data)); out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w') as tout:
        for m in tin.getmembers():
            m.name = idm.replace_str(m.name); m.uname = m.gname = ''; m.uid = m.gid = 0
            if m.linkname: m.linkname = idm.replace_str(m.linkname)
            if m.isfile():
                body = process(tin.extractfile(m).read(), m.name, idm, depth + 1)
                m.size = len(body); tout.addfile(m, io.BytesIO(body))
            else: tout.addfile(m)
    return out.getvalue()


def sw(data, idm, depth, name):
    def edit(n, o):
        if n in ('PreviewPNG', 'Preview') and (o[:8] == b'\x89PNG\r\n\x1a\n' or o[:2] == b'BM'): return blank_image(o)
        return idm.replace(o)
    return swfmt.rewrite(data, edit, idm.replace)


def zlb_replace(d, idm, loc=None):
    """Pre-2015 SolidWorks OLE streams named *__ZLB: 24-byte header (..., inflated size u32, deflated size u32 at +20), a zlib stream,
    then 8 trailer bytes. Replace identities inside the inflated data; recompress into the same compressed size (zero-padded) so the
    header and stream length stay valid. Returns new stream bytes, or None when not applicable / does not fit."""
    import zlib
    if len(d) < 32: return None
    comp = struct.unpack_from('<I', d, 20)[0]
    if comp <= 0 or 24 + comp > len(d): return None
    try:
        co = zlib.decompressobj(); inf = co.decompress(d[24:24 + comp])
        if not co.eof: return None
    except zlib.error: return None
    new = idm.replace(inf)
    if loc is not None and loc.ids: new = loc.replace(new, names=True)
    if new == inf: return idm.replace(d[:24]) + d[24:]          # nothing inside; header/trailer untouched
    for lvl in (9, 6, 5, 4, 3, 1):
        z = zlib.compress(new, lvl)
        if len(z) <= comp: return d[:24] + z + b'\0' * (comp - len(z)) + d[24 + comp:]
    idm.errors.append(('zlb', 'ole', 'recompressed stream does not fit'))
    return None


def ole(data, idm, depth, name):
    import olefile
    from .ident import IdMap
    v3 = os.environ.get('MACHINA_OLE') == '3'
    loc = None
    if v3:   # attempt 5: every saved account/author of THIS file (even generic ones such as Administrator) is replaced in all streams
        try:
            md = olefile.OleFileIO(io.BytesIO(data)).get_metadata(); loc = IdMap(idm.secret); loc.mode = idm.mode
            for k in ('author', 'last_saved_by', 'manager', 'company'):
                v = getattr(md, k, None)
                v = v.decode('cp1252', 'ignore') if isinstance(v, bytes) else v
                if v and len(v.strip()) >= 2 and v.strip().lower() not in ('dassault', 'autodesk', 'microsoft', 'solidworks', 'ptc', 'siemens'): loc.add(v.strip(), min_len=2)
        except Exception: loc = None
    rep = (lambda b: loc.replace(idm.replace(b), names=True)) if loc and loc.ids else idm.replace
    with tempfile.NamedTemporaryFile(suffix='.ole') as t:
        t.write(data); t.flush()
        f = olefile.OleFileIO(t.name, write_mode=True)
        for s in f.listdir(streams=True, storages=False):
            old = f.openstream(s).read()
            new = zlb_replace(old, idm, loc) if s[-1].endswith('__ZLB') else None
            if new is None:
                new = idm.replace(old)
                if loc and loc.ids: new = loc.replace(new, names=True)
            if new != old and len(new) == len(old): f.write_stream(s, new)
        f.close()
        out = rep(open(t.name, 'rb').read())  # also catches free/unreferenced sectors and directory names
    if v3:
        from . import olefree
        out = olefree.scrub(out)
    return out


def png(data, idm, depth, name):
    out = [data[:8]]; p = 8
    while p + 8 <= len(data):
        ln, = struct.unpack('>I', data[p:p + 4]); typ = data[p + 4:p + 8]; body = data[p + 8:p + 8 + ln]
        if typ in (b'tEXt', b'iTXt', b'eXIf') and ln:
            nb = idm.replace(body)
            if nb != body:
                crc = zlib.crc32(typ + nb) & 0xffffffff
                out.append(data[p:p + 8] + nb + struct.pack('>I', crc)); p += 12 + ln; continue
        out.append(data[p:p + 12 + ln]); p += 12 + ln
    out.append(data[p:])
    return b''.join(out)


PDF_ID = re.compile(rb'/ID\s*\[\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\]')


def stable_pdf_id(b):
    """MuPDF writes a random trailer /ID on every save; replace it with md5 of the file (IDs zeroed) so outputs are reproducible."""
    import hashlib
    ms = list(PDF_ID.finditer(b, max(0, len(b) - 4096)))
    if not ms: return b
    m = ms[-1]; z = b[:m.start(1)] + b'0' * len(m.group(1)) + b[m.end(1):m.start(2)] + b'0' * len(m.group(2)) + b[m.end(2):]
    h = hashlib.md5(z).hexdigest().upper().encode()
    f = lambda n: (h * (n // 32 + 1))[:n]
    return b[:m.start(1)] + f(len(m.group(1))) + b[m.end(1):m.start(2)] + f(len(m.group(2))) + b[m.end(2):]


def pdf(data, idm, depth, name):
    if os.environ.get('MACHINA_PDF') == '3':
        from . import pdfx
        return pdfx.pdf3(data, idm, depth, name, process)
    import fitz
    fitz.TOOLS.set_small_glyph_heights(True)   # redaction boxes must not swallow neighbouring glyphs
    doc = fitz.open(stream=data, filetype='pdf')
    if doc.needs_pass: raise ValueError('encrypted')
    md = {k: idm.replace_str(v) if isinstance(v, str) else v for k, v in doc.metadata.items() if k not in ('format', 'encryption')}
    for pg in doc:
        txt = pg.get_text('text')
        if not txt.strip(): continue
        sur = idm.surfaces(txt)
        for s in sur:
            for r in pg.search_for(s):
                pg.add_redact_annot(r, text=idm.replace_str(s), fontsize=max(4, r.height * 0.7), fill=(1, 1, 1))
        if sur:
            pg.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
        try:
            for an in pg.annots() or ():
                inf = an.info; new = {k: idm.replace_str(v) for k, v in inf.items() if isinstance(v, str)}
                if new != {k: v for k, v in inf.items() if isinstance(v, str)}: an.set_info(**{k: v for k, v in new.items() if k in ('title', 'content', 'subject', 'name')}); an.update()
            for w in pg.widgets() or ():
                if isinstance(w.field_value, str) and idm.replace_str(w.field_value) != w.field_value:
                    w.field_value = idm.replace_str(w.field_value); w.update()
        except Exception: pass
        for lk in pg.get_links():
            if lk.get('uri') and idm.replace_str(lk['uri']) != lk['uri']:
                try: lk['uri'] = idm.replace_str(lk['uri']); pg.update_link(lk)
                except Exception: pg.delete_link(lk)  # a link that cannot be rewritten is dropped rather than leaking the URL
    doc.set_metadata(md)
    try:   # custom /Info keys (Company, Manager, SourceModified ...) are not covered by set_metadata
        t, v = doc.xref_get_key(-1, 'Info')
        if t == 'xref':
            ix = int(v.split()[0])
            for k in doc.xref_get_keys(ix):
                kt, kv = doc.xref_get_key(ix, k)
                if kt == 'string':
                    nv = idm.replace_str(kv)
                    if nv != kv: doc.xref_set_key(ix, k, fitz.get_pdf_str(nv))
    except Exception: pass
    try:
        x = doc.get_xml_metadata()
        if x: doc.set_xml_metadata(idm.replace_str(x))
    except Exception: pass
    try:
        doc.set_toc([[l, idm.replace_str(t), p] for l, t, p in doc.get_toc(simple=True)])
    except Exception: pass
    for n in list(doc.embfile_names()):
        b = doc.embfile_get(n); nb = process(b, n, idm, depth + 1)
        doc.embfile_upd(n, nb, filename=idm.replace_str(n), desc=idm.replace_str(doc.embfile_info(n).get('desc') or ''))
    try: out = doc.tobytes(garbage=3, deflate=True)
    except Exception: out = doc.tobytes(garbage=1, deflate=True, clean=True)
    return stable_pdf_id(out)


RAW_STREAM_EXT = ('.step', '.stp', '.p21', '.stl', '.obj', '.iges', '.igs', '.sat', '.wrl', '.dxf', '.x_t', '.xmt_txt', '.unv', '.neu', '.brep', '.ps', '.eps', '.svg', '.txt')


def stream_replace(src, dst, idm, window=64 << 20, overlap=2048):
    """Same-length identity replacement of a big raw file in windows; a match straddling a window edge is finished before
    cutting. Returns (md5 hex, size)."""
    import hashlib
    h = hashlib.md5(); n = 0; buf = b''; eof = False
    with open(src, 'rb') as f, open(dst, 'wb') as o:
        while True:
            while len(buf) < window + overlap and not eof:
                b = f.read(window + overlap - len(buf))
                if b: buf += b
                else: eof = True
            hits = idm._hits(buf); out = idm.replace(buf)
            cut = len(buf) if eof else window
            for s_, e_, enc, _ in hits:
                if s_ < cut < e_: cut = e_
            o.write(out[:cut]); h.update(out[:cut]); n += cut
            buf = buf[cut:]
            if eof: break
    return h.hexdigest(), n
