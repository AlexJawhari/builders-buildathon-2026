"""Attempt 5: second PDF identity harvest. The PDF checks reward end-to-end recall of the identifiers a reader sees in a drawing, and those are
more than names: part/drawing numbers, institutions, phones, addresses, project names, initials. Rule-based extraction from page text
(label-driven part numbers, code chains, the drawing's own file-name stem, part codes found in any file name of the corpus), plus person,
company and contact rules. Output only feeds the PDF-only dictionary (never paths, never native CAD content).
  python -m deid.pdfharv2 harvest cache out.jsonl manifest.tsv     per-PDF candidates (surface, kind)
  python -m deid.pdfharv2 merge out.jsonl df.json base_idents.json manifest.tsv extra_in.json extra_out.json"""
import collections, json, os, re, sys
from concurrent.futures import ProcessPoolExecutor

NAMECH = r"[A-ZÀ-Ý][a-zà-ÿ'’-]{1,20}"
CAPW = r"[A-ZÀ-Ý][A-Za-zÀ-ÿ'’-]+"
LABELS = (r"drawn(?:\s+by)?|dwn|drn|checked(?:\s+by)?|chk'?d?|approved(?:\s+by)?|appr?'?d?|designed(?:\s+by)?|designer|engineer|author|created\s+by|"
          r"made\s+by|prepared\s+by|reviewed\s+by|released\s+by|issued\s+by|gezeichnet|gepr(?:ü|ue)ft|bearbeitet|konstrukteur|dessin(?:é|e)|v(?:é|e)rifi(?:é|e)|"
          r"dibujado|revisado|disegnato|controllato|设计|审核|校对|批准|制图|绘图|設計")
LABEL_NAME = re.compile(rf"(?:^|\s)(?i:{LABELS})\s*[:：.]?[ \t]+({NAMECH}(?:[ \t]+{NAMECH}){{0,2}})")
INIT_SURNAME = re.compile(r"(?<![\w.])([A-Z]\.\s?(?:[A-Z]\.\s?)?[A-ZÀ-Ý][A-Za-zà-ÿ]{2,20})(?![\w])")
INITIALS_LABEL = re.compile(r"(?:DRAWN(?: BY)?|DRN|CHECKED(?: BY)?|CHK'?D|APPROVED(?: BY)?|APPV?'?D|DESIGNED(?: BY)?|ENG|ENGINEER|DWN|BY)\s*[:.]?\s+([A-Z]{2,3})(?![A-Za-z])")
FULLNAME = re.compile(rf"(?<![\w.])({NAMECH})[ \t]+((?:{NAMECH}[ \t]+)?{NAMECH})(?![\w])")
INST = (r"(?:INSTITUTE|Institute|UNIVERSITY|University|Institut|Instituto|Istituto|Fundació|Fundación|Fondazione|Stiftung|LABORATORY|Laboratory|Laboratorio|"
        r"Hochschule|Universität|Université|Universidad|Università|Politecnico|Polytechnic|COLLEGE|College|SCHOOL|School|Academy|ACADEMY)")
INSTITUTION = re.compile(rf"((?:{CAPW}[ \t]+){{0,3}}{INST}(?:[ \t]+(?:of|OF|für|FÜR|de|DE|for|FOR|di|DI|del|des|du|and|AND)(?:[ \t]+{CAPW}){{1,4}}|[ \t]+[A-Z]{{2,6}}\b)?)")
ORG_SUFFIX = (r"GmbH|AG|Inc\.?|Ltd\.?|LLC|L\.L\.C\.|Corp\.?|Corporation|Company|Co\.,?\s?Ltd\.?|S\.A\.S?\.?|SAS|S\.r\.l\.|SRL|B\.V\.|BV|Oy|AB|A/S|Pty\.?\s?Ltd\.?|"
              r"S\.L\.|S\.A\.|KG|Limited|LTD|INC|CORP|Systems|Technologies|Industries|Instruments|Engineering|Robotics|Labs?|Laboratories|Electronics|Products|Services")
