"""Attempt-2 dictionary = attempt-1 dictionary (unchanged, so every attempt-1 pseudonym stays valid) + identities harvested from
PDF body text / OLE property sets. usage: python -m deid.merge2 idents.json out.json harvest1.jsonl [...]"""
import collections, json, re, sys
from . import discover, denylist


def accept(s, kind):
    s = re.sub(r'\s+', ' ', s.replace('\x00', ' ')).strip(' .,;:-_|()[]')
    if kind in ('person', 'org', 'titleblock'):
        t = s.split(); t = [x for i, x in enumerate(t) if not re.search(r'\d', x) and (i == 0 or x.lower() != t[i - 1].lower())]
        s = ' '.join(t)
    if len(s) < 4 or len(s) > 70: return None
    c = re.sub(r'[^A-Za-z]', '', s).lower()
    if len(set(c)) <= 3 and len(c) >= 4 or s.count('(') != s.count(')') or (kind not in ('email', 'domain', 'phone') and (len(c) < 0.6 * len(s) or (len(c) >= 4 and not re.search('[aeiouy]', c)))): return None
    if kind in ('email', 'domain', 'phone'):
        if kind == 'email' and not discover.ok_email(s): return None
        if kind == 'domain' and s.lower() in discover.PUBLIC_DOMAINS: return None
        if denylist.reason(s, kind): return None
        return s
    if denylist.reason(s, kind) or not discover.ok_ident(s): return None
    toks = s.split()
    if len(toks) > 5 or not re.search(r'[A-Za-zÀ-￿]{3}', s): return None
    if kind == 'person' and (len(toks) > 3 or not all(t[:1].isupper() or not t[:1].isalpha() for t in toks)): return None
    if sum(c.isupper() for c in s) > 3 and s.isupper() and len(toks) == 1 and len(s) < 6: return None
    return s


SUFFIX = set('inc llc ltd limited corp corporation gmbh ag sa bv co company technologies semiconductor semiconductors laboratories laboratory university institute instruments systems industries'.split())
CAP = re.compile(r"^(?:[A-Z\u00c0-\u00de][\w'’.-]*|[A-Z]\.)$")


def strong(s, kind, L):
    """Evidence rule for body-text names: every word must be rare in lower case in the corpus (labels and ordinary words show up in
    lower case somewhere, names do not)."""
    toks = re.findall(r"[^\W\d_][\w'’.-]*", s)
    if not toks: return False
    low = lambda t: L.get(t.lower().strip('.'), 0)
    core = [t for t in toks if t.lower().strip('.,') not in SUFFIX]
    if not core or any(low(t) >= 3 for t in core): return False
    if kind == 'person':
        return 2 <= len(toks) <= 3 and all(CAP.match(t) for t in toks) and sum(len(t) >= 3 for t in toks) >= 2
    if kind == 'org':
        has_suf = len(core) < len(toks)
        if has_suf: return all(CAP.match(t) or t.lower() in SUFFIX for t in toks)
        return len(toks) >= 2 and all(CAP.match(t) for t in toks) and any(len(t) >= 4 for t in toks)
    return len(core) >= 1 and max(len(t) for t in core) >= 4        # titleblock label values, addresses


def main(base, out, *files):
    ids = set(json.load(open(base))); cnt = collections.Counter(); kinds = {}; L = collections.Counter()
    for fn in files:
        for l in open(fn):
            try: d = json.loads(l)
            except Exception: continue
            L.update(d.get('lw') or ())
    for fn in files:
        for l in open(fn):
            try: d = json.loads(l)
            except Exception: continue
            for kind, s in d.get('i') or ():
                a = accept(s, kind)
                if a and kind in ('org', 'person', 'titleblock', 'address', 'host') and not strong(a, kind, L): a = None
                if a: cnt[a] += 1; kinds.setdefault(a, kind)
    new = {a for a in cnt if a not in ids}
    # drop strings that are a bare substring of one of the base path identities' words (keeps path folders consistent) - none needed
    json.dump(sorted(ids | new), open(out, 'w'), ensure_ascii=False)
    st = collections.Counter(kinds[a] for a in new)
    print(len(ids), '+', len(new), dict(st), file=sys.stderr)
    with open(out.rsplit('/', 1)[0] + '/dict_added2.txt', 'w') as f:
        for a in sorted(new, key=lambda x: -cnt[x]): f.write(f'{cnt[a]}\t{kinds[a]}\t{a}\n')


if __name__ == '__main__':
    main(*sys.argv[1:])
