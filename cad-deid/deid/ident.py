"""Identity dictionary + deterministic same-length pseudonyms + multi-encoding byte replacement.

Pseudonym for an identity = HMAC(secret, normalized identity) -> letters/digits, one per original
letter/digit, case + separators copied from the matched surface text. Because it is a pure function
of the identity, every file/path/archive member gets the same pseudonym with no shared state,
and replacement never changes byte length (safe for binary formats).
"""
import hashlib, hmac, os, re
import ahocorasick

SEPS = '-_ .'
DOMAIN = re.compile(r'^(?:www\.)?[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,6}$')
CONS = 'bcdfghjklmnprstvwz'
VOW = 'aeiou'
_alnum = re.compile(rb'[A-Za-z0-9]')


def norm(s):
    return re.sub(r'[-_ .]+', ' ', s.strip().lower())


class IdMap:
    def __init__(self, secret: bytes):
        self.secret = secret
        self.mode = os.environ.get('MACHINA_PSEUDO', 'v2')   # v1 = attempt-1 function, v2 = component-wise
        self.ids = {}          # norm key -> original text
        self._auto = {}
        self.errors = []

    def add(self, text, min_len=3):
        k = norm(text)
        if len(k.replace(' ', '')) >= min_len and k not in self.ids:
            self.ids[k] = text.strip()
            self._auto = {}

    def _tok(self, tok: str) -> str:
        """pseudonym of one alphanumeric token: depends only on the token (lower-cased), same length, case copied per character"""
        k = tok.lower(); h = b''; i = 0
        core = tok.rstrip('.')
        if core.isdigit() and core.isascii():       # digit-only words (part-number segments): keyed bijection on 10^n, no fixed point
            hh = hmac.new(self.secret, b'digits:%d' % len(core), hashlib.sha256).digest(); M = 10 ** len(core)
            K = 1 + 10 * (int.from_bytes(hh[:4], 'big') % max(1, M // 10)) if len(core) > 1 else 1
            C = int.from_bytes(hh[4:8], 'big') % M
            if C % 10 == 0: C += 1
            return str((int(core) * K + C) % M).zfill(len(core)) + tok[len(core):]
        if 1 <= len(core) <= 3 and core.isalpha() and core.isascii():
            return self._short(core) + tok[len(core):]
        while len(h) < len(tok) + 2:
            h += hmac.new(self.secret, b'tok:' + k.encode() + bytes([i]), hashlib.sha256).digest(); i += 1
        out = []; n = 0
        for ch in tok:
            if ch.isalpha():
                b = h[n]; c = VOW[b % 5] if n % 2 == 1 else CONS[b % len(CONS)]; n += 1
                out.append(c.upper() if ch.isupper() else c)
            elif ch.isdigit():
                out.append(str(h[n] % 10)); n += 1
            else:
                out.append(ch)
        res = ''.join(out)
        if res.lower() == tok.lower() and tok.isalpha() and len(tok) > 1:  # never echo the original
            res = res[:-1] + ('x' if res[-1].lower() != 'x' else 'q')
        return res

    def _short(self, tok: str) -> str:
        """1-3 letter words (initials, 'Li', 'Kim'): keyed affine bijection on 26^n, odd offset => no fixed point, so two different short words
        never share a pseudonym (the consonant/vowel scheme collides for them)."""
        n = len(tok); M = 26 ** n; h = hmac.new(self.secret, b'short:%d' % n, hashlib.sha256).digest()
        K = int.from_bytes(h[:4], 'big') % M | 1
        while K % 13 == 0 or K == 1: K = (K + 2) % M
        C = int.from_bytes(h[4:8], 'big') % M | 1
        v = 0
        for ch in tok.lower(): v = v * 26 + ord(ch) - 97
        v = (v * K + C) % M; out = []
        for _ in range(n): out.append(chr(97 + v % 26)); v //= 26
        return ''.join(c.upper() if o.isupper() else c for c, o in zip(reversed(out), tok))

    def pseudo(self, surface: str) -> str:
        if self.mode == 'v1': return self.pseudo_v1(surface)
        return self.pseudo_v2(surface)

    def pseudo_v1(self, surface: str, key: str = None) -> str:
        """attempt-1 pseudonym: one HMAC stream per whole identity (kept so a fix-only attempt can reuse every attempt-1 output).
        key: canonical identity (default: the normalised surface) so different spellings of one identity share letters."""
        k = key if key is not None else norm(surface); h = b''; i = 0
        while len(h) < len(surface) + 8:
            h += hmac.new(self.secret, k.encode() + bytes([i]), hashlib.sha256).digest(); i += 1
        out = []; n = 0
        for ch in surface:
            if ch.isalpha():
                b = h[n]; n += 1
                c = (VOW[b % 5] if n % 2 == 0 else CONS[b % len(CONS)])
                out.append(c.upper() if ch.isupper() else c)
            elif ch.isdigit():
                out.append(str(h[n] % 10)); n += 1
            else:
                out.append(ch)
        res = ''.join(out)
        if res.lower() == surface.lower() and any(c.isalpha() for c in surface):
            res = res[:-1] + ('x' if res[-1] != 'x' else 'q') if res[-1].isalpha() else res
        return res

    def pseudo_v2(self, surface: str) -> str:
        """Component-wise: every word of an identity is mapped on its own, so 'John Smith', 'SMITH, JOHN', 'jsmith@acme.com' and
        'www.acme.com' share the pseudonyms of 'john', 'smith' and 'acme'. E-mail/domain structure ('@', 'www', the TLD) is kept."""
        parts = re.split(r'([^\w]+)', surface)
        keep = set()
        if '@' in surface or DOMAIN.match(surface):
            labels = [i for i, x in enumerate(parts) if x and re.match(r'\w', x)]
            for i in labels:
                if parts[i].lower() == 'www': keep.add(i)
            if labels:
                keep.add(labels[-1])   # TLD
                if len(labels) > 2 and parts[labels[-2]].lower() in ('co', 'com', 'ac', 'org', 'edu', 'gov', 'net'): keep.add(labels[-2])
        return ''.join(x if (not x or not re.match(r'\w', x) or i in keep) else self._tok(x) for i, x in enumerate(parts))

    @staticmethod
    def _content_ok(k):
        c = k.replace(' ', '')
        return len(c) >= 5 if c.isascii() else len(c) >= 3

    def _build(self, names):
        """names=True: every identity (paths/file names, safe: a name is short and exact). False: only identities long enough
        that a chance match inside binary/compressed bytes is negligible (>=5 ASCII chars / >=3 non-ASCII)."""
        a = ahocorasick.Automaton()
        for k in self.ids:
            if not names and not self._content_ok(k): continue
            variants = {k.replace(' ', s) for s in ('-', '_', ' ', '', '.')} if ' ' in k else {k}
            if '__' not in self.ids[k]: variants.add(self.ids[k].lower())   # exact original spelling (mixed separators such as 'A-B c d'); joint owner__repo ids stay per-component
            encs = ['utf-8', 'utf-16-le'] + (['utf-16-be'] if os.environ.get('MACHINA_BE') else [])   # BE off by default: misaligned LE strings give false matches in SolidWorks chunks
            if not k.isascii():
                variants |= {v.capitalize() for v in variants} | {v.upper() for v in variants}
                encs += ['gbk', 'cp1251', 'cp1252']
            variants |= {v.replace("'", "''") for v in variants if "'" in v}   # STEP doubles quotes inside strings
            for v in variants:
                for enc in encs:
                    try: b = v.encode(enc)
                    except Exception: continue
                    if enc != 'utf-8' and not enc.startswith('utf') and b.isascii(): continue
                    a.add_word(b.lower().decode('latin1'), (len(b), enc, k))
        if len(a):
            a.make_automaton()
        self._auto[names] = a

    def surfaces(self, text: str):
        """Distinct matched identity surfaces in text (for PDF redaction)."""
        return sorted({data[s:e].decode(enc, 'ignore') for s, e, enc, data in self._hits(text.encode('utf-8'))})

    def _hits(self, data: bytes, names=False):
        """Non-overlapping identity matches as (start, end, encoding, data)."""
        if not self.ids:
            return []
        if names not in self._auto:
            self._build(names)
        if not len(self._auto[names]): return []
        low = data.lower()
        hits = []
        for end, (ln, enc, k) in self._auto[names].iter(low.decode('latin1')):
            s = end - ln + 1; end += 1
            if enc == 'utf-16-le':
                if s >= 2 and data[s - 1:s] == b'\x00' and _alnum.match(data[s - 2:s - 1]): continue
                if data[end + 1:end + 2] == b'\x00' and _alnum.match(data[end:end + 1]): continue
            elif enc == 'utf-16-be':
                if s >= 2 and data[s - 2:s - 1] == b'\x00' and _alnum.match(data[s - 1:s]): continue
                if data[end:end + 1] == b'\x00' and _alnum.match(data[end + 1:end + 2]): continue
            else:
                if s > 0 and _alnum.match(data[s - 1:s]): continue
                if _alnum.match(data[end:end + 1]): continue
            hits.append((s, end, enc))
        hits.sort(key=lambda t: (t[0], -(t[1] - t[0])))
        res = []; last = 0
        for s, e, enc in hits:
            if s >= last:
                res.append((s, e, enc, data)); last = e
        return res

    def _surf_bytes(self, data: bytes, names=False):
        return sorted({d[s:e].decode(enc, 'ignore') for s, e, enc, d in self._hits(data, names)})

    def replace(self, data: bytes, pad=True, names=False) -> bytes:
        """Same-length (pad=True) replacement of every known identity, UTF-8/ASCII and UTF-16LE, case-insensitive."""
        hits = self._hits(data, names)
        if not hits:
            return data
        out = []; last = 0
        for s, e, enc, _ in hits:
            rep = self.pseudo(data[s:e].decode(enc, 'ignore')).encode(enc)
            if pad and len(rep) < e - s:
                rep += {'utf-16-le': b' \x00', 'utf-16-be': b'\x00 '}.get(enc, b' ') * ((e - s - len(rep)) // (2 if enc.startswith('utf-16') else 1))
            out.append(data[last:s]); out.append(rep); last = e
        out.append(data[last:])
        return b''.join(out)

    def replace_str(self, s: str, names=False) -> str:
        """Text replacement; length may change for non-ASCII originals (no padding). Identities shorter than 5 characters are
        not applied here (names=False) so ordinary short words in file/member names are never renamed."""
        return self.replace(s.encode('utf-8'), pad=False, names=names).decode('utf-8', 'replace')

    def replace_path(self, rel: str) -> str:
        """Corpus path: the owner/repo folder (sources/<site>/<folder>, or the archive file name) may use every identity,
        including short owner names; the rest of the path only identities of 5+ characters."""
        parts = rel.split('/')
        k = 2 if parts[0] == 'sources' and len(parts) > 3 else (1 if parts[0] == 'archives' and len(parts) > 1 else -1)
        return '/'.join((self.replace_str(x, names=(i == k)) for i, x in enumerate(parts)))


def load_secret(path):
    if not os.path.exists(path):
        with open(path, 'wb') as f: f.write(os.urandom(32))
        os.chmod(path, 0o600)
    return open(path, 'rb').read()
