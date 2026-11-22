"""Decision core: one mode per tick (zone / retreat / drop / fight / loot / hunt / idle), laser-first economy, laser fight band,
lead + charge control, and a rollout planner (plan.py) for all movement. Navigation, tracking and loot valuation come from base.py."""
import json
import math
import os
import time
from types import SimpleNamespace

import base
import plan
from base import P, TAU, UPG67, dist2, wrap

V = {"explore": 120, "farm": 1, "idle_k": 0.9, "juke_all": 0, "nofirst": 1, "harm": 1, "hunt": 1, "dp": 1, "site": 1, "salt": 0}
if os.environ.get("MECH_V"):
    V.update(json.loads(os.environ["MECH_V"]))
P.update(acc=1, site=V["site"], harm=V["harm"], salt=V["salt"], lethal=1)

PREF = {"gun": 85.0, "laser": 140.0, "lightning": 30.0, "mortar": 90.0}
BAND = 8.0


class Bot(base.Bot):
    name = "mech-core1"

    def __init__(self, hello):
        super().__init__(hello)
        self.id = hello.get("slot", 0)
        self.dlog = []          # (t, attacker id, amount)
        self.retreating = False
        self.tgt_id = None
        self.los_t = {}         # id -> last time we had a line to it
        self.ban = {}           # id -> banned until
        self.chg_t = {}         # id -> first time seen charging a laser
        self.prev_dir = None
        self.juke_side = 1
        self.juke_until = 0.0
        self.holding = False
        self.last_tgt_t = -99.0
        self.ftgt = None
        self.mission = None

    # ------------------------------------------------------------------ zone
    def zone_at(self, z, t):
        f = min(1.0, max(0.0, (t - z["shrink_start"]) / max(1e-6, z["shrink_end"] - z["shrink_start"])))
        c, n = z["center"], z["next_center"]
        return (c[0] + (n[0] - c[0]) * f, c[1] + (n[1] - c[1]) * f, z["radius"] + (z["next_radius"] - z["radius"]) * f)

    def safe_goal(self, obs):
        z = obs["zone"]
        nc, nr = z["next_center"], z["next_radius"]
        safe = max(3.0, nr - min(18.0, nr / 2) - 12.0)
        if nr <= 30:  # last rings: spread around the centre instead of piling on it
            a = self.id * 0.9
            return (nc[0] + 0.5 * safe * math.cos(a), nc[1] + 0.5 * safe * math.sin(a))
        dx, dy = self.pos[0] - nc[0], self.pos[1] - nc[1]
        d = math.hypot(dx, dy) or 1.0
        k = min(d, V["idle_k"] * safe) / d
        return (nc[0] + dx * k, nc[1] + dy * k)

    def urgent(self, obs, me, goal):
        z, t, pos = obs["zone"], obs["t"], self.pos
        if not me["in_zone"] or z["radius"] - dist2(pos, z["center"]) < 8:
            return True
        pt = dist2(pos, goal) * 1.15 / 11.7
        fx, fy, fr = self.zone_at(z, t + 9 + pt)
        if dist2(pos, (fx, fy)) > fr:
            return True
        nc, nr = z["next_center"], z["next_radius"]
        return dist2(pos, nc) > nr - min(18.0, nr / 2) and z["shrink_end"] - t < pt + 12

    # ------------------------------------------------------------------ threats and targets
    def threats(self, mechs, me, t):
        out = []
        scale = 1 + 0.05 * me["cubes"]
        for m in mechs[:4]:
            if m["d"] > 280 or self.harmless(m, t):
                continue
            w = m.get("weapons") or ["gun"]
            dps = 150.0 if "gun" in w else (40.0 if "mortar" in w else 120.0)
            if "lightning" in w and m["d"] < 50:
                dps = 200.0
            chg = None
            if m.get("charging") and "laser" in w:
                t0 = self.chg_t.setdefault(m["id"], t)
                if abs(wrap(math.atan2(self.pos[1] - m["pos"][1], self.pos[0] - m["pos"][0]) - m["aim"][0])) < 0.35:
                    chg = min(0.8, max(0.1, 0.75 - (t - t0)))
            else:
                self.chg_t.pop(m["id"], None)
            out.append({"eye": (m["pos"][0], m["pos"][1], m["pos"][2] + 8 * scale), "aim": m["aim"][0], "dps": dps, "chg": chg, "m": m})
        if t - self.last_dmg_t < 3 and self.dmg_bearing is not None and not any(t - self.hit_t.get(o["m"]["id"], -99) < 3 for o in out):
            b = self.dmg_bearing
            gp = (self.pos[0] + 110 * math.cos(b), self.pos[1] + 110 * math.sin(b))
            out.append({"eye": (gp[0], gp[1], self.w.ground(*gp) + 8), "aim": b + math.pi, "dps": 100.0, "chg": None, "m": None})
        return out

    def pick_mode_target(self, obs, mechs, me, has_laser, t):
        z = obs["zone"]
        best = None
        for m in mechs:
            i, d = m["id"], m["d"]
            self.los_t.setdefault(i, t)
            if m["los"]:
                self.los_t[i] = t
            hf = m.get("hp", 1400) / max(1.0, m.get("max_hp", 1400))
            if t - self.los_t[i] > (8 if hf < 0.25 else 4):
                self.ban[i] = t + 8; self.los_t[i] = t
            att = t - self.hit_t.get(i, -99) < 5
            if self.ban.get(i, 0) > t or d > 200 or (d > 170 and not att):
                continue
            if d > 60 and not att and (dist2(m["pos"], z["center"]) > z["radius"] or dist2(m["pos"], z["next_center"]) - z["next_radius"] > 60):
                continue
            if V["nofirst"] and not has_laser and not self.hunter(m, t) and not (d < 55 and not self.trk[i]["laser"]):
                continue
            sc = 60 * (1 - d / 200) + 80 * (1 - hf) + 100 + (0.1 * self.dealt.get(i, 0) if att else 0) + 30 * (i == self.tgt_id) + 30
            if best is None or sc > best[0]:
                best = (sc, m)
        return best[1] if best else None

    def pick_fire_target(self, mechs, me, has_laser, t):
        best = None
        for m in mechs[:6]:
            d, i = m["d"], m["id"]
            if d > (274 if has_laser else 188):
                continue
            att = t - self.hit_t.get(i, -99) < 6
            if V["nofirst"] and not has_laser and not self.hunter(m, t) and not (d < 55 and not self.trk[i]["laser"]):
                continue
            hf = m.get("hp", 1400) / max(1.0, m.get("max_hp", 1400))
            sc = -0.3 * d + 40 * (1 - hf) + 100 * bool(m["los"]) + 40 * att + 50 * (i == self.ftgt) + 50 * (d < 188)
            if best is None or sc > best[0]:
                best = (sc, m)
        return best[1] if best else None

    # ------------------------------------------------------------------ retreat
    def want_retreat(self, obs, me, mechs, target, t):
        hf = me["hp"] / me["max_hp"]
        att = {b for tt, b, a in self.dlog if t - tt < 2 and b is not None}
        if self.late(obs):
            return False
        if self.retreating:
            return hf < 0.8
        threat = any(m["d"] < 200 for m in mechs) or t - self.last_dmg_t < 3
        laserp = t - self.last_dmg_t < 4 and any(m["d"] < 130 and m["los"] and (self.trk[m["id"]]["laser"]) for m in mechs)
        go = (hf < 0.38 and threat) or (len(att) >= 2 and hf < 0.65) or ((len(att) >= 2 or laserp) and hf < 0.75)
        if go and target is not None and len(att) <= 1 and target.get("hp", 1e9) < 0.6 * me["hp"] and t - self.trk[target["id"]]["t"] < 0.3:
            return False  # finish rule: the target is about to die
        return go

    def cover_goal(self, obs, threats, t):
        pos, z = self.pos, obs["zone"]
        if self.cover and t - self.cover_t < 0.7:
            return self.cover
        eyes = [o for o in threats if o["m"] is not None]
        t0 = time.perf_counter()
        best = None
        for r in (12, 24, 40, 60):
            for k in range(3):
                a = TAU * k / 3 + r
                c = (pos[0] + r * math.cos(a), pos[1] + r * math.sin(a))
                if time.perf_counter() - t0 > 0.010:
                    break
                if self.w.blocked_nav(*c) or dist2(c, z["center"]) > z["radius"] - 12:
                    continue
                hidden = not any(self.exposed(o["m"], c) for o in eyes)
                near = min((dist2(o["m"]["pos"], c) for o in eyes), default=150)
                sc = r + 0.6 * max(0.0, dist2(c, z["next_center"]) - z["next_radius"]) - 0.15 * min(150, near) + (0 if hidden else 400)
                if best is None or sc < best[0]:
                    best = (sc, c, hidden)
        if best and best[2]:
            c = best[1]
        elif eyes:  # nowhere hidden: flee 55 m directly away from the nearest threat
            e = min(eyes, key=lambda o: dist2(o["m"]["pos"], pos))["m"]["pos"]
            d = dist2(e, pos) or 1.0
            c = (pos[0] + (pos[0] - e[0]) / d * 55, pos[1] + (pos[1] - e[1]) / d * 55)
        else:
            c = best[1] if best else pos[:2]
        self.cover, self.cover_t = c, t
        return c

    # ------------------------------------------------------------------ fight geometry
    def pref_range(self, me, tg, slot1, has_laser, t):
        hf = me["hp"] / me["max_hp"]
        p = PREF["laser"] if has_laser else (PREF["lightning"] if slot1 == "lightning" and me["energy"] > 35 and hf > 0.5 else PREF["gun"])
        if "lightning" in (tg.get("weapons") or []):
            p = max(p, 70.0)
        if not has_laser and self.harmless(tg, t):
            p = min(p, 55.0)
        return p

    # ------------------------------------------------------------------ weapons control
    def control(self, me, tg, tkind, t, close):
        gun = me["weapons"][0]
        w1 = me["weapons"][1] if len(me["weapons"]) > 1 else None
        k1 = (w1 or {}).get("kind")
        fire = [False, False]
        aim = None
        reload_ = False
        ay = me["aim"]
        if k1 == "laser":
            ls = w1["stats"]
            full, hold = ls.get("charge", 0.75), ls.get("hold_s", 2.0)
            ch = w1.get("charge", 0)
        if tg is None:
            if k1 == "laser" and ch > 0:
                fire[1] = ch < full + hold - 0.4  # target lost: keep the charge a little, then let go
            if gun and (gun["ammo"] == 0 or (gun["ammo"] <= gun["stats"]["mag"] / 2 and t - self.last_tgt_t > 1.5)) and gun["reload"] == 0:
                reload_ = True
            return aim, fire, reload_
        self.last_tgt_t = t
        mech = tkind == "mech"
        scale = 1 + 0.05 * tg.get("cubes", 0) if mech else 1.0
        off = 4.5 * scale if mech else (1.75 if tkind == "npc" else 1.2)
        gs = gun["stats"] if gun else {"speed": 252, "range": 230}
        yaw, pit, d = self.solve_lead(me, tg, gs["speed"], off, 0.05)
        aim = [yaw, pit]
        los = tg.get("los", True) if mech else self.w.los((self.pos[0], self.pos[1], self.pos[2] + 7), (tg["pos"][0], tg["pos"][1], tg["pos"][2] + 1.5), 2.5)
        slew = 0.2
        py = ay[0] + max(-slew, min(slew, wrap(yaw - ay[0])))
        pp = ay[1] + max(-0.15, min(0.15, pit - ay[1]))
        ty = 0.8 * math.atan2(2.5 * scale, max(d, 5)); tp = 0.8 * math.atan2(4.5 * scale, max(d, 5))
        lasering = False
        if mech and k1 == "laser" and los and d <= 0.98 * ls.get("range", 280) and w1["cooldown"] <= 0.05:
            lyaw, lpit, _ = self.solve_lead(me, tg, 1e9, off, 0.05)
            mf_ = ls.get("min_frac", 0.35)
            part = ls.get("damage", 315) * (mf_ + (1 - mf_) * min(1.0, ch / max(full, 0.05)))
            lethal = ch >= ls.get("min_charge", 0.15) and part >= 1.05 * tg.get("hp", 1e9)
            final = ch >= full - 0.2
            if final:
                aim = [lyaw, lpit]; lasering = True
            lerr = abs(wrap(lyaw - ay[0]))
            aimed = lerr < 0.8 * math.atan2(2.5 * scale, d) + (0.01 if final else 0)
            if (ch >= full - 0.051 or lethal) and aimed:
                fire[1] = False  # let go: the beam fires along the aim right now
            elif ch >= full + hold - 0.4:
                fire[1] = False
            elif ch > 0 or lerr < 0.25:
                fire[1] = True
        if gun and not lasering and los and d < 0.82 * gs["range"] and gun["ammo"] > 0 and gun["reload"] == 0 \
                and abs(wrap(yaw - py)) < ty and abs(pit - pp) < tp:
            fire[0] = True
        if mech and k1 == "lightning" and los and d < 0.95 * w1["stats"].get("range", 45) and me["energy"] >= 8 \
                and abs(wrap(math.atan2(tg["pos"][1] - self.pos[1], tg["pos"][0] - self.pos[0]) - ay[0])) < 0.85 * math.radians(w1["stats"].get("cone_deg", 30)) / 2:
            fire[1] = True
        if mech and k1 == "mortar" and (not los or d > 0.82 * gs["range"]) and 28 < d < 150 and not self.exposed_to_known(close) \
                and math.hypot(*(tg.get("vel") or [0, 0])[:2]) < 13:
            ma = self.solve_mortar(me, tg)
            if ma:
                aim = list(ma)
                if abs(wrap(ma[0] - ay[0])) < 0.05 and abs(ma[1] - ay[1]) < 0.04:
                    fire[1] = True
        if gun and not fire[0] and gun["reload"] == 0 and (gun["ammo"] == 0 or (not los and gun["ammo"] < gun["stats"]["mag"] * 0.6)):
            reload_ = True
        return aim, fire, reload_

    def idle_aim(self, obs, me, t, heading):
        if self.dmg_bearing is not None and t - self.last_dmg_t < 3:
            return [self.dmg_bearing, 0.0]
        sn = [s for s in obs["sounds"] if s["kind"] in ("gun", "step", "boost", "laser", "mortar", "lightning") and s["dist"] != "far"]
        if sn:
            return [wrap(sn[0]["bearing"]), 0.0]
        return [wrap(heading + 1.1 * math.sin(TAU * t / 3.2)), 0.0]

    def laser_search(self, me, t, aim, act, boost, calm_blocked):
        """Spin 3 s at spawn, then a 360 sweep every 6 s while laserless and calm; jump-boost for a viewpoint on the first sweeps."""
        yaw = me["aim"][0]
        calm = t - self.last_dmg_t > 3 and not any(m["d"] < 150 and not self.harmless(m, t) for m in self.mechs_cache)
        if t < 3.0:
            return [wrap(yaw + 0.8), 0.0], boost
        if self.sweep_on:
            if not calm:
                self.sweep_on = False; self.jet_on = False
            else:
                self.sweep_acc += abs(wrap(yaw - self.sweep_yaw)); self.sweep_yaw = yaw
                if self.sweep_acc >= TAU - 0.4:
                    self.sweep_on = False; self.last_sweep = t
                else:
                    aim = [wrap(yaw + 1.5), 0.0]
        elif calm and not calm_blocked and t - self.last_sweep > 6:
            self.sweep_on = True; self.sweep_acc = 0.0; self.sweep_yaw = yaw; self.last_sweep = t
            self.jet_on = me["energy"] >= 90
            aim = [wrap(yaw + 1.5), 0.0]
        if self.jet_on:
            if not self.sweep_on or me["energy"] < 35:
                self.jet_on = False
            else:
                act["jump"] = bool(me["grounded"]); boost = boost or not me["grounded"]
        return aim, boost

    # ------------------------------------------------------------------ main
    def act(self, obs):
        t0 = time.perf_counter()
        me, t = obs["self"], obs["t"]
        self.t_now, self.pos = t, me["pos"]
        pos = self.pos
        act = {"tick": obs["tick"]}
        hp, mhp = me["hp"], me["max_hp"]
        if obs["offer"]:
            ids = [c["id"] for c in obs["offer"]["choices"]]
            act["pick"] = min(range(len(ids)), key=lambda i: UPG67.index(ids[i]) if ids[i] in UPG67 else 99)
        for p in obs["pickups"]:
            self.pmem[p["id"]] = (p["kind"], p["pos"])
        seen = {p["id"] for p in obs["pickups"]}
        for i, (k_, pp_) in list(self.pmem.items()):
            if i not in seen and dist2(pp_, pos) < 30:
                del self.pmem[i]
        for e in obs["events"]:
            if e["type"] == "kill":
                self.loot_until = t + 10
            elif e["type"] == "damaged":
                self.last_dmg_t = t
                if e.get("by") is not None:
                    self.dmg_bearing = e["bearing"]
                    self.dlog.append((t, e["by"], e.get("amount") or 0.0))
                    self.last_npc_hit = t if e.get("weapon") == "npc" else getattr(self, "last_npc_hit", -99.0)
        self.dlog = [x for x in self.dlog if t - x[0] < 4]
        mechs = [m for m in obs["mechs"] if m["kind"] == "mech"]
        npcs = [m for m in obs["mechs"] if m["kind"] == "npc"]
        for m in mechs:
            m["d"] = dist2(m["pos"], pos)
        mechs.sort(key=lambda m: m["d"])
        self.mechs_cache = mechs
        self.track_update(obs, mechs, t)
        mz = pos[2] + 7
        for k, m in enumerate(mechs):
            m["los"] = k < 6 and m["d"] < 200 and self.w.los((pos[0], pos[1], mz), (m["pos"][0], m["pos"][1], m["pos"][2] + 4.5), 2.5)
        close = [m for m in mechs if m["d"] < 140]
        wpn = [(w or {}).get("kind") for w in me["weapons"]]
        slot1 = wpn[1] if len(wpn) > 1 else None
        has_laser = slot1 == "laser"
        zc, zr = obs["zone"]["center"], obs["zone"]["radius"]

        # ---- decide
        target = self.pick_mode_target(obs, mechs, me, has_laser, t)
        if target is not None:
            self.tgt_id = target["id"]
        ftg = self.pick_fire_target(mechs, me, has_laser, t)
        self.ftgt = ftg["id"] if ftg else None
        tkind = "mech" if ftg else None
        if ftg is None:
            for n in npcs:
                if dist2(n["pos"], pos) < 50 and t - getattr(self, "last_npc_hit", -99.0) < 3:
                    ftg, tkind = n, "npc"; break
        ch = self.econ_choice(obs, me, wpn, close)
        if ch and ch[0] == "cube" and me["cubes"] >= 10:
            ch = None  # past the stat cap cubes only make us a bigger target
        farm = None
        if V["farm"] and target is None and not close and hp > 0.6 * mhp and t < 200 and self.pick_goal(obs, wpn) is None:
            for kind_, lst in (("npc", npcs), ("crate", obs["crates"])):
                for m in lst:
                    d_ = dist2(m["pos"], pos)
                    if d_ < 100 and dist2(m["pos"], zc) < zr - 30 and (farm is None or d_ < farm[0]):
                        farm = (d_, m, kind_)
            if farm and ftg is None:
                ftg, tkind = farm[1], farm[2]
        threats = self.threats(mechs, me, t)
        sgoal = self.safe_goal(obs)
        retreat = self.want_retreat(obs, me, mechs, target, t)
        self.retreating = retreat
        hot = self.hot(t)
        strong = False
        if target is not None:
            tr = self.trk.get(target["id"])
            strong = bool(tr and (tr["laser"] or self.dealt.get(target["id"], 0.0) >= 150))
        mode, goal = "idle", None
        if self.urgent(obs, me, sgoal):
            mode, goal = "zone", sgoal
        else:
            gear = None
            if ch:
                gear = (ch[0], ch[1]["pos"][:2], ch[2])
            if retreat:
                dlim = 150 if hp < 0.6 * mhp else 70
                if gear and gear[0] == "drop" and gear[2] < dlim and hp < 0.6 * mhp:
                    mode, goal = "drop", gear[1]
                elif gear and gear[0] != "crate" and gear[2] < 24 and hp > 0.25 * mhp:
                    mode, goal = "loot", gear[1]
                else:
                    mode, goal = "retreat", self.cover_goal(obs, threats, t)
            elif target is not None:
                dlim = 350 if (V["dp"] and not strong) else 70
                if gear and gear[0] == "drop" and gear[2] < dlim:
                    mode, goal = "drop", gear[1]
                elif gear and gear[0] != "crate" and gear[2] < 24 and hp > 0.25 * mhp:
                    mode, goal = "loot", gear[1]
                else:
                    mode, goal = "fight", target["pos"][:2]
            elif gear:
                mode, goal = ("drop" if gear[0] == "drop" else "loot"), gear[1]
            if mode in ("idle", "fight", "loot"):
                gg = self.pick_goal(obs, wpn) if (slot1 != "laser" and hp > 0.3 * mhp) else None
                if gg is not None and (dist2(gg, pos) < 40 or mode != "fight" or target["d"] > 110):
                    mode, goal = "loot", gg
            if mode == "idle" and V["hunt"] and has_laser and hp >= 0.5 * mhp:
                hg = self.hunt_goal(obs, t)
                if hg:
                    mode, goal = "hunt", hg
            if mode == "idle" and me["cubes"] < 10 and hp > 0.5 * mhp and not close and V["site"]:
                sg = self.site_goal(obs, t)
                if sg:
                    mode, goal = "loot", sg
            if mode == "idle" and farm:
                mode, goal = "fight", farm[1]["pos"][:2]
            if mode == "idle" and t < V["explore"] and slot1 is None and hp > 0.6 * mhp and not close:
                if self.xgoal is None or t - self.xt > 25 or dist2(self.xgoal, pos) < 20:
                    for _ in range(12):
                        a_, r_ = self.rng.uniform(0, TAU), self.rng.uniform(120, 260)
                        c_ = (pos[0] + r_ * math.cos(a_), pos[1] + r_ * math.sin(a_))
                        if not self.w.blocked_nav(*c_) and dist2(c_, zc) < zr - 60:
                            self.xgoal, self.xt = c_, t; break
                if self.xgoal is not None:
                    mode, goal = "loot", self.xgoal
            if mode == "idle":
                goal = sgoal
        # weapon pickup
        if mode in ("loot", "idle", "fight", "drop", "retreat", "hunt"):
            for p in obs["pickups"]:
                if dist2(p["pos"], pos) < 3.8 and p["kind"] in base.PICK_W2 and p["kind"] not in wpn and \
                        (slot1 is None or (slot1 == "lightning" and p["kind"] == "laser") or (slot1 == "mortar" and p["kind"] != "mortar")) \
                        and (p["kind"] != "mortar" or slot1 is None):
                    act["take"] = p["id"]
                    if slot1 is not None:
                        act["drop"] = 1
                    break

        # ---- aim and fire
        aim, fire, rld = self.control(me, ftg, tkind, t, close)
        if rld:
            act["reload"] = True
        self.holding = fire[1] and slot1 == "laser"

        # ---- movement planner
        d0 = dist2(goal, pos) if goal else 0.0
        pref = 0.0
        navgoal = goal
        if mode == "fight":
            pref = self.pref_range(me, target, slot1, has_laser, t) if target is not None else 45.0
            if d0 <= pref + BAND:
                navgoal = None
        elif mode == "idle" and d0 < 6:
            navgoal = None
        elif mode in ("drop", "loot") and d0 < 2.5:
            navgoal = None
        nav = None
        if navgoal is not None:
            nav, _ = self.follow(navgoal, t)
        mst = me["stats"]
        sc_ = 1 + 0.05 * me["cubes"]
        shots, shells = plan.prep_shots(obs["projectiles"], pos, self.w.ground)
        underfire = t - self.last_dmg_t < 1.5 or bool(shots) or bool(shells) or not me["in_zone"]
        thr = 3 if mode == "zone" else (45 if slot1 == "lightning" else 25)
        can_boost = me["grounded"] and me["energy"] > thr and (
            mode in ("zone", "retreat", "drop") or underfire
            or (mode == "hunt" and me["energy"] > 0.5 * me.get("max_energy", 100))
            or (mode == "loot" and me["energy"] > 60 and goal is not None and any(p["kind"] == "laser" and dist2(p["pos"], goal) < 6 for p in obs["pickups"])))
        jk = None
        for o in threats:
            m = o["m"]
            if m is not None and m["d"] > 30 and (V["juke_all"] or "gun" in (m.get("weapons") or ["gun"]) or "mortar" in (m.get("weapons") or [])) \
                    and abs(wrap(math.atan2(pos[1] - m["pos"][1], pos[0] - m["pos"][0]) - m["aim"][0])) < (0.6 if V["juke_all"] else 0.35):
                bx, by = pos[0] - m["pos"][0], pos[1] - m["pos"][1]
                bl = math.hypot(bx, by) or 1.0
                if t > self.juke_until:
                    self.juke_side = -self.juke_side; self.juke_until = t + self.rng.uniform(0.35, 0.95)
                jk = (-by / bl, bx / bl, self.juke_side)
                break
        ctx = SimpleNamespace(
            pos=(pos[0], pos[1]), z=pos[2], vel=(me["vel"][0], me["vel"][1]), energy=me["energy"],
            walk_speed=mst.get("walk_speed", 11.7), boost_speed=mst.get("boost_speed", 19.5),
            accel=self.cfg["mech"].get("ground_accel", 58.5) if me["grounded"] else self.cfg["mech"].get("air_accel", 12.0),
            boost_drain=self.cfg["mech"].get("boost_drain", 17.0), radius=2.5 * sc_,
            slow=0.5 if (self.holding or (slot1 == "laser" and me["weapons"][1].get("charge", 0) > 0)) else 1.0,
            can_boost=can_boost, navdir=nav if nav and (nav[0] or nav[1]) else None,
            goal=navgoal if nav is None else None, gw=plan.GOAL_W[mode],
            target=(goal[0], goal[1], (target["pos"][2] if target else pos[2]) + 4.5) if mode == "fight" else None,
            pref=pref, band=BAND, armed=bool(me["weapons"][0] and me["weapons"][0]["ammo"] > 0) or slot1 is not None,
            threats=threats, shots=shots, shells=shells,
            zones=[self.zone_at(obs["zone"], t + (k + 1) * plan.DT) for k in range(plan.N)], dps=obs["zone"]["dps"],
            npcs=[(n["pos"][0], n["pos"][1]) for n in npcs if dist2(n["pos"], pos) < 80 and n is not ftg],
            prev=self.prev_dir, juke=jk, xw=2.0 if mode == "retreat" else 0.5)
        wish, boost = plan.choose(self.w, ctx, t0)
        self.prev_dir = wish if (wish[0] or wish[1]) else None

        # ---- stuck handling
        self.hist.append((t, pos[0], pos[1]))
        while self.hist and t - self.hist[0][0] > 2.0:
            self.hist.pop(0)
        if t < self.stuck_until:
            wish = self.stuck_dir; act["jump"] = True; boost = me["energy"] > 20
        elif nav is not None and (wish[0] or wish[1]) and len(self.hist) > 15 and me["grounded"] and dist2(pos, self.hist[0][1:]) < 2.0:
            a = self.rng.uniform(0, TAU)
            self.stuck_dir = (math.cos(a), math.sin(a)); self.stuck_until = t + 1.2
            self.path = []; self.path_goal = None

        # ---- idle aim, laser search
        if aim is None:
            heading = math.atan2(wish[1], wish[0]) if (wish[0] or wish[1]) else me["aim"][0]
            aim = self.idle_aim(obs, me, t, heading)
            if not has_laser and mode != "zone":
                aim, boost = self.laser_search(me, t, aim, act, boost, False)
        elif self.sweep_on:
            self.sweep_on = False; self.jet_on = False
        act.update(move=[round(wish[0], 3), round(wish[1], 3)], boost=boost, aim=[aim[0], aim[1]], fire=fire)
        self.run_search(t, max(0.003, 0.012 - (time.perf_counter() - t0)))
        return act
