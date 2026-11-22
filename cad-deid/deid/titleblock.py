"""Title-block harvesting from drawing text (PDF page text, DXF TEXT/MTEXT): value that follows a label such as
DRAWN BY / CHECKED / APPROVED / COMPANY / CUSTOMER / DESIGNER, plus emails, www domains, copyright holders, labelled phones."""
import re

LABELS = r'(?:drawn(?:\s+by)?|drawn|checked(?:\s+by)?|approved(?:\s+by)?|designed(?:\s+by)?|designer|design(?:ed)?\s+by|created\s+by|author|engineer|draftsman|drafted\s+by|' \
         r'zeichner|gezeichnet|gepr(?:ü|u)ft|dessin(?:é|e)\s+par|dibujado|revisado|approved|company|customer|client|owner|manufacturer|supplier|vendor|organisation|organization|firm|' \
         r'copyright|\(c\)|©|institute|university|department|dept\.?|lab(?:oratory)?|address|tel(?:ephone)?\.?|phone|fax|e-?mail|web(?:site)?|www)'
LABEL_RX = re.compile(r'^\s*' + LABELS + r'\s*[:.\-]?\s*(.*)$', re.I)
EMAIL = re.compile(r'[A-Za-z0-9._%+-]{2,40}@[A-Za-z0-9.-]{3,60}\.[A-Za-z]{2,}')
WWW = re.compile(r'\b(?:https?://)?(www\.[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)')
PHONE = re.compile(r'(?:tel|phone|fax|mobile|ph)\.?\s*[:.]?\s*(\+?\d[\d ()./-]{6,18}\d)', re.I)
COPY = re.compile(r'(?:copyright|\(c\)|©)\s*(?:\d{4}(?:\s*[-–]\s*\d{4})?)?[ ,:.]*([A-Z][\w&.,\' -]{2,50})', re.I)
STOP = re.compile(r'^(?:name|date|sign(?:ature)?|scale|size|sheet|rev(?:ision)?|title|drawing|material|none|n/?a|tbd|-+|\d+|yes|no|by|date:?|unit|units|mm|inch|dwg|part|assy|' \
                  r'standard|general|default|unknown|all rights reserved|the|and|of|for|this|drawing|document|file|page|released|version)$', re.I)


def _clean(v):
    v = re.sub(r'\s+', ' ', v).strip(' :.,;-_|')
    if not (3 <= len(v) <= 60): return None
    if STOP.match(v) or not re.search(r'[A-Za-zÀ-ÿЀ-鿿]{3}', v): return None
    if sum(c.isdigit() for c in v) > len(v) // 2: return None
    if LABEL_RX.match(v) and not LABEL_RX.match(v).group(1): return None
    if v[0].islower() or "'" in v[:6] or len(v.split()) > 6: return None
    if v.isupper() and (len(v) < 5 or len(v.split()) > 3): return None
    return v


def from_lines(lines):
    """lines: list of text lines (title block lines stay adjacent). returns [(kind, string)]"""
    out = []; n = len(lines)
    for i, ln in enumerate(lines):
        m = LABEL_RX.match(ln)
        if m:
            lab = ln[:ln.lower().find(m.group(1).lower())] if m.group(1) else ln
            val = m.group(1) or (lines[i + 1] if i + 1 < n else '')
            lab_l = lab.lower()
            if re.search(r'tel|phone|fax', lab_l): continue
            if re.search(r'e-?mail|web|www', lab_l): continue
            v = _clean(val)
            if v and not LABEL_RX.match(v): out.append(('titleblock', v))
            if re.search(r'address', lab_l):
                for k in (1, 2):
                    if i + k < n and _clean(lines[i + k]) and re.search(r'\d', lines[i + k]): out.append(('address', _clean(lines[i + k])))
        for m in COPY.finditer(ln):
            v = _clean(m.group(1))
            if v: out.append(('titleblock', re.sub(r'\.?\s*all rights.*$', '', v, flags=re.I).strip()))
        for m in EMAIL.finditer(ln): out.append(('email', m.group(0)))
        for m in WWW.finditer(ln): out.append(('domain', m.group(1)))
        for m in PHONE.finditer(ln): out.append(('phone', m.group(1)))
    return out


def from_text(text):
    return from_lines([l for l in text.splitlines() if l.strip()])


def dxf_text_lines(data: bytes):
    """TEXT/MTEXT strings of an ASCII DXF (group codes 1 and 3), MTEXT format codes stripped."""
    lines = data.decode('latin1', 'replace').replace('\r', '').split('\n')
    out = []
    for i in range(0, len(lines) - 1, 2):
        if lines[i].strip() in ('1', '3') and lines[i + 1].strip():
            t = re.sub(r'\\[A-Za-z][^;]*;|[{}]|\\P', ' ', lines[i + 1]); out.append(t.strip())
    return out
