"""Static arena model shared by the bot and the simulator: terrain, solids, line of sight, walkability."""
import gzip, math, os
from array import array

NX, NY, MINX, MINY = 1070, 724, -513.0, -334.0
HERE = os.path.dirname(os.path.abspath(__file__))


class World:
    def __init__(self, map_dir="/arena", grid_path=None):
        t = array("h")
        with open(os.path.join(map_dir, "v2", "terrain.i16"), "rb") as f:
            t.frombytes(f.read())
        self.ter = t
        raw = gzip.open(grid_path or os.path.join(HERE, "arena_grid.bin.gz")).read()
        n = NX * NY
        a = array("h"); a.frombytes(raw[: 8 * n])
        self.lo, self.hi, self.blo, self.bhi = a[:n], a[n:2 * n], a[2 * n:3 * n], a[3 * n:4 * n]
        self.nav = raw[8 * n: 9 * n]

    def ground(self, x, y):
        fx = x - MINX; fy = y - MINY
        i = int(fx); j = int(fy)
        if i < 0 or j < 0 or i >= NX - 1 or j >= NY - 1:
            i = min(max(i, 0), NX - 2); j = min(max(j, 0), NY - 2)
        u = fx - i; v = fy - j
        t = self.ter; k = j * NX + i
        return 0.01 * ((t[k] * (1 - u) + t[k + 1] * u) * (1 - v) + (t[k + NX] * (1 - u) + t[k + NX + 1] * u) * v)

    def solid(self, x, y, z):
        i = int(x - MINX); j = int(y - MINY)
        if i < 0 or j < 0 or i >= NX or j >= NY:
            return True
        k = j * NX + i
        if self.hi[k] and self.lo[k] * 0.1 <= z <= self.hi[k] * 0.1:
            return True
        return bool(self.bhi[k]) and self.blo[k] * 0.1 <= z <= self.bhi[k] * 0.1

    def blocked_nav(self, x, y):
        i = int(x - MINX); j = int(y - MINY)
        return i < 0 or j < 0 or i >= NX or j >= NY or self.nav[j * NX + i] == 1

    def los(self, a, b, step=1.5):
        """True if the segment a->b (x,y,z) is clear of buildings, bridges and terrain."""
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        d = math.sqrt(dx * dx + dy * dy + dz * dz)
        n = max(1, int(d / step))
        for s in range(1, n):
            f = s / n
            x = a[0] + dx * f; y = a[1] + dy * f; z = a[2] + dz * f
            if self.solid(x, y, z) or z < self.ground(x, y) - 0.2:
                return False
        return True


# ---------------------------------------------------------------- 2 m navigation layer (built lazily by the bot, not by the simulator)
NS = 2
NNX, NNY = (NX + 1) // 2, (NY + 1) // 2
NBR = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))


