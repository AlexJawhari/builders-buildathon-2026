"""Approximate local simulator (NOT the official engine) for comparing bot variants.

One bot under test (slot 0) vs 15 scripted house bots acting as a hunting squad. Flat-ground physics (no jumping or
roofs), grid collision, gun/lightning/laser for the bot, gun for the house, zone, hearing and vision per protocol.md.
    python3 sim/sim.py [n_matches] [procs] [intel: vision|omni] [bot module path]
"""
import importlib.util, json, math, os, random, sys, time
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from world import World, MINX, MINY, NX, NY  # noqa: E402

HELLO = json.load(open(os.path.join(HERE, "hello.json")))
CFG = HELLO["config"]
MAPDIR = os.environ.get("MAPDIR", "/home/alex/projects/buildathon/_downloads/mech/mechfight-kit/arena")
DT = 0.05
TAU = math.tau
# (aim sigma deg, lead quality 0..1, strafe) for easy, medium, hard, expert
_LV = os.environ.get('SIM_SKILL', '1')
LEVELS = ([(3.0, 0.5, 0.0)] * 4 + [(1.8, 0.8, 0.5)] * 4 + [(1.0, 0.95, 0.8)] * 4 + [(0.5, 1.0, 1.0)] * 3) if _LV == '1' else \
         ([(1.5, 0.8, 0.5)] * 4 + [(0.9, 0.95, 0.8)] * 4 + [(0.5, 1.0, 1.0)] * 4 + [(0.3, 1.0, 1.0)] * 3)


def wrap(a):
    return (a + math.pi) % TAU - math.pi


class Mech:
    def __init__(s, i, pos, hp=1400):
        s.id = i; s.pos = list(pos); s.vel = [0.0, 0.0, 0.0]; s.aim = [random.uniform(-3, 3), 0.0]
        s.hp = s.max_hp = hp; s.energy = 100.0; s.alive = True; s.death_t = None
        s.cmd = {"move": [0, 0], "aim": list(s.aim), "fire": [False, False], "boost": False}
        s.cd = [0.0, 0.0]; s.ammo = 12; s.reload = 0.0; s.charge = 0.0; s.slot1 = None
        s.last_dmg = -99.0; s.last_spend = -99.0; s.step_t = 0.0; s.mag = 12; s.rl = 2.4; s.cdm = 1.0; s.dealt = 0.0; s.cubes = 0; s.dmgm = 1.0; s.armor = 0.0