ORG_RX = re.compile(rf"\b((?:[A-Z][A-Za-z&'.-]{{1,25}}\s+){{1,5}}(?:{ORG_SUFFIX}))(?![A-Za-z])")
PROPERTY = re.compile(r"(?:PROPERTY\s+OF|EIGENTUM\s+(?:DER|VON)|PROPRI(?:É|E)T(?:É|E)\s+DE|COPYRIGHT\s*(?:\(C\)|©)?\s*(?:\d{4}\s*)?|©\s*(?:\d{4}\s*)?)\s+((?:[A-Z][A-Za-z&.,'-]+\s*){1,6})", re.I)
COMPANY_UP = re.compile(r"(?<![A-Za-z])((?:[A-Z][A-Z&.-]{1,20}\s+){1,3}(?:DRAUGHTING|DRAFTING|ENGINEERING|DESIGN)\s+(?:SERVICES|LTD\.?|LIMITED|CO\.?))(?![A-Za-z])")
LEGAL_LOWER = re.compile(r"((?:[A-Z][\w&'-]+[ \t]+){1,4}(?:spol\.[ \t]+)?(?:s\.r\.o\.|s\.r\.l\.|S\.r\.l\.|sp\.[ \t]?z[ \t]?o\.o\.|Co\.,?[ \t]?Ltd\.?|Oy|A/S|B\.V\.|GmbH(?:[ \t]&[ \t]Co\.[ \t]KG)?))(?![\w])")
DWGNO = re.compile(r"(?i)(?:drawing\s*(?:code|no\.?|number|#)|dwg\.?\s*(?:no\.?|#)|part\s*(?:no\.?|number|#)|p/n|document\s*(?:no\.?|number|code)|doc\.?\s*no\.?|item\s*no\.?|"
                   r"zeichnungs?-?nr\.?|zeichnungsnummer|sachnummer|teilenummer|artikel-?nr\.?|n[°º]\s*plan|codice|图号|图样代号|零件代号)\s*[:：.]?\s*([A-Za-z0-9][A-Za-z0-9_.-]{4,40})")
