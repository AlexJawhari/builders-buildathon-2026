"""Survival-first mech bot: stay in the zone, hide in cover, kite and shoot back with lead, collect lightning/laser."""
import heapq
import json
import os
import math
import random
import time

import mechfight as mf
from world import World, MINX, MINY, NX, NY

C = 2  # coarse A* cell size in metres
CNX, CNY = NX // C, NY // C
TAU = math.tau
ADJ = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0), (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414)]
UPG = ["nanites", "plating", "armor", "gun_damage", "servos", "reactor", "gun_mag", "gun_rate", "capacitor", "gyros", "lightning_efficiency",
       "lightning_chain", "laser_damage", "sensors", "jets"]
UPG2 = ["gun_mag", "gun_damage", "plating", "armor", "gun_rate", "nanites", "lightning_efficiency", "lightning_chain", "laser_damage", "servos", "reactor", "capacitor", "gyros", "sensors", "jets"]
UPG67 = ["plating", "armor", "nanites", "gun_damage", "gun_rate", "laser_damage", "laser_charge", "lightning_efficiency", "servos", "gyros",
         "gun_velocity", "gun_mag", "reactor", "capacitor", "jets", "lightning_chain", "lightning_range", "sensors", "laser_pierce"]
UPG3 = ["laser_damage", "laser_charge", "gun_mag", "gun_damage", "plating", "armor", "gun_rate", "nanites", "laser_pierce", "lightning_efficiency", "lightning_chain", "servos", "reactor", "capacitor", "gyros", "sensors", "jets"]
UPG4 = ["plating", "armor", "laser_damage", "laser_charge", "nanites", "gun_damage", "gun_rate", "lightning_efficiency", "servos", "gun_mag", "gun_velocity", "gyros", "lightning_chain", "reactor", "capacitor", "sensors", "jets", "laser_pierce"]
UPG5 = ["plating", "armor", "gun_damage", "nanites", "gun_rate", "lightning_efficiency", "laser_damage", "servos", "laser_charge", "gun_mag", "gun_velocity", "gyros", "lightning_chain", "reactor", "capacitor", "sensors", "jets", "laser_pierce"]
PICK_W2 = {"laser": 3, "lightning": 2, "mortar": 1}
PICK_W = {"lightning": 3, "laser": 2, "mortar": 1}
P = {"retreat_hp": 0.5, "outnum": 2, "lo": 55, "hi": 110, "home_frac": 0.9, "scan": 0.28, "strafe": 1.0, "near_lo": -0.7, "flip_lo": 0.4, "flip_hi": 1.1, "retreat_jit": 0.0, "farm_until": 200, "farm_r": 100, "hyst": 0, "pick_r": 90, "cube_r": 45, "tol": 1.0, "stay": 0, "fire_max": 999, "upg": 4, "tscore": 1, "pmem": 1, "drop_r": 300, "engage": 0, "sticky": 0, "sneak": 0, "lzdodge": 0, "dtime": 0.4, "dboost": 1, "heal_to": 0.95, "dcross": 6.0, "dwt": 1.5, "dmin_e": 12, "ring_frac": 0.8, "mort": 1, "endg": 0, "lcap": 120, "laser1": 1, "scan0": 1, "pick_r0": 120, "lband": 0, "swap": 1, "explore": 120, "killloot": 1, "gunlaser": 0, "grab": 1, "xcells": 0, "kcube": 0, "avoid": 0, "farm_r0": 0, "eco": 1, "circ": 1, "band": 0,
     # ideas studied from a public 6.29 bot, all off by default (see NOTES.md Round 6)
     "lseek": 1, "lfar": 300, "lhot": 70, "sweep": 1, "scan0_t": 3.0, "jet": 1, "lsr": 1, "lethal": 1,
     "harm": 0, "nofirst": 0, "hunt": 0, "dpf": 0, "finish": 0, "site": 0, "acc": 0,
     # round 7: ideas from public 6.2-6.4 bots (see TECHNIQUES.md), all default off
     "fm": 1, "lose": 1, "eng2": 0, "fin": 0, "salt": 1, "late_r": 70}
if os.environ.get("MECH_P"):
    P.update(json.loads(os.environ["MECH_P"]))


def wrap(a):
    return (a + math.pi) % TAU - math.pi