def _build_nav(self, map_dir=None):
    """Blocked / surface / clearance / connected-component layers on 2 m cells (cell c = j*NNX+i sits at MINX+2i, MINY+2j).
    With map_dir the layers come from the exact prisms (shot geometry is loaded too); the 1 m raster is the fallback."""
    from collections import deque
    n = NNX * NNY
    blk = bytearray(n)
    surf = [0.0] * n
    for j in range(NNY):
        blk[j * NNX:(j + 1) * NNX] = self.nav[2 * j * NX:2 * j * NX + NX:2]
        surf[j * NNX:(j + 1) * NNX] = [v * 0.01 for v in self.ter[2 * j * NX:2 * j * NX + NX:2]]
    self.blk, self.surf = blk, surf
    if map_dir:
        try:
            self.load_geo(map_dir)
            cache = os.path.join(HERE, "arena_nav.bin.gz")
            if os.path.exists(cache):  # written by `python3 world.py <map_dir>`; recomputed (about 5 s) when absent
                raw = gzip.open(cache).read()
                self.blk[:] = raw[:n]
                self.surf[:] = array("f", raw[n:]).tolist()
            else:
                self.nav_from_geo()
        except Exception:
            import traceback
            traceback.print_exc()
    for i in range(NNX):
        blk[i] = blk[(NNY - 1) * NNX + i] = 1
    for j in range(NNY):
        blk[j * NNX] = blk[j * NNX + NNX - 1] = 1
    clr = [1e9] * n
    q = deque()
    for c in range(n):
        if blk[c]:
            clr[c] = 0.0
            q.append(c)
    while q:
        c = q.popleft()
        d = clr[c] + NS
        if d > 20:
            continue
        for nb in (c + 1, c - 1, c + NNX, c - NNX):
            if 0 <= nb < n and clr[nb] > d:
                clr[nb] = d
                q.append(nb)
    comp = [0] * n
    label = best_n = best_size = 0
    for c0 in range(n):
        if blk[c0] or comp[c0]:
            continue
        label += 1
        comp[c0] = label
        q = [c0]
        for c in q:
            for nb in (c + 1, c - 1, c + NNX, c - NNX):
                if not blk[nb] and not comp[nb] and abs(surf[nb] - surf[c]) <= 2.2:
                    comp[nb] = label
                    q.append(nb)
        if len(q) > best_size:
            best_size, best_n = len(q), label
    self.blk, self.surf, self.clr, self.comp, self.main = blk, surf, [min(v, 20.0) for v in clr], comp, best_n
    self._nf = {}
    self._near_main = None


def cell(self, x, y):
    i = int(math.floor((x - MINX) / NS + 0.5))
    j = int(math.floor((y - MINY) / NS + 0.5))
    return (NNY - 1 if j >= NNY else max(j, 0)) * NNX + (NNX - 1 if i >= NNX else max(i, 0))


