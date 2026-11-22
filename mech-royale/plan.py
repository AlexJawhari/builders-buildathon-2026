"""Per-tick movement planner: stand + 16 walk + 8 boost directions, each rolled out 0.8 s with engine-like physics
(accelerate toward the wish velocity, slide along walls, boost energy) and scored on goal progress, range band, zone damage,
incoming shots (swept), exposure to enemy lines of sight, charging enemy lasers and a lateral juke. Pure functions."""
import math
import time

DT, N = 0.1, 8
W16 = [(math.cos(k * math.pi / 8), math.sin(k * math.pi / 8)) for k in range(16)]
W8 = W16[::2]
GOAL_W = {"idle": 2.0, "zone": 12.0, "retreat": 6.0, "loot": 5.0, "drop": 6.0, "fight": 4.0, "hunt": 5.0}


def wrap(a):
    return (a + math.pi) % math.tau - math.pi


def rollout(w, c, d, boost):
    """Positions after each 0.1 s step, wall-blocked step count and energy spent."""
    sp = (c.boost_speed if boost else c.walk_speed) * c.slow
    tx, ty = d[0] * sp, d[1] * sp
    x, y = c.pos
    vx, vy = c.vel
    acc = c.accel * DT
    en = c.energy
    pts = []
    blocked = 0
    for _ in range(N):
        dx, dy = tx - vx, ty - vy
        m = math.hypot(dx, dy)
        if m > acc:
            dx *= acc / m; dy *= acc / m
        vx += dx; vy += dy
        nx, ny = x + vx * DT, y + vy * DT
        if w.blocked_nav(nx, ny):
            blocked += 1
            if not w.blocked_nav(nx, y):
                ny = y; vy = 0.0
            elif not w.blocked_nav(x, ny):
                nx = x; vx = 0.0
            else:
                nx, ny = x, y; vx = vy = 0.0
        x, y = nx, ny
        pts.append((x, y))
        if boost:
            en -= c.boost_drain * DT
            if en <= 0:
                boost = False
                sp = c.walk_speed * c.slow
                tx, ty = d[0] * sp, d[1] * sp
    return pts, blocked, c.energy - max(en, 0.0)


def prep_shots(projs, pos, ground, gravity=22.0):
    """Predicted bullet tracks (N+1 points) near us, and mortar impacts (x, y, time) within 60 m. Bounded to 64 shots."""
    shots, shells = [], []
    for p in projs[:64]:
        px, py, pz = p["pos"]
        vx, vy, vz = p["vel"]
        if p.get("kind") == "mortar":
            t = 0.0
            while t < 7.0:
                t += 0.1
                x, y, z = px + vx * t, py + vy * t, pz + vz * t - 0.5 * gravity * t * t
                if z <= ground(x, y) + 0.5:
                    break
            if math.hypot(x - pos[0], y - pos[1]) < 60:
                shells.append((x, y, t))
            continue
        pts = [(px + vx * k * DT, py + vy * k * DT, pz + vz * k * DT) for k in range(N + 1)]
        if min(_segd(pos, pts[k], pts[k + 1]) for k in range(N)) > 32:
            continue
        shots.append((pts, 25.0 if p.get("kind") == "npc" else 45.0))
    return shots, shells


