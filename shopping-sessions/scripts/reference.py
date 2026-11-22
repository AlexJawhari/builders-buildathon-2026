#!/usr/bin/env python3
"""Slow, independent reference written straight from rules.md. Usage: reference.py DATA_DIR OUT_DIR
Writes OUT_DIR/user_daily.csv and campaign_daily.csv (same layout as dev-answer)."""
import csv, glob, json, os, re, sys
from datetime import datetime, timezone, timedelta

DATA, OUT = sys.argv[1], sys.argv[2]
DAY = 86400000
WEEK = 7 * DAY


def split_line(line, n):
    """Return list of n field values or None if malformed (rules.md CSV dialect)."""
    parts = line.split(",")
    if len(parts) != n:
        return None
    out = []
    for p in parts:
        if '"' in p:
            if len(p) >= 2 and p[0] == '"' and p[-1] == '"' and '"' not in p[1:-1]:
                p = p[1:-1]
            else:
                return None
        out.append(p)
    return out


def read_rows(folder, n):
    for path in sorted(glob.glob(os.path.join(DATA, folder, "*.csv"))):
        with open(path, "rb") as f:
            raw = f.read().decode("utf-8")
        lines = raw.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        for line in lines[1:]:
            r = split_line(line, n)
            if r is not None:
                yield r


ASCII_DIGITS = re.compile(r"[0-9]+\Z")
F1 = re.compile(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(Z|[+-]\d\d:\d\d)\Z", re.A)


def ts_ms(s):
    if ASCII_DIGITS.match(s):
        return int(s)
    m = F1.match(s)
    if not m:
        return None
    y, mo, d, h, mi, se = (int(m.group(i)) for i in range(1, 7))
    z = m.group(7)
    off = 0
    if z != "Z":
        oh, om = int(z[1:3]), int(z[4:6])
        if oh > 23 or om > 59:
            return None
        off = (oh * 60 + om) * 60000 * (1 if z[0] == "+" else -1)
    try:
        dt = datetime(y, mo, d, h, mi, se, tzinfo=timezone.utc)
    except ValueError:
        return None
    return int((dt - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds()) * 1000 - off


man = json.load(open(os.path.join(DATA, "manifest.json")))
WS, WE = ts_ms(man["window_start"]), ts_ms(man["window_end"])

cat_of = {}
with open(os.path.join(DATA, "products.csv"), encoding="utf-8") as f:
    for r in list(csv.reader(f))[1:]:
        cat_of[r[0]] = r[1]
chan = {}
with open(os.path.join(DATA, "campaigns.csv"), encoding="utf-8") as f:
    for r in list(csv.reader(f))[1:]:
        chan[r[0]] = r[1]

# links
parent = {}


def find(x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


for a, b in read_rows("links", 2):
    if a == "" or b == "":
        continue
    parent.setdefault(a, a)
    parent.setdefault(b, b)
    ra, rb = find(a), find(b)
    if ra != rb:
        parent[ra] = rb
members = {}
for x in parent:
    members.setdefault(find(x), []).append(x)
name = {}
for r, ms in members.items():
    m = min(ms, key=lambda s: s.encode())
    for x in ms:
        name[x] = m


def person(u):
    return name.get(u, u)


# events
best = {}
for r in read_rows("events", 7):
    eid, uid, ts, ing, ty, pid, price = r
    if eid == "":
        continue
    ingv = ts_ms(ing)
    cur = best.get(eid)
    if cur is None or ingv < cur[0]:
        best[eid] = (ingv, r)
events = []  # (person, ts, type, price, cat)
for eid, (ingv, r) in best.items():
    _, uid, ts, ing, ty, pid, price = r
    t = ts_ms(ts)
    if uid == "" or t is None or not (WS <= t < WE):
        continue
    if ty not in ("view", "cart", "purchase") or pid == "":
        continue
    p = 0
    if ty == "purchase":
        if not ASCII_DIGITS.match(price) or not (1 <= int(price) <= 10_000_000):
            continue
        p = int(price)
    if ingv - t > DAY:
        continue
    events.append((person(uid), t, ty, p, cat_of.get(pid, "unknown")))

# clicks
clicks = {}
for r in read_rows("clicks", 4):
    cid, uid, camp, ts = r
    if cid == "" or uid == "":
        continue
    t = ts_ms(ts)
    if t is None or not (WS - WEEK <= t < WE) or camp not in chan:
        continue
    clicks.setdefault(person(uid), set()).add((t, cid, camp))

byp = {}
for e in events:
    byp.setdefault(e[0], []).append(e)

ud = {}
cd = {}


def cday(t):
    return datetime.fromtimestamp(t // 1000, timezone.utc).date().isoformat()


for p, evs in byp.items():
    evs.sort(key=lambda e: e[1])
    sessions = []
    for e in evs:
        if not sessions or e[1] - sessions[-1][-1][1] > 30 * 60000:
            sessions.append([])
        sessions[-1].append(e)
    cl = sorted(clicks.get(p, []), key=lambda c: (c[0], c[1]))
    for s in sessions:
        start = s[0][1]
        day = cday(start)
        rev = sum(e[3] for e in s)
        key = (p, day)
        u = ud.setdefault(key, [0, 0, 0, {}])
        u[0] += 1
        u[1] += len(s)
        u[2] += rev
        for e in s:
            if e[2] == "purchase":
                u[3][e[4]] = u[3].get(e[4], 0) + e[3]
        win = [c for c in cl if 0 <= start - c[0] <= WEEK]
        if win:
            mx = max(c[0] for c in win)
            camp = min((c for c in win if c[0] == mx), key=lambda c: c[1].encode())[2]
        else:
            camp = "organic"
        c = cd.setdefault((camp, day), [0, 0, 0, 0])
        c[0] += 1
        c[1] += any(e[2] == "purchase" for e in s)
        c[2] += rev
        for k in {c[2] for c in win}:
            cd.setdefault((k, day), [0, 0, 0, 0])[3] += 1

os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT, "user_daily.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["user_id", "day", "sessions", "events", "revenue_cents", "top_category"])
    for (p, day), (s, n, rev, cats) in sorted(ud.items()):
        top = ""
        if cats:
            top = sorted(cats.items(), key=lambda kv: (-kv[1], kv[0].encode()))[0][0]
        w.writerow([p, day, s, n, rev, top])
with open(os.path.join(OUT, "campaign_daily.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["campaign_id", "channel", "day", "sessions", "purchasing_sessions", "revenue_cents", "assisted_sessions"])
    for (camp, day), v in sorted(cd.items()):
        w.writerow([camp, "organic" if camp == "organic" else chan[camp], day] + v)