LIGO = re.compile(r"(?<![A-Za-z0-9])([A-Z]\d{6,7}(?:-(?:v?\d{1,3}|[A-Z]{1,2})){0,2})(?![A-Za-z0-9])")
CODECHAIN = re.compile(r"(?<![A-Za-z0-9_.-])([A-Z][A-Z0-9]{1,9}(?:[-_.][A-Z0-9]{1,10}){2,6})(?![A-Za-z0-9_-])")
FAMILY = re.compile(r"(?<![A-Za-z0-9_.-])([A-Z0-9][A-Z0-9_]{3,15}(?:-[A-Z0-9]{1,8}){0,3})-{1,2}(\d{2,3})(?=[-_ \t\n]|$)")
US_PHONE = re.compile(r"(?<![\d.(])(\(\d{3}\)\s?\d{3}-\d{4}|\d{3}\.\d{3}\.\d{4})(?![\d.])")
DOTTED_PHONE = re.compile(r"(?<![\d.])(\d{2,3}(?:\.\d{2,3}){3,4})(?![\d.])")
INTL00 = re.compile(r"(?<![\d.])(00[1-9]\d{1,2}[-\s]\d{1,4}[-\s]\d{5,8})(?![\d.])")
PHONE = re.compile(r"(?<![\d.,/])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,5}\)[\s.-]?)?\d{2,4}(?:[\s.-]\d{2,4}){2,4}(?![\d.,/])")
STREET = r"(?:STREET|Street|ST\.|ROAD|Road|RD\.?|AVENUE|Avenue|AVE\.?|DRIVE|Drive|DR\.|LANE|Lane|WAY|Way|BOULEVARD|Boulevard|BLVD\.?|PLACE|Place|CRESCENT|Crescent|Strasse|Straße|STRASSE|STRAßE|Platz|Weg|Allee|Rue|Via|Calle)"
ADDRESS = re.compile(rf"(?<![\w.,-])(\d{{1,5}}[A-Za-z]?\s+(?:[A-Z][A-Za-z'-]+\s+){{1,3}}{STREET})")
ADDRESS_DE = re.compile(rf"(?<![\w.,-])([A-ZÄÖÜ][a-zäöüß]{{3,20}}(?:stra(?:ss|ß)e|weg|platz|allee)\s+\d{{1,4}}[a-z]?)")
POSTCODE = re.compile(r"(?<![\d.,-])(\d{4,5}\s+[A-ZÄÖÜ][a-zäöüß]{3,20}(?:-[A-ZÄÖÜ][a-zäöüß]+)?)(?![\w])")
PROJECT = re.compile(r"(?i)\b(?:project(?:\s*name)?|projekt|projet|proyecto|progetto)\s*[:：]\s*([A-Z0-9][A-Za-z0-9+&.' -]{2,40}?)(?=\s{2,}|\n|$|\s+(?:date|drawn|scale|sheet|rev))")
CAMEL = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]*[a-z][A-Z][A-Za-z0-9+]*)(?![A-Za-z0-9])")
STEMCODE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]{0,9}(?:[-_.][A-Za-z0-9]{1,10}){0,6}?[-_]?\d[A-Za-z0-9]*(?:[-_][A-Za-z0-9]{1,10}){0,5})(?![A-Za-z0-9])")
STD = re.compile(r"^(?:iso|din|ansi|astm|asme|jis|gb|en|bs|ieee|iec|nema|ul|csa|ce|rohs|ip|pn|nf|sae|ra|rz|m|dn|x|v|rev|sheet|page|fig|figure|sch|rf|rj|cat)[-_ ]?\d", re.I)
NOT_CODE = re.compile(r"\d+x\d+|^\d{4}-\d{2}-\d{2}$|^v?\d+(?:\.\d+)+$|^(?:19|20)\d{2}$|^[\d.,/-]+$|\.(?:step|stp|stl|pdf|dxf|png|jpg)$", re.I)
SOFTWARE = set("""solidworks freecad kicad autocad openscad onshape github gitlab youtube labview powerpoint iphone ipad macos javascript linkedin facebook wordpress
sketchup inventor coreldraw photoshop illustrator acrobat microsoft openoffice libreoffice creo catia siemens fusion autodesk solidedge altium eagle matlab simulink ansys
comsol abaqus paraview blender meshlab cura mosfet igbt ethercat canopen modbus profinet devicenet bluetooth zigbee lorawan wifi pcie mipi hdmi displayport thunderbolt""".split())


def _words():
    from .discover import english
    return english()