def _segd(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy
    s = 0.0 if L < 1e-9 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
    return math.hypot(a[0] + dx * s - p[0], a[1] + dy * s - p[1])


def shot_cost(c, mp):
    """Expected damage (x1.2) from bullets and shell blasts for the mech path mp (N+1 points)."""
    cost = 0.0
    rh = c.radius + 0.3 + 1.5
    mz = c.z + 4.5
    for pts, dmg in c.shots:
        worst = 0.0
        for k in range(N):
            b0, b1, m0, m1 = pts[k], pts[k + 1], mp[k], mp[k + 1]
            r0x, r0y = b0[0] - m0[0], b0[1] - m0[1]
            ex, ey = (b1[0] - m1[0]) - r0x, (b1[1] - m1[1]) - r0y
            L = ex * ex + ey * ey
            s = 0.0 if L < 1e-9 else max(0.0, min(1.0, -(r0x * ex + r0y * ey) / L))
            dm = math.hypot(r0x + ex * s, r0y + ey * s)
            if dm < rh and abs(b0[2] + (b1[2] - b0[2]) * s - mz) < 5.3:
                worst = max(worst, 1.2 * dmg * (1.0 - 0.5 * dm / rh))
        cost += worst
    for sx, sy, st in c.shells:
        m = mp[min(N, max(0, int(round(st / DT))))]
        d = math.hypot(m[0] - sx, m[1] - sy)
        if d < 7 + c.radius:
            cost += 1.2 * (200.0 if d < c.radius else 160.0 * (1 - 0.7 * min(1.0, d / 7)))
    return cost


def exposure(w, c, cand, t0, cap):
    """Extra cost/bonus from enemy lines of sight to the path's middle and end point (+ target LOS bonus)."""
    pts = cand["pts"]
    mid, end = pts[N // 2 - 1], pts[-1]
    gz = [w.ground(*mid), w.ground(*end)]
    cost = 0.0
    for th in c.threats:
        if time.perf_counter() - t0 > cap:
            break
        e = sum(1 for p, g in zip((mid, end), gz) if w.los(th["eye"], (p[0], p[1], g + 4.5), 3.0)) * 0.5
        if e <= 0:
            continue
        ang = abs(wrap(math.atan2(end[1] - th["eye"][1], end[0] - th["eye"][0]) - th["aim"])) if th["aim"] is not None else 0.0
        fac = 1.0 if ang < 0.3 else max(0.2, 1.0 - (ang - 0.3) / 1.2)
        cost += c.xw * th["dps"] * fac * e
        if th["chg"] is not None and e > 0:  # charging laser aimed at us: does the path still sit on the beam at release?
            k = min(N, max(1, int(th["chg"] / DT))) - 1
            p = pts[k]
            ux, uy = math.cos(th["aim"]), math.sin(th["aim"])
            rx, ry = p[0] - th["eye"][0], p[1] - th["eye"][1]
            if rx * ux + ry * uy > 0 and abs(rx * uy - ry * ux) < c.radius + 1.5:
                cost += 0.8 * 315.0
    if c.target is not None and c.armed and time.perf_counter() - t0 <= cap:
        if w.los((end[0], end[1], gz[1] + 7), c.target, 3.0):
            cost -= 30.0
    return cost


def choose(w, c, t0, cap=0.014):
    """Best (direction, boost) for the context c; direction (0, 0) means stand."""
    cands = [((0.0, 0.0), False)] + [(d, False) for d in W16]
    if c.can_boost:
        cands += [(d, True) for d in W8]
    scored = []
    px, py = c.pos
    for d, b in cands:
        pts, blocked, spent = rollout(w, c, d, b)
        mp = [(px, py)] + pts
        ex, ey = pts[-1][0] - px, pts[-1][1] - py
        s = 0.0
        if c.navdir is not None:
            s += c.gw * (ex * c.navdir[0] + ey * c.navdir[1])
            el = math.hypot(ex, ey)
            if el > 1:
                s += 4.0 * (ex * c.navdir[0] + ey * c.navdir[1]) / el
        elif c.goal is not None:  # no path: straight-line progress
            s += c.gw * (math.hypot(px - c.goal[0], py - c.goal[1]) - math.hypot(pts[-1][0] - c.goal[0], pts[-1][1] - c.goal[1]))
        if c.target is not None and c.pref:
            dt_ = math.hypot(pts[-1][0] - c.target[0], pts[-1][1] - c.target[1])
            s -= 1.5 * max(0.0, abs(dt_ - c.pref) - c.band)
        s -= 12.0 * min(1.0, blocked / 3.0) + 0.25 * spent
        if d == (0.0, 0.0):
            s += 1.0
        elif c.prev is not None:
            s -= 3.0 * abs(wrap(math.atan2(d[1], d[0]) - math.atan2(c.prev[1], c.prev[0]))) / math.pi
        for k in range(N):
            cx, cy, r = c.zones[k]
            dd = math.hypot(pts[k][0] - cx, pts[k][1] - cy)
            if dd > r:
                s -= DT * c.dps
            s -= max(0.0, dd - (r - 4.0))
        for nx_, ny_ in c.npcs:
            dn = math.hypot(pts[-1][0] - nx_, pts[-1][1] - ny_)
            if dn < 52:
                s -= 52 - dn
        s -= shot_cost(c, mp)
        if c.juke is not None:
            s += 22.0 * max(-1.0, min(1.0, (ex * c.juke[0] + ey * c.juke[1]) * c.juke[2] / 8.0))
        scored.append({"s": s, "d": d, "b": b, "pts": pts})
    scored.sort(key=lambda r: -r["s"])
    if c.threats or (c.target is not None and c.armed):
        for r in scored[:6]:
            if time.perf_counter() - t0 > cap:
                break
            r["s"] += -exposure(w, c, r, t0, cap)
        scored[:6] = sorted(scored[:6], key=lambda r: -r["s"])
    return scored[0]["d"], scored[0]["b"]