class Sim:
    def __init__(s, seed, bot_cls, intel="vision", world=None):
        s.rng = random.Random(seed); random.seed(seed)
        s.w = world or World(MAPDIR)
        s.intel = intel
        s.t = 0.0; s.tick = 0
        s.zone_plan = s.make_zone()
        c0, r0 = CFG["zone"]["start_center"], CFG["zone"]["start_radius"]
        s.mechs = []
        for i in range(16):
            while True:
                a = s.rng.uniform(0, TAU); r = r0 * 0.8 * math.sqrt(s.rng.random())
                p = (c0[0] + r * math.cos(a), c0[1] + r * math.sin(a))
                if not s.w.blocked_nav(*p) and all(math.hypot(p[0] - m.pos[0], p[1] - m.pos[1]) > 130 for m in s.mechs):
                    break
            s.mechs.append(Mech(i, (p[0], p[1], s.w.ground(*p))))
        s.bullets = []
        s.sounds = []  # (t, kind, x, y, id)
        s.events = {0: []}
        s.pickups = []
        for k in range(40):
            while True:
                p = (s.rng.uniform(-480, 520), s.rng.uniform(-300, 360))
                if not s.w.blocked_nav(*p):
                    break
            s.pickups.append({"id": 8000 + k, "kind": s.rng.choice(["mortar", "laser", "lightning"]), "pos": [p[0], p[1], s.w.ground(*p)]})
        s.npcs = []
        for k in range(12):
            while True:
                p = (s.rng.uniform(-480, 520), s.rng.uniform(-300, 360))
                if not s.w.blocked_nav(*p):
                    break
            s.npcs.append({"id": 100 + k, "pos": [p[0], p[1], s.w.ground(*p)], "home": (p[0], p[1]), "hp": 350.0, "cd": 0.0, "agro": False, "alive": True})
        s.crates = []
        for k in range(36):
            while True:
                p = (s.rng.uniform(-480, 520), s.rng.uniform(-300, 360))
                if not s.w.blocked_nav(*p):
                    break
            s.crates.append({"id": 1000 + k, "pos": [p[0], p[1], s.w.ground(*p)], "hp": 300.0})
        s.drops = []; s.drops_called = 0
        s.cubes = []; s.cube_id = 5000; s.offer = None; s.offer_q = 0; s.offer_n = 0; s.picked = []
        s.seen = {}  # for bot: id -> last seen t
        s.squad_known_until = -1.0; s.squad_last = None
        s.deaths = []  # (tick, id)
        h = dict(HELLO); h["slot"] = 0; h["seed"] = seed; h["map"] = {"dir": MAPDIR, "files": []}
        if os.environ.get('SIM_START'):
            s.mechs[0].slot1 = os.environ['SIM_START']; s.mechs[0].cd[1] = 1.0
        s.bot = bot_cls(h)
        for _m in s.mechs[1:]:
            if (_m.id - 1) // 4 >= 2 and os.environ.get('SIM_LASER', '1') == '1':
                _m.slot1 = 'laser'
        s.house = [{"strafe": 1, "until": 0.0, "lv": LEVELS[i - 1]} for i in range(16)]
        s.dmg_taken = 0.0
        s.weakest_first = os.environ.get('SIM_WEAK', '1') == '1'; s.all_shoot = os.environ.get('SIM_ALL', '1') == '1'
        s.dbw = {}
        s.shots = 0; s.sb = {}; s.hb = {}

    # ------------------------------------------------------------ zone
    def make_zone(s):
        z = CFG["zone"]; c = list(z["start_center"]); r = z["start_radius"]; t0 = 0.0; plan = []
        for ph in z["phases"]:
            ang = s.rng.uniform(0, TAU); rr = max(0.0, (r - ph["radius"])) * math.sqrt(s.rng.random())
            nc = [c[0] + rr * math.cos(ang), c[1] + rr * math.sin(ang)]
            plan.append(dict(c=c, r=r, nc=nc, nr=ph["radius"], ss=t0 + ph["hold_s"], se=t0 + ph["hold_s"] + ph["shrink_s"], dps=ph["dps"], t0=t0))
            c, r, t0 = nc, ph["radius"], t0 + ph["hold_s"] + ph["shrink_s"]
        return plan

    def zone(s):
        p = s.zone_plan[-1]
        for q in s.zone_plan:
            if s.t < q["se"]:
                p = q; break
        if s.t >= p["se"]:
            return p["nc"], p["nr"], p, p["dps"]
        f = 0.0 if s.t < p["ss"] else (s.t - p["ss"]) / (p["se"] - p["ss"])
        c = [p["c"][0] + (p["nc"][0] - p["c"][0]) * f, p["c"][1] + (p["nc"][1] - p["c"][1]) * f]
        return c, p["r"] + (p["nr"] - p["r"]) * f, p, p["dps"]

    # ------------------------------------------------------------ damage / sound
    def damage(s, m, amt, weapon, frm, by):
        if not m.alive:
            return
        dx, dy = frm[0] - m.pos[0], frm[1] - m.pos[1]
        rear = abs(wrap(math.atan2(dy, dx) - m.aim[0])) > math.pi / 2
        if rear:
            amt *= 1.5
        amt *= (1 - m.armor)
        if by is not None:
            amt *= s.mechs[by].dmgm
        m.hp -= amt; m.last_dmg = s.t
        if m.id == 0:
            s.dmg_taken += amt; s.dbw[weapon] = s.dbw.get(weapon, 0) + amt
            s.events[0].append({"type": "damaged", "amount": amt, "weapon": weapon, "bearing": math.atan2(dy, dx), "by": by, "rear": rear})
        if by is not None:
            s.mechs[by].dealt += amt; s.mechs[by].last_dmg = s.t
        if m.hp <= 0:
            m.alive = False; m.death_t = s.t; s.deaths.append((s.tick, m.id))
            s.sounds.append((s.t, "death", m.pos[0], m.pos[1], m.id))
            if m.slot1 and m.id:
                s.pickups.append({"id": s.cube_id, "kind": m.slot1, "pos": list(m.pos)}); s.cube_id += 1
            if m.id:
                for _k in range(2):
                    s.cubes.append({"id": s.cube_id, "pos": [m.pos[0] + _k * 2, m.pos[1], m.pos[2]]}); s.cube_id += 1
            if by == 0:
                s.events[0].append({"type": "kill", "target": m.id})

    def sound(s, kind, m):
        s.sounds.append((s.t, kind, m.pos[0], m.pos[1], m.id))

    # ------------------------------------------------------------ physics
    def step_mech(s, m):
        c = m.cmd
        st = CFG["mech"]
        mv = c["move"]; ml = math.hypot(*mv)
        if ml > 1:
            mv = (mv[0] / ml, mv[1] / ml); ml = 1.0
        boosting = c["boost"] and ml > 0.05 and m.energy > 1
        sp = st["boost_speed"] if boosting else st["walk_speed"]
        if m.charge > 0:
            sp *= 0.5
        want = (mv[0] * sp, mv[1] * sp)
        dvx, dvy = want[0] - m.vel[0], want[1] - m.vel[1]
        dl = math.hypot(dvx, dvy); mx = st["ground_accel"] * DT
        if dl > mx:
            dvx, dvy = dvx / dl * mx, dvy / dl * mx
        m.vel[0] += dvx; m.vel[1] += dvy
        for ax in (0, 1):
            np_ = [m.pos[0], m.pos[1]]
            np_[ax] += m.vel[ax] * DT
            if s.w.blocked_nav(np_[0], np_[1]):
                m.vel[ax] = 0.0
            else:
                m.pos[ax] = np_[ax]
        m.pos[2] = s.w.ground(m.pos[0], m.pos[1])
        if boosting:
            m.energy -= st["boost_drain"] * DT; m.last_spend = s.t
        elif s.t - m.last_spend > st["energy_regen_delay"]:
            m.energy = min(100.0, m.energy + st["energy_regen"] * DT)
        # aim slew
        ty, tp = c["aim"]
        d = wrap(ty - m.aim[0]); m.aim[0] = wrap(m.aim[0] + max(-st["turn_rate"] * DT, min(st["turn_rate"] * DT, d)))
        tp = max(-0.7, min(1.3, tp)); m.aim[1] += max(-st["pitch_rate"] * DT, min(st["pitch_rate"] * DT, tp - m.aim[1]))
        # sounds
        spd = math.hypot(m.vel[0], m.vel[1])
        if spd > 5:
            m.step_t += DT
            if m.step_t >= 0.6:
                m.step_t = 0; s.sound("boost" if boosting else "step", m)
        # hp
        zc, zr, _, dps = s.zone()
        if math.hypot(m.pos[0] - zc[0], m.pos[1] - zc[1]) > zr:
            s.damage_zone(m, dps * DT)
        elif s.t - m.last_dmg >= st["regen_delay"]:
            m.hp = min(m.max_hp, m.hp + st["regen_per_s"] * DT)

    def damage_zone(s, m, amt):
        m.hp -= amt; m.last_dmg = s.t
        if m.id == 0:
            s.dmg_taken += amt
        if m.hp <= 0 and m.alive:
            m.alive = False; m.death_t = s.t; s.deaths.append((s.tick, m.id))
            s.sounds.append((s.t, "death", m.pos[0], m.pos[1], m.id))

    def aim_vec(s, m):
        cy, sy = math.cos(m.aim[0]), math.sin(m.aim[0]); cp, sp = math.cos(m.aim[1]), math.sin(m.aim[1])
        return cp * cy, cp * sy, sp

    def weapons_fire(s, m):
        W = CFG["weapons"]; f = m.cmd["fire"]
        m.cd[0] = max(0, m.cd[0] - DT); m.cd[1] = max(0, m.cd[1] - DT)
        if m.reload > 0:
            m.reload -= DT
            if m.reload <= 0:
                m.ammo = m.mag; m.reload = 0.0
        if m.cmd.get("reload") and m.reload <= 0 and m.ammo < m.mag:
            m.reload = m.rl
        if f[0] and m.cd[0] <= 0 and m.ammo > 0 and m.reload <= 0 and m.charge <= 0:
            dx, dy, dz = s.aim_vec(m)
            sg = math.radians(W["gun"]["spread_deg"]) + math.radians(getattr(m, "extra_sigma", 0.0))
            yaw = math.atan2(dy, dx) + s.rng.gauss(0, sg); pit = math.asin(dz) + s.rng.gauss(0, sg)
            v = W["gun"]["speed"]
            s.bullets.append(dict(by=m.id, x=m.pos[0] + 3 * dx, y=m.pos[1] + 3 * dy, z=m.pos[2] + 7 + 3 * dz,
                                  vx=v * math.cos(pit) * math.cos(yaw), vy=v * math.cos(pit) * math.sin(yaw), vz=v * math.sin(pit), d=0.0, ox=m.pos[0], oy=m.pos[1]))
            m.cd[0] = W["gun"]["cooldown"] * m.cdm; m.ammo -= 1; s.shots += 1; s.sb[m.id] = s.sb.get(m.id, 0) + 1
            if m.ammo == 0:
                m.reload = m.rl
            s.sound("gun", m)
        if m.slot1 == "lightning" and f[1] and m.cd[1] <= 0 and m.energy >= 3:
            m.energy -= 3; m.last_spend = s.t; m.cd[1] = W["lightning"]["cooldown"]; s.sound("lightning", m)
            best = None
            for o in s.mechs:
                if o is m or not o.alive:
                    continue
                dx, dy = o.pos[0] - m.pos[0], o.pos[1] - m.pos[1]; d = math.hypot(dx, dy)
                if d < 45 and abs(wrap(math.atan2(dy, dx) - m.aim[0])) < math.radians(30) and (best is None or d < best[0]) \
                        and s.w.los((m.pos[0], m.pos[1], m.pos[2] + 7), (o.pos[0], o.pos[1], o.pos[2] + 4.5), 2.5):
                    best = (d, o)
            if best:
                o = best[1]; s.damage(o, 60, "lightning", m.pos, m.id)
                cur = o; hit = {o.id}; amt = 60
                for _ in range(2):
                    nxt = [q for q in s.mechs if q.alive and q is not m and q.id not in hit and math.hypot(q.pos[0] - cur.pos[0], q.pos[1] - cur.pos[1]) < 14]
                    if not nxt:
                        break
                    cur = nxt[0]; hit.add(cur.id); amt *= 0.7; s.damage(cur, amt, "lightning", m.pos, m.id)
        if m.slot1 == "laser":
            if f[1] and m.cd[1] <= 0:
                m.charge = min(m.charge + DT, 2.75)
                if m.charge >= 2.75:
                    f = [f[0], False]
            if (not f[1]) and m.charge > 0:
                if m.charge >= W["laser"]["min_charge"]:
                    frac = 0.35 + 0.65 * min(m.charge, 0.75) / 0.75
                    s.laser_shot(m, W["laser"]["damage"] * frac)
                    m.cd[1] = W["laser"]["cooldown"]; m.charge = 0.0
                else:
                    m.charge += DT

    def laser_shot(s, m, dmg):
        dx, dy, dz = s.aim_vec(m); o = (m.pos[0], m.pos[1], m.pos[2] + 7)
        s.sound("laser", m)
        if m.id == 0: s.lz_shot = getattr(s, 'lz_shot', 0) + 1
        for k in range(1, 190):
            d = k * 1.5; x, y, z = o[0] + dx * d, o[1] + dy * d, o[2] + dz * d
            if s.w.solid(x, y, z) or z < s.w.ground(x, y):
                return
            for q in s.mechs:
                if q is not m and q.alive and math.hypot(q.pos[0] - x, q.pos[1] - y) < 2.5 and q.pos[2] <= z <= q.pos[2] + 9:
                    s.damage(q, dmg, "laser", o, m.id)
                    if m.id == 0: s.lz_hit = getattr(s, 'lz_hit', 0) + 1
                    return

    def step_bullets(s):
        keep = []
        for b in s.bullets:
            dead = False
            n = 9
            for _ in range(n):
                b["x"] += b["vx"] * DT / n; b["y"] += b["vy"] * DT / n; b["z"] += b["vz"] * DT / n
                b["d"] += math.sqrt(b["vx"] ** 2 + b["vy"] ** 2 + b["vz"] ** 2) * DT / n
                if b["d"] > 230 or s.w.solid(b["x"], b["y"], b["z"]) or b["z"] < s.w.ground(b["x"], b["y"]):
                    dead = True; break
                if b["by"] == 0:
                    hit = False
                    for nn in s.npcs:
                        if nn["alive"] and math.hypot(nn["pos"][0] - b["x"], nn["pos"][1] - b["y"]) < 2.1 and nn["pos"][2] <= b["z"] <= nn["pos"][2] + 3.5:
                            nn["hp"] -= 45 * s.mechs[0].dmgm; nn["agro"] = True; hit = True
                            if nn["hp"] <= 0:
                                nn["alive"] = False
                                for _k in range(2):
                                    s.cubes.append({"id": s.cube_id, "pos": [nn["pos"][0] + _k, nn["pos"][1], nn["pos"][2]]}); s.cube_id += 1
                            break
                    if not hit:
                        for c in s.crates:
                            if math.hypot(c["pos"][0] - b["x"], c["pos"][1] - b["y"]) < 1.7 and c["pos"][2] <= b["z"] <= c["pos"][2] + 2.4:
                                c["hp"] -= 45 * s.mechs[0].dmgm; hit = True
                                if c["hp"] <= 0:
                                    s.crates.remove(c); s.cubes.append({"id": s.cube_id, "pos": list(c["pos"])}); s.cube_id += 1
                                    if s.rng.random() < 0.5:
                                        s.pickups.append({"id": s.cube_id, "kind": s.rng.choice(["mortar", "laser", "lightning"]), "pos": list(c["pos"])}); s.cube_id += 1
                                break
                    if hit:
                        dead = True; break
                for q in s.mechs:
                    if q.alive and q.id != b["by"] and math.hypot(q.pos[0] - b["x"], q.pos[1] - b["y"]) < 2.8 and q.pos[2] <= b["z"] <= q.pos[2] + 9:
                        s.damage(q, 45, "gun", (b["ox"], b["oy"]), b["by"]); s.hb[b["by"]] = s.hb.get(b["by"], 0) + 1; dead = True; break
                if dead:
                    break
            if not dead:
                keep.append(b)
        s.bullets = keep

    def env_step(s):
        me = s.mechs[0]
        if s.drops_called < 3 and s.t >= (70, 140, 210)[s.drops_called]:
            zc_, zr_, p_, _ = s.zone()
            while True:
                a = s.rng.uniform(0, TAU); r = p_["nr"] * 0.7 * math.sqrt(s.rng.random())
                q = (p_["nc"][0] + r * math.cos(a), p_["nc"][1] + r * math.sin(a))
                if not s.w.blocked_nav(*q):
                    break
            s.drops.append({"id": 20000 + s.drops_called, "pos": [q[0], q[1], s.w.ground(*q)], "land": s.t + 25, "prog": 0.0})
            s.drops_called += 1
        for d in list(s.drops):
            if s.t < d["land"] or not me.alive:
                continue
            ins = [m for m in s.mechs if m.alive and math.hypot(m.pos[0] - d["pos"][0], m.pos[1] - d["pos"][1]) < 8]
            if len(ins) == 1 and ins[0].id == 0:
                d["prog"] += DT
                if d["prog"] >= 4:
                    s.drops.remove(d)
                    for _k in range(3):
                        s.cubes.append({"id": s.cube_id, "pos": [d["pos"][0] + _k, d["pos"][1], d["pos"][2]]}); s.cube_id += 1
                    me.hp = me.max_hp; me.energy = 100.0
            elif len(ins) >= 1 and d["prog"] > 0:
                d["prog"] = 0.0
        for n in s.npcs:
            if not n["alive"] or not me.alive:
                continue
            d = math.hypot(me.pos[0] - n["pos"][0], me.pos[1] - n["pos"][1])
            seen = d < 45 and math.hypot(n["pos"][0] - n["home"][0], n["pos"][1] - n["home"][1]) < 30 and s.w.los((n["pos"][0], n["pos"][1], n["pos"][2] + 2), (me.pos[0], me.pos[1], me.pos[2] + 4.5), 3)
            if (n["agro"] or seen) and d < 90 and math.hypot(n["pos"][0] - n["home"][0], n["pos"][1] - n["home"][1]) < 60:
                tx, ty, sp = me.pos[0], me.pos[1], 5.0 if d > 30 else 0.0
            else:
                n["agro"] = False; tx, ty = n["home"]; sp = 5.0 if math.hypot(n["pos"][0] - tx, n["pos"][1] - ty) > 3 else 0.0
            dd = math.hypot(tx - n["pos"][0], ty - n["pos"][1]) or 1
            np_ = (n["pos"][0] + (tx - n["pos"][0]) / dd * sp * DT, n["pos"][1] + (ty - n["pos"][1]) / dd * sp * DT)
            if not s.w.blocked_nav(*np_):
                n["pos"][0], n["pos"][1] = np_; n["pos"][2] = s.w.ground(*np_)
            n["cd"] = max(0.0, n["cd"] - DT)
            if (n["agro"] or seen) and n["cd"] <= 0 and d < 60 and s.w.los((n["pos"][0], n["pos"][1], n["pos"][2] + 2), (me.pos[0], me.pos[1], me.pos[2] + 4.5), 3):
                n["cd"] = 1.0
                if s.rng.random() < 0.45:
                    s.damage(me, 25, "npc", n["pos"], None)
        if me.alive:
            for c in list(s.cubes):
                if math.hypot(c["pos"][0] - me.pos[0], c["pos"][1] - me.pos[1]) < 3.5:
                    s.cubes.remove(c); me.cubes += 1
                    k = min(me.cubes, 10)
                    new_max = 1400 * (1 + 0.04 * k) * (1.15 ** s.picked.count("plating"))
                    me.hp += new_max - me.max_hp; me.max_hp = new_max; me.dmgm = (1 + 0.04 * k) * (1.15 ** s.picked.count("gun_damage"))
                    s.events[0].append({"type": "pickup", "cubes": me.cubes})
                    if me.cubes in (1, 3, 6, 10, 15, 21, 28):
                        s.offer_q += 1
            if s.offer is None and s.offer_q > 0:
                s.offer_q -= 1; s.offer_n += 1
                ids = s.rng.sample(["plating", "armor", "servos", "capacitor", "reactor", "nanites", "gyros", "sensors", "gun_damage", "gun_rate", "gun_mag", "jets"], 3)
                s.offer = {"id": s.offer_n, "expires": s.t + 5, "choices": [{"id": i, "name": i, "desc": ""} for i in ids]}
            if s.offer and s.t >= s.offer["expires"]:
                s.apply_offer(0)

    def apply_offer(s, i):
        me = s.mechs[0]; u = s.offer["choices"][i]["id"]; s.offer = None
        s.picked.append(u); k = min(me.cubes, 10)
        if u == "plating":
            new_max = 1400 * (1 + 0.04 * k) * (1.15 ** s.picked.count("plating")); me.hp += new_max - me.max_hp; me.max_hp = new_max
        elif u == "armor":
            me.armor += 0.08
        elif u == "gun_mag":
            me.mag += 8; me.rl *= 0.8
        elif u == "gun_rate":
            me.cdm *= 0.87
        elif u == "gun_damage":
            me.dmgm = (1 + 0.04 * k) * (1.15 ** s.picked.count("gun_damage"))
        s.events[0].append({"type": "upgrade", "id": u})

    def vis_point(s, me, pos, h):
        dx, dy, dz = pos[0] - me.pos[0], pos[1] - me.pos[1], pos[2] - me.pos[2]
        d3 = math.sqrt(dx * dx + dy * dy + dz * dz)
        if d3 > 200 or (d3 > 45 and abs(wrap(math.atan2(dy, dx) - me.aim[0])) > math.radians(60)):
            return False
        return s.w.los((me.pos[0], me.pos[1], me.pos[2] + 8), (pos[0], pos[1], pos[2] + h), 3.0)

    # ------------------------------------------------------------ vision
    def can_see(s, a, o):
        dx, dy, dz = o.pos[0] - a.pos[0], o.pos[1] - a.pos[1], o.pos[2] - a.pos[2]
        d3 = math.sqrt(dx * dx + dy * dy + dz * dz); dh = math.hypot(dx, dy)
        if d3 > 200:
            return False
        if d3 > 45 and abs(wrap(math.atan2(dy, dx) - a.aim[0])) > math.radians(60):
            return False
        eye = (a.pos[0], a.pos[1], a.pos[2] + 8)
        return s.w.los(eye, (o.pos[0], o.pos[1], o.pos[2] + 4.5), 3.0) or s.w.los(eye, (o.pos[0], o.pos[1], o.pos[2] + 9), 3.0)

    def make_obs(s):
        me = s.mechs[0]; W = CFG["weapons"]
        vis = []
        for o in s.mechs[1:]:
            if not o.alive:
                continue
            if s.can_see(me, o):
                s.seen[o.id] = s.t
            if s.t - s.seen.get(o.id, -99) <= 5:
                vis.append({"id": o.id, "kind": "mech", "pos": list(o.pos), "vel": list(o.vel), "aim": list(o.aim), "hp": o.hp, "max_hp": o.max_hp,
                            "cubes": 0, "weapons": ["gun"] + ([o.slot1] if o.slot1 else []), "charging": o.charge > 0})
        for n in s.npcs:
            if n["alive"] and s.vis_point(me, n["pos"], 1.75):
                vis.append({"id": n["id"], "kind": "npc", "pos": list(n["pos"]), "vel": [0, 0, 0], "aim": [0, 0], "hp": n["hp"], "max_hp": 350})
        crates = [{"id": c["id"], "pos": c["pos"], "hp": c["hp"]} for c in s.crates if s.vis_point(me, c["pos"], 1.2)]
        cubes = [{"id": c["id"], "pos": c["pos"]} for c in s.cubes if s.vis_point(me, c["pos"], 0.5)]
        projs = [{"id": 1, "kind": "gun", "pos": [b["x"], b["y"], b["z"]], "vel": [b["vx"], b["vy"], b["vz"]]} for b in s.bullets
                 if b["by"] != 0 and math.hypot(b["x"] - me.pos[0], b["y"] - me.pos[1]) < 200]
        snd = []
        R = CFG["sound"]["range"]
        for (t, kind, x, y, i) in s.sounds:
            if i == 0 or s.t - t > 0.11 or kind not in R:
                continue
            d = math.hypot(x - me.pos[0], y - me.pos[1])
            if d <= R[kind]:
                snd.append({"kind": kind, "bearing": math.atan2(y - me.pos[1], x - me.pos[0]) + s.rng.gauss(0, math.radians(7)),
                            "dist": "near" if d < 60 else "mid" if d < 160 else "far", "age": s.t - t})
        zc, zr, p, dps = s.zone()
        ws = [{"kind": "gun", "cooldown": me.cd[0], "ammo": me.ammo, "reload": max(0, me.reload), "stats": dict(W["gun"], mag=me.mag, reload=me.rl, cooldown=0.24 * me.cdm)}, None]
        if me.slot1:
            ws[1] = {"kind": me.slot1, "cooldown": me.cd[1], "stats": dict(W[me.slot1])}
            if me.slot1 == "laser":
                ws[1]["charge"] = me.charge
        ev = s.events[0]; s.events[0] = []
        near_p = [{"id": q["id"], "kind": q["kind"], "pos": q["pos"]} for q in s.pickups if s.vis_point(me, q["pos"], 1.0)]
        return {"type": "obs", "tick": s.tick, "t": s.t, "alive": sum(1 for m in s.mechs if m.alive),
                "self": {"id": 0, "pos": list(me.pos), "vel": list(me.vel), "aim": list(me.aim), "hp": me.hp, "max_hp": me.max_hp, "energy": me.energy,
                         "max_energy": 100, "grounded": True, "cubes": me.cubes, "upgrades": list(s.picked), "in_zone": math.hypot(me.pos[0] - zc[0], me.pos[1] - zc[1]) < zr,
                         "weapons": ws, "stats": dict(CFG["mech"])},
                "mechs": vis, "projectiles": projs, "crates": crates, "cubes": cubes, "pickups": near_p, "sounds": snd, "beams": [], "events": ev, "drops": [{"id": d["id"], "pos": d["pos"], "radius": 8, "lands_in": max(0.0, d["land"] - s.t), "capture_s": 4, "progress": d["prog"], "holder": None} for d in s.drops],
                "zone": {"center": zc, "radius": zr, "next_center": p["nc"], "next_radius": p["nr"], "shrink_start": p["ss"], "shrink_end": p["se"], "dps": dps},
                "offer": s.offer}

    # ------------------------------------------------------------ house AI
    def house_think(s):
        me = s.mechs[0]
        squad = [m for m in s.mechs[1:] if m.alive]
        zc, zr, p, _ = s.zone()
        if me.alive:
            for m in squad:
                if s.can_see(m, me):
                    s.squad_known_until = s.t + 5.0; s.squad_last = list(me.pos); break
            # hearing: loud actions give a rough position
            if any(sd[4] == 0 and s.t - sd[0] < 0.11 and sd[1] in ("gun", "laser", "lightning") for sd in s.sounds):
                s.squad_known_until = max(s.squad_known_until, s.t + 3.0); s.squad_last = [me.pos[0] + s.rng.gauss(0, 25), me.pos[1] + s.rng.gauss(0, 25), me.pos[2]]
        known = me.alive and (s.intel == "omni" or s.t < s.squad_known_until)
        tpos = list(me.pos) if s.intel == "omni" else s.squad_last
        n_dead = 15 - len(squad)
        nh = (1 + n_dead // 2) if s.t < 300 else len(squad)
        squad.sort(key=lambda m: ((m.id - 1) // 4 if s.weakest_first else 0, math.hypot(m.pos[0] - me.pos[0], m.pos[1] - me.pos[1])))
        hunters = {m.id for m in squad[:nh]}
        for m in squad:
            h = s.house[m.id]; sig, lead, strafe = h["lv"]
            tgt = None
            hunting = known and m.id in hunters and tpos
            if not hunting and not s.all_shoot:
                sees = False
            dme = math.hypot(me.pos[0] - m.pos[0], me.pos[1] - m.pos[1]) if me.alive else 1e9
            sees = me.alive and dme < 200 and (m.id in hunters or s.all_shoot) and s.can_see(m, me)
            mv = (0.0, 0.0)
            if hunting or sees:
                tg = me.pos if sees else tpos
                dx, dy = tg[0] - m.pos[0], tg[1] - m.pos[1]; d = math.hypot(dx, dy) or 1
                ux, uy = dx / d, dy / d
                if s.t > h["until"]:
                    h["strafe"] = s.rng.choice((-1, 1)); h["until"] = s.t + s.rng.uniform(0.5, 1.2)
                rad = 0.8 if d > 70 else (-0.4 if d < 35 else 0.0)
                mv = (ux * rad - uy * h["strafe"] * strafe, uy * rad + ux * h["strafe"] * strafe)
                if not sees and d < 40:
                    mv = (ux * 0.6, uy * 0.6)
                aimp = list(me.pos) if sees else tg
                # lead
                tvx, tvy = (me.vel[0], me.vel[1]) if sees else (0, 0)
                tt = d / 252.0
                if m.slot1 == "laser" and sees and d < 250 and (m.charge > 0 or m.cd[1] <= 0):
                    tt = 0.12
                px, py = aimp[0] + tvx * tt * lead, aimp[1] + tvy * tt * lead
                yaw = math.atan2(py - m.pos[1], px - m.pos[0]) + s.rng.gauss(0, math.radians(sig))
                pit = math.atan2(me.pos[2] + 4.5 - (m.pos[2] + 7), d) if sees else 0
                m.cmd["aim"] = [yaw, pit]
                aerr = abs(wrap(yaw - m.aim[0]))
                lz = False
                if m.slot1 == "laser" and sees and d < 250 and m.cd[1] <= 0 and s.w.los((m.pos[0], m.pos[1], m.pos[2] + 7), (me.pos[0], me.pos[1], me.pos[2] + 4.5), 3):
                    lz = m.charge < 0.65
                m.cmd["fire"] = [bool(sees and d < 210 and aerr < 0.06 and s.w.los((m.pos[0], m.pos[1], m.pos[2] + 7), (me.pos[0], me.pos[1], me.pos[2] + 4.5), 3)), lz]
            else:
                # go to a spot in the next zone and sit
                tx, ty = p["nc"][0] + math.cos(m.id) * p["nr"] * 0.5, p["nc"][1] + math.sin(m.id) * p["nr"] * 0.5
                dx, dy = tx - m.pos[0], ty - m.pos[1]; d = math.hypot(dx, dy)
                mv = (dx / d, dy / d) if d > 10 else (0.0, 0.0)
                m.cmd["fire"] = [False, False]
            # simple obstacle steering and zone bias
            if mv != (0.0, 0.0):
                base = math.atan2(mv[1], mv[0]); ml = min(1.0, math.hypot(*mv))
                for off in (0, 0.5, -0.5, 1.0, -1.0, 1.6, -1.6, 2.4):
                    a = base + off
                    if not s.w.blocked_nav(m.pos[0] + 5 * math.cos(a), m.pos[1] + 5 * math.sin(a)):
                        mv = (ml * math.cos(a), ml * math.sin(a)); break
            if math.hypot(m.pos[0] - zc[0], m.pos[1] - zc[1]) > zr - 15:
                dx, dy = zc[0] - m.pos[0], zc[1] - m.pos[1]; d = math.hypot(dx, dy) or 1
                mv = (dx / d, dy / d)
            m.cmd["move"] = list(mv); m.cmd["boost"] = False
            m.cmd["reload"] = m.ammo < 4

    # ------------------------------------------------------------ bot step
    def bot_act(s):
        me = s.mechs[0]
        if not me.alive:
            return
        obs = s.make_obs()
        t0 = time.perf_counter()
        try:
            a = s.bot.act(obs) or {}
        except Exception:
            if os.environ.get('SIM_TRACE'):
                import traceback; traceback.print_exc()
            return  # a crashed bot coasts on its last action, as in the real game
        s.bot_ms.append((time.perf_counter() - t0) * 1000)
        c = me.cmd
        c["move"] = a.get("move", [0, 0]); c["boost"] = a.get("boost", False); c["aim"] = a.get("aim", c["aim"])
        c["fire"] = a.get("fire", [False, False]); c["reload"] = a.get("reload", False)
        if "pick" in a and s.offer:
            s.apply_offer(int(a["pick"]) if 0 <= int(a["pick"]) < 3 else 0)
        if "take" in a and (me.slot1 is None or a.get("drop") == 1):
            for q in s.pickups:
                if q["id"] == a["take"] and math.hypot(q["pos"][0] - me.pos[0], q["pos"][1] - me.pos[1]) < 4.2:
                    old = me.slot1; me.slot1 = q["kind"]; me.cd[1] = 1.0; s.pickups.remove(q); me.charge = 0.0
                    if old:
                        s.pickups.append({"id": s.cube_id, "kind": old, "pos": list(me.pos)}); s.cube_id += 1
                    break

    def run(s):
        s.bot_ms = []
        max_t = CFG["match"]["duration_s"]
        while s.t < max_t:
            if s.tick % 2 == 0:
                s.bot_act(); s.house_think()
            for m in s.mechs:
                if m.alive:
                    s.step_mech(m); s.weapons_fire(m)
            s.step_bullets(); s.env_step()
            s.sounds = [x for x in s.sounds if s.t - x[0] < 1.0]
            s.t += DT; s.tick += 1
            if not s.mechs[0].alive or sum(1 for m in s.mechs if m.alive) <= 1:
                break
        alive = sorted([m for m in s.mechs if m.alive], key=lambda m: -m.hp)
        me = s.mechs[0]
        if me.alive:
            place = 1 + sum(1 for m in alive if m.id != 0 and m.hp > me.hp) if len(alive) > 1 else 1
            if s.t < max_t:
                place = 1
        else:
            # place = 16 - number of mechs that died strictly before us on an earlier tick, with ties sharing
            before = sum(1 for tk, i in s.deaths if tk < s.tick - 1 and i != 0)
            tick_me = [tk for tk, i in s.deaths if i == 0][0]
            before = sum(1 for tk, i in s.deaths if tk < tick_me)
            place = 16 - before
        return dict(place=place, t=me.death_t if me.death_t is not None else s.t, kills=sum(1 for _ in []), dealt=me.dealt, taken=s.dmg_taken,
                    ms_max=max(s.bot_ms) if s.bot_ms else 0, ms_mean=sum(s.bot_ms) / max(1, len(s.bot_ms)), dbw=s.dbw, lz=(getattr(s,'lz_hit',0), getattr(s,'lz_shot',0)), cubes=me.cubes, me_acc=(s.hb.get(0, 0), s.sb.get(0, 0)), hs_acc=(sum(v for k, v in s.hb.items() if k), sum(v for k, v in s.sb.items() if k)), w1=me.slot1, squad_dead_at_end=15 - sum(1 for m in s.mechs[1:] if m.alive))


def load_bot(path):
    spec = importlib.util.spec_from_file_location("botmod", path)
    mod = importlib.util.module_from_spec(spec); sys.modules["botmod"] = mod; spec.loader.exec_module(mod)
    return mod.Bot


def one(args):
    seed, intel, path = args
    sim = Sim(seed, load_bot(path), intel)
    r = sim.run(); r["seed"] = seed
    return r


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    procs = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    intel = sys.argv[3] if len(sys.argv) > 3 else "vision"
    path = sys.argv[4] if len(sys.argv) > 4 else os.path.join(ROOT, "bot.py")
    t0 = time.time()
    with Pool(procs) as p:
        res = p.map(one, [(1000 + i, intel, path) for i in range(n)])
    sc = [16 - r["place"] for r in res]
    print("our hit rate %.2f, house hit rate %.2f" % (sum(r["me_acc"][0] for r in res) / max(1, sum(r["me_acc"][1] for r in res)), sum(r["hs_acc"][0] for r in res) / max(1, sum(r["hs_acc"][1] for r in res))))
    tot = {}
    for r in res:
        for k, v in r["dbw"].items(): tot[k] = tot.get(k, 0) + v / n
    print("dmg taken per match by weapon", {k: round(v) for k, v in tot.items()})
    print("our laser hits/shots", sum(r["lz"][0] for r in res), sum(r["lz"][1] for r in res))
    print("avg cubes %.1f, with weapon %.0f%%, avg death t %.0f" % (sum(r["cubes"] for r in res) / n, 100 * sum(1 for r in res if r["w1"]) / n, sum(r["t"] for r in res) / n))
    for r in res:
        print(f"seed {r['seed']} place {r['place']:2d} died {r['t']:6.1f}s dealt {r['dealt']:6.0f} taken {r['taken']:6.0f} squad_dead {r['squad_dead_at_end']} ms_mean {r['ms_mean']:.2f} max {r['ms_max']:.1f}")
    print(f"mean score {sum(sc)/len(sc):.2f} (n={n}, intel={intel}) in {time.time()-t0:.0f}s")
