"""HTTP fetch from the input mirror with Range support and per-thread keep-alive connections."""
import http.client, threading, time

HOST = 'machina-input.joinbuilders.org'
_tl = threading.local()


def _conn():
    c = getattr(_tl, 'c', None)
    if c is None:
        c = _tl.c = http.client.HTTPSConnection(HOST, timeout=60)
    return c


def fetch(urlpath, rng=None, dest=None, retries=6):
    """GET /<urlpath> (already percent-encoded, as in manifest). rng=(a,b) inclusive. Returns bytes, or writes to dest and returns size."""
    hdr = {'Range': f'bytes={rng[0]}-{rng[1]}'} if rng else {}
    for i in range(retries):
        try:
            c = _conn(); c.request('GET', '/' + urlpath, headers=hdr); r = c.getresponse()
            if r.status in (200, 206):
                if dest is None:
                    return r.read()
                n = 0
                with open(dest, 'wb') as f:
                    while True:
                        b = r.read(1 << 20)
                        if not b: break
                        f.write(b); n += len(b)
                return n
            r.read()
            if r.status == 416: return b''
            if r.status == 404: raise FileNotFoundError(urlpath)
        except FileNotFoundError: raise
        except Exception:
            _tl.c = None
        time.sleep(min(2 ** i, 20))
    raise IOError('fetch failed ' + urlpath)