def dist2(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


class Bot:
    name = "mech-base"

    def __init__(self, hello):
        cfg = hello["config"]
        self.cfg = cfg
        self.rng = random.Random(hello.get("seed", 0) * 1000003 + P["salt"])
        self.w = World(hello["map"]["dir"])
        nav = self.w.nav
        self.cblock = bytearray()
        for j in range(CNY):
            self.cblock += nav[(C * j + 1) * NX + 1:(C * j + 1) * NX + 1 + C * CNX:C]
        self.path = []
        self.path_goal = None
        self.path_t = -99
        self.search = None
        self.hist = []
        self.strafe = 1
        self.strafe_until = 0
        self.stuck_until = 0
        self.stuck_dir = (0, 0)
        self.cover = None
        self.cover_t = -99
        self.home = None
        self.home_key = None
        self.scan = 0.0
        self.last_hp = None
        self.last_dmg_t = -99
        self.laser_hold = 0
        self.dmg_bearing = None
        self.tgt_id = None
        self.healing = False
        self.pmem = {}
        self.emem = {}
        self.eignored = {}
        self.echosen = None
        self.estart = 0.0
        self.healing_ok = False
        self.visited = set()
        self.loot_until = 0.0
        self.xgoal = None
        self.xt = -99.0
        self.t_now = 0.0
        self.dodge_until = 0.0
        self.chg = {}
        self.dodge_dir = (0.0, 0.0)
        self.stay_spot = None
        self.trk = {}
        self.dealt = {}
        self.hit_t = {}
        self.host_t = {}
        self.sites = []
        self.snd = []
        self.hbans = {}
        self.hgoal = None
        self.sweep_on = False
        self.sweep_acc = 0.0
        self.sweep_yaw = 0.0
        self.last_sweep = 0.0
        self.jet_on = False
        self.lose_at = -99.0

    # ---------------------------------------------------------------- navigation
    def cfree(self, ci, cj):
        return 0 <= ci < CNX and 0 <= cj < CNY and not self.cblock[cj * CNX + ci]

    def cell(self, x, y):
        return int((x - MINX) // C), int((y - MINY) // C)

    def nearest_free(self, ci, cj):
        for r in range(0, 12):
            for di in range(-r, r + 1):
                for dj in range(-r, r + 1):
                    if max(abs(di), abs(dj)) == r and self.cfree(ci + di, cj + dj):
                        return ci + di, cj + dj
        return ci, cj

    def astar(self, start, goal):
        """Generator: yields every ~300 expansions, finally yields ('done', path of world points) or ('fail', None)."""
        s = self.nearest_free(*self.cell(start[0], start[1]))
        g = self.nearest_free(*self.cell(goal[0], goal[1]))
        gx, gy = g
        openh = [(0.0, 0.0, s)]
        gs = {s: 0.0}
        par = {}
        n = 0
        while openh:
            f, gc, cur = heapq.heappop(openh)
            if gc > gs.get(cur, 1e18):
                continue
            if cur == g:
                pts = [cur]
                while cur in par:
                    cur = par[cur]; pts.append(cur)
                pts.reverse()
                yield ("done", [(MINX + (a + 0.5) * C, MINY + (b + 0.5) * C) for a, b in pts])
                return
            n += 1
            if n % 300 == 0:
                yield None
            if n > 40000:
                break
            ci, cj = cur
            for di, dj, cost in ADJ:
                a, b = ci + di, cj + dj
                if not self.cfree(a, b):
                    continue
                if di and dj and not (self.cfree(ci + di, cj) and self.cfree(ci, cj + dj)):
                    continue
                ng = gc + cost
                nb = (a, b)
                if ng < gs.get(nb, 1e18):
                    gs[nb] = ng
                    par[nb] = cur
                    h = math.hypot(a - gx, b - gy)
                    heapq.heappush(openh, (ng + 1.5 * h, ng, nb))
        yield ("fail", None)

    def want_path(self, goal, t):
        if self.search and self.search[0] == goal:
            return
        if self.path_goal and dist2(self.path_goal, goal) < 6 and t - self.path_t < 6:
            return
        self.search = (goal, self.astar(self.pos, goal))

    def run_search(self, t, budget):
        if not self.search:
            return
        goal, gen = self.search
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < budget:
            r = next(gen, ("fail", None))
            if r and r[0] == "done":
                self.path = r[1]; self.path_goal = goal; self.path_t = t; self.search = None
                return
            if r and r[0] == "fail":
                self.path = []; self.path_goal = goal; self.path_t = t; self.search = None
                return

    def line_free(self, a, b):
        d = dist2(a, b)
        n = int(d / 2) + 1
        for k in range(1, n + 1):
            f = k / n
            if self.w.blocked_nav(a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f):
                return False
        return True

    def follow(self, goal, t):
        """Unit direction toward goal along the planned path (straight if it is clear)."""
        pos = self.pos
        d = dist2(pos, goal)
        if d < 3:
            return (0.0, 0.0), d
        if d < 40 and self.line_free(pos, goal):
            return ((goal[0] - pos[0]) / d, (goal[1] - pos[1]) / d), d
        self.want_path(goal, t)
        p = self.path
        if p and self.path_goal and dist2(self.path_goal, goal) < 8:
            best = 0
            bd = 1e9
            for k, q in enumerate(p):
                dd = dist2(pos, q)
                if dd < bd:
                    bd = dd; best = k
            tgt = p[min(best + 1, len(p) - 1)]
            for k in range(min(best + 10, len(p) - 1), best, -1):
                if self.line_free(pos, p[k]):
                    tgt = p[k]; break
            dd = dist2(pos, tgt)
            if dd > 0.5:
                return ((tgt[0] - pos[0]) / dd, (tgt[1] - pos[1]) / dd), d
        return ((goal[0] - pos[0]) / d, (goal[1] - pos[1]) / d), d

    def steer(self, dx, dy):
        """Rotate the wish direction until the cell 5 m ahead is walkable."""
        if abs(dx) + abs(dy) < 1e-6:
            return dx, dy
        pos = self.pos
        base = math.atan2(dy, dx)
        for off in (0, 0.45, -0.45, 0.9, -0.9, 1.4, -1.4, 2.0, -2.0):
            a = base + off
            ca, sa = math.cos(a), math.sin(a)
            if not self.w.blocked_nav(pos[0] + ca * 5, pos[1] + sa * 5) and not self.w.blocked_nav(pos[0] + ca * 2.5, pos[1] + sa * 2.5):
                return ca * math.hypot(dx, dy), sa * math.hypot(dx, dy)
        return dx, dy

    # ---------------------------------------------------------------- tactics helpers
    def exposed(self, e, p):
        """Can enemy mech e (eye) see point p (centre or head)?"""
        ex = (e["pos"][0], e["pos"][1], e["pos"][2] + 8)
        z = self.w.ground(p[0], p[1])
        return self.w.los(ex, (p[0], p[1], z + 4.5), 2.5) or self.w.los(ex, (p[0], p[1], z + 8), 2.5)

    def pick_cover(self, enemies, zc, zr):
        pos = self.pos
        best = None
        cands = [(pos[0], pos[1])]
        for r in (8, 16, 26, 38):
            for k in range(12 if r < 20 else 16):
                a = TAU * k / (12 if r < 20 else 16) + r
                cands.append((pos[0] + r * math.cos(a), pos[1] + r * math.sin(a)))
        for c in cands:
            if self.w.blocked_nav(*c):
                continue
            if math.hypot(c[0] - zc[0], c[1] - zc[1]) > zr - 18:
                continue
            exp = sum(1 for e in enemies if self.exposed(e, c))
            tr = dist2(pos, c)
            near = min((dist2(e["pos"], c) for e in enemies), default=999)
            sc = exp * 100 + tr * 0.8 - min(near, 120) * 0.25
            if best is None or sc < best[0]:
                best = (sc, c, exp)
        return best

    def late(self, obs):
        return obs["zone"]["next_radius"] <= P["late_r"] or obs.get("alive", 16) <= 3

    def fight_move(self, obs, me, tg, lo_r, hi_r, slot1, mechs, t, t0):
        """Pick the best of 17 nearby spots: hold the range band, be visible to the target only while a weapon is ready
        (peek-fire: hide while the gun reloads), stay out of other mechs' sight, inside the zone, with a strafe bias."""
        pos = self.pos
        w = self.w
        ex, ey = tg["pos"][0], tg["pos"][1]
        dd = math.hypot(ex - pos[0], ey - pos[1]) or 1.0
        ux, uy = (ex - pos[0]) / dd, (ey - pos[1]) / dd
        sx, sy = -uy * self.strafe, ux * self.strafe
        gun = me["weapons"][0]
        ready = bool(gun and gun["ammo"] > 0 and gun["reload"] <= 0.25 and dd <= gun["stats"]["range"] - 10) or slot1 in ("laser", "lightning")
        want = 1.0 if ready else -1.0
        tz = tg["pos"][2] + 4.5
        z = obs["zone"]
        ss, se = z.get("shrink_start", 1e9), z.get("shrink_end", 1e9)
        f = min(1.0, max(0.0, (t + 8 - ss) / max(1.0, se - ss)))
        zcx = z["center"][0] + (z["next_center"][0] - z["center"][0]) * f
        zcy = z["center"][1] + (z["next_center"][1] - z["center"][1]) * f
        zr = z["radius"] + (z["next_radius"] - z["radius"]) * f
        mid = 0.5 * (lo_r + hi_r)
        cands = []
        blocked_side = False
        rings = (6.0,) if ready else (6.0, 13.0)
        for k in range(-1, 16 * len(rings)):
            if k < 0:
                cx, cy, dx, dy, r = pos[0], pos[1], 0.0, 0.0, 0.0
            else:
                a = (k % 16) * math.pi / 8
                r = rings[k // 16]
                dx, dy = math.cos(a), math.sin(a)
                cx, cy = pos[0] + dx * r, pos[1] + dy * r
                if w.blocked_nav(cx, cy) or not self.line_free(pos, (cx, cy)):
                    if k < 16 and dx * sx + dy * sy > 0.9:
                        blocked_side = True
                    continue
            d = math.hypot(ex - cx, ey - cy)
            s = -1.1 * max(0.0, lo_r - d, d - hi_r) - 0.1 * abs(d - mid) - 0.4 * r
            s -= 1.5 * max(0.0, math.hypot(cx - zcx, cy - zcy) - (zr - 10))
            if want > 0:
                s += 9 * (dx * sx + dy * sy)
            if k < 0:
                s -= 8
            cands.append([s, cx, cy, dx, dy, k])
        if blocked_side:
            self.strafe = -self.strafe
            self.strafe_until = t + 0.5
        # visibility to the target (the one LOS per candidate) and to the other mechs (top few only)
        for c in cands:
            if time.perf_counter() - t0 > 0.010:
                break
            gz = w.ground(c[1], c[2])
            los = w.los((c[1], c[2], gz + 7), (ex, ey, tz), 3.0)
            c.append(los)
            if los:
                c[0] += 45 * want
            elif c[5] < 0 and want < 0:
                c[0] += 14
        cands = [c for c in cands if len(c) > 6]
        cands.sort(key=lambda c: -c[0])
        others = [m for m in mechs if m is not tg and m["d"] < 170][:2]
        if others:
            for c in cands[:5]:
                if time.perf_counter() - t0 > 0.013:
                    break
                gz = w.ground(c[1], c[2])
                for m in others:
                    if w.los((m["pos"][0], m["pos"][1], m["pos"][2] + 8), (c[1], c[2], gz + 5.4), 3.0):
                        c[0] -= 30
            cands[:5] = sorted(cands[:5], key=lambda c: -c[0])
        if not cands:
            return (-uy * self.strafe, ux * self.strafe)
        return (cands[0][3], cands[0][4])

    def exposed_to_known(self, close):
        """Keep the mortar for quiet moments: skip when a visible mech is already close."""
        return any(m["d"] < 45 for m in close)

    def solve_mortar(self, me, tg, v=72.0, g=22.0):
        """[yaw, pitch] of the low-angle lob that lands on the (led) target, or None when out of range."""
        mp = (me["pos"][0], me["pos"][1], me["pos"][2] + me["stats"].get("height", 9) + 0.5)
        tp = tg["pos"]; tv = tg.get("vel", [0, 0, 0])
        ft = 0.0
        for _ in range(4):
            px, py = tp[0] + tv[0] * ft, tp[1] + tv[1] * ft
            R = math.hypot(px - mp[0], py - mp[1])
            dz = tp[2] + 1.0 - mp[2]
            lo, hi = 0.0, math.pi / 4
            if R * math.tan(hi) - g * R * R / (2 * v * v * math.cos(hi) ** 2) < dz:
                return None
            for _ in range(24):
                th = (lo + hi) / 2
                if R * math.tan(th) - g * R * R / (2 * v * v * math.cos(th) ** 2) < dz:
                    lo = th
                else:
                    hi = th
            ft = R / (v * math.cos(hi))
        return math.atan2(py - mp[1], px - mp[0]), hi

    def solve_lead(self, me, tg, speed, gz_off=4.5, lat=0.12):
        """Aim [yaw,pitch] to hit a moving target with a straight, instant-speed projectile."""
        mp = (me["pos"][0], me["pos"][1], me["pos"][2] + me["stats"].get("muzzle_height", 7))
        tp = tg["pos"]; tv = tg.get("vel", [0, 0, 0])
        t = 0.0
        for _ in range(4):
            px, py, pz = tp[0] + tv[0] * t, tp[1] + tv[1] * t, tp[2] + gz_off + tv[2] * t * 0.3
            if P["acc"] and "acc" in tg:
                ta = min(t, 0.35)
                px += 0.25 * tg["acc"][0] * ta * ta; py += 0.25 * tg["acc"][1] * ta * ta
            d = math.sqrt((px - mp[0]) ** 2 + (py - mp[1]) ** 2 + (pz - mp[2]) ** 2)
            t = d / speed + lat
        yaw = math.atan2(py - mp[1], px - mp[0])
        pit = math.atan2(pz - mp[2], math.hypot(px - mp[0], py - mp[1]))
        return yaw, max(-0.7, min(1.3, pit)), d

    def incoming(self, obs):
        """Sideways dodge vector from enemy projectiles that will pass close."""
        me = self.pos
        ax = ay = 0.0
        for p in obs["projectiles"]:
            vx, vy = p["vel"][0], p["vel"][1]
            sp = math.hypot(vx, vy)
            if sp < 1:
                continue
            rx, ry = me[0] - p["pos"][0], me[1] - p["pos"][1]
            along = (rx * vx + ry * vy) / sp
            shell = p.get("kind") == "mortar"
            if along < 0 or along > (150 if shell else 80):
                continue
            cross = (rx * vy - ry * vx) / sp
            if abs(cross) < (P["dcross"] * 1.6 if shell else P["dcross"]):
                s = 1 if cross < 0 else -1  # step away to the side we are already on
                ax += s * -vy / sp; ay += s * vx / sp
        return ax, ay

    # ---------------------------------------------------------------- zone / home
    def zone_state(self, obs):
        z = obs["zone"]
        zc = z["center"]; zr = z["radius"]
        d = math.hypot(self.pos[0] - zc[0], self.pos[1] - zc[1])
        return zc, zr, zr - d

    def ring_point(self, obs, c, r, frac=None):
        """Spot on a ring inside circle (c, r), on our side and away from known mechs: no centre pile-up."""
        frac = frac or P["ring_frac"]
        ux, uy = self.pos[0] - c[0], self.pos[1] - c[1]
        ul = math.hypot(ux, uy) or 1
        ux, uy = ux / ul * 0.5, uy / ul * 0.5
        ms = [m for m in obs["mechs"] if m["kind"] == "mech"]
        if ms:
            ex = sum(m["pos"][0] for m in ms) / len(ms) - c[0]
            ey = sum(m["pos"][1] for m in ms) / len(ms) - c[1]
            el = math.hypot(ex, ey) or 1
            ux -= ex / el; uy -= ey / el
        ul = math.hypot(ux, uy) or 1
        return (c[0] + ux / ul * r * frac, c[1] + uy / ul * r * frac)

    def pick_home(self, obs):
        z = obs["zone"]
        key = (tuple(z["next_center"]), z["next_radius"])
        if key == self.home_key and self.home:
            return self.home
        nc = z["next_center"]; nr = z["next_radius"]
        self.home_key = key
        if nr < 30:
            self.home = self.ring_point(obs, nc, nr) if P["endg"] else (nc[0], nc[1])
            return self.home
        dx, dy = self.pos[0] - nc[0], self.pos[1] - nc[1]
        d = math.hypot(dx, dy) or 1
        k = min(d, nr * P["home_frac"]) / d
        h = (nc[0] + dx * k, nc[1] + dy * k)
        # nudge to a walkable spot with some building cover nearby
        self.home = self.cover_spot(h, nc, nr)
        return self.home

    def cover_spot(self, h, nc, nr):
        """Walkable spot near h that building walls enclose the most (hard to see from afar)."""
        best = None
        cands = [h] + [(h[0] + r * math.cos(a * TAU / 10), h[1] + r * math.sin(a * TAU / 10)) for r in (10, 20, 30, 40) for a in range(10)]
        for c in cands:
            if self.w.blocked_nav(*c) or math.hypot(c[0] - nc[0], c[1] - nc[1]) > nr * 0.9:
                continue
            enc = 0
            zc = self.w.ground(*c)
            for a in range(12):
                aa = a * TAU / 12
                if not self.w.los((c[0], c[1], zc + 8), (c[0] + 45 * math.cos(aa), c[1] + 45 * math.sin(aa), zc + 8), 3):
                    enc += 1
            sc = enc * 3 - dist2(c, h) * 0.1
            if best is None or sc > best[0]:
                best = (sc, c)
        return best[1] if best else h

    # ---------------------------------------------------------------- track memory (ideas from a public 6.29 bot)
    def track_update(self, obs, mechs, t):
        """Per-mech memory: last pos/vel/acc, laser seen, damage dealt to us, 'came at us' marks, kill sites."""
        pos = self.pos
        for e in obs["events"]:
            if e["type"] == "damaged" and e.get("by") is not None:
                self.dealt[e["by"]] = self.dealt.get(e["by"], 0.0) + max(0.0, e.get("amount") or 0.0)
                self.hit_t[e["by"]] = t
            elif e["type"] == "kill":
                tr = self.trk.pop(e["target"], None)
                if tr and P["site"]:
                    self.sites.append([tr["pos"][0], tr["pos"][1], t])
        for m in mechs:
            old = self.trk.get(m["id"])
            vel = m.get("vel") or [0, 0, 0]
            acc = (0.0, 0.0)
            if old and 0 < t - old["t"] <= 0.25:
                dt = t - old["t"]
                acc = ((vel[0] - old["vel"][0]) / dt * 0.5 + old["acc"][0] * 0.5, (vel[1] - old["vel"][1]) / dt * 0.5 + old["acc"][1] * 0.5)
            w = m.get("weapons") or []
            laser = ("laser" in w) if w else bool(old and old["laser"])
            self.trk[m["id"]] = {"pos": m["pos"], "vel": vel, "acc": acc, "t": t, "hp": m.get("hp", 1400), "mhp": m.get("max_hp", 1400),
                                 "laser": laser, "cubes": m.get("cubes", 0)}
            m["acc"] = acc
            dx, dy = pos[0] - m["pos"][0], pos[1] - m["pos"][1]
            d = math.hypot(dx, dy)
            if d > 1e-6:
                vr = (vel[0] * dx + vel[1] * dy) / d
                aimd = abs(wrap(m["aim"][0] - math.atan2(dy, dx)))
                if (vr > 2.5 and d < 250) or (aimd < 0.2 and d < 230):
                    self.host_t[m["id"]] = t
        for i in [i for i, v in self.trk.items() if t - v["t"] > 30]:
            del self.trk[i]
        for s_ in obs.get("sounds") or []:
            if s_.get("bearing") is not None and s_["kind"] != "npc":
                self.snd.append((t - (s_.get("age") or 0.0), s_["bearing"], s_["dist"]))
        self.snd = [x for x in self.snd if t - x[0] < 4][-32:]

    def harmless(self, m, t):
        """A mech that has no laser, few cubes and has not hurt us much: not worth fearing or hiding from."""
        if not P["harm"] or t >= 120:
            return False
        tr = self.trk.get(m["id"])
        return not (tr and tr["laser"]) and m.get("cubes", 0) <= 3 and self.dealt.get(m["id"], 0.0) < 150

    def hunter(self, m, t):
        return t - self.hit_t.get(m["id"], -99) < 6 or t - self.host_t.get(m["id"], -99) < 6

    def hot(self, t):
        return t - self.last_dmg_t < 3 or any(t - v["t"] < 3 and dist2(v["pos"], self.pos) < 200 for v in self.trk.values())

    def wrad(self, kind, base, t, has_laser):
        """Pickup search radius: a laser is worth a long walk when it is quiet, a short one under fire."""
        if P["lseek"] and kind == "laser" and not has_laser:
            return P["lhot"] if self.hot(t) else P["lfar"]
        return base

    def in_play(self, obs, q, d):
        z = obs["zone"]
        if dist2(q, z["center"]) > z["radius"] - 6:
            return False
        out_next = dist2(q, z["next_center"]) - z["next_radius"]
        return not (out_next > -5 and z["shrink_end"] - obs["t"] < 25 + d / 11.7 + max(0.0, out_next) / 11.7)

    def hunt_goal(self, obs, t):
        """Walk toward the best remembered mech (isolated, hurt, close), else toward the last sound."""
        pos = self.pos
        self.hbans = {k: v for k, v in self.hbans.items() if v > t}
        best = None
        for i, v in self.trk.items():
            if t - v["t"] > 30 or i in self.hbans:
                continue
            dt = min(t - v["t"], 1.0)
            q = (v["pos"][0] + v["vel"][0] * dt, v["pos"][1] + v["vel"][1] * dt)
            d = dist2(q, pos)
            if d > 300 or not self.in_play(obs, q, min(d, 200)) or (d < 20 and t - v["t"] > 2):
                continue
            iso = not any(j != i and t - o["t"] < 5 and dist2(o["pos"], q) < 60 for j, o in self.trk.items())
            sc = d + 80 * min(1.0, v["hp"] / max(v["mhp"], 1)) + (t - v["t"]) * 3 - (30 if iso else 0) - (25 if self.hgoal == i else 0)
            if best is None or sc < best[0]:
                best = (sc, i, q)
        if best:
            if dist2(best[2], pos) < 20:
                self.hbans[best[1]] = t + 8
            self.hgoal = best[1]
            return best[2]
        sc_ = self.cfg.get("sound", {})
        near, mid = sc_.get("near", 60), sc_.get("mid", 150)
        for ts, b, dd in sorted(self.snd, key=lambda x: -x[0]):
            r = near * 0.7 if dd == "near" else ((near + mid) * 0.5 if dd == "mid" else mid * 1.3)
            r = min(max(r, 20), 300)
            q = (pos[0] + r * math.cos(b), pos[1] + r * math.sin(b))
            if self.in_play(obs, q, r) and not self.w.blocked_nav(*q):
                self.hgoal = None
                return q
        self.hgoal = None
        return None

    def site_goal(self, obs, t):
        pos = self.pos
        self.sites = [s for s in self.sites if t - s[2] < 40]
        best = None
        for s in self.sites:
            d = dist2(s[:2], pos)
            if d < 5:
                s[2] = -1e9
            elif d < 130 and self.in_play(obs, s[:2], d) and (best is None or d < best[0]):
                best = (d, (s[0], s[1]))
        return best[1] if best else None

    # ---------------------------------------------------------------- main
    def act(self, obs):
        t0 = time.perf_counter()
        me = obs["self"]
        t = obs["t"]
        self.t_now = t
        self.pos = me["pos"]
        pos = self.pos
        act = {"tick": obs["tick"]}
        if obs["offer"]:
            ids = [c["id"] for c in obs["offer"]["choices"]]
            hl_ = "laser" in [(w or {}).get("kind") for w in me["weapons"]]
            up = (UPG4 if hl_ else UPG5) if P["upg"] == 4 else UPG67 if P["upg"] == 3 else (UPG3 if (P["upg"] == 2 and "laser" in [(w or {}).get("kind") for w in me["weapons"]]) else UPG2) if P["upg"] else UPG
            order = sorted(range(len(ids)), key=lambda i: up.index(ids[i]) if ids[i] in up else 99)
            act["pick"] = order[0]
        hp, mhp = me["hp"], me["max_hp"]
        self.visited.add((int((pos[0] - MINX) // 60), int((pos[1] - MINY) // 60)))
        seen_ids = set()
        for p in obs["pickups"]:
            self.pmem[p["id"]] = (p["kind"], p["pos"]); seen_ids.add(p["id"])
        for i, (k_, pp_) in list(self.pmem.items()):
            if i not in seen_ids and dist2(pp_, pos) < 30:
                del self.pmem[i]
        for e in obs["events"]:
            if e["type"] == "kill":
                self.loot_until = t + 10
            if e["type"] == "damaged" and e.get("by") is not None:
                self.last_dmg_t = t
                self.dmg_bearing = e["bearing"]
        zc, zr, edge = self.zone_state(obs)
        mechs = [m for m in obs["mechs"] if m["kind"] == "mech"]
        npcs = [m for m in obs["mechs"] if m["kind"] == "npc"]
        for m in mechs:
            m["d"] = dist2(m["pos"], pos)
        mechs.sort(key=lambda m: m["d"])
        self.track_update(obs, mechs, t)
        close = [m for m in mechs if m["d"] < 140]
        has_laser = any((w or {}).get("kind") == "laser" for w in me["weapons"])
        wide = 200 if (P["lsr"] and has_laser) else 140
        tpool = close if wide == 140 else [m for m in mechs if m["d"] < wide]
        real = [m for m in close if not self.harmless(m, t)]
        wpn = [(w or {}).get("kind") for w in me["weapons"]]
        slot1 = wpn[1] if len(wpn) > 1 else None
        stats = me["stats"]
        gun = me["weapons"][0]
        fire = [False, False]
        aim = None
        wish = (0.0, 0.0)
        boost = False
        mode = "idle"
        hurt_recent = t - self.last_dmg_t < 3

        # target selection
        target = None
        tkind = None
        mz = pos[2] + 7
        for m in tpool[:6]:
            m["los"] = self.w.los((pos[0], pos[1], mz), (m["pos"][0], m["pos"][1], m["pos"][2] + 4.5), 2.5)
        if tpool:
            vis = [m for m in tpool[:6] if m["los"]] or tpool
            if P["nofirst"] and not has_laser:
                # no laser yet: do not start fights; answer only mechs that hit us, come at us or aim at us, or a laserless one that is close
                vis = [m for m in vis if self.hunter(m, t) or (m["d"] < 55 and not self.trk[m["id"]]["laser"])]
            if P["tscore"]:
                for m in vis:
                    ax, ay = pos[0] - m["pos"][0], pos[1] - m["pos"][1]
                    back = abs(wrap(math.atan2(ay, ax) - m["aim"][0])) > math.pi / 2
                    m["sc"] = m.get("hp", 1400) / (1.5 if back else 1.0) + m["d"] * 3
                vis = sorted(vis, key=lambda m: m["sc"])
            if P["eng2"]:
                # giddy practice rule: fight what is near (<60 m), hit us in 6 s, closes in (>3 m/s, <180 m), is weak (<40%, <160 m) or aims at us (<190 m)
                keep_ = []
                for m in vis:
                    ax, ay = pos[0] - m["pos"][0], pos[1] - m["pos"][1]
                    vx_, vy_ = (m.get("vel") or [0, 0, 0])[:2]
                    clos = (vx_ * ax + vy_ * ay) / (math.hypot(ax, ay) or 1)
                    aimed = abs(wrap(math.atan2(ay, ax) - m["aim"][0])) < 0.22
                    if (m["d"] < 60 or t - self.hit_t.get(m["id"], -99) < 6 or (clos > 3 and m["d"] < 180)
                            or (m.get("hp", 1400) < 0.4 * m.get("max_hp", 1400) and m["d"] < 160) or (aimed and m["d"] < 190)
                            or self.late(obs)):
                        keep_.append(m)
                vis = keep_
            if P["engage"]:
                for m in vis:
                    ax, ay = pos[0] - m["pos"][0], pos[1] - m["pos"][1]
                    m["danger"] = m["d"] < 70 or hurt_recent or abs(wrap(math.atan2(ay, ax) - m["aim"][0])) < 0.45
                vis = [m for m in vis if m["danger"]]
            if vis:
                target = vis[0]; tkind = "mech"
                for m in vis:
                    if m["id"] == self.tgt_id and (P["sticky"] or (m["d"] < vis[0]["d"] + 25 if not P["tscore"] else m["sc"] < vis[0]["sc"] + 250)):
                        target = m
                self.tgt_id = target["id"]
        elif not close and hp > 0.6 * mhp and t < P["farm_until"] and self.pick_goal(obs, wpn) is None:
            best = None
            for kind_, lst in (("npc", npcs), ("crate", obs["crates"])):
                for m in lst:
                    d_ = dist2(m["pos"], pos)
                    if d_ < (P["farm_r0"] if (P["farm_r0"] and slot1 is None) else P["farm_r"]) and math.hypot(m["pos"][0] - zc[0], m["pos"][1] - zc[1]) < zr - 30 and (best is None or d_ < best[0]):
                        best = (d_, m, kind_)
            if best:
                target = best[1]; tkind = best[2]
        elif npcs and hp > 0.6 * mhp:
            n = min(npcs, key=lambda m: dist2(m["pos"], pos))
            if dist2(n["pos"], pos) < 70:
                target = n; tkind = "npc"
        outnum = len(real if P["harm"] else close) >= P["outnum"]
        low0 = hp < P["retreat_hp"] * mhp
        retreat_now = None
        if P["lose"]:
            # giddy loss rule: retreat only when really losing the exchange, held 1.5 s; never in the end game
            hf = hp / mhp
            near = sum(1 for m in mechs if m["d"] < 160)
            tf = target.get("hp", 1400) / max(1.0, target.get("max_hp", 1400)) if (target is not None and tkind == "mech") else None
            losing = (near >= 2 and hf < 0.55) or (near >= 3 and hf < 0.8)
            if tf is not None:
                if hf < 0.35 and tf > hf + 0.1:
                    losing = True
                if tf < 0.25 and hf > 0.2 and near <= 2:
                    losing = False
            if losing:
                self.lose_at = t
            retreat_now = t - self.lose_at < 1.5 and not self.late(obs)
            outnum = False; low0 = False
        finish = (P["finish"] and target is not None and tkind == "mech" and not outnum and not self.healing and low0
                  and target.get("hp", 1e9) < 0.6 * hp and t - self.trk[target["id"]]["t"] < 0.3)
        low = (hp < P["retreat_hp"] * mhp and not finish) or (P["hyst"] and self.healing and hp < P["heal_to"] * mhp)
        self.healing = low or (self.healing and hp < P["heal_to"] * mhp)

        goal = None
        circ = P["circ"] and obs["zone"]["radius"] <= 150 and self.circle_forced(obs, me)
        if edge < 12 or circ:
            mode = "zone"
            goal = zc if (edge < 12 or not circ) else obs["zone"]["next_center"]
            if P["endg"]:
                if edge >= 12 or zr > 30:
                    if circ and obs["zone"]["next_radius"] < 30:
                        goal = self.ring_point(obs, obs["zone"]["next_center"], obs["zone"]["next_radius"])
                else:
                    goal = self.ring_point(obs, zc, zr)
            boost = edge < 0 and me["energy"] > 10
        elif target is not None and tkind == "mech" and P["lose"] and retreat_now:
            mode = "retreat"
        elif target is not None and tkind == "mech" and not P["lose"] and (low or outnum or (hurt_recent and target["d"] > 150) or (P["avoid"] and slot1 != "laser" and target["d"] > P["avoid"] and not self.healing_ok)):
            mode = "retreat"
        elif target is not None:
            mode = "fight"
        else:
            mode = "go"

        # ---- laser telegraph: step out of the beam line (or behind cover) while a laser carrier charges at us
        if P["lzdodge"] == 2:
            # house lasers lead our velocity: reverse our lateral velocity late in their charge
            live = set()
            for m in mechs:
                if m.get("charging") and "laser" in (m.get("weapons") or []) and m["d"] < 290 and m.get("los", True):
                    live.add(m["id"])
                    t0 = self.chg.setdefault(m["id"], t)
                    if t - t0 >= P["dtime"] and t < t0 + 0.95 and t >= self.dodge_until:
                        bx, by = m["pos"][0] - pos[0], m["pos"][1] - pos[1]
                        bl = math.hypot(bx, by) or 1
                        px, py = -by / bl, bx / bl
                        vx = vy = 0.0
                        for ht, hx, hy in self.hist:
                            if t - ht >= 0.25:
                                vx, vy = (pos[0] - hx) / (t - ht), (pos[1] - hy) / (t - ht); break
                        lat = vx * px + vy * py
                        sg = -1 if lat > 0.5 else (1 if lat < -0.5 else self.rng.choice((-1, 1)))
                        c = (pos[0] + px * sg * 7, pos[1] + py * sg * 7)
                        if self.w.blocked_nav(*c):
                            sg = -sg
                        self.dodge_dir = (px * sg, py * sg); self.dodge_until = t + 0.4
            self.chg = {k: v for k, v in self.chg.items() if k in live}
        elif P["lzdodge"]:
            for m in mechs:
                if m.get("charging") and "laser" in (m.get("weapons") or []) and m["d"] < 290:
                    ex, ey = pos[0] - m["pos"][0], pos[1] - m["pos"][1]
                    ux, uy = math.cos(m["aim"][0]), math.sin(m["aim"][0])
                    along = ex * ux + ey * uy
                    cross = ex * uy - ey * ux
                    if along > 0 and abs(cross) < 6.5:
                        side = 1 if cross >= 0 else -1
                        best = None
                        eye = (m["pos"][0], m["pos"][1], m["pos"][2] + 8)
                        for sg in (side, -side):
                            px, py = -uy * sg, ux * sg
                            c = (pos[0] + px * 9, pos[1] + py * 9)
                            if self.w.blocked_nav(*c) or self.w.blocked_nav(pos[0] + px * 4.5, pos[1] + py * 4.5):
                                continue
                            hidden = not self.w.los(eye, (c[0], c[1], self.w.ground(*c) + 4.5), 2.5)
                            sc = (2 if hidden else 0) + (1 if sg == side else 0)
                            if best is None or sc > best[0]:
                                best = (sc, (px, py))
                        if best:
                            self.dodge_dir = best[1]; self.dodge_until = t + 0.35
                        break
        # ---- aiming/firing at target
        if target is not None:
            tg = target
            los_ok = tg.get("los", True) if tkind == "mech" else self.w.los((pos[0], pos[1], mz), (tg["pos"][0], tg["pos"][1], tg["pos"][2] + 1.5), 2.5)
            gs = gun["stats"] if gun else {"speed": 252, "range": 230}
            yaw, pit, d = self.solve_lead(me, tg, gs["speed"], 4.5 if tkind == "mech" else (1.75 if tkind == "npc" else 1.2))
            aim = [yaw, pit]
            err = abs(wrap(yaw - me["aim"][0]))
            perr = abs(pit - me["aim"][1])
            tol = P["tol"] * max(0.035, math.atan2(2.0, max(d, 10)))
            self.dd = (los_ok, round(err, 3), round(perr, 3), round(tol, 3), gun['ammo'], gun['reload'])
            if gun and los_ok and d < gs["range"] - 10 and (d < P["fire_max"] or hurt_recent or tkind != "mech") and gun["ammo"] > 0 and gun["reload"] == 0 and err < tol and perr < 0.1:
                fire[0] = True
            if tkind == "mech" and slot1 == "lightning" and d < 42 and err < 0.4 and me["energy"] > 8:
                fire[1] = True
            if tkind == "mech" and slot1 == "laser" and los_ok and d < 250:
                lw = me["weapons"][1]
                lyaw, lpit, _ = self.solve_lead(me, tg, 1e9)
                ready = lw["cooldown"] <= 0.05 and lw.get("charge", 0) >= 0
                if ready:
                    aim = [lyaw, lpit]
                    lerr = abs(wrap(lyaw - me["aim"][0]))
                    ch = lw.get("charge", 0)
                    ls_ = lw["stats"]
                    mf_ = ls_.get("min_frac", 0.35)
                    part = ls_.get("damage", 315) * (mf_ + (1 - mf_) * min(1.0, ch / max(ls_.get("charge", 0.75), 0.05)))
                    lethal = P["lethal"] and ch >= ls_.get("min_charge", 0.15) and part >= target.get("hp", 1e9) * 1.05
                    if (ch >= ls_.get("charge", 0.75) - 0.02 or lethal) and lerr < tol:
                        fire[1] = False  # release
                    elif lerr < 0.25 or ch > 0:
                        fire[1] = True
                    fire[0] = False if (ch > 0 and not P["gunlaser"]) else fire[0]
            if tkind == "mech" and slot1 == "mortar" and not los_ok and 45 < d < 215 and not self.exposed_to_known(close):
                ma = self.solve_mortar(me, tg)
                if ma:
                    aim = list(ma)
                    if abs(wrap(ma[0] - me["aim"][0])) < 0.05 and abs(ma[1] - me["aim"][1]) < 0.04:
                        fire[1] = True
            if gun and not fire[0] and (gun["ammo"] == 0 or (P["upg"] and not los_ok and gun["ammo"] < gun["stats"]["mag"] * 0.6 and gun["reload"] == 0)):
                act["reload"] = True
        else:
            if gun and gun["ammo"] < gun["stats"]["mag"] * 0.5 and gun["reload"] == 0:
                act["reload"] = True
            if mode == "go" or mode == "idle":
                self.scan = wrap(self.scan + P["scan"])
                if self.dmg_bearing is not None and t - self.last_dmg_t < 4:
                    aim = [self.dmg_bearing, 0.0]
                else:
                    sn = [s for s in obs["sounds"] if s["kind"] in ("gun", "step", "boost", "laser", "mortar", "lightning")]
                    aim = [wrap(sn[0]["bearing"]), 0.0] if sn and sn[0]["dist"] != "far" else [self.scan, 0.0]

        # ---- movement
        dodge = self.incoming(obs)
        if mode == "zone":
            (wx, wy), _ = self.follow(zc, t)
            wish = (wx, wy)
        elif mode == "retreat":
            if t > self.strafe_until:
                self.strafe = self.rng.choice((-1, 1))
                self.strafe_until = t + self.rng.uniform(P['flip_lo'], P['flip_hi'])
            if t - self.cover_t > 0.6 or self.cover is None:
                b = self.pick_cover(close, zc, zr)
                self.cover = b[1] if b else None
                self.cover_t = t
            if self.cover and dist2(self.cover, pos) > 3:
                (wx, wy), _ = self.follow(self.cover, t)
                wish = (wx, wy)
            else:
                # at cover: back away from nearest enemy slowly while facing it
                e = target["pos"]
                dx, dy = pos[0] - e[0], pos[1] - e[1]
                dd = math.hypot(dx, dy) or 1
                wish = (dx / dd * 0.3, dy / dd * 0.3)
            if P["retreat_jit"] and target.get("los"):
                ex, ey = target["pos"][0] - pos[0], target["pos"][1] - pos[1]
                el = math.hypot(ex, ey) or 1
                wish = (wish[0] - ey / el * self.strafe * P["retreat_jit"], wish[1] + ex / el * self.strafe * P["retreat_jit"])
            boost = low and target["d"] < 80 and me["energy"] > 40
        elif mode == "fight":
            e = target["pos"]
            dx, dy = e[0] - pos[0], e[1] - pos[1]
            dd = math.hypot(dx, dy) or 1
            ux, uy = dx / dd, dy / dd
            if t > self.strafe_until:
                self.strafe = self.rng.choice((-1, 1))
                self.strafe_until = t + self.rng.uniform(P['flip_lo'], P['flip_hi'])
            rad = 0.0
            lo_r, hi_r = (35, 55) if tkind != "mech" else (P["lo"], P["hi"])
            if slot1 == "lightning" and tkind == "mech" and P["lband"]:
                lo_r, hi_r = 20, 38
            if tkind == "mech" and P["lsr"] and slot1 == "laser":
                lo_r, hi_r = 125, 155  # laser needs no lead: stand off where guns miss
            elif tkind == "mech" and P["harm"] and not has_laser and self.harmless(target, t):
                lo_r, hi_r = 40, 55
            if dd < lo_r:
                rad = P["near_lo"]
            elif dd > hi_r:
                rad = 0.6
            wish = (ux * rad - uy * self.strafe, uy * rad + ux * self.strafe)
            if edge < 40:  # bias inward near the zone edge
                ix, iy = zc[0] - pos[0], zc[1] - pos[1]
                il = math.hypot(ix, iy) or 1
                wish = (wish[0] + ix / il * 0.8, wish[1] + iy / il * 0.8)
            if P["fm"] and tkind == "mech":
                fin_ = (P["fin"] and target.get("hp", 1400) < 0.38 * target.get("max_hp", 1400) and hp > 0.42 * mhp
                        and sum(1 for m in mechs if m["d"] < 150) <= 2)
                if fin_:
                    lo_r, hi_r = 15, 25
                wish = self.fight_move(obs, me, target, lo_r, hi_r, slot1, mechs, t, t0)
        else:
            if P["stay"] and edge > 45 and t < P["stay"]:
                if self.stay_spot is None:
                    self.stay_spot = self.cover_spot((pos[0], pos[1]), zc, zr)
                home = self.stay_spot
            else:
                home = self.pick_home(obs)
            # opportunistic loot when nothing hostile is near
            tgt = None
            isdrop = False
            if P["eco"]:
                ch = self.econ_choice(obs, me, wpn, close)
                if ch:
                    tgt = ch[1]["pos"][:2]; isdrop = ch[0] == "drop"
            elif P["drop_r"] and hp > 0.5 * mhp and not close:
                tgt = self.drop_goal(obs)
                isdrop = tgt is not None
            if tgt is None and not P["eco"] and hp > 0.6 * mhp and (not close or (P["killloot"] and t < self.loot_until)):
                tgt = self.loot_goal(obs, me, wpn)
            if tgt is None and P["explore"] and t < P["explore"] and not (len(wpn) > 1 and wpn[1] is not None) and hp > 0.6 * mhp and not close:
                if self.xgoal is None or t - self.xt > 25 or dist2(self.xgoal, pos) < 20:
                    nz = obs["zone"]
                    cand = []
                    if P["xcells"]:
                        for ci_ in range(int((pos[0] - MINX) // 60) - 4, int((pos[0] - MINX) // 60) + 5):
                            for cj_ in range(int((pos[1] - MINY) // 60) - 4, int((pos[1] - MINY) // 60) + 5):
                                if (ci_, cj_) in self.visited:
                                    continue
                                c_ = (MINX + (ci_ + 0.5) * 60, MINY + (cj_ + 0.5) * 60)
                                dd_ = dist2(c_, pos)
                                if 60 < dd_ < 280 and not self.w.blocked_nav(*c_) and math.hypot(c_[0] - nz["center"][0], c_[1] - nz["center"][1]) < nz["radius"] - 60:
                                    cand.append((dd_, c_))
                        if cand:
                            self.xgoal = min(cand)[1]; self.xt = t
                    if not (P["xcells"] and cand):
                        for _ in range(12):
                            a_ = self.rng.uniform(0, TAU); r_ = self.rng.uniform(120, 260)
                            c_ = (pos[0] + r_ * math.cos(a_), pos[1] + r_ * math.sin(a_))
                            if not self.w.blocked_nav(*c_) and math.hypot(c_[0] - nz["center"][0], c_[1] - nz["center"][1]) < nz["radius"] - 60:
                                self.xgoal = c_; self.xt = t; break
                if self.xgoal is not None:
                    tgt = self.xgoal
            if tgt is None and P["site"] and not close and me["cubes"] < 10 and hp > 0.5 * mhp:
                tgt = self.site_goal(obs, t)
            hunting = False
            if P["hunt"] and has_laser and hp >= 0.5 * mhp and not isdrop and not (tgt is not None and dist2(tgt, pos) < 40):
                hg = self.hunt_goal(obs, t)
                if hg:
                    tgt = hg; hunting = True
            g = tgt or home
            (wx, wy), d = self.follow(g, t)
            if tgt is None and d < 6:
                wish = (0.0, 0.0)
            else:
                wish = (wx, wy)
            if hunting and me["grounded"] and me["energy"] > 0.5 * me.get("max_energy", 100):
                boost = True
            if P["sneak"] and mechs and not isdrop:
                wish = (wish[0] * 0.42, wish[1] * 0.42)
            if tgt is not None and dist2(tgt, pos) < 3.5:
                for p in obs["pickups"]:
                    if dist2(p["pos"], pos) < 3.8:
                        act["take"] = p["id"]
                        if len(wpn) > 1 and wpn[1] is not None:
                            act["drop"] = 1
            # a quiet walk once close to home
        # npc drones: keep away unless we are hunting them
        for n in npcs:
            nd = dist2(n["pos"], pos)
            if nd < 50 and target is not n and mode not in ("fight",):
                wish = (wish[0] + (pos[0] - n["pos"][0]) / nd * 0.8, wish[1] + (pos[1] - n["pos"][1]) / nd * 0.8)
        dflag = P["dboost"] and (abs(dodge[0]) + abs(dodge[1])) > 0.1
        wish = (wish[0] + dodge[0] * P["dwt"], wish[1] + dodge[1] * P["dwt"])

        if P["eco"] and mode in ("fight", "retreat"):
            ch = self.econ_choice(obs, me, wpn, close)
            dlim = 70
            if P["dpf"] and ch and ch[0] == "drop":
                tr_ = self.trk.get(target["id"]) if (target is not None and tkind == "mech") else None
                strong = bool(tr_ and (tr_["laser"] or self.dealt.get(target["id"], 0.0) >= 150))
                if mode == "retreat":
                    dlim = 150 if hp < 0.6 * mhp else 70
                elif not strong:
                    dlim = 350
            if ch and ch[0] != "crate" and (ch[2] < 24 or (ch[0] == "drop" and ch[2] < dlim)) and hp > 0.25 * mhp:
                (ex_, ey_), _d = self.follow(ch[1]["pos"][:2], t)
                wish = (ex_, ey_)
                if ch[0] == "weapon" and ch[2] < 3.8:
                    act["take"] = ch[1]["id"]
                    if len(wpn) > 1 and wpn[1] is not None:
                        act["drop"] = 1
        if P["grab"] and mode != "zone" and slot1 != "laser" and hp > 0.3 * mhp:
            gg = self.pick_goal(obs, wpn)
            if gg is not None and dist2(gg, pos) < (max(140, P["lhot"] if self.hot(t) else P["lfar"]) if P["lseek"] else 140):
                (gx_, gy_), gd_ = self.follow(gg, t)
                wish = (gx_, gy_)
                if gd_ < 3.8:
                    for p in obs["pickups"]:
                        if dist2(p["pos"], pos) < 3.8 and p["kind"] in PICK_W and p["kind"] not in wpn and (p["kind"] != "mortar" or (P["mort"] and not (len(wpn) > 1 and wpn[1]))) and not (len(wpn) > 1 and wpn[1] == "lightning" and p["kind"] != "laser"):
                            act["take"] = p["id"]
                            if len(wpn) > 1 and wpn[1] is not None:
                                act["drop"] = 1
        if t < self.dodge_until:
            wish = self.dodge_dir
            boost = me["energy"] > 15
        elif dflag and me["energy"] > P["dmin_e"] and mode != "zone":
            boost = True  # the boost is the bullet dodge
        # stuck detection
        self.hist.append((t, pos[0], pos[1]))
        while self.hist and t - self.hist[0][0] > 2.0:
            self.hist.pop(0)
        wl = math.hypot(*wish)
        if t < self.stuck_until:
            wish = self.stuck_dir
            act["jump"] = True
            boost = me["energy"] > 20
        elif wl > 0.5 and len(self.hist) > 15 and me["grounded"]:
            h0 = self.hist[0]
            if math.hypot(pos[0] - h0[1], pos[1] - h0[2]) < 2.0:
                a = self.rng.uniform(0, TAU)
                self.stuck_dir = (math.cos(a), math.sin(a))
                self.stuck_until = t + 1.0
                self.path = []; self.path_goal = None
        if wl > 1:
            wish = (wish[0] / wl, wish[1] / wl)
        if t >= self.stuck_until:
            wish = self.steer(*wish)

        if P["scan0"] and t < P["scan0_t"] and not target:
            aim = [wrap(me["aim"][0] + 0.8), 0.0]
        if P["sweep"] and not has_laser and target is None and mode != "zone":
            yaw = me["aim"][0]
            calm = t - self.last_dmg_t > 3 and not any(m["d"] < 150 and not self.harmless(m, t) for m in mechs)
            if self.sweep_on:
                if not calm:
                    self.sweep_on = False
                else:
                    self.sweep_acc += abs(wrap(yaw - self.sweep_yaw)); self.sweep_yaw = yaw
                    if self.sweep_acc >= TAU - 0.4:
                        self.sweep_on = False; self.last_sweep = t
                    else:
                        aim = [wrap(yaw + 1.5), 0.0]
            elif calm and (t - self.last_sweep > 6 or (self.last_sweep <= 0 and t < 3)):
                self.sweep_on = True; self.sweep_acc = 0.0; self.sweep_yaw = yaw; self.last_sweep = t
                self.jet_on = bool(P["jet"]) and me["energy"] >= 90
                aim = [wrap(yaw + 1.5), 0.0]
            if self.jet_on:
                if not self.sweep_on or me["energy"] < 35:
                    self.jet_on = False
                else:
                    act["jump"] = bool(me["grounded"]); boost = boost or not me["grounded"]
        elif self.sweep_on:
            self.sweep_on = False; self.jet_on = False
        if aim is None:
            aim = [math.atan2(wish[1], wish[0]) if wl > 0.1 else me["aim"][0], 0.0]
        self.dbg = (mode, None if target is None else round(dist2(target['pos'], pos)), fire[0], getattr(self, 'dd', None))
        act.update(move=[round(wish[0], 3), round(wish[1], 3)], boost=boost, aim=[aim[0], aim[1]], fire=fire)
        # background planning with whatever budget remains
        self.run_search(t, max(0.003, 0.012 - (time.perf_counter() - t0)))
        return act

    def econ_choice(self, obs, me, wpn, close):
        """Value cubes, weapons, crates and supply drops; return (kind, item, dist) of the best, or None."""
        now = obs["t"]; pos = me["pos"]; zone = obs["zone"]
        for kind, lst in (("cube", obs["cubes"]), ("weapon", obs["pickups"]), ("crate", obs["crates"])):
            for it in lst:
                self.emem[it["id"]] = (kind, it, now)
        for e in obs["events"]:
            if e["type"] == "took" or (e["type"] == "take_failed" and e.get("reason") == "gone"):
                self.emem.pop(e.get("id"), None)
        nearest = min((m["d"] for m in close if not self.harmless(m, now)), default=999)
        have1 = len(wpn) > 1 and wpn[1] is not None
        cands = []
        for iid, (kind, it, seen) in list(self.emem.items()):
            d = dist2(pos, it["pos"])
            if now - seen > 18 or (now > seen + 0.4 and d < 8):
                del self.emem[iid]; continue
            if kind == "weapon":
                if it["kind"] not in PICK_W2 or it["kind"] in wpn or d > self.wrad(it["kind"], P["lcap"], now, False) or (it["kind"] == "mortar" and not P["mort"]):
                    continue
                if have1 and not ((wpn[1] == "lightning" and it["kind"] == "laser") or (wpn[1] == "mortar" and it["kind"] != "mortar")):
                    continue
                value = {"laser": 150, "lightning": 138}.get(it["kind"], 110)
            elif kind == "cube":
                value = 135 if me["cubes"] < 10 else 62
            else:
                if nearest < 90 or me["cubes"] >= 10 or d > 62 or (have1 and me["cubes"] >= 6):
                    continue
                value = 71 + (30 if not have1 else 0)
            cands.append((value, kind, it, d, now - seen))
        for dr in obs.get("drops", []):
            d = dist2(pos, dr["pos"])
            if dr["lands_in"] > d / 11.7 + 9 or dr.get("holder") not in (None, me["id"]):
                continue
            cands.append((188 + 150 * (1 - me["hp"] / me["max_hp"]) + 20 * dr.get("progress", 0), "drop", dr, d, 0))
        best, choice = 0, None
        for value, kind, it, d, age in cands:
            if now < self.eignored.get(it["id"], -1):
                continue
            tp = it["pos"]
            outside = max(0, math.hypot(tp[0] - zone["next_center"][0], tp[1] - zone["next_center"][1]) - zone["next_radius"])
            if math.hypot(tp[0] - zone["center"][0], tp[1] - zone["center"][1]) > zone["radius"] - 5 or (
                    outside > 0 and now + (d + outside) / 11.7 + 7 > zone["shrink_end"]):
                continue
            value -= d + outside * 0.55 + age * 1.5
            if self.echosen == it["id"]:
                value += 14
            if nearest < 80 and kind != "drop" and d > 24:
                value -= 65
            if value > best:
                best, choice = value, (kind, it, d)
        cid = choice[1]["id"] if choice else None
        if cid != self.echosen:
            self.echosen, self.estart = cid, now
        elif cid is not None and now - self.estart > 19 and choice[0] != "drop":
            self.eignored[cid] = now + 30; self.echosen = None
            return None
        return choice

    def circle_forced(self, obs, me):
        """True when the shrinking circle must take priority over detours and duels (final phases)."""
        z = obs["zone"]; r = z["radius"]; pos = me["pos"]
        if not me["in_zone"]:
            return True
        if r <= 13:
            return True
        if r > 150 and z["dps"] < 60:
            return False
        end = z["next_center"]; er = z["next_radius"]; c = z["center"]
        out = max(0, dist2(pos, end) - er)
        horizon = 3.0 + 1.5 * out / 11.7
        wait = max(0, z["shrink_start"] - obs["t"])
        rem = max(0.01, z["shrink_end"] - max(obs["t"], z["shrink_start"]))
        f = max(0, min(1, (horizon - wait) / rem))
        fx = c[0] + f * (end[0] - c[0]); fy = c[1] + f * (end[1] - c[1])
        fr = r + f * (er - r)
        return dist2(pos, (fx, fy)) >= max(0, fr - min(10, max(2, fr * 0.35)))

    def pick_goal(self, obs, wpn):
        """Position of the best weapon pickup worth walking to, or None."""
        if (len(wpn) > 1 and wpn[1] is not None) and not ((P["swap"] and wpn[1] == "lightning") or wpn[1] == "mortar"):
            return None
        pos = self.pos
        zc, zr, edge = self.zone_state(obs)
        best = None
        plist = [{"id": i, "kind": k_, "pos": pp_} for i, (k_, pp_) in self.pmem.items()] if P["pmem"] else obs["pickups"]
        for p in plist:
            if p["kind"] not in PICK_W or p["kind"] in wpn or (p["kind"] == "mortar" and not P["mort"]) or (len(wpn) > 1 and wpn[1] == "lightning" and p["kind"] != "laser"):
                continue
            d = dist2(p["pos"], pos)
            if d > self.wrad(p["kind"], P["pick_r0"] if (P["pick_r0"] and self.t_now < 60) else P["pick_r"], self.t_now, False) or math.hypot(p["pos"][0] - zc[0], p["pos"][1] - zc[1]) > zr - 25:
                continue
            sc = (PICK_W2 if P["laser1"] else PICK_W)[p["kind"]] * 40 - d
            if best is None or sc > best[0]:
                best = (sc, p["pos"])
        return best[1][:2] if best else None

    def drop_goal(self, obs):
        zc, zr, edge = self.zone_state(obs)
        for d in obs["drops"]:
            if d.get("holder") not in (None, obs["self"]["id"]):
                continue
            dd = dist2(d["pos"], self.pos)
            if dd > P["drop_r"] or math.hypot(d["pos"][0] - zc[0], d["pos"][1] - zc[1]) > zr - 20:
                continue
            if dd / 11.0 + 8 >= d["lands_in"]:
                return d["pos"][:2]
        return None

    def loot_goal(self, obs, me, wpn):
        g = self.pick_goal(obs, wpn)
        if g:
            return g
        pos = self.pos
        zc, zr, edge = self.zone_state(obs)
        for c in obs["cubes"]:
            if dist2(c["pos"], pos) < (max(P["cube_r"], 90) if (P["kcube"] and self.t_now < self.loot_until) else P["cube_r"]) and math.hypot(c["pos"][0] - zc[0], c["pos"][1] - zc[1]) < zr - 25:
                return c["pos"][:2]
        return None


if __name__ == "__main__":
    mf.run(Bot)
