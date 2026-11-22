"""Decision core: Python port of the MIT-licensed giddygiddyboy Go pilot (see LICENSE), with the rogue_agi parameter set
(EndGhost 140). One mode per tick: escape / unstuck / dodge-shell / zone / retreat+cover / hide / fight / zone / loot / home.
Parameters that are zero in both parameter sets (explore, econ, odds, shy, ...) and the never-picked mortar are left out.
Per-tick work is bounded by a soft deadline (cover search, home scoring and path search degrade or spread over ticks)."""
import json
import math
import os
import random
import time
from types import SimpleNamespace

from world import World, NNX, NNY

# survival-layer switches (A/B with MECH_FLAGS='{"uf":0}'): uf = cover and keep moving under fire in every mode, sg = skip contested
# weapons and give up slow ones, bub = stay out of other mechs' fights, home2 = off-centre hiding spots that respect the shrinking
# circle, end = heal before looting and pick on clearly weaker mechs in the end game, drop = supply drops when young, near and safe
FLAGS = dict(uf=0, sg=0, bub=0, home2=0, end=0, drop=0, pthr=2)
FLAGS.update(json.loads(os.environ.get("MECH_FLAGS", "{}")))
F = FLAGS
LAT = 0.075
hyp = math.hypot
PI = math.pi
SOFT = 0.034  # soft deadline per decision, seconds
SALT = int(os.environ.get("MECH_SALT", 0))  # mixed into the RNG seed; change it to re-roll strafing and home choices

BASE = dict(RetreatHP=0.38, RecoverHP=0.8, GunRange=85, LightRange=28, LaserRange=85, EngageDist=60, FarmNPC=True, Ghost=True,
            HomeExpo=120, LootRadius=90, CrateUntil=150, Dodge=True, NoMortar=True, Peek=True, LaserFirst=True, CrowdHide=True,
            Practice=True, EndGhost=140, Stick=0, HPw=0, LowBonus=0, MaxTarget=0, LoseRule=False, FightLoot=0, FarmHP=0,
            LaserSeek=0, LaserUp=False, Lethal=False, Finish=0, LethalK=0)
SQUAD = dict(Ghost=False, Peek=False, LaserFirst=True, LaserRange=120, LootRadius=150, CrateUntil=1e9, Stick=200, HPw=0.18,
             LowBonus=200, MaxTarget=240, LoseRule=True, FightLoot=70, FarmHP=0.5, LaserSeek=240, LaserUp=True, Lethal=True,
             Finish=20, LethalK=0.7)

_TAIL = ["gun_mag", "gun_velocity", "gyros", "lightning_chain", "reactor", "capacitor", "mortar_reload", "mortar_splash",
         "mortar_velocity", "sensors", "jets", "laser_pierce"]
ORDER = ["plating", "armor", "gun_damage", "nanites", "gun_rate", "lightning_efficiency", "lightning_range", "laser_damage",
         "servos", "laser_charge"] + _TAIL
ORDER_LASER = ["plating", "armor", "laser_damage", "laser_charge", "nanites", "gun_damage", "gun_rate", "lightning_efficiency",
               "lightning_range", "servos"] + _TAIL
WEAPON_RANK = {"lightning": 3, "laser": 2, "mortar": 1}
RING_Q = [(math.cos(q * PI / 8) * 7, math.sin(q * PI / 8) * 7) for q in range(0, 16, 2)]
CAND = [(math.cos(k * PI / 8), math.sin(k * PI / 8)) for k in range(16)]


def ang_diff(a, b):
    d = math.fmod(a - b, 2 * PI)
    return d - 2 * PI if d > PI else d + 2 * PI if d < -PI else d


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class Track:
    __slots__ = ("id", "kind", "pos", "vel", "aim", "hp", "max_hp", "cubes", "weapons", "charging", "seen", "hit_me", "dist",
                 "los", "closing", "engage")

    def __init__(self, m=None):
        m = m or {}
        self.id, self.kind = m.get("id", -1), m.get("kind", "mech")
        self.pos, self.vel, self.aim = m.get("pos", (0, 0, 0)), m.get("vel", (0, 0, 0)), m.get("aim", (0, 0))
        self.hp, self.max_hp, self.cubes = m.get("hp", 0), m.get("max_hp", 1), m.get("cubes", 0)
        self.weapons, self.charging = m.get("weapons") or [], bool(m.get("charging"))
        self.seen, self.hit_me, self.dist, self.los, self.closing, self.engage = 0.0, -100.0, 0.0, False, 0.0, False

    def load(self, m):
        self.kind, self.pos, self.vel, self.aim = m["kind"], m["pos"], m["vel"], m["aim"]
        self.hp, self.max_hp, self.cubes = m.get("hp", 0), m.get("max_hp", 1), m.get("cubes", 0)
        self.weapons, self.charging = m.get("weapons") or [], bool(m.get("charging"))


class Known:
    __slots__ = ("id", "kind", "pos", "hp", "seen")


