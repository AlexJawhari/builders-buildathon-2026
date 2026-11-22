"""Offline: rasterize world.json prisms + terrain into arena_grid.bin.gz (1 m grid) for the bot and simulator.

Layers (all 1070x724, row-major j*nx+i, int16):
  lo, hi   : building/stadium solid interval in decimeters (absolute), lo=hi=0 -> empty. Prisms that start >= ground+9 (overhangs) are left out here.
  blo, bhi : bridge interval (walk/shoot under), decimeters.
  nav      : uint8 1 = a mech centre may NOT stand here (wall taller than step, steep terrain), inflated by the mech radius + margin.
"""
import gzip, json, sys
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

KIT = sys.argv[1]
OUT = sys.argv[2]
w = json.load(open(f"{KIT}/v2/world.json"))
T = w["terrain"]; nx, ny, minx, miny = T["nx"], T["ny"], T["minX"], T["minY"]
ter = np.fromfile(f"{KIT}/v2/terrain.i16", dtype="<i2").reshape(ny, nx) / 100.0

lo = np.full((ny, nx), 1e9); hi = np.full((ny, nx), -1e9)
blo = np.full((ny, nx), 1e9); bhi = np.full((ny, nx), -1e9)
wall = np.zeros((ny, nx), bool)
for p in w["prisms"]:
    im = Image.new("L", (nx, ny), 0)
    r = p["ring"]
    ImageDraw.Draw(im).polygon([((r[k] - minx), (r[k + 1] - miny)) for k in range(0, len(r), 2)], fill=1)
    for h in p["holes"]:
        ImageDraw.Draw(im).polygon([((h[k] - minx), (h[k + 1] - miny)) for k in range(0, len(h), 2)], fill=0)
    m = np.array(im, bool)
    if p["kind"] == "bridge":
        blo[m] = np.minimum(blo[m], p["base"]); bhi[m] = np.maximum(bhi[m], p["top"])
        continue
    over = m & (p["base"] >= ter + 9)
    m = m & ~over
    lo[m] = np.minimum(lo[m], p["base"]); hi[m] = np.maximum(hi[m], p["top"])
    wall |= m & (p["top"] > ter + 1.2)
steep = np.zeros((ny, nx), bool)
dx = np.abs(np.diff(ter, axis=1)); dy = np.abs(np.diff(ter, axis=0))
steep[:, :-1] |= dx > 1.2; steep[:, 1:] |= dx > 1.2; steep[:-1, :] |= dy > 1.2; steep[1:, :] |= dy > 1.2
print("wall", wall.mean(), "steep", steep.mean())
edge = np.ones((ny, nx), bool); edge[3:-3, 3:-3] = False
blocked = wall | steep | edge
yy, xx = np.mgrid[-3:4, -3:4]
nav = ndimage.binary_dilation(blocked, structure=(xx**2 + yy**2) <= 9)
print("nav blocked", nav.mean())
dm = lambda a, e: np.where(a == e, 0, np.round(a * 10)).astype(np.int16)
with gzip.open(OUT, "wb") as f:
    for a in (dm(lo, 1e9), dm(hi, -1e9), dm(blo, 1e9), dm(bhi, -1e9), nav.astype(np.uint8)):
        f.write(a.tobytes())
