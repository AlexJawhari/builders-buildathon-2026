"""Check a staged attempt in the bucket against state.db: every row present, size/etag(md5) matching.
usage: MACHINA_WORK=<work dir> python verify_attempt.py <prefix ending in /corpus/>"""
import os, sqlite3, sys, urllib.parse, http.client, xml.etree.ElementTree as ET
sys.path.insert(0, '.')
from deid import upload_store as u
from deid.pipeline import W
prefix = sys.argv[1]; tok = u.Tok(os.path.join(W, '..', 'work', 'token.txt'))
have = {}; cont = None
while True:
    c = http.client.HTTPSConnection(u.HOST, timeout=120)
    q = f'list-type=2&max-keys=1000&prefix={urllib.parse.quote(prefix)}&encoding-type=url' + (f'&continuation-token={urllib.parse.quote(cont)}' if cont else '')
    c.request('GET', f'/{u.BUCKET}/?{q}', headers={'Authorization': 'Bearer ' + tok.get()}); r = c.getresponse(); b = r.read()
    if u.expired(r.status, b): tok.renew(tok.get()); continue
    root = ET.fromstring(b); ns = root.tag.split('}')[0] + '}' if root.tag.startswith('{') else ''
    for it in root.findall(ns + 'Contents'):
        have[urllib.parse.unquote_plus(it.findtext(ns + 'Key'))[len(prefix):]] = (int(it.findtext(ns + 'Size')), (it.findtext(ns + 'ETag') or '').strip('"').lower())
    if root.findtext(ns + 'IsTruncated') != 'true': break
    cont = root.findtext(ns + 'NextContinuationToken')
db = sqlite3.connect(os.path.join(W, 'state.db')); rows = db.execute("select out,md5 from files where status='ok'").fetchall()
missing = [o for o, m in rows if o not in have]; bad = [o for o, m in rows if o in have and have[o][1] != m and not have[o][1].endswith('-')]
print('objects', len(have), 'expected', len(rows), 'missing', len(missing), 'etag mismatch', len(bad), 'extra', len(set(have) - {o for o, _ in rows}))
print(missing[:5], bad[:5])