class Bot:
    name = "giddy-py"

    def __init__(self, hello):
        self.cfg = hello["config"]
        self.slot = hello.get("slot", 0)
        seed = hello.get("seed", 0) or self.slot + 1
        self.rng = random.Random(seed + SALT)
        self.W = World(hello["map"]["dir"])
        self.W.build_nav(hello["map"]["dir"])
        if hasattr(self.W, "exact"):
            self.W.clear = self.W.clear_exact
        else:
            self.W.walk_at = self.W.ground
        self.W.main_near(0)  # prebuild the escape lookup so no tick pays for it
        self.P = SimpleNamespace(**BASE)
        self.base = None
        self.tracks, self.npcs, self.crates, self.cubes, self.pickups, self.bad = {}, {}, {}, {}, {}, {}
        self.now = 0.0
        self.path, self.path_goal, self.path_at, self.path_i, self.srch = None, (0, 0), 0.0, 0, None
        self.strafe, self.strafe_until = 1.0, 0.0
        self.retreat, self.cover_at, self.cover_goal, self.cover_ok = False, 0.0, (0, 0), False
        self.last_hurt, self.hurt_bearing = -100.0, 0.0
        self.loot_id, self.loot_since = -1, 0.0
        self.hold, self.hold_start = (0, 0, 0), -1
        self.home_xy, self.home_at, self.home_set, self.hpend, self.hbest, self.hout = (0, 0), 0.0, False, None, None, False
        self.expo = {}
        self.stuck_since, self.unstuck_until, self.unstuck_dir = -1.0, 0.0, (0, 0)
        self.last_threat = -100.0
        self.last_aim = (0.0, 0.0)
        self.esc_launch = self.esc_land = (0, 0)
        self.esc_at, self.esc_ok, self.esc_flying, self.esc_try = 0.0, False, False, -100.0
        self.end_ghost = self.prac_on = self.not_practice = False
        self.npc_hurt, self.npc_bearing = 0.0, 0.0
        self.start_alive, self.kills, self.other_dead = 0, 0, 0
        self.stick_id, self.stick_at, self.lose_at = 0, 0.0, 0.0
        self.t0 = 0.0
        self.mode = ""
        self.fights = []
        self.csearch, self.cs_best, self.cs_touch, self.cs_deadline = None, (-1, math.inf), 0.0, 0.0
        self.trunc = {"cover": 0, "cover_n": 0, "fight": 0, "fight_n": 0, "astar_ticks": 0, "home_ticks": 0, "slow": 0, "n": 0}

    # ------------------------------------------------------------------ entry
    def act(self, o):
        self.t0 = time.perf_counter()
        act = {}
        try:
            self.step(o, act)
        except Exception:
            import traceback
            traceback.print_exc()
            act = {"aim": list(self.last_aim)}
        self.trunc["n"] += 1
        if time.perf_counter() - self.t0 > 0.03:
            self.trunc["slow"] += 1
        a = act.get("aim")
        if a is not None:
            if all(math.isfinite(v) for v in a):
                self.last_aim = tuple(a)
            else:
                del act["aim"]
        mv = act.get("move")
        if mv is not None and not all(math.isfinite(v) for v in mv):
            act["move"] = [0.0, 0.0]
        return act

    def left(self):
        return SOFT - (time.perf_counter() - self.t0)

    # ------------------------------------------------------------------ geometry helpers
    def scale_of(self, cubes):
        return 1 + self.cfg["cubes"]["size_per_cube"] * cubes

    def center(self, t):
        h = self.cfg["npc"]["height"] if t.kind == "npc" else self.cfg["mech"]["height"] * self.scale_of(t.cubes)
        return (t.pos[0], t.pos[1], t.pos[2] + h * 0.5)

    def eye(self, t):
        return (t.pos[0], t.pos[1], t.pos[2] + self.cfg["mech"]["eye_height"] * self.scale_of(t.cubes))

    def clear(self, a, b):
        return self.W.clear(a[0], a[1], a[2], b[0], b[1], b[2])

    # ------------------------------------------------------------------ observation / bookkeeping
    def observe(self, o):
        me = o["self"]
        now = o["t"]
        pos = me["pos"]
        st = me["stats"]
        eye = (pos[0], pos[1], pos[2] + st["eye_height"])
        muzzle = (pos[0], pos[1], pos[2] + st["muzzle_height"])
        for m in o["mechs"]:
            tab = self.npcs if m["kind"] == "npc" else self.tracks
            t = tab.get(m["id"])
            if t is None:
                t = tab[m["id"]] = Track(m)
            t.load(m)
            t.seen = now
            dx, dy = m["pos"][0] - pos[0], m["pos"][1] - pos[1]
            t.dist = hyp(dx, dy)
            t.los = self.clear(muzzle, self.center(t))
            if not t.los and m["kind"] == "mech":
                h = self.cfg["mech"]["height"] * self.scale_of(t.cubes)
                t.los = self.clear(muzzle, (m["pos"][0], m["pos"][1], m["pos"][2] + h * 0.85))
            if t.dist > 1:
                t.closing = -((m["vel"][0] - me["vel"][0]) * dx + (m["vel"][1] - me["vel"][1]) * dy) / t.dist
        for tab, ttl in ((self.tracks, 20), (self.npcs, 120)):
            for i in [i for i, t in tab.items() if now - t.seen > ttl]:
                del tab[i]
        for tab, items in ((self.crates, o["crates"]), (self.cubes, o["cubes"]), (self.pickups, o["pickups"])):
            for it in items:
                k = tab.get(it["id"])
                if k is None:
                    k = tab[it["id"]] = Known()
                    k.id = it["id"]
                k.kind, k.pos, k.hp, k.seen = it.get("kind", ""), it["pos"], it.get("hp", 0), now
            for i in [i for i, k in tab.items() if k.seen != now and hyp(k.pos[0] - pos[0], k.pos[1] - pos[1]) < 40
                      and now - k.seen > 0.35 and self.clear(eye, (k.pos[0], k.pos[1], k.pos[2] + 1.5))]:
                del tab[i]
        for e in o["events"]:
            ty = e.get("type")
            if ty == "damaged":
                by = e.get("by")
                if e.get("weapon") == "npc" and e.get("bearing") is not None:
                    self.npc_hurt, self.npc_bearing = now, e["bearing"]
                if by is not None:
                    self.last_hurt = now
                    if e.get("bearing") is not None:
                        self.hurt_bearing = e["bearing"]
                    for tab in (self.tracks, self.npcs):
                        if by in tab:
                            tab[by].hit_me = now
            elif ty == "kill":
                self.kills += 1
                self.tracks.pop(e.get("target"), None)
        if self.base is None:
            self.base, self.start_alive = self.P, o["alive"]
        self.other_dead = self.other_dead + 1 if self.start_alive - o["alive"] - self.kills > F["pthr"] else 0
        if self.other_dead >= 5:
            self.not_practice = True
        on = self.base.Practice and not self.not_practice and now > 20
        if on != self.prac_on:
            self.prac_on = on
            self.P = SimpleNamespace(**{**vars(self.base), **SQUAD}) if on else self.base
        # end game in practice: a small circle means stop fighting the squad and hide
        if self.prac_on and not self.end_ghost and self.base.EndGhost > 0 and o["zone"]["next_radius"] <= self.base.EndGhost:
            self.end_ghost = True
            self.P = SimpleNamespace(**{**vars(self.base), "Practice": False})
        self.fights = []
        if F["bub"]:
            ts = [t for t in self.tracks.values() if now - t.seen < 3 and t.kind == "mech"]
            for i, a in enumerate(ts):
                for b in ts[i + 1:]:
                    d = hyp(a.pos[0] - b.pos[0], a.pos[1] - b.pos[1])
                    ab = math.atan2(b.pos[1] - a.pos[1], b.pos[0] - a.pos[0])
                    aims = d < 150 and (abs(ang_diff(a.aim[0], ab)) < 0.15 or abs(ang_diff(b.aim[0], ab + PI)) < 0.15)
                    if d < 70 or aims:
                        self.fights.append(((a.pos[0] + b.pos[0]) / 2, (a.pos[1] + b.pos[1]) / 2, d / 2 + 100))
        z = o["zone"]
        if self.hold_start != z["shrink_start"]:
            self.hold_start = z["shrink_start"]
            self.hold = (z["center"][0], z["center"][1], z["radius"])
        elif now <= z["shrink_start"]:
            self.hold = (z["center"][0], z["center"][1], z["radius"])

    # ------------------------------------------------------------------ zone
    def zone_at(self, z, t):
        if t <= z["shrink_start"] or z["shrink_end"] <= z["shrink_start"]:
            return self.hold
        f = clamp((t - z["shrink_start"]) / (z["shrink_end"] - z["shrink_start"]), 0, 1)
        h, n = self.hold, z["next_center"]
        return (h[0] + (n[0] - h[0]) * f, h[1] + (n[1] - h[1]) * f, h[2] + (z["next_radius"] - h[2]) * f)

    def cur_zone(self, z):
        live = (z["center"][0], z["center"][1], z["radius"])
        if self.now <= z["shrink_start"] or abs(live[2] - self.hold[2]) > 0.01 or abs(live[0] - self.hold[0]) > 0.01:
            return live
        return self.zone_at(z, self.now)

    def zone_deadline(self, z, x, y, pad):
        nc = z["next_center"]
        if hyp(x - nc[0], y - nc[1]) <= z["next_radius"] - pad:
            return math.inf
        for k in range(25):
            t = z["shrink_start"] + (z["shrink_end"] - z["shrink_start"]) * k / 24
            c = self.zone_at(z, t)
            if hyp(x - c[0], y - c[1]) > c[2] - pad:
                return t
        return z["shrink_end"]

    def zone_penalty(self, z, x, y):
        c = self.cur_zone(z)
        p = 0.0
        d = hyp(x - c[0], y - c[1]) - c[2]
        if d > -5:
            p += 150 + 5 * (d + 5)
        nc = z["next_center"]
        dn = hyp(x - nc[0], y - nc[1]) - z["next_radius"]
        if dn > -5:
            p += clamp(1 - (z["shrink_start"] - self.now) / 25, 0.1, 1) * (25 + 0.8 * (dn + 5))
        return p

    def zone_goal(self, z, x, y):
        nc, nr = z["next_center"], z["next_radius"]
        if nr <= 30:
            return nc[0], nc[1]
        dx, dy = x - nc[0], y - nc[1]
        d = hyp(dx, dy)
        if d < 1:
            return x, y
        r = max(0, nr - clamp(0.3 * nr, 3, 30) - 5)
        if d <= r:
            return x, y
        return nc[0] + dx / d * r, nc[1] + dy / d * r

    # ------------------------------------------------------------------ navigation
    def _search(self, gx, gy, pos):
        if self.srch is None or hyp(gx - self.srch[0], gy - self.srch[1]) > 5:
            self.srch = (gx, gy, self.W.astar(pos[0], pos[1], gx, gy))
        t_end = time.perf_counter() + max(0.0, min(0.008, self.left() - 0.004))
        while True:
            r = next(self.srch[2], None)
            if r is not None:
                self.path, self.path_goal, self.path_at, self.path_i, self.srch = r[1], (self.srch[0], self.srch[1]), self.now, 0, None
                return
            if time.perf_counter() > t_end:
                self.trunc["astar_ticks"] += 1
                return

    def travel(self, me, gx, gy):
        W = self.W
        x, y = me["pos"][0], me["pos"][1]
        d = hyp(gx - x, gy - y)
        if d < 0.8:
            return 0.0, 0.0

        def unit(tx, ty):
            l = hyp(tx - x, ty - y)
            return ((tx - x) / l, (ty - y) / l) if l > 1e-6 else (0.0, 0.0)

        if me["pos"][2] - W.ground(x, y) > 3 and W.obst(x, y):
            return unit(gx, gy)
        if d < 45 and W.line_walkable(x, y, gx, gy):
            return unit(gx, gy)
        near = self.path is not None and hyp(gx - self.path_goal[0], gy - self.path_goal[1]) <= 5
        if not near or self.now - self.path_at > 2.5:
            self._search(gx, gy, me["pos"])
        if self.path is None or hyp(gx - self.path_goal[0], gy - self.path_goal[1]) > 5 or not self.path:
            return unit(gx, gy)
        path, cp = self.path, W.cell_pos
        bi, bd = self.path_i, math.inf
        for i in range(self.path_i, min(len(path), self.path_i + 10)):
            px, py = cp(path[i])
            dd = hyp(px - x, py - y)
            if dd < bd:
                bi, bd = i, dd
        self.path_i = bi
        if bd > 12:
            self.path = None
            return unit(gx, gy)
        for i in range(min(bi + 14, len(path) - 1), bi, -1):
            px, py = cp(path[i])
            if W.line_walkable(x, y, px, py):
                if i == len(path) - 1 and hyp(px - gx, py - gy) < 4 and W.line_walkable(x, y, gx, gy):
                    return unit(gx, gy)
                return unit(px, py)
        if bi + 1 < len(path):
            return unit(*cp(path[bi + 1]))
        return unit(gx, gy)

    # ------------------------------------------------------------------ escape (pocket cut off from the main walkable area)
    def escape(self, me):
        """Returns (active, mx, my, boost, jump)."""
        W = self.W
        x, y, zf = me["pos"]
        if me["grounded"]:
            if zf - W.ground(x, y) > 3 and W.obst(x, y):
                self.esc_ok = self.esc_flying = False
                return False, 0, 0, False, False
            if not W.off_main(x, y):
                self.esc_ok = self.esc_flying = False
                return False, 0, 0, False, False
            self.esc_flying = False
            if not self.esc_ok or self.now - self.esc_at > 6:
                if self.now - self.esc_try < 3:
                    return False, 0, 0, False, False
                self.esc_try = self.now
                plan = W.escape_plan(x, y)
                self.esc_ok = plan is not None
                self.esc_at = self.now
                if plan:
                    self.esc_launch, self.esc_land = plan
                else:
                    return False, 0, 0, False, False
            if hyp(self.esc_launch[0] - x, self.esc_launch[1] - y) > 2.5:
                mx, my = self.travel(me, *self.esc_launch)
                return True, mx, my, False, False
            if me["energy"] < min(70, me["max_energy"] * 0.7):
                return True, 0.0, 0.0, False, False
            self.esc_flying = True
            jump = True
        elif not self.esc_flying:
            return False, 0, 0, False, False
        else:
            jump = False
        dx, dy = self.esc_land[0] - x, self.esc_land[1] - y
        d = hyp(dx, dy)
        if d < 1.5:
            return True, 0, 0, False, jump
        mx, my = dx / d, dy / d
        need = -1e9
        for t in range(int(min(d, 30)) + 1):
            need = max(need, W.top_at(x + mx * t, y + my * t))
        return True, mx, my, zf < need + 2.5 and me["energy"] > 1, jump

    # ------------------------------------------------------------------ dodging
    def dodge_shell(self, o):
        me = o["self"]
        mort = self.cfg["weapons"]["mortar"]
        g = mort.get("gravity") or 22
        rad = mort.get("splash_radius", 0) + me["stats"]["radius"] + 2
        W = self.W
        for p in o["projectiles"]:
            if p["kind"] != "mortar":
                continue
            px, py, pz = p["pos"]
            vx, vy, vz = p["vel"]
            for k in range(80):
                px += vx * 0.05
                py += vy * 0.05
                pz += vz * 0.05
                vz -= g * 0.05
                if W.solid(px, py, pz) or pz < W.ground(px, py):
                    t = k * 0.05
                    d = hyp(px - me["pos"][0], py - me["pos"][1])
                    if d < rad and t < 2.5:
                        dx, dy = me["pos"][0] - px, me["pos"][1] - py
                        if d < 0.5:
                            dx, dy = -vy, vx
                            d = hyp(dx, dy)
                        dx, dy = dx / d, dy / d
                        for rot in (0, 0.7, -0.7, 1.4, -1.4):
                            c, s = math.cos(rot), math.sin(rot)
                            ex, ey = dx * c - dy * s, dx * s + dy * c
                            if W.line_walkable(me["pos"][0], me["pos"][1], me["pos"][0] + ex * 6, me["pos"][1] + ey * 6):
                                return ex, ey
                    break
        return None

    def dodge(self, o):
        me = o["self"]
        cx, cy, cz = me["pos"][0], me["pos"][1], me["pos"][2] + me["stats"]["height"] * 0.56
        reach = me["stats"]["radius"] + 1.1
        dx = dy = 0.0
        laser = False
        for p in o["projectiles"]:
            if p["kind"] == "mortar":
                continue
            vx, vy, vz = p["vel"]
            v2 = vx * vx + vy * vy + vz * vz
            if v2 < 1:
                continue
            tc = ((cx - p["pos"][0]) * vx + (cy - p["pos"][1]) * vy + (cz - p["pos"][2]) * vz) / v2
            if tc <= 0 or tc > 0.7:
                continue
            mxx, myy = p["pos"][0] + vx * tc - cx, p["pos"][1] + vy * tc - cy
            if hyp(mxx, myy) < reach:
                vh = hyp(vx, vy)
                if vh < 1:
                    continue
                px, py = -vy / vh, vx / vh
                if px * mxx + py * myy > 0:
                    px, py = -px, -py
                dx += px
                dy += py
        for m in o["mechs"]:
            if m["kind"] != "mech" or not m.get("charging"):
                continue
            to_me = math.atan2(cy - m["pos"][1], cx - m["pos"][0])
            if abs(ang_diff(m["aim"][0], to_me)) > 0.15:
                continue
            dx += math.cos(to_me + PI / 2 * self.strafe) * 1.5
            dy += math.sin(to_me + PI / 2 * self.strafe) * 1.5
            laser = True
        l = hyp(dx, dy)
        return (dx / l, dy / l, laser) if l > 0.01 else (0.0, 0.0, False)

    # ------------------------------------------------------------------ fight positioning (peek-fire)
    def pref_range(self, me, t):
        light, laser = self.weapon(me, "lightning"), self.weapon(me, "laser")
        P = self.P
        r = P.GunRange
        if light is not None and me["energy"] > 12:
            r = P.LightRange
        elif laser is not None:
            r = P.LaserRange
        if t.kind == "npc":
            return 58
        if light is None:
            if "lightning" in t.weapons:
                r = max(r, 64)
            if "mortar" in t.weapons:
                r = max(r, 80)
        return r

    def fight_move(self, o, tg, seen, want_los):
        me = o["self"]
        W = self.W
        x, y = me["pos"][0], me["pos"][1]
        P = self.P
        rp = self.pref_range(me, tg)
        bx, by = tg.pos[0] - x, tg.pos[1] - y
        bd = hyp(bx, by) + 1e-9
        ux, uy = bx / bd, by / bd
        if self.now >= self.strafe_until:
            if self.rng.random() < 0.6:
                self.strafe = -self.strafe
            self.strafe_until = self.now + 0.3 + self.rng.random() * 0.7
        sx, sy = -uy * self.strafe, ux * self.strafe
        tc = self.center(tg)
        mh = me["stats"]["muzzle_height"]
        height = me["stats"]["height"]
        cube, cube_d = None, math.inf
        for c in self.cubes.values():
            d = hyp(c.pos[0] - x, c.pos[1] - y)
            if d < 40 and d < cube_d and abs(c.pos[2] - me["pos"][2]) < 4:
                cube, cube_d = c, d
        self.trunc["fight_n"] += 1
        best, bmx, bmy = -math.inf, 0.0, 0.0
        blocked_side = False
        rings = 2 if P.Peek else 1
        z = o["zone"]
        near_seen = [e for e in seen if e is not tg]
        for k in range(-1, 16 * rings):
            cx, cy, dx, dy, r = x, y, 0.0, 0.0, 0.0
            if k >= 0:
                if self.left() < 0.008 and k >= 8:
                    self.trunc["fight"] += 1
                    break
                dx, dy = CAND[k % 16]
                r = 6.0 * (1 + k // 16)
                cx, cy = x + dx * r, y + dy * r
                if not W.line_walkable(x, y, cx, cy) or not W.free(cx, cy):
                    if k < 16 and dx * sx + dy * sy > 0.9:
                        blocked_side = True
                    continue
            cz = W.walk_at(cx, cy)
            d = hyp(tg.pos[0] - cx, tg.pos[1] - cy)
            s = -1.1 * abs(d - rp) - r * 0.4
            los = W.clear(cx, cy, cz + mh, tc[0], tc[1], tc[2])
            if los:
                s += 45 * want_los
            if P.Peek and 0 <= k < 16 and los == (want_los > 0):
                for qx, qy in RING_Q:
                    qx += cx
                    qy += cy
                    if W.free(qx, qy) and W.clear(qx, qy, W.walk_at(qx, qy) + mh, tc[0], tc[1], tc[2]) != los:
                        s += 8
                        break
            bz = cz + height * 0.6
            for e in near_seen:
                if hyp(e.pos[0] - cx, e.pos[1] - cy) > 170:
                    continue
                if self.clear(self.eye(e), (cx, cy, bz)):
                    s -= 30
            s -= self.zone_penalty(z, cx, cy)
            if cube is not None:
                s += 1.5 * (cube_d - hyp(cube.pos[0] - cx, cube.pos[1] - cy))
            cl = W.clr[W.cell(cx, cy)]
            if cl < 4:
                s -= (4 - cl) * 2.5
            if want_los > 0:
                s += 9 * (dx * sx + dy * sy)
            if k < 0:
                s -= 8
                if want_los < 0 and not los:
                    s += 14
            if s > best:
                best, bmx, bmy = s, dx, dy
        if blocked_side:
            self.strafe = -self.strafe
            self.strafe_until = self.now + 0.5
        return bmx, bmy

    # ------------------------------------------------------------------ cover
    def cover_move(self, o, seen):
        """Move toward the nearest cell hidden from every threat; (mx, my) or None. The BFS over candidate cells is resumable:
        when a tick's time slice runs out it continues next tick, heading for the best cell found so far meanwhile."""
        me = o["self"]
        x, y = me["pos"][0], me["pos"][1]
        if self.csearch is not None and self.now - self.cs_touch > 1.0:
            self.csearch = None
        if self.csearch is None:
            if self.cover_ok and self.now - self.cover_at < 0.4:
                return self.travel_cover(me)
            threats = sorted((t for t in seen if hyp(t.pos[0] - x, t.pos[1] - y) < 230),
                             key=lambda t: hyp(t.pos[0] - x, t.pos[1] - y))[:4]
            if not threats:
                return None
            self.cover_at = self.now
            eyes = [self.eye(t) for t in threats]
            height = me["stats"]["height"]
            W = self.W
            if W.cover_hidden(eyes, x, y, W.walk_at(x, y), height) and self.zone_penalty(o["zone"], x, y) == 0:
                self.cover_ok, self.cover_goal = True, (x, y)
                return 0.0, 0.0
            self.csearch = self._cover_search(o["zone"], x, y, threats, eyes, height)
            self.cs_best = (-1, math.inf)
        self.cs_touch = self.now
        t_end = time.perf_counter() + max(0.003, min(0.012, self.left() - 0.006))
        self.cs_deadline = t_end
        for r in self.csearch:
            if r is None:
                self.trunc["cover"] += 1
                break
            self.csearch = None
            self.cover_at = self.now
            if r[0] < 0:
                self.cover_ok = False
                return None
            self.cover_ok, self.cover_goal = True, self.W.cell_pos(r[0])
            return self.travel_cover(me)
        if self.csearch is not None and self.cs_best[0] >= 0:  # provisional: the best cell so far
            self.cover_ok, self.cover_goal = True, self.W.cell_pos(self.cs_best[0])
        return self.travel_cover(me) if self.cover_ok else None

    def _cover_search(self, z, x, y, threats, eyes, height):
        W = self.W
        s = W.nearest_free(W.cell(x, y))
        if s < 0:
            yield (-1, 0)
            return
        self.trunc["cover_n"] += 1
        steps = {s: 0}
        queue = [s]
        best_c, best_s = -1, math.inf
        checked = 0
        blk, surf, hidden = W.blk, W.surf, W.cover_hidden
        for c in queue:
            if checked >= 350:
                break
            st = steps[c]
            if st > 50 or (best_c >= 0 and st * 2 > best_s + 30):
                break
            ci, cj = c % NNX, c // NNX
            if (ci + cj) % 2 == 0 and st > 0:
                cx, cy = W.cell_pos(c)
                checked += 1
                if hidden(eyes, cx, cy, W.walk_at(cx, cy), height):
                    sc = st * 2 + self.zone_penalty(z, cx, cy)
                    near = min(hyp(t.pos[0] - cx, t.pos[1] - cy) for t in threats)
                    sc -= min(near, 80) * 0.25
                    if sc < best_s:
                        best_c, best_s = c, sc
                        self.cs_best = (best_c, best_s)
                if time.perf_counter() > self.cs_deadline:
                    yield None
            for k, (di, dj) in enumerate(NBRS):
                nb = c + dj * NNX + di
                if nb in steps or blk[nb] or surf[nb] - surf[c] > (3.1 if k >= 4 else 2.2):
                    continue
                if k >= 4 and (blk[c + dj * NNX] or blk[c + di]):
                    continue
                steps[nb] = st + 1
                queue.append(nb)
        yield (best_c, best_s)

    def travel_cover(self, me):
        if hyp(self.cover_goal[0] - me["pos"][0], self.cover_goal[1] - me["pos"][1]) < 1.5:
            return 0.0, 0.0
        return self.travel(me, *self.cover_goal)

    def hide_from_bearing(self, o, seen):
        me = o["self"]
        g = Track()
        g.pos = (me["pos"][0] + math.cos(self.hurt_bearing) * 80, me["pos"][1] + math.sin(self.hurt_bearing) * 80, me["pos"][2])
        return self.cover_move(o, [g] + seen)

    # ------------------------------------------------------------------ loot
    def weapon(self, me, kind):
        for w in me["weapons"]:
            if w and w["kind"] == kind:
                return w
        return None

    def want_pickup(self, me, kind):
        ws = me["weapons"]
        if kind == "gun":
            return len(ws) > 0 and ws[0] is None
        if self.weapon(me, kind) is not None or (kind == "mortar" and self.P.NoMortar):
            return False

        def rank(k):
            return 4 if self.P.LaserFirst and k == "laser" else WEAPON_RANK.get(k, 0)

        cur = rank(ws[1]["kind"]) if len(ws) > 1 and ws[1] else 0
        return rank(kind) > cur

    def contested(self, pos, x, y):
        """A mech seen in the last 10 s stands near this pickup or is closer to it than we are."""
        mine = hyp(pos[0] - x, pos[1] - y)
        for t in self.tracks.values():
            if self.now - t.seen < 10:
                d = hyp(t.pos[0] - pos[0], t.pos[1] - pos[1])
                if d < 45 or d < mine * 0.8 + 10:
                    return True
        return False

    def cube_cap(self):
        return self.cfg["cubes"]["stat_cap"]

    def choose_loot(self, o, hp_frac, danger):
        me = o["self"]
        W = self.W
        x, y = me["pos"][0], me["pos"][1]
        z = o["zone"]
        now = self.now
        cur = self.cur_zone(z)
        walk = me["stats"]["walk_speed"]
        P = self.P
        best = [None, 0.0]

        def consider(kind, id_, pos, value):
            if self.bad.get(id_, 0) > now:
                return
            d = hyp(pos[0] - x, pos[1] - y)
            if d > 260 or hyp(pos[0] - cur[0], pos[1] - cur[1]) > cur[2] - 6:
                return
            for fx, fy, fr in self.fights:
                if hyp(pos[0] - fx, pos[1] - fy) < fr:
                    return
            if now + d * 1.4 / walk + 5 > self.zone_deadline(z, pos[0], pos[1], 6):
                return
            if pos[2] - W.ground(pos[0], pos[1]) > 3.2:
                return
            nf = W.nearest_free(W.cell(pos[0], pos[1]))
            if nf < 0 or W.comp[nf] != W.main:
                return
            u = value / (d + 18)
            if id_ == self.loot_id:
                u *= 1.35
            if u > best[1]:
                best[0], best[1] = (kind, id_, pos), u

        for p in self.pickups.values():
            rad = P.LootRadius
            seek = p.kind == "laser" and P.LaserSeek > rad
            if seek:
                rad = P.LaserSeek
            if self.want_pickup(me, p.kind) and hyp(p.pos[0] - x, p.pos[1] - y) < rad:
                if F["sg"] and self.contested(p.pos, x, y):
                    continue
                v = 130.0 if len(me["weapons"]) > 1 and me["weapons"][1] is None else 60.0
                if seek:
                    v = max(v, 150)
                consider("pickup", p.id, p.pos, v)
        cube_v = 25.0 if me["cubes"] >= self.cube_cap() else 75.0
        for c in self.cubes.values():
            if hyp(c.pos[0] - x, c.pos[1] - y) < max(60, P.LootRadius * 0.7):
                consider("cube", c.id, c.pos, cube_v)
        farm_hp = P.FarmHP if P.FarmHP > 0 else 0.7
        if hp_frac > farm_hp and not danger and now < P.CrateUntil:
            for c in self.crates.values():
                if hyp(c.pos[0] - x, c.pos[1] - y) < P.LootRadius:
                    consider("crate", c.id, c.pos, cube_v * 0.6)
            if P.FarmNPC and me["cubes"] < self.cube_cap():
                for n in self.npcs.values():
                    if now - n.seen < 12 and hyp(n.pos[0] - x, n.pos[1] - y) < P.LootRadius:
                        consider("npc", n.id, n.pos, 85)
        if F["drop"] and not danger and hp_frac > 0.5:
            for d in o["drops"]:
                dist = hyp(d["pos"][0] - x, d["pos"][1] - y)
                if dist < 140 and dist / walk < d["lands_in"] + 12 and d["holder"] in (None, me["id"]) and \
                        not any(now - t.seen < 6 and hyp(t.pos[0] - d["pos"][0], t.pos[1] - d["pos"][1]) < 90 for t in self.tracks.values()):
                    consider("drop", d["id"], d["pos"], 260)
        if best[0] is None:
            self.loot_id = -1
            return None
        if best[0][1] != self.loot_id:
            self.loot_id, self.loot_since = best[0][1], now
        elif now - self.loot_since > (14 if F["sg"] and best[0][0] == "pickup" else 30):
            self.bad[best[0][1]] = now + 60
            self.loot_id = -1
            return None
        return best[0]

    # ------------------------------------------------------------------ home (hiding spot), scored a few candidates per tick
    def home_cost(self, o, px, py):
        z = o["zone"]
        crowd = 0.0
        for t in self.tracks.values():
            if self.now - t.seen < 20:
                crowd += max(0, 80 - hyp(t.pos[0] - px, t.pos[1] - py)) / 80
        for d in o["drops"]:
            crowd += max(0, 60 - hyp(d["pos"][0] - px, d["pos"][1] - py)) / 60
        key = (round(px / 2), round(py / 2))
        e = self.expo.get(key)
        if e is None:
            e = self.expo[key] = self.W.exposure(px, py)
        edge = hyp(px - z["next_center"][0], py - z["next_center"][1]) / max(30, z["next_radius"])
        cost = self.P.HomeExpo * e + 0.25 * hyp(px - o["self"]["pos"][0], py - o["self"]["pos"][1]) + 40 * crowd + 10 * edge
        if F["home2"]:
            cost += 30 * max(0.0, 0.4 - edge) / 0.4 + 0.4 * self.zone_penalty(z, px, py)
        for fx, fy, fr in self.fights:
            cost += 60 * max(0.0, 1 - hyp(px - fx, py - fy) / fr)
        return cost

    def home(self, o):
        z = o["zone"]
        W = self.W
        nc, nr = z["next_center"], z["next_radius"]
        if self.hpend is None and (not self.home_set or hyp(self.home_xy[0] - nc[0], self.home_xy[1] - nc[1]) > nr * 0.8
                                   or self.now - self.home_at > 12):
            self.hout = not self.home_set or hyp(self.home_xy[0] - nc[0], self.home_xy[1] - nc[1]) > nr * 0.8
            self.home_at = self.now
            cands = []
            for _ in range(40):
                a = self.rng.random() * 2 * PI
                r = math.sqrt(self.rng.random()) * nr * 0.7
                px, py = nc[0] + math.cos(a) * r, nc[1] + math.sin(a) * r
                if W.in_bounds(px, py) and W.reachable(px, py):
                    cands.append((px, py))
            self.hpend, self.hbest = cands, (math.inf, None)
        if self.hpend is not None:
            self.trunc["home_ticks"] += 1
            while self.hpend and self.left() > 0.012:
                px, py = self.hpend.pop()
                c = self.home_cost(o, px, py)
                if c < self.hbest[0]:
                    self.hbest = (c, (px, py))
            if not self.hpend:
                bc, bxy = self.hbest
                if bxy is not None and (self.hout or bc < self.home_cost(o, *self.home_xy) - 15):
                    self.home_xy, self.home_set = bxy, True
                self.hpend = None
        return self.home_xy[0], self.home_xy[1], self.home_set

    # ------------------------------------------------------------------ aiming helpers
    def lead(self, muzzle, tp, tv, speed, grounded):
        speed = speed if speed > 0 else 252
        t = 0.0
        for _ in range(4):
            tt = LAT + t
            px, py, pz = tp[0] + tv[0] * tt, tp[1] + tv[1] * tt, tp[2]
            if not grounded:
                pz += tv[2] * tt * 0.5
            d = math.sqrt((px - muzzle[0]) ** 2 + (py - muzzle[1]) ** 2 + (pz - muzzle[2]) ** 2)
            t = max(0.0, d - 3) / speed
        return math.atan2(py - muzzle[1], px - muzzle[0]), math.atan2(pz - muzzle[2], hyp(px - muzzle[0], py - muzzle[1]))

    # ------------------------------------------------------------------ the tick
    def step(self, o, act):
        me = o["self"]
        self.now = now = o["t"]
        self.observe(o)
        P = self.P
        st = me["stats"]
        x, y, zf = me["pos"]
        z = o["zone"]
        W = self.W
        hp_frac = me["hp"] / max(1, me["max_hp"])
        energy = me["energy"]
        weapons = me["weapons"]

        if o.get("offer") and o["offer"].get("choices"):
            order = ORDER_LASER if P.LaserUp else ORDER
            best, br = 0, 1 << 30
            for i, c in enumerate(o["offer"]["choices"]):
                r = order.index(c["id"]) if c["id"] in order else len(order)
                if r < br:
                    best, br = i, r
            act["pick"] = best

        gun, light, laser = self.weapon(me, "gun"), self.weapon(me, "lightning"), self.weapon(me, "laser")
        late = (z["next_radius"] <= 70 and not self.end_ghost) or o["alive"] <= 3

        seen = []
        target = hidden = None
        best_score = math.inf
        max_t = P.MaxTarget if P.MaxTarget > 0 else 215.0
        hpw = P.HPw if P.HPw > 0 else 60.0 / 1400
        for t in self.tracks.values():
            if now - t.seen > 2.5:
                continue
            t.engage = False
            seen.append(t)
            if t.seen != now:
                t.los = False
                continue
            to_me = math.atan2(y - t.pos[1], x - t.pos[0])
            aimed = abs(ang_diff(t.aim[0], to_me)) < 0.22
            if P.Ghost:
                t.engage = (t.dist < P.EngageDist or now - t.hit_me < 4 or (aimed and t.charging and t.dist < 120)
                            or (t.hp < 350 and t.dist < 120) or o["alive"] <= 2)
                if F["end"] and self.end_ghost and hp_frac > 0.6 and t.hp < 0.5 * me["hp"] and t.dist < 110:
                    t.engage = True
            else:
                t.engage = (t.dist < P.EngageDist or now - t.hit_me < 6 or late or (t.closing > 3 and t.dist < 180)
                            or (t.hp < 0.4 * t.max_hp and t.dist < 160) or (aimed and t.dist < 190))
            if t.engage and not t.los and t.dist < max_t and (hidden is None or t.dist < hidden.dist):
                hidden = t
            if not t.engage or not t.los or t.dist > max_t:
                continue
            s = t.dist + hpw * t.hp
            if P.LowBonus > 0 and t.hp <= 0.3 * t.max_hp:
                s -= P.LowBonus
            if P.Stick > 0 and t.id == self.stick_id and now - self.stick_at < 2:
                s -= P.Stick
            if now - t.hit_me < 4:
                s -= 40
            if t.closing > 3:
                s -= 15
            if s < best_score:
                best_score, target = s, t
        if target is not None:
            self.stick_id, self.stick_at = target.id, now
        foe = target
        if foe is None and P.Peek:
            foe = hidden
        danger = False
        crowd = 0
        for t in seen:
            if t.engage or now - t.hit_me < 4:
                danger = True
            if t.seen == now and (t.engage or (t.los and t.dist < 150)):
                crowd += 1
        if danger or now - self.last_hurt < 3:
            self.last_threat = now

        finishing = target is not None and target.dist < 120 and (target.hp < 200 or target.hp < me["hp"] * 0.5)
        outgunned = (target is not None and "laser" in target.weapons and laser is None and target.dist > 55 and hp_frac < 0.8)
        swamped = P.CrowdHide and (crowd >= 3 or (crowd == 2 and hp_frac < 0.65) or outgunned)
        if not self.retreat and ((hp_frac < P.RetreatHP and now - self.last_threat < 2) or swamped):
            self.retreat = True
            self.cover_at = -1
        if self.retreat and not swamped and (hp_frac > P.RecoverHP or (now - self.last_threat > 7 and hp_frac > 0.55)):
            self.retreat = False
        if P.LoseRule:
            near = sum(1 for t in seen if t.seen == now and t.dist < 160)
            losing = (near >= 2 and hp_frac < 0.55) or (near >= 3 and hp_frac < 0.8)
            if target is not None:
                tf = target.hp / max(1, target.max_hp)
                if hp_frac < 0.35 and tf > hp_frac + 0.1:
                    losing = True
                if tf < 0.25 and hp_frac > 0.2 and near <= 2:
                    losing = False
            if losing:
                self.lose_at = now
            self.retreat = now - self.lose_at < 1.5 and self.lose_at > 0

        mx = my = 0.0
        boost = False
        mode = "idle"
        farm = None
        farm_pos = None
        zgx, zgy = self.zone_goal(z, x, y)
        zdist = hyp(zgx - x, zgy - y)
        t_need = zdist * 1.45 / st["walk_speed"] + 4
        deadline = self.zone_deadline(z, x, y, 8)
        zone_must = not me["in_zone"] or now + t_need + 3 >= deadline
        zone_should = now + t_need + 14 >= deadline

        esc, emx, emy, eboost, esc_jump = self.escape(me)
        shell = None
        cm = None
        if esc:
            mx, my, boost, mode = emx, emy, eboost, "escape"
        elif now < self.unstuck_until:
            mx, my = self.unstuck_dir
            mode = "unstuck"
        elif (shell := self.dodge_shell(o)) is not None:
            mx, my = shell
            boost = energy > 15
            mode = "dodge"
        elif zone_must and zdist > 2:
            mx, my = self.travel(me, zgx, zgy)
            boost = (not me["in_zone"] or now + t_need >= deadline) and energy > 5
            mode = "zone"
        elif self.retreat and not finishing and not late:
            cm = self.cover_move(o, seen)
            if cm is not None:
                mx, my = cm
                boost = energy > 10 and (mx != 0 or my != 0)
                mode = "cover"
            elif target is not None:
                mx, my = self.fight_move(o, target, seen, 1)
                mode = "cornered"
            else:
                mx, my = self.travel(me, zgx, zgy)
                if self.npc_hurt > 0 and now - self.npc_hurt < 3 and mx == 0 and my == 0:
                    side = -1.0 if math.fmod(now, 3) > 1.5 else 1.0
                    mx, my = -math.sin(self.npc_bearing) * side, math.cos(self.npc_bearing) * side
                mode = "flee"
        elif target is None and now - self.last_hurt < 2.5 and not late and (P.Ghost or F["uf"]) and \
                (cm := self.hide_from_bearing(o, seen)) is not None:
            mx, my = cm
            boost = energy > 30 and (mx != 0 or my != 0)
            mode = "cover"
        elif foe is not None:
            ready = gun is not None and gun["ammo"] > 0 and gun["reload"] <= 0.25 and foe.dist <= gun["stats"]["range"] - 10
            hot = laser is not None and laser.get("charge", 0) >= laser["stats"]["charge"] - 0.3 and \
                foe.dist <= laser["stats"]["range"] - 5
            if hot or (light is not None and foe.dist <= light["stats"]["range"] + 6 and energy >= light["stats"]["energy"] * 3):
                ready = True
            if not P.Peek and (light is not None or laser is not None):
                ready = True
            want_los = 1.0 if ready else -1.0
            if P.Peek and not hot:
                for t in seen:
                    if t.seen == now and t.charging and abs(ang_diff(t.aim[0], math.atan2(y - t.pos[1], x - t.pos[0]))) < 0.3:
                        want_los = -1.6
            if P.Finish > 0 and foe.hp < 0.38 * foe.max_hp and hp_frac > 0.42 and crowd <= 2:
                keep = (P.GunRange, P.LaserRange, P.LightRange)
                P.GunRange = P.LaserRange = P.LightRange = P.Finish
                mx, my = self.fight_move(o, foe, seen, want_los)
                P.GunRange, P.LaserRange, P.LightRange = keep
            else:
                mx, my = self.fight_move(o, foe, seen, want_los)
            if light is not None and foe.dist > 50 and energy > 55 and want_los > 0:
                boost = True
            mode = "fight"
            if P.FightLoot > 0:
                r = P.FightLoot * (0.57 if hp_frac <= 0.5 else 1)
                bc = None
                for c in self.cubes.values():
                    d = hyp(c.pos[0] - x, c.pos[1] - y)
                    if d < r:
                        r, bc = d, c.pos
                if bc is not None:
                    mx, my = self.travel(me, bc[0], bc[1])
                    mode = "fight:cube"
        elif zone_should and zdist > 2:
            mx, my = self.travel(me, zgx, zgy)
            mode = "zone"
        else:
            lc = None if F["end"] and self.end_ghost and hp_frac < 0.92 else self.choose_loot(o, hp_frac, danger)
            if lc is not None:
                kind, lid, lpos = lc
                mode = "loot:" + kind
                if kind == "npc":
                    t = self.npcs.get(lid)
                    if t is not None and now - t.seen < 1 and t.los and t.dist < 75:
                        farm = t
                        mx, my = self.fight_move(o, t, seen, 1)
                    else:
                        mx, my = self.travel(me, lpos[0], lpos[1])
                elif kind == "crate":
                    d = hyp(lpos[0] - x, lpos[1] - y)
                    cp = (lpos[0], lpos[1], lpos[2] + self.cfg["crates"]["height"] * 0.5)
                    if d < 50 and W.clear(x, y, zf + st["muzzle_height"], cp[0], cp[1], cp[2]):
                        farm_pos = cp
                        if d < 12:
                            mx, my = (x - lpos[0]) / max(d, 0.1), (y - lpos[1]) / max(d, 0.1)
                    else:
                        mx, my = self.travel(me, lpos[0], lpos[1])
                else:
                    mx, my = self.travel(me, lpos[0], lpos[1])
                    if kind == "drop" and hyp(lpos[0] - x, lpos[1] - y) < 2.5:
                        mx, my = 0.0, 0.0
            elif zdist > 3 and (not P.Ghost or z["next_radius"] <= 30):
                mx, my = self.travel(me, zgx, zgy)
                mode = "zone-walk"
            elif P.Ghost:
                hx, hy, ok = self.home(o)
                if ok and hyp(hx - x, hy - y) > 3:
                    mx, my = self.travel(me, hx, hy)
                    if not danger and now - self.last_hurt > 6:
                        q = min(1.0, 4.6 / st["walk_speed"])
                        mx, my = mx * q, my * q
                    mode = "home"

        if F["uf"] and mx == 0 and my == 0 and now - self.last_hurt < 1.5 and mode in ("idle", "home", "zone-walk", "flee", "cornered"):
            side = 1.0 if int(now * 2) % 2 == 0 else -1.0
            mx, my = -math.sin(self.hurt_bearing) * side, math.cos(self.hurt_bearing) * side
        if esc_jump:
            act["jump"] = True
        if P.Dodge and mode not in ("unstuck", "escape"):
            ddx, ddy, lz = self.dodge(o)
            if ddx != 0 or ddy != 0:
                mx, my = mx * 0.4 + ddx * 0.9, my * 0.4 + ddy * 0.9
                l = hyp(mx, my)
                if l > 1:
                    mx, my = mx / l, my / l
                if lz and energy > 25:
                    boost = True

        if mode == "escape":
            self.stuck_since = -1
        elif (mx != 0 or my != 0) and hyp(me["vel"][0], me["vel"][1]) < 1.2 and me["grounded"]:
            if self.stuck_since < 0:
                self.stuck_since = now
            elif now - self.stuck_since > 0.7:
                a = self.rng.random() * 2 * PI
                self.unstuck_dir = (math.cos(a), math.sin(a))
                self.unstuck_until = now + 0.5
                self.stuck_since = -1
                self.path = None
                act["jump"] = True
        else:
            self.stuck_since = -1

        self.aim_and_fire(o, act, target, foe, farm, farm_pos, seen, mx, my, hp_frac, gun)

        if gun is not None and gun["reload"] <= 0 and gun["ammo"] < gun["stats"]["mag"]:
            if target is None and farm is None and farm_pos is None and (gun["ammo"] <= gun["stats"]["mag"] * 0.6
                                                                          or now - self.last_threat > 3):
                act["reload"] = True

        slot1 = weapons[1]["kind"] if len(weapons) > 1 and weapons[1] else ""
        for p in o["pickups"]:
            if hyp(p["pos"][0] - x, p["pos"][1] - y) > self.cfg["loot"]["pickup_radius"] - 0.3 or abs(p["pos"][2] - zf) > 3.6:
                continue
            if not self.want_pickup(me, p["kind"]):
                continue
            act["take"] = p["id"]
            if slot1 and weapons and weapons[0]:
                if slot1 == "laser" and weapons[1].get("charge", 0) > 0:
                    del act["take"]
                    break
                act["drop"] = 1
            break
        act["move"] = [mx, my]
        act["boost"] = bool(boost and (mx != 0 or my != 0))
        self.mode = mode

    # ------------------------------------------------------------------ aim and weapons
    def aim_and_fire(self, o, act, target, foe, farm, farm_pos, seen, mx, my, hp_frac, gun):
        me = o["self"]
        st = me["stats"]
        now = self.now
        x, y, zf = me["pos"]
        W = self.W
        P = self.P
        weapons = me["weapons"]
        muzzle = (x + me["vel"][0] * LAT, y + me["vel"][1] * LAT, zf + st["muzzle_height"])
        aim = [me["aim"][0], 0.0]
        have_aim = False
        defend = False
        fire = [False, False]
        if target is None and self.npc_hurt > 0 and now - self.npc_hurt < 3:
            worst, best_off = None, 0.7
            for n in self.npcs.values():
                if now - n.seen > 0.5 or not n.los or n.dist > 150:
                    continue
                off = abs(ang_diff(math.atan2(n.pos[1] - y, n.pos[0] - x), self.npc_bearing))
                if now - n.hit_me < 3:
                    off = 0
                if off < best_off:
                    best_off, worst = off, n
            if worst is not None:
                farm, farm_pos, defend = worst, None, True
        tp = tv = None
        shootable = is_mech = False
        tr = th = 0.0
        if target is not None:
            sc = self.scale_of(target.cubes)
            tr, th = self.cfg["mech"]["radius"] * sc, self.cfg["mech"]["height"] * sc
            tp, tv = list(target.pos), target.vel
            tp[2] += th * 0.5
            if not W.clear(muzzle[0], muzzle[1], muzzle[2], tp[0], tp[1], tp[2]):
                tp[2] = target.pos[2] + th * 0.85
            shootable = is_mech = True
        elif farm is not None:
            tr, th = self.cfg["npc"]["radius"], self.cfg["npc"]["height"]
            tp, tv = list(farm.pos), farm.vel
            tp[2] += th * 0.5
            shootable = True
        elif farm_pos is not None:
            tr, th = self.cfg["crates"]["radius"], self.cfg["crates"]["height"]
            tp, tv = list(farm_pos), (0, 0, 0)
            shootable = True
        if not is_mech and target is None and (self.retreat or hp_frac < 0.5) and not defend:
            shootable = False

        if shootable:
            dist = hyp(tp[0] - x, tp[1] - y)
            grounded = abs(tv[2]) < 0.5
            gy = gp = 0.0
            gun_ok = False
            if gun is not None:
                gy, gp = self.lead(muzzle, tp, tv, gun["stats"].get("speed", 0), grounded)
                gun_ok = dist <= gun["stats"]["range"] * 0.96
            dp = (tp[0] + tv[0] * (LAT + 0.05), tp[1] + tv[1] * (LAT + 0.05), tp[2])
            hy = math.atan2(dp[1] - muzzle[1], dp[0] - muzzle[0])
            hpitch = math.atan2(dp[2] - muzzle[2], hyp(dp[0] - muzzle[0], dp[1] - muzzle[1]))
            aim = [gy, gp] if gun is not None else [hy, hpitch]
            have_aim = True
            tol_y = math.atan2(tr * 0.75, max(dist, 1))
            tol_p = math.atan2(th * 0.4, max(dist, 1))
            for i, w in enumerate(weapons):
                if w is None or i > 1:
                    continue
                kind = w["kind"]
                ws = w["stats"]
                if kind == "gun":
                    if gun_ok and w["ammo"] > 0 and w["reload"] <= 0:
                        ey = max(0.0, abs(ang_diff(gy, me["aim"][0])) - st["turn_rate"] * 0.05)
                        ep = max(0.0, abs(gp - me["aim"][1]) - st["pitch_rate"] * 0.05)
                        if ey < tol_y and ep < tol_p:
                            fire[i] = True
                elif kind == "lightning":
                    cone = ws["cone_deg"] * PI / 180
                    if dist < ws["range"] - 1.5 and abs(ang_diff(hy, me["aim"][0])) < cone * 0.5 - 0.05 and me["energy"] >= ws["energy"]:
                        fire[i] = True
                elif kind == "laser":
                    if not is_mech or dist > ws["range"] * 0.97:
                        continue
                    full = ws["charge"]
                    ch = w.get("charge", 0)
                    if P.Lethal and ch >= ws["min_charge"] and ch < full - 0.051:
                        dmg = ws["damage"] * (ws["min_frac"] + (1 - ws["min_frac"]) * ch / full) * \
                            (1 + self.cfg["cubes"]["damage_per_cube"] * min(me["cubes"], self.cfg["cubes"]["stat_cap"]))
                        if "mortar" in target.weapons:
                            dmg *= 1 + ws["cookoff"]
                        if dmg * P.LethalK >= target.hp and abs(ang_diff(hy, me["aim"][0])) < tol_y * 0.9 and \
                                abs(hpitch - me["aim"][1]) < tol_p:
                            aim = [hy, hpitch]
                            fire[i] = False
                            continue
                    if ch < full - 0.051:
                        fire[i] = True
                        if ch > full * 0.45:
                            aim = [hy, hpitch]
                    else:
                        aim = [hy, hpitch]
                        fire[i] = not (abs(ang_diff(hy, me["aim"][0])) < tol_y * 0.9 and abs(hpitch - me["aim"][1]) < tol_p)

        if target is None and foe is not None:
            for i, w in enumerate(weapons):
                if w and i < 2 and w["kind"] == "laser" and w["cooldown"] <= 0.05 and foe.dist < w["stats"]["range"] and \
                        w.get("charge", 0) < w["stats"]["charge"] + w["stats"]["hold_s"] - 0.35:
                    fire[i] = True

        if not have_aim:
            watch = None
            for t in seen:
                if t.engage or now - t.hit_me < 6:
                    if watch is None or t.dist < watch.dist:
                        watch = t
            if watch is not None:
                aim[0] = math.atan2(watch.pos[1] - y, watch.pos[0] - x)
            elif self.npc_hurt > 0 and now - self.npc_hurt < 2.5:
                aim[0] = self.npc_bearing
            elif now - self.last_hurt < 2.5:
                aim[0] = self.hurt_bearing
            elif mx != 0 or my != 0:
                aim[0] = math.atan2(my, mx) + 1.15 * math.sin(now * 1.3)
            else:
                aim[0] = me["aim"][0] + 0.35
            aim[1] = 0.0
        aim[1] = clamp(aim[1], st["min_pitch"], st["max_pitch"])
        act["aim"] = aim
        act["fire"] = fire


NBRS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