def harvest_text(t, common, first):
    out = []
    for m in LABEL_NAME.finditer(t): out.append((m.group(1), 'person'))
    for m in INIT_SURNAME.finditer(t):
        sur = re.split(r'\.\s?', m.group(1))[-1].lower()
        if len(sur) >= 3 and sur not in common: out.append((m.group(1), 'person'))
    for m in INITIALS_LABEL.finditer(t):
        if m.group(1).lower() not in common and m.group(1) not in ('THE', 'AND', 'FOR', 'MFG', 'QTY', 'REV', 'DWG', 'TBD', 'ALL', 'SEE', 'PER', 'MAT', 'DIA', 'TYP', 'REF', 'DATE', 'NAME', 'ISO', 'DIN', 'MAX', 'MIN', 'NOM', 'BOM', 'CAD', 'PDF'):
            out.append((m.group(1), 'initials'))
    for m in FULLNAME.finditer(t):
        a = m.group(1); rest = m.group(2).split()
        if a.lower() in first and a.lower() not in common and all(w.lower() not in common or len(w) < 4 for w in rest[-1:]): out.append((a + ' ' + ' '.join(rest), 'person'))
    for rx in (INSTITUTION, ORG_RX, COMPANY_UP, LEGAL_LOWER):
        for m in rx.finditer(t):
            v = re.sub(r'\s+', ' ', m.group(1)).strip(' .,;:-')
            if len(v.split()) >= 2: out.append((v, 'org'))
    for m in PROPERTY.finditer(t):
        v = re.sub(r'\s+', ' ', re.split(r'\.\s|,\s(?:IT|AND)\b', m.group(1))[0]).strip(' .,;:-')
        if len(v) >= 4: out.append((v, 'org'))
    for m in DWGNO.finditer(t):
        v = m.group(1).strip('-_.')
        if re.search(r'\d', v) and (re.search(r'[A-Za-z]', v) or len(v) >= 7): out.append((v, 'code'))
    for m in LIGO.finditer(t): out.append((m.group(1), 'code'))
    for m in CODECHAIN.finditer(t):
        v = m.group(1)
        if len(v) >= 7 and re.search(r'[A-Z]{2}', v) and not re.fullmatch(r'[\d.\-_]+', v): out.append((v, 'code'))
    nums = {}
    for m in FAMILY.finditer(t): nums.setdefault(m.group(1), set()).add(m.group(2))
    for pre, ns in nums.items():
        if len(ns) >= 3 and re.search(r'[A-Z]{2}', pre): out += [(f'{pre}-{n}', 'code') for n in ns] + [(pre, 'code')]
    for rx in (US_PHONE, DOTTED_PHONE, INTL00):
        for m in rx.finditer(t):
            if len(re.sub(r'\D', '', m.group(1))) >= 8: out.append((m.group(1), 'phone'))
    for m in PHONE.finditer(t):
        ph = m.group(0).strip(); d = re.sub(r'\D', '', ph)
        if 8 <= len(d) <= 15 and not re.fullmatch(r'(?:19|20)\d{6}', d) and (ph.startswith('+') or re.search(r'(?i)(?:tel|phone|fax|mobile|mob|cell)\.?\s*[:.]?\s*$', t[max(0, m.start() - 16):m.start()])):
            out.append((ph, 'phone'))
    for rx in (ADDRESS, ADDRESS_DE):
        for m in rx.finditer(t):
            v = m.group(1)
            ws = re.findall(r"[A-Za-zäöüß'-]+", v)[:-1] or re.findall(r"[A-Za-zäöüß'-]+", v)
            if not re.match(r'\d+x\b', v) and any(len(w) >= 4 and w.lower() not in common for w in ws): out.append((re.sub(r'\s+', ' ', v), 'address'))
    for m in POSTCODE.finditer(t):
        city = m.group(1).split()[-1].lower()
        if city not in common and city.rstrip('s') not in common: out.append((re.sub(r'\s+', ' ', m.group(1)), 'address'))
    for m in PROJECT.finditer(t):
        v = m.group(1).strip(' .-')
        if len(v) >= 3 and v.lower() not in common and not v.lower().startswith(('name', 'no', 'number')): out.append((v, 'project'))
    for m in CAMEL.finditer(t):
        w = m.group(1); lw = w.lower()
        if 4 <= len(w) <= 24 and len(re.findall(r'[A-Z]', w)) >= 2 and lw not in SOFTWARE and lw not in common:
            parts = [p.lower() for p in re.findall(r'[A-Z]+(?![a-z])|[A-Z]?[a-z]+', w)]
            if not all(p in common or len(p) <= 1 for p in parts): out.append((w, 'project'))
    return out


def stem_codes(stem, low):
    out = []
    cands = {stem}
    for m in STEMCODE.finditer(stem): cands.add(m.group(1).strip('-_.'))
    for v in cands:
        if len(v) >= 5 and re.search(r'\d', v) and v.lower() in low: out.append((v, 'code'))
    return out


def one(args):
    import fitz
    path, rel = args
    from .discover import english
    common = english(); first = _first()
    out = []
    try:
        d = fitz.open(path)
        if d.needs_pass: return rel, []
        stem = re.sub(r'\.[A-Za-z0-9]{1,5}$', '', rel.rsplit('/', 1)[-1])
        for i, pg in enumerate(d):
            if i >= 40: break
            t = re.sub('[​‌‍­﻿]', '', pg.get_text())[:200000]
            if i >= 6:           # long documents: contact details and codes only
                out += [(v, k) for v, k in harvest_text(t, common, first) if k in ('phone', 'address', 'code')]
                continue
            out += harvest_text(t, common, first)
            out += stem_codes(stem, re.sub(r'\s+', ' ', t).lower())
    except Exception: pass
    return rel, list(dict.fromkeys(out))


_FIRST = None


def _first():
    global _FIRST
    if _FIRST is None:
        from .merge3 import FIRST
        _FIRST = FIRST
    return _FIRST


