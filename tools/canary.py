"""Pick canary tests: greedy line cover per CPU second under a CPU budget. usage: canary.py BUDGET OUT_JSON"""
import sys,json
sys.path.insert(0,'/home/alex/projects/buildathon/blast/tools')
import sim
mp=sim.mp; T=sim.tests; cpu=sim.cpu_est
cover={}
for rel in mp.sqlglot_files():
    for l,b in mp.lines(rel).items():
        while b:
            low=b&-b; cover.setdefault(low.bit_length()-1,set()).add((rel,l)); b^=low
universe=set().union(*cover.values())
cands=[t for t in cover if T[t]['seconds']<3 and 'test_executor' not in T[t]['file']]
chosen=[]; cov=set(); spent=0.0; budget=float(sys.argv[1])
while True:
    best=None
    for t in cands:
        if t in chosen: continue
        gain=len(cover[t]-cov)
        if gain and (best is None or gain/(cpu[t]+0.003)>best[0]): best=(gain/(cpu[t]+0.003),t)
    if not best or spent+cpu[best[1]]>budget: break
    chosen.append(best[1]); cov|=cover[best[1]]; spent+=cpu[best[1]]
print(len(chosen),'canaries cpu',round(spent,2),'line coverage',round(len(cov)/len(universe),3),'files',len({T[t]['file'] for t in chosen}))
json.dump(chosen,open(sys.argv[2],'w'))