def cell_pos(self, c):
    return MINX + (c % NNX) * NS, MINY + (c // NNX) * NS


def free(self, x, y):
    return not self.blk[self.cell(x, y)]


def reachable(self, x, y):
    c = self.cell(x, y)
    return not self.blk[c] and self.comp[c] == self.main


def in_bounds(self, x, y):
    return MINX + 1 < x < MINX + NX - 2 and MINY + 1 < y < MINY + NY - 2


def nearest_free(self, c):
    """Nearest unblocked cell by ring search (radius 20 cells), or -1."""
    if not self.blk[c]:
        return c
    r0 = self._nf.get(c)
    if r0 is not None:
        return r0
    ci, cj = c % NNX, c // NNX
    res = -1
    for r in range(1, 21):
        best, bd = -1, 1 << 30
        for dj in range(-r, r + 1):
            j = cj + dj
            if j < 0 or j >= NNY:
                continue
            step = 1 if abs(dj) == r else 2 * r
            for di in range(-r, r + 1, step):
                i = ci + di
                if 0 <= i < NNX and not self.blk[j * NNX + i]:
                    d = di * di + dj * dj
                    if d < bd:
                        best, bd = j * NNX + i, d
        if best >= 0:
            res = best
            break
    self._nf[c] = res
    return res


def line_walkable(self, x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0
    n = int(math.hypot(dx, dy)) + 1
    blk, surf, cell = self.blk, self.surf, self.cell
    prev = cell(x0, y0)
    for k in range(1, n + 1):
        t = k / n
        c = cell(x0 + dx * t, y0 + dy * t)
        if c == prev:
            continue
        if blk[c] or surf[c] - surf[prev] > 3.1:
            return False
        prev = c
    return True


def astar(self, sx, sy, gx, gy, max_expand=12000):
    """Generator: yields None every 400 expansions, then ('done', path, reached) with path a list of cells (weighted octile A*,
    cheap near walls). Partial path to the closest cell when the expansion cap is hit."""
    import heapq
    s = self.nearest_free(self.cell(sx, sy))
    g = self.nearest_free(self.cell(gx, gy))
    if s < 0 or g < 0:
        yield ("done", [], False)
        return
    if s == g:
        yield ("done", [s], True)
        return
    blk, surf, clr = self.blk, self.surf, self.clr
    gi, gj = g % NNX, g // NNX

    def h(c):
        dx, dy = abs(c % NNX - gi), abs(c // NNX - gj)
        return ((dx + 0.4142 * dy) if dx > dy else (dy + 0.4142 * dx)) * NS * 1.15

    gs = {s: 0.0}
    came = {s: -1}
    closed = set()
    heap = [(h(s), s)]
    best, best_h, found = s, h(s), False
    n = 0
    while heap and n < max_expand:
        _, c = heapq.heappop(heap)
        if c in closed:
            continue
        closed.add(c)
        n += 1
        if n % 400 == 0:
            yield None
        if c == g:
            found, best = True, c
            break
        hc = h(c)
        if hc < best_h:
            best, best_h = c, hc
        gc = gs[c]
        sc = surf[c]
        for k, (di, dj) in enumerate(NBR):
            nb = c + dj * NNX + di
            if blk[nb] or nb in closed:
                continue
            step = NS
            if k >= 4:
                if blk[c + dj * NNX] or blk[c + di] or surf[nb] - sc > 3.1:
                    continue
                step = NS * 1.4142
            elif surf[nb] - sc > 2.2:
                continue
            cl = clr[nb]
            if cl < 4:
                step *= 1 + (4 - cl) * 0.15
            ng = gc + step
            if ng < gs.get(nb, 1e18):
                gs[nb] = ng
                came[nb] = c
                heapq.heappush(heap, (ng + h(nb), nb))
    path = []
    c = best
    while c >= 0:
        path.append(c)
        c = came[c]
    path.reverse()
    yield ("done", path, found)


def main_near(self, c):
    """Nearest main-component cell within 20 steps of c (multi-source BFS, built once), or -1."""
    if self._near_main is None:
        from collections import deque
        n = NNX * NNY
        near = [-1] * n
        depth = [0] * n
        q = deque()
        for c0 in range(n):
            if not self.blk[c0] and self.comp[c0] == self.main:
                near[c0] = c0
                q.append(c0)
        while q:
            c1 = q.popleft()
            if depth[c1] >= 20:
                continue
            for di, dj in NBR:
                nb = c1 + dj * NNX + di
                if 0 <= nb < n and near[nb] < 0:
                    near[nb] = near[c1]
                    depth[nb] = depth[c1] + 1
                    q.append(nb)
        self._near_main = near
    return self._near_main[c]


def off_main(self, x, y):
    c = self.nearest_free(self.cell(x, y))
    return c < 0 or self.comp[c] != self.main


def escape_plan(self, x, y, cap=8000):
    """(launch, land) points to jet out of a pocket cut off from the main walkable area, or None."""
    s = self.nearest_free(self.cell(x, y))
    if s < 0:
        return None
    seen = {s: 0}
    q = [s]
    best, best_s = -1, 1e18
    for c in q:
        if len(q) > cap:
            break
        m = self.main_near(c)
        if m >= 0:
            cx, cy = self.cell_pos(c)
            mx, my = self.cell_pos(m)
            sc = seen[c] * NS + 4 * math.hypot(mx - cx, my - cy)
            if sc < best_s:
                best, best_s = c, sc
        ci, cj = c % NNX, c // NNX
        for k, (di, dj) in enumerate(NBR):
            nb = c + dj * NNX + di
            if nb in seen or self.blk[nb] or self.surf[nb] - self.surf[c] > (3.1 if k >= 4 else 2.2):
                continue
            if k >= 4 and (self.blk[c + dj * NNX] or self.blk[c + di]):
                continue
            seen[nb] = seen[c] + 1
            q.append(nb)
    if best < 0:
        return None
    lx, ly = self.cell_pos(best)
    mx, my = self.cell_pos(self.main_near(best))
    d = math.hypot(mx - lx, my - ly)
    if d > 0.1:
        ex, ey = mx + (mx - lx) / d * 5, my + (my - ly) / d * 5
        if self.in_bounds(ex, ey) and self.reachable(ex, ey):
            mx, my = ex, ey
    return (lx, ly), (mx, my)


def obst(self, x, y):
    """A building or wall column stands here (taller than a step)."""
    i = int(x - MINX + 0.5); j = int(y - MINY + 0.5)
    if i < 0 or j < 0 or i >= NX or j >= NY:
        return False
    k = j * NX + i
    return bool(self.hi[k]) and self.hi[k] * 0.1 - self.ter[k] * 0.01 > 1.2


def top_at(self, x, y):
    i = int(x - MINX + 0.5); j = int(y - MINY + 0.5)
    g = self.ground(x, y)
    if 0 <= i < NX and 0 <= j < NY:
        k = j * NX + i
        if self.hi[k] and self.lo[k] * 0.1 <= g + 1.5:
            return max(g, self.hi[k] * 0.1)
    return g


def clear(self, ax, ay, az, bx, by, bz):
    """Shot line a->b clear of buildings, bridges and terrain (2 m samples; trees never block shots)."""
    dx, dy, dz = bx - ax, by - ay, bz - az
    n = int(math.sqrt(dx * dx + dy * dy + dz * dz) * 0.5) + 1
    sx, sy, sz = dx / n, dy / n, dz / n
    hi, lo, bhi, blo, ter = self.hi, self.lo, self.bhi, self.blo, self.ter
    x, y, z = ax - MINX, ay - MINY, az
    for _ in range(n - 1):
        x += sx; y += sy; z += sz
        i = int(x); j = int(y)
        if i < 0 or j < 0 or i >= NX or j >= NY:
            continue
        k = j * NX + i
        zz = z * 10
        h = hi[k]
        if h and lo[k] <= zz <= h:
            return False
        h = bhi[k]
        if h and blo[k] <= zz <= h:
            return False
        if z * 100 < ter[k] + 120 and z < self.ground(x + MINX, y + MINY):
            return False
    return True


def cover_hidden(self, eyes, cx, cy, cz, height):
    """No eye in `eyes` sees the head or the middle of a mech standing at (cx, cy): the 1 m raster rejects exposed cells
    cheaply, the exact test confirms hidden ones."""
    raster, exact = World.clear, self.clear
    for e in eyes:
        if raster(self, e[0], e[1], e[2], cx, cy, cz + height) or raster(self, e[0], e[1], e[2], cx, cy, cz + height * 0.5):
            return False
    for e in eyes:
        if exact(e[0], e[1], e[2], cx, cy, cz + height) or exact(e[0], e[1], e[2], cx, cy, cz + height * 0.5):
            return False
    return True


def exposure(self, x, y):
    """Mean free sight distance (0..1 of 200 m) over 24 rays from head height; low = enclosed."""
    z = self.top_at(x, y) + 7
    hi, lo, ter = self.hi, self.lo, self.ter
    zz = z * 10
    total = 0.0
    for k in range(24):
        a = k * math.pi / 12
        c, s = math.cos(a), math.sin(a)
        d = 2.5
        while d < 200:
            i = int(x + c * d - MINX); j = int(y + s * d - MINY)
            if i < 0 or j < 0 or i >= NX or j >= NY:
                break
            kk = j * NX + i
            if z < ter[kk] * 0.01 or (hi[kk] and lo[kk] <= zz <= hi[kk]):
                break
            d += 2.5
        total += d
    return total / (24 * 200)


for _f in (_build_nav, cell, cell_pos, free, reachable, in_bounds, nearest_free, line_walkable, astar, main_near, off_main, escape_plan,
           obst, top_at, clear, cover_hidden, exposure):
    setattr(World, _f.__name__.lstrip("_") if _f.__name__ != "_build_nav" else "build_nav", _f)


# ---------------------------------------------------------------- exact shot geometry from world.json prisms + terrain heightfield
GEO_CELL = 16.0


def _point_in_poly(e, x, y):
    inside = False
    for i in range(0, len(e), 4):
        y0, y1 = e[i + 1], e[i + 3]
        if (y0 > y) != (y1 > y) and x < (e[i + 2] - e[i]) * (y - y0) / (y1 - y0) + e[i]:
            inside = not inside
    return inside


def load_geo(self, map_dir):
    """Prisms (buildings, stadium, bridges) on a 16 m grid plus 8 m block maxima of the terrain; enables clear_exact/walk_at."""
    import json
    wj = json.load(open(os.path.join(map_dir, "v2", "world.json")))
    b = wj["bounds"]
    self.bx0, self.bx1, self.by0, self.by1 = b["minX"], b["maxX"], b["minY"], b["maxY"]
    self.gx0, self.gy0 = self.bx0 - GEO_CELL, self.by0 - GEO_CELL
    self.gnx = int(math.ceil((self.bx1 - self.bx0) / GEO_CELL)) + 3
    self.gny = int(math.ceil((self.by1 - self.by0) / GEO_CELL)) + 3
    self.prisms = []
    self.gridp = [[] for _ in range(self.gnx * self.gny)]
    for p in wj["prisms"]:
        edges = []
        mnx = mny = 1e18
        mxx = mxy = -1e18
        for r in [p["ring"]] + p["holes"]:
            m = len(r) // 2
            for a in range(m):
                c = (a + 1) % m
                edges += [r[a * 2], r[a * 2 + 1], r[c * 2], r[c * 2 + 1]]
                mnx, mxx = min(mnx, r[a * 2]), max(mxx, r[a * 2])
                mny, mxy = min(mny, r[a * 2 + 1]), max(mxy, r[a * 2 + 1])
        idx = len(self.prisms)
        self.prisms.append((p["base"], p["top"], edges, mnx, mny, mxx, mxy, p["kind"] == "bridge"))
        i0 = max(0, int((mnx - self.gx0) // GEO_CELL)); i1 = min(self.gnx - 1, int((mxx - self.gx0) // GEO_CELL))
        j0 = max(0, int((mny - self.gy0) // GEO_CELL)); j1 = min(self.gny - 1, int((mxy - self.gy0) // GEO_CELL))
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                self.gridp[j * self.gnx + i].append(idx)
    self.bnx, self.bny = (NX + 7) // 8, (NY + 7) // 8
    bm = [-1e9] * (self.bnx * self.bny)
    ter = self.ter
    for j in range(NY):
        row = j * NX
        for bi in range(self.bnx):
            v = max(ter[row + bi * 8: row + bi * 8 + 8])
            k = (j // 8) * self.bnx + bi
            if v > bm[k]:
                bm[k] = v
    bmx = bm[:]
    for j in range(self.bny):
        for i in range(self.bnx):
            v = bm[j * self.bnx + i]
            if i + 1 < self.bnx:
                v = max(v, bm[j * self.bnx + i + 1])
            if j + 1 < self.bny:
                v = max(v, bm[(j + 1) * self.bnx + i])
                if i + 1 < self.bnx:
                    v = max(v, bm[(j + 1) * self.bnx + i + 1])
            bmx[j * self.bnx + i] = v * 0.01
    self.blockmax = bmx
    self.exact = True


def _ray_hits_prism(p, x, y, z, dx, dy, dz):
    """Segment (x,y,z)+t*(dx,dy,dz), t in [0,1], touches prism p (boolean port of the original rayPrism)."""
    base, top, e, mnx, mny, mxx, mxy, _ = p
    t0, t1 = 0.0, 1.0
    for o, d, lo, hi in ((x, dx, mnx, mxx), (y, dy, mny, mxy), (z, dz, base, top)):
        if -1e-9 < d < 1e-9:
            if o < lo or o > hi:
                return False
        else:
            a, b = (lo - o) / d, (hi - o) / d
            if a > b:
                a, b = b, a
            if a > t0:
                t0 = a
            if b < t1:
                t1 = b
            if t0 > t1:
                return False
    if base <= z <= top and _point_in_poly(e, x, y):
        return True
    for i in range(0, len(e), 4):
        ex, ey = e[i + 2] - e[i], e[i + 3] - e[i + 1]
        den = dx * ey - dy * ex
        if -1e-9 < den < 1e-9:
            continue
        px, py = e[i] - x, e[i + 1] - y
        t = (px * ey - py * ex) / den
        if t < 0 or t >= 1:
            continue
        s = (px * dy - py * dx) / den
        if s < 0 or s > 1:
            continue
        hz = z + dz * t
        if base <= hz <= top:
            return True
    if dz > 1e-9 or dz < -1e-9:
        for cz, up in ((top, True), (base, False)):
            t = (cz - z) / dz
            if t < 0 or t >= 1 or (up and dz > 0) or (not up and dz < 0):
                continue
            if _point_in_poly(e, x + dx * t, y + dy * t):
                return True
    return False


def _ray_terrain(self, x, y, z, dx, dy, dz):
    ground = self.ground
    if z < ground(x, y):
        return True
    hl = math.hypot(dx, dy)
    step = 0.5 / hl if hl > 1e-9 else 1.0
    bm, bnx, bny = self.blockmax, self.bnx, self.bny
    t = 0.0
    while t < 1.0:
        px, py = x + dx * t, y + dy * t
        bi = int(math.floor((px - MINX) / 8))
        bj = int(math.floor((py - MINY) / 8))
        tb = 1.0
        if hl > 1e-9:
            ex, ey = MINX + bi * 8, MINY + bj * 8
            nx = ny = 1e18
            if dx > 1e-9:
                nx = (ex + 8 - px) / dx
            elif dx < -1e-9:
                nx = (ex - px) / dx
            if dy > 1e-9:
                ny = (ey + 8 - py) / dy
            elif dy < -1e-9:
                ny = (ey - py) / dy
            tb = min(t + max(0.0, min(nx, ny)) + 1e-6, 1.0)
        if 0 <= bi < bnx and 0 <= bj < bny and min(z + dz * t, z + dz * tb) > bm[bj * bnx + bi]:
            t = tb
            continue
        s = min(t + step, 1.0)
        if z + dz * s < ground(x + dx * s, y + dy * s):
            return True
        if s >= 1.0:
            break
        t = s
    return False


def clear_exact(self, ax, ay, az, bx, by, bz):
    """Shot line a->b clear of prisms and terrain (the original's ShotClear: trees never block)."""
    dx, dy, dz = bx - ax, by - ay, bz - az
    gx0, gy0, gnx, gny = self.gx0, self.gy0, self.gnx, self.gny
    ci = int(math.floor((ax - gx0) / GEO_CELL)); cj = int(math.floor((ay - gy0) / GEO_CELL))
    ei = int(math.floor((bx - gx0) / GEO_CELL)); ej = int(math.floor((by - gy0) / GEO_CELL))
    si = 1 if dx > 0 else -1
    sj = 1 if dy > 0 else -1
    tdx = tdy = tmx = tmy = 1e18
    if abs(dx) > 1e-9:
        tdx = GEO_CELL / abs(dx)
        tmx = ((ci + 1) * GEO_CELL + gx0 - ax) / dx if dx > 0 else (ax - (ci * GEO_CELL + gx0)) / -dx
    if abs(dy) > 1e-9:
        tdy = GEO_CELL / abs(dy)
        tmy = ((cj + 1) * GEO_CELL + gy0 - ay) / dy if dy > 0 else (ay - (cj * GEO_CELL + gy0)) / -dy
    grid, prisms = self.gridp, self.prisms
    done = set()
    for _ in range(4096):
        if 0 <= ci < gnx and 0 <= cj < gny:
            for pi in grid[cj * gnx + ci]:
                if pi not in done:
                    done.add(pi)
                    if _ray_hits_prism(prisms[pi], ax, ay, az, dx, dy, dz):
                        return False
        if ci == ei and cj == ej:
            break
        if tmx < tmy:
            t = tmx; tmx += tdx; ci += si
        else:
            t = tmy; tmy += tdy; cj += sj
        if t > 1:
            break
    return not self._ray_terrain(ax, ay, az, dx, dy, dz)


def stack_kind(self, x, y, deck=True):
    """(standing height, id of the topmost prism or -1): ground raised by prisms within a step, plus bridge decks."""
    g = self.ground(x, y)
    z = g
    pid = -1
    c = int(math.floor((y - self.gy0) / GEO_CELL)) * self.gnx + int(math.floor((x - self.gx0) / GEO_CELL))
    if not 0 <= c < len(self.gridp):
        return z, pid
    ps = self.gridp[c]
    for _ in range(8):
        moved = False
        for pi in ps:
            base, top, e, mnx, mny, mxx, mxy, bridge = self.prisms[pi]
            if top <= z or x < mnx or x > mxx or y < mny or y > mxy:
                continue
            if not (base <= z + 1.5 or (deck and bridge and top - g < 6.5)):
                continue
            if _point_in_poly(e, x, y):
                z, pid = top, pi
                moved = True
        if not moved:
            break
    return z, pid


def walk_at(self, x, y, deck=True):
    return self.stack_kind(x, y, deck)[0]


def wall_within(self, x, y, z, r, h=9.0):
    """A prism taller than a step and lower than z+h has an edge within r of (x, y), or contains it."""
    lo, hi, r2 = z + 1.2, z + h, r * r
    gx0, gy0, gnx, gny = self.gx0, self.gy0, self.gnx, self.gny
    for j in range(max(0, int((y - r - gy0) // GEO_CELL)), min(gny - 1, int((y + r - gy0) // GEO_CELL)) + 1):
        for i in range(max(0, int((x - r - gx0) // GEO_CELL)), min(gnx - 1, int((x + r - gx0) // GEO_CELL)) + 1):
            for pi in self.gridp[j * gnx + i]:
                base, top, e, mnx, mny, mxx, mxy, _ = self.prisms[pi]
                if top <= lo or base >= hi or x + r < mnx or x - r > mxx or y + r < mny or y - r > mxy:
                    continue
                for k in range(0, len(e), 4):
                    x0, y0 = e[k], e[k + 1]
                    ux, uy = e[k + 2] - x0, e[k + 3] - y0
                    l2 = ux * ux + uy * uy
                    t = max(0.0, min(1.0, ((x - x0) * ux + (y - y0) * uy) / l2)) if l2 > 1e-9 else 0.0
                    mx, my = x0 + ux * t - x, y0 + uy * t - y
                    if mx * mx + my * my < r2:
                        return True
                if _point_in_poly(e, x, y):
                    return True
    return False


def nav_from_geo(self, r=2.6):
    """Exact blocked/surface layers from the prisms (needs load_geo): walls within r, roofs, map edge."""
    for c in range(NNX * NNY):
        x, y = self.cell_pos(c)
        z, pid = self.stack_kind(x, y, True)
        self.surf[c] = z
        roof = pid >= 0 and not self.prisms[pid][7] and z - self.ground(x, y) > 1.5
        self.blk[c] = int(roof or x < self.bx0 + r or x > self.bx1 - r or y < self.by0 + r or y > self.by1 - r
                          or self.wall_within(x, y, z, r))


for _f in (load_geo, _ray_terrain, clear_exact, stack_kind, walk_at, wall_within, nav_from_geo):
    setattr(World, _f.__name__, _f)


if __name__ == "__main__":
    import sys
    w = World(sys.argv[1])
    w.build_nav(sys.argv[1])
    with gzip.open(os.path.join(HERE, "arena_nav.bin.gz"), "wb") as f:
        f.write(bytes(w.blk) + array("f", w.surf).tobytes())
