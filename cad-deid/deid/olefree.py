"""Zero the unallocated sectors of an OLE compound file (FAT FREESECT sectors and free mini-sectors of the mini stream). CAD applications leave
stale copies of earlier stream versions there (old SummaryInformation with author / last saved by, old Header2); they belong to no stream, so
zeroing them changes no stream content, offset or file length. Self-check: every stream reads back identical."""
import io

FREE, ENDCH = 0xFFFFFFFF, 0xFFFFFFFE


def scrub(data):
    import olefile
    try: ole = olefile.OleFileIO(io.BytesIO(data))
    except Exception: return data
    buf = None
    try:
        ss = ole.sectorsize; nsec = (len(data) - 512 + ss - 1) // ss
        for i, nxt in enumerate(ole.fat[:nsec]):
            if nxt == FREE:
                off = 512 + i * ss
                if data[off:off + ss].strip(b'\0'):
                    buf = buf or bytearray(data); buf[off:off + ss] = b'\0' * len(data[off:off + ss])
        try: ole.loadminifat(); mini = ole.minifat
        except Exception: mini = None
        root = ole.root
        if mini is not None and root is not None and root.isectStart not in (FREE, ENDCH):
            chain, s = [], root.isectStart
            while s < len(ole.fat) and len(chain) < nsec: chain.append(s); s = ole.fat[s]
            mss = ole.minisectorsize
            for j, nxt in enumerate(mini):
                if nxt != FREE: continue
                pos = j * mss; k, w = divmod(pos, ss)
                if k >= len(chain) or pos + mss > root.size: continue
                off = 512 + chain[k] * ss + w
                if data[off:off + mss].strip(b'\0'):
                    buf = buf or bytearray(data); buf[off:off + mss] = b'\0' * mss
    except Exception: return data
    finally: ole.close()
    if buf is None: return data
    out = bytes(buf); a, b = olefile.OleFileIO(io.BytesIO(data)), olefile.OleFileIO(io.BytesIO(out))
    try:
        for e in a.listdir(streams=True, storages=False):
            if a.openstream(e).read() != b.openstream(e).read(): return data
    finally: a.close(); b.close()
    return out
