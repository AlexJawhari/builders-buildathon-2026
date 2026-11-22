"""Calibration: run each real-scored commit's bot.py in the sim and compare with the real board score.
    python3 sim/calib.py [n_matches] [procs] [intel] [tag]   -> prints table + Spearman, appends sim/calib.csv
"""
import os, subprocess, sys, time
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sim as S

REAL = [("547637f", 2.40), ("023fe24", 2.94), ("db23d0f", 2.98), ("fdeb3bf", 2.38), ("f0273fe", 3.06), ("8aa5d44", 3.10),
        ("a865161", 3.58), ("4cbe53d", 3.35), ("e46eb8d", 4.04), ("5b1ea9f", 4.35), ("d26c385", 4.31), ("8301b89", 4.46),
        ("855c858", 4.54), ("c78132c", 4.75)]


def rank(v):
    o = sorted(range(len(v)), key=lambda i: v[i]); r = [0.0] * len(v); i = 0
    while i < len(o):
        j = i
        while j + 1 < len(o) and v[o[j + 1]] == v[o[i]]:
            j += 1
        for k in range(i, j + 1):
            r[o[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(a, b):
    ra, rb = rank(a), rank(b); n = len(a); ma = sum(ra) / n; mb = sum(rb) / n
    c = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    return c / (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5


def bot_path(commit, tmp="/tmp/mech_calib"):
    os.makedirs(tmp, exist_ok=True); p = os.path.join(tmp, commit + ".py")
    if not os.path.exists(p):
        open(p, "wb").write(subprocess.check_output(["git", "-C", S.ROOT, "show", commit + ":bot.py"]))
    return p


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    procs = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    intel = sys.argv[3] if len(sys.argv) > 3 else "vision"
    tag = sys.argv[4] if len(sys.argv) > 4 else ""
    sims = []
    with Pool(procs) as pool:
        for c, real in REAL:
            res = pool.map(S.one, [(1000 + i, intel, bot_path(c)) for i in range(n)])
            m = sum(16 - r["place"] for r in res) / n
            sims.append(m)
            print(f"{c} real {real:.2f} sim {m:.2f}", flush=True)
            open(os.path.join(S.HERE, "calib.csv"), "a").write(f"{tag},{intel},{n},{c},{real},{m:.3f}\n")
    reals = [r for _, r in REAL]
    d = [s - r for s, r in zip(sims, reals)]
    print(f"Spearman {spearman(sims, reals):.2f}  mean gap {sum(d)/len(d):.2f}  gap sd {(sum((x - sum(d)/len(d))**2 for x in d)/len(d))**0.5:.2f}")
