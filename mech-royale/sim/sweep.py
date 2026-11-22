"""Random search over the bot's P dict in the approximate sim. Appends one CSV row per evaluated config.
    setsid nohup python3 sim/sweep.py --base HEAD --configs 300 --n 60 --procs 4 --csv sim/sweep.csv > sim/sweep.log 2>&1 &
Stages: A random configs (seeds 2000..), B top --top configs re-run on fresh seeds (3x matches), C mutate the
best until --hours run out. Same seeds for every config in a stage (common random numbers). The sim is optimistic and
approximate: only the ranking between configs is of interest, and the winners must still be scored on the real board.
Space entries: key -> list (choice) or (lo, hi) tuple (uniform; int if both ints). Edit SPACE or pass --space file.json.
"""
import argparse, csv, json, os, random, subprocess, sys, time
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sim as S

SPACE = {
    "retreat_hp": (0.25, 0.6), "outnum": [2, 3, 4], "lo": (30, 70), "hi": (70, 150), "home_frac": (0.6, 1.0),
    "strafe": (0.5, 1.6), "flip_lo": (0.2, 0.6), "flip_hi": (0.8, 1.6), "tol": (0.6, 2.0),
    "farm_until": (60, 300), "farm_r": (50, 160), "explore": (60, 180), "pick_r": (50, 140), "pick_r0": (60, 200),
    "lcap": (80, 240), "cube_r": (30, 90), "scan": (0.1, 0.5), "hyst": [0, 1], "heal_to": (0.6, 0.98),
    "dboost": [0, 1], "dcross": (3.5, 9.0), "dwt": (0.8, 3.0), "dmin_e": (5, 40), "endg": [0, 1], "ring_frac": (0.5, 0.95),
    "circ": [0, 1], "engage": [0, 1], "fire_max": [90, 140, 999], "kcube": [0, 1], "avoid": [0, 100, 150],
}


def sample(rng, space, keys=None):
    out = {}
    for k, v in space.items():
        if keys and k not in keys:
            continue
        if isinstance(v, list):
            out[k] = rng.choice(v)
        else:
            lo, hi = v
            out[k] = rng.randint(lo, hi) if isinstance(lo, int) and isinstance(hi, int) else round(rng.uniform(lo, hi), 3)
    return out


def mutate(rng, best, space, rate=0.3):
    out = dict(best)
    for k, v in space.items():
        if k in out and rng.random() < rate:
            out[k] = sample(rng, {k: v})[k] if isinstance(v, list) else sample(rng, {k: (max(v[0], out[k] - (v[1] - v[0]) * 0.25), min(v[1], out[k] + (v[1] - v[0]) * 0.25))})[k]
    return out


def work(a):
    cid, seed, intel, path, params = a
    os.environ["MECH_P"] = json.dumps(params)
    r = S.one((seed, intel, path))
    return cid, 16 - r["place"]


def evaluate(pool, cfgs, seeds, intel, path):
    tasks = [(cid, sd, intel, path, p) for cid, p in cfgs for sd in seeds]
    acc = {cid: [] for cid, _ in cfgs}
    for cid, sc in pool.imap_unordered(work, tasks, chunksize=2):
        acc[cid].append(sc)
    res = {}
    for cid, v in acc.items():
        n = len(v); m = sum(v) / n
        res[cid] = (m, (sum((x - m) ** 2 for x in v) / max(1, n - 1) / n) ** 0.5)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="HEAD"); ap.add_argument("--configs", type=int, default=300); ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--procs", type=int, default=4); ap.add_argument("--csv", default=os.path.join(S.HERE, "sweep.csv"))
    ap.add_argument("--intel", default="vision"); ap.add_argument("--top", type=int, default=12); ap.add_argument("--hours", type=float, default=0.0)
    ap.add_argument("--space"); ap.add_argument("--seed", type=int, default=1); ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--fixed", default="{}", help="JSON params applied to every config (e.g. the base variant's settings)")
    a = ap.parse_args()
    space = json.load(open(a.space)) if a.space else SPACE
    src = subprocess.check_output(["git", "-C", S.ROOT, "show", a.base + ":bot.py"]).decode()
    space = {k: (v if isinstance(v, list) else tuple(v)) for k, v in space.items() if '"%s"' % k in src}
    path = "/tmp/sweep_base_%s.py" % a.base.replace("/", "_"); open(path, "w").write(src)
    rng = random.Random(a.seed); fixed = json.loads(a.fixed); t_end = time.time() + a.hours * 3600
    new = not os.path.exists(a.csv); f = open(a.csv, "a", newline=""); w = csv.writer(f)
    if new:
        w.writerow(["time", "stage", "base", "intel", "n", "mean", "se", "params"]); f.flush()
    seen = []

    def log(stage, n, cid, p, m, se):
        w.writerow([int(time.time()), stage, a.base, a.intel, n, round(m, 3), round(se, 3), json.dumps(p, sort_keys=True)]); f.flush()
        seen.append((m, p))

    with Pool(a.procs) as pool:
        seedsA = list(range(2000, 2000 + a.n))
        base = evaluate(pool, [(0, dict(fixed))], seedsA, a.intel, path)[0]; log("A0-base", a.n, 0, fixed, *base)
        for b in range(0, a.configs, a.batch):
            cfgs = [(i, {**fixed, **sample(rng, space)}) for i in range(b + 1, min(a.configs, b + a.batch) + 1)]
            for cid, (m, se) in evaluate(pool, cfgs, seedsA, a.intel, path).items():
                log("A", a.n, cid, dict(cfgs)[cid], m, se)
        top = sorted(seen, key=lambda x: -x[0])[:a.top]
        seedsB = list(range(5000, 5000 + 3 * a.n))
        cfgs = [(i, p) for i, (_, p) in enumerate(top)]
        scoreB = []
        for cid, (m, se) in evaluate(pool, cfgs, seedsB, a.intel, path).items():
            log("B", 3 * a.n, cid, dict(cfgs)[cid], m, se); scoreB.append((m, dict(cfgs)[cid]))
        best = max(scoreB, key=lambda x: x[0])[1]; bestm = max(scoreB)[0]; rnd = 0
        while time.time() < t_end:
            rnd += 1; seeds = list(range(9000 + rnd * 7, 9000 + rnd * 7 + 3 * a.n))
            cfgs = [(0, best)] + [(i, mutate(rng, best, space)) for i in range(1, a.batch + 1)]
            res = evaluate(pool, cfgs, seeds, a.intel, path)
            for cid, (m, se) in res.items():
                log("C%d" % rnd, 3 * a.n, cid, dict(cfgs)[cid], m, se)
            cid = max(res, key=lambda c: res[c][0])
            if cid:
                best = dict(cfgs)[cid]


if __name__ == "__main__":
    main()
