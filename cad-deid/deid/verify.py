"""Verify an output tree: files still open/parse, and no known identifier remains (incl. inside archives, UTF-16LE)."""
import gzip, io, json, os, re, sys, tarfile, zipfile, collections
from . import swfmt
from .handlers import sniff
from .ident import IdMap


def residue(data, name, idm, depth=0):
    """list of leftover identity surfaces (recurse into containers)."""
    k = sniff(data, name); found = []
    try:
        if k == 'zip' and depth < 6:
            z = zipfile.ZipFile(io.BytesIO(data))
            for zi in z.infolist():
                found += [('name', x) for x in idm._surf_bytes(zi.filename.encode(), True)]
                if not zi.is_dir(): found += residue(z.read(zi), zi.filename, idm, depth + 1)
            return found
        if k == 'gz': return residue(gzip.decompress(data), name, idm, depth + 1)
        if k == 'sw':
            for p, n, ps, c, o in swfmt.chunks(data): found += [(n, s) for s in idm.surfaces(o.decode('latin1'))] + [(n, s) for s in idm.surfaces(o.decode('utf-16-le', 'ignore'))]
        if k == 'ole':
            import olefile
            f = olefile.OleFileIO(io.BytesIO(data))
            import struct, zlib
            for s in f.listdir(streams=True, storages=False):
                d = f.openstream(s).read(); found += [('/'.join(s), x) for x in idm._surf_bytes(d)]
                if s[-1].endswith('__ZLB') and len(d) >= 32:
                    try:
                        comp = struct.unpack_from('<I', d, 20)[0]; inf = zlib.decompressobj().decompress(d[24:24 + comp])
                        found += [('/'.join(s) + ' (inflated)', x) for x in idm._surf_bytes(inf)]
                    except Exception: pass
        if k == 'pdf':
            import fitz
            d = fitz.open(stream=data, filetype='pdf')
            t = ' '.join(str(v) for v in d.metadata.values()) + (d.get_xml_metadata() or '') + ''.join(pg.get_text() for pg in d)
            found += [('pdf', s) for s in idm.surfaces(t)]
    except Exception as e:
        found.append(('verify-error', repr(e)[:60]))
    found += [('raw', s) for s in idm._surf_bytes(data)]
    return found


def check_valid(data, name):
    k = sniff(data, name); ext = name.lower().rsplit('.', 1)[-1]
    try:
        if k == 'pdf':
            import fitz; d = fitz.open(stream=data, filetype='pdf'); assert d.page_count >= 0
        elif k == 'zip': assert zipfile.ZipFile(io.BytesIO(data)).testzip() is None
        elif k == 'gz': gzip.decompress(data)
        elif k == 'ole':
            import olefile; olefile.OleFileIO(io.BytesIO(data)).listdir()
        elif k == 'sw': assert sum(1 for _ in swfmt.chunks(data)) > 0
        elif ext in ('step', 'stp'): assert b'ISO-10303-21' in data[:200] and b'END-ISO-10303-21' in data[-300:]
        elif ext == 'svg':
            from lxml import etree; etree.fromstring(data)
        elif k == 'png':
            from PIL import Image; Image.open(io.BytesIO(data)).load()
        elif ext in ('jpg', 'jpeg'):
            from PIL import Image; Image.open(io.BytesIO(data)).load()
    except Exception as e:
        return repr(e)[:80]
    return None


if __name__ == '__main__':
    inroot, outroot, idents = sys.argv[1:4]
    from .ident import load_secret
    idm = IdMap(load_secret('work/secret.key'))
    for s in json.load(open(idents)): idm.add(s)
    bad = collections.Counter(); res = collections.Counter(); n = 0; ex = {}
    sizes = collections.Counter()
    for dp, dn, fn in os.walk(outroot):
        for f in fn:
            ap = os.path.join(dp, f); rel = os.path.relpath(ap, outroot); data = open(ap, 'rb').read(); n += 1
            e = check_valid(data, rel)
            ext = rel.lower().rsplit('.', 1)[-1]
            if e: bad[ext] += 1; ex.setdefault('invalid:' + ext, (rel, e))
            r = residue(data, rel, idm)
            if r: res[ext] += 1; ex.setdefault('residue:' + ext, (rel, r[:3]))
    print('files', n, '\ninvalid by ext', dict(bad), '\nresidue by ext', dict(res))
    for k, v in ex.items(): print(k, str(v)[:300])
