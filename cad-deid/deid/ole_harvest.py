"""Harvest account/identity strings from OLE compound files (Inventor, Solid Edge, old SolidWorks): author-like property values in
every property-set stream (SummaryInformation, DocumentSummaryInformation, Inventor Design Tracking / User Defined Properties),
'Saved From/On' provenance lines, DOMAIN\\user, C:\\Users\\<name>, UNC hosts, e-mails. usage: python -m deid.ole_harvest cache out.jsonl"""
import io, json, os, re, sys, time
from concurrent.futures import ProcessPoolExecutor

NAMEY = re.compile(r'author|company|designer|engineer|manager|owner|customer|vendor|supplier|checked|drawn|approved|creator|user|account|machine|computer|host|organi[sz]ation|department|saved|modified', re.I)
DOMUSER = re.compile(rb'\b([A-Z][A-Z0-9-]{2,20})\\([A-Za-z0-9._-]{2,30})\b')
SAVED = re.compile(r'Saved (?:From|On|By)[:\s]+([^\r\n\x00]{3,80})', re.I)
UNC = re.compile(rb'\\\\([A-Za-z0-9._-]{3,30})\\')


def dec(v):
    if isinstance(v, bytes):
        for e in ('utf-8', 'cp1252'):
            try: return v.decode(e).strip('\x00 ')
            except Exception: pass
        return ''
    return str(v).strip('\x00 ') if isinstance(v, str) else ''


def one(path):
    import olefile
    from .discover import _text_ids
    out = []
    try: o = olefile.OleFileIO(path)
    except Exception: return path, None
    try:
        md = o.get_metadata()
        for a in ('author', 'last_saved_by', 'company', 'manager'):
            v = dec(getattr(md, a, None))
            if v: out.append(('person', v))
        for s in o.listdir(streams=True, storages=False):
            nm = s[-1]
            if nm.startswith('\x05'):
                try: props = o.getproperties(s, convert_time=False, no_conversion=[])
                except Exception: props = {}
                names = props.get(0) if isinstance(props.get(0), dict) else {}
                for pid, val in props.items():
                    if pid == 0 or not isinstance(val, (str, bytes)): continue
                    label = dec(names.get(pid, '')) if names else ''
                    if (label and NAMEY.search(label)) or (not label and ('SummaryInformation' in nm) and pid in (4, 8, 15, 14, 18)):
                        v = dec(val)
                        if v: out.append(('person', v))
            if o.get_size(s) < 4_000_000:
                d = o.openstream(s).read(); d16 = d.replace(b'\x00', b'')
                for b in (d, d16):
                    for m in DOMUSER.finditer(b):
                        out.append(('person', m.group(2).decode('latin1'))); out.append(('host', m.group(1).decode('latin1')))
                    for m in UNC.finditer(b): out.append(('host', m.group(1).decode('latin1')))
                    for m in SAVED.finditer(b.decode('latin1', 'ignore')): out.append(('person', m.group(1)))
                    tmp = []; _text_ids(b[:2_000_000], tmp); out += tmp
    except Exception: pass
    return path, list(dict.fromkeys(out))


if __name__ == '__main__':
    root, outp = sys.argv[1:3]
    files = [os.path.join(dp, f) for dp, _, fn in os.walk(root) for f in fn]
    import olefile
    files = [f for f in files if os.path.getsize(f) > 512 and open(f, 'rb').read(8) == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1']
    print(len(files), 'ole files', file=sys.stderr, flush=True); t0 = time.time(); n = 0
    with open(outp, 'w') as fo, ProcessPoolExecutor(4) as ex:
        for p, ids in ex.map(one, files, chunksize=8):
            fo.write(json.dumps({'r': os.path.relpath(p, root), 'i': ids}, ensure_ascii=False) + '\n'); n += 1
    print('OLE HARVEST FINISHED', n, time.time() - t0, file=sys.stderr)
