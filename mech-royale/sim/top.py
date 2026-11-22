"""Print the best configs in a sweep CSV: python3 sim/top.py [csv] [k]  (stage B/C rows have 3x the matches, trust them more)."""
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1] if len(sys.argv) > 1 else "sim/sweep.csv")))
k = int(sys.argv[2]) if len(sys.argv) > 2 else 10
print("rows", len(rows), "base row:", [r["mean"] for r in rows if r["stage"] == "A0-base"][:1])
for r in sorted(rows, key=lambda r: (-float(r["mean"]) + 0.0))[:k]:
    print(r["stage"], r["n"], r["mean"], "+-" + r["se"], r["params"])
