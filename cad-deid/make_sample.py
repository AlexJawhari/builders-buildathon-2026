"""Pick a stratified recon sample from manifest.tsv -> sample list (path<TAB>bytes<TAB>urlpath)."""
import collections, os, random, sys
random.seed(7)
man, out, budget = sys.argv[1], sys.argv[2], int(float(sys.argv[3]))
rows = [l.rstrip('\n').split('\t') for l in open(man) if not l.startswith('#')]
rows = [r for r in rows if r[1] != '']
byext = collections.defaultdict(list)
for r in rows:
    byext[os.path.splitext(r[0])[1].lower() or '(none)'].append(r)
quota = {'.step':250,'.sat':80,'.sldprt':150,'.1':60,'.stl':80,'.pdf':200,'.svg':50,'.ipt':100,'.fcstd':70,'.stp':60,'.wrl':40,
 '.sldasm':90,'.zip':70,'.dxf':60,'.slddrw':90,'.jt':20,'.iam':70,'.prt':40,'.gz':25,'.asm':30,'.dwg':50,'.3mf':30,'.f3d':30,
 '.z':20,'.png':25,'.jpg':25,'.obj':25,'.igs':30,'.iges':25,'.par':30,'.x_b':20,'.unv':10,'.catpart':20,'.idw':30,'.tar':6}
CAP = 60e6
chosen = {}
def add(r):
    chosen[r[0]] = r
for e, lst in byext.items():
    q = quota.get(e, 8)
    ok = [r for r in lst if int(r[1]) <= CAP]
    random.shuffle(ok)
    # tiny files included too; spread by size: take half smallest-ish random, half random
    for r in ok[:q]: add(r)
# whole small repos with assemblies
repo_files = collections.defaultdict(list)
for r in rows:
    p = r[0].split('/')
    if len(p) > 3 and p[1] in ('github', 'gitlab'): repo_files[p[2]].append(r)
cand = [k for k, v in repo_files.items() if 30 <= len(v) <= 400 and sum(int(x[1]) for x in v) < 150e6
        and any(x[0].lower().endswith(('.sldasm', '.iam')) for x in v)]
random.shuffle(cand)
for k in cand[:6]:
    for r in repo_files[k]: add(r)
for r in rows:
    if r[0].startswith('archives/') and int(r[1]) < 15e6: add(r)
tot = sum(int(r[1]) for r in chosen.values())
print(len(chosen), tot / 1e9, 'GB', file=sys.stderr)
# trim to budget by dropping random largest-first files (not from whole repos is fine)
lst = sorted(chosen.values(), key=lambda r: -int(r[1]))
i = 0
while tot > budget and i < len(lst):
    tot -= int(lst[i][1]); chosen.pop(lst[i][0]); i += 1
print(len(chosen), tot / 1e9, 'GB after trim', file=sys.stderr)
with open(out, 'w') as f:
    for r in chosen.values(): f.write(f'{r[0]}\t{r[1]}\t{r[3]}\n')
