"""Harvest identities from PDF body text: emails, URLs/domains, phones, street addresses, title-block label values, and
NER (spaCy: ORG/PERSON) over the first pages + last page. usage: python -m deid.pdf_harvest <cache_dir> out.jsonl [--workers 4]"""
import json, os, re, sys, time
from concurrent.futures import ProcessPoolExecutor
from . import titleblock, denylist

NLP = None
EMAIL = re.compile(r'[A-Za-z0-9._%+-]{2,40}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,6}')
URL = re.compile(r'(?:https?://)?((?:www\.)?[A-Za-z0-9-]{2,40}(?:\.[A-Za-z0-9-]{2,40})*\.(?:com|org|net|edu|gov|io|de|fr|it|es|nl|ch|se|uk|jp|cn|ru|ca|au|eu|info|co|tw|kr|at|dk|fi|no|pl|cz|br|in|us|sg|hk|be|pt|gr|il|tr)\b)(?![\w-])', re.I)
PHONE = re.compile(r'(?<![\w.])(\+\d{1,3}[ .-]?\(?\d{1,4}\)?[ .-]?\d{2,4}[ .-]?\d{2,4}(?:[ .-]?\d{2,4})?|\(\d{3}\) ?\d{3}[-. ]\d{4}|\b\d{3}[-.]\d{3}[-.]\d{4})\b')
ADDR = re.compile(r'\b\d{1,5}\s+(?:[A-Z][\w.\'-]+\s+){1,3}(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|Drive|Dr\.?|Lane|Ln\.?|Way|Court|Ct\.?|Place|Pl\.?|Stra(?:ss|ß)e|Str\.?|Platz|Weg|Rue|Via|Calle)\b[^\n]{0,40}')
ORGSUF = re.compile(r'\b((?:[A-Z][\w&.\'-]*\s+){0,4}(?:Inc\.?|LLC|Ltd\.?|Limited|Corp\.?|Corporation|GmbH|AG|S\.?A\.?|B\.?V\.?|Co\.|Company|Technologies|Semiconductors?|Laboratories|Laboratory|University|Institute|Instruments|Systems|Industries))\b')
TITLED = re.compile(r"(?:\b(?:Dr|Prof|Mr|Mrs|Ms|Eng|Ing|Dipl\.?-Ing|Author|Authors|By|Prepared by|Contact|Written by|Designed by|Drawn by|Checked by|Approved by)\.?:?\s+)((?:[A-Z][a-zà-ÿ'’-]+|[A-Z]\.)(?:\s+(?:[A-Z][a-zà-ÿ'’-]+|[A-Z]\.)){1,2})")
COPY = re.compile(r'(?:©|\(c\)|copyright)\s*(?:\d{4}(?:\s*[-–,]\s*\d{4})?)?[\s,:.]*([A-Z][\w&.\'-]*(?:\s+[A-Z][\w&.\'-]*){0,4})', re.I)


def nlp():
    global NLP
    if NLP is None:
        import spacy
        NLP = spacy.load('en_core_web_sm', disable=['parser', 'lemmatizer', 'tagger', 'attribute_ruler'])
        NLP.max_length = 400_000
    return NLP


def text_of(path):
    import fitz
    d = fitz.open(path)
    if d.needs_pass: return '', ''
    pages = list(range(min(8, d.page_count))) + ([d.page_count - 1] if d.page_count > 8 else [])
    t = '\n'.join(d[i].get_text() for i in pages)[:100000]
    t = re.sub('[\u200b\u200c\u200d\u00ad\ufeff]', '', t)   # zero-width characters glue names together and break NER tokens
    meta = ' '.join(str(v) for v in d.metadata.values() if v) + ' ' + (d.get_xml_metadata() or '')[:20000]
    for i in pages[:2]:
        try:
            for a in d[i].annots() or (): t += '\n' + ' '.join(v for v in a.info.values() if isinstance(v, str))
        except Exception: pass
    return t, meta


def one(path):
    out = []
    try: t, meta = text_of(path)
    except Exception: return path, None, []
    full = t + '\n' + meta
    for m in EMAIL.finditer(full): out.append(('email', m.group(0)))
    for m in URL.finditer(full): out.append(('domain', m.group(1)))
    for m in PHONE.finditer(full):
        if 8 <= sum(c.isdigit() for c in m.group(1)) <= 15 and not re.search(r'\d\.\d{3}\)', m.group(1)): out.append(('phone', m.group(1)))
    for m in ADDR.finditer(t): out.append(('address', re.sub(r'\s+', ' ', m.group(0)).strip()))
    for m in ORGSUF.finditer(t): out.append(('org', re.sub(r'\s+', ' ', m.group(1)).strip()))
    for m in COPY.finditer(t): out.append(('org', re.sub(r'\s+', ' ', m.group(1)).strip()))
    out += [tuple(x) for x in titleblock.from_text(t)]
    for m in TITLED.finditer(t): out.append(('person', re.sub(r'\s+', ' ', m.group(1)).strip()))
    try:
        md = fitz_meta(path)
        for v in md: out.append(('person', v))
    except Exception: pass
    try:
        n = nlp()
        for ent in n(t[:30000]).ents:
            if ent.label_ in ('ORG', 'PERSON'): out.append((ent.label_.lower(), re.sub(r'\s+', ' ', ent.text).strip()))
    except Exception: pass
    lw = sorted({w for w in re.findall(r'(?<![\w@/.])[a-zà-ÿ][a-zà-ÿ\'’-]{2,}', t) })[:1500]
    return path, list(dict.fromkeys(out)), lw


def fitz_meta(path):
    import fitz
    d = fitz.open(path); return [d.metadata[k] for k in ('author',) if d.metadata.get(k)]


if __name__ == '__main__':
    root, outp = sys.argv[1:3]; workers = int(sys.argv[sys.argv.index('--workers') + 1]) if '--workers' in sys.argv else 4
    done = set()
    if os.path.exists(outp):
        for l in open(outp):
            try: done.add(json.loads(l)['r'])
            except Exception: pass
    files = [os.path.join(dp, f) for dp, _, fn in os.walk(root) for f in fn if f.lower().endswith('.pdf')]
    todo = [f for f in files if os.path.relpath(f, root) not in done]
    print(len(todo), 'pdfs', file=sys.stderr, flush=True); t0 = time.time(); n = 0
    with open(outp, 'a') as fo, ProcessPoolExecutor(workers) as ex:
        for p, ids, lw in ex.map(one, todo, chunksize=8):
            fo.write(json.dumps({'r': os.path.relpath(p, root), 'i': ids, 'lw': lw}, ensure_ascii=False) + '\n'); n += 1
            if n % 500 == 0: fo.flush(); print(n, round(time.time() - t0), 's', file=sys.stderr, flush=True)
    print('HARVEST FINISHED', n, time.time() - t0, file=sys.stderr)