def harvest(cache, out):
    files = [(os.path.join(dp, f), os.path.relpath(os.path.join(dp, f), cache)) for dp, _, fn in os.walk(cache) for f in fn if f.lower().endswith('.pdf')]
    with open(out, 'w') as fo, ProcessPoolExecutor(10) as ex:
        for rel, o in ex.map(one, files, chunksize=8): fo.write(json.dumps({'r': rel, 'i': o}, ensure_ascii=False) + '\n')


def manifest_codes(manifest):
    """part codes in file and folder names below the repository level (counted over files)"""
    from .harvest_names import path_codes
    return path_codes(manifest)


def merge(jsonl, dfp, basep, manifest, extra_in, out):
    df = json.load(open(dfp)); base = {x.lower() for x in json.load(open(basep))}; common = _words(); cnt = collections.Counter(); first = _first()
    for l in open(jsonl):
        d = json.loads(l)
        for v, k in set(map(tuple, d['i'])): cnt[(v, k)] += 1
    from .harvest_names import path_codes
    codes = path_codes(manifest)
    def commonw(t):
        t = t.lower().strip('.,'); return t in common or df.get(t, 0) >= 80
    res = {}
    def codeok(v):
        if not v.isascii() or len(v) < 5 or len(v) > 40 or STD.match(v) or NOT_CODE.search(v) or v.lower() in common or v.lower() in SOFTWARE: return False
        segs = [re.sub(r'\d', '', x) for x in re.split(r'[-_.\s]+', v)]
        if any(len(x) >= 4 and (x.lower() in common or x.lower().rstrip('s') in common) for x in segs): return False   # 'hex_head_bolt_m4', 'Part1064'
        if re.fullmatch(r'[0-9a-f]{6,}', v.lower()) and re.search(r'[a-f]', v.lower()) and not v.isupper(): return False   # hash-like export ids
        if v.islower() and ('_' in v or len(segs) >= 3) and not re.search(r'[A-Z]', v) and sum(len(x) for x in segs) >= 8: return False   # descriptive snake_case file names
        nd = len(re.findall(r'\d', v))
        return (v.isdigit() and 7 <= len(v) <= 12) or (nd >= 2 and re.search(r'[A-Za-z]', v) is not None and (nd >= 3 or len(v) >= 6))
    for (v, k), n in cnt.items():
        if v.lower() in base: continue
        toks = re.findall(r"[^\W\d_][\w'’.-]*", v)
        if k == 'code':
            if codeok(v) and df.get(v.lower(), 0) < 400 and n < 400: res[v] = k
        elif k == 'phone': res[v] = k
        elif k == 'address':
            if len(v) >= 8: res[v] = k
        elif k == 'initials':
            if n <= 40: res[v] = k
        elif k == 'project':
            if len(v) >= 4 and n <= 300 and not commonw(v) and v.lower() not in SOFTWARE: res[v] = k
        elif k == 'person':
            if 2 <= len(toks) <= 3 and all(re.match(r"^(?:[A-ZÀ-Ý][\w'’-]*\.?|[A-Z]\.)$", t) for t in toks) and any(not commonw(t) for t in toks[-1:]) and n <= 200:
                res[v] = k
        elif k == 'org':
            if 2 <= len(toks) <= 6 and any(not commonw(t) for t in toks) and n <= 400 and all(re.match(r"^[A-ZÀ-Ý&]", t) for t in toks if len(t) > 2): res[v] = k
    for c, n in codes.items():
        if n <= 150 and codeok(c) and c.lower() not in base: res.setdefault(c, 'code')
    old = set(json.load(open(extra_in))) if extra_in and os.path.exists(extra_in) else set()
    allv = sorted(old | set(res))
    json.dump(allv, open(out, 'w'), ensure_ascii=False)
    print(len(old), '+', len(set(res) - old), '=', len(allv), dict(collections.Counter(res.values())))


if __name__ == '__main__':
    if sys.argv[1] == 'harvest': harvest(*sys.argv[2:4])
    elif sys.argv[1] == 'merge': merge(*sys.argv[2:8])
