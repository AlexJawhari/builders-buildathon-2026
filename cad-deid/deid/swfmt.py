"""SolidWorks 2015+ container: sequence of deflate chunks, each header = MAGIC + marker, crc32(unc), comp, unc, namelen, nibble-swapped name."""
import struct, zlib
M = b'\x14\x00\x06\x00\x08\x00'
def _name(b): return bytes(((x & 15) << 4) | (x >> 4) for x in b).decode('latin1')
def _enc(s): return bytes(((x & 15) << 4) | (x >> 4) for x in s.encode('latin1'))

def chunks(d):
    """yield (header_pos, name, payload_start, comp, unc_bytes) for chunks that inflate cleanly with matching crc."""
    p = d.find(M)
    while 0 <= p and d[p:p + 6] == M:
        try:
            mk, crc, comp, unc, nl = struct.unpack('<IIIII', d[p + 6:p + 26])
            if nl > 300: break
            name = _name(d[p + 26:p + 26 + nl]); ps = p + 26 + nl
            pl = d[ps:ps + comp]
            try:
                o = zlib.decompressobj(-15).decompress(pl)
                if zlib.crc32(o) == crc and len(o) == unc:
                    yield p, name, ps, comp, o
            except zlib.error: pass
            p = ps + comp
            if d[p:p + 6] != M: p = d.find(M, p)
        except Exception:
            p = d.find(M, p + 1)

def _deflate(n, limit):
    """raw deflate of n; prefer a level whose result fits in `limit` bytes (then zero-padded to exactly `limit`, so nothing shifts)."""
    best = None
    for lvl in (9, 6, 5, 4, 3, 1):
        c = zlib.compressobj(lvl, zlib.DEFLATED, -15); z = c.compress(n) + c.flush()
        if best is None or len(z) < len(best): best = z
        if len(z) <= limit: return z + b'\0' * (limit - len(z))
    return best


def rewrite(d, edit, gap=lambda b: b):
    """edit(name, data)->data or None for chunks that parse; gap(bytes) is applied to every byte range outside parsed chunks
    and must keep length. Edited chunks get new crc/sizes. The file also carries a directory of repeated frame headers
    (offset field = chunk position - 12 at frame+36, then the same crc/comp/unc triple at frame+10): those are rewritten
    to match, so a chunk whose compressed size changes does not leave stale directory entries."""
    out = []; last = 0; cur = 0; info = {}
    for p, name, ps, comp, o in chunks(d):
        g = gap(d[last:p]); out.append(g); cur += len(g)
        n = edit(name, o); hdr = bytearray(d[p:ps]); old = struct.unpack_from('<III', hdr, 10)
        if n is None or n == o:
            body = d[ps:ps + comp]; new = old
        else:
            body = _deflate(n, comp) if len(n) == len(o) else _deflate(n, 0)
            new = (zlib.crc32(n), len(body), len(n)); struct.pack_into('<III', hdr, 10, *new)
        info[p] = (old, cur, new)
        out.append(bytes(hdr)); out.append(body); cur += len(hdr) + len(body); last = ps + comp
    out.append(gap(d[last:]))
    res = bytearray(b''.join(out))
    if any(v[0] != v[2] or k != v[1] for k, v in info.items()):
        q = res.find(M)
        while q >= 0:
            if q + 40 <= len(res):
                off, = struct.unpack_from('<I', res, q + 36); op = off + 12
                if op in info and tuple(struct.unpack_from('<III', res, q + 10)) == info[op][0] and q != info[op][1]:
                    _, np_, new = info[op]
                    struct.pack_into('<III', res, q + 10, *new); struct.pack_into('<I', res, q + 36, np_ - 12)
            q = res.find(M, q + 6)
    return bytes(res)
