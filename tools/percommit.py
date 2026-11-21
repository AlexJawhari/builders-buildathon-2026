import json,sys
ev,outf=sys.argv[1],sys.argv[2]
tests=json.load(open(ev+'/tests.json'))['tests']; ans=json.load(open(ev+'/answers.json'))['commits']
ms=[round(t['seconds']*1000) for t in tests]
tot=0
for l in open(outf):
    o=json.loads(l); ws=[w for w in o['workers'] if w]
    c=(max(sum(ms[n] for n in w) for w in ws)+2000*len(ws)) if ws else 0
    fl=ans[o['commit']]['failing']; got={n for w in ws for n in w}
    miss=any(f not in got for f in fl)
    tot+=c+300000*miss
    print(o['commit'],ans[o['commit']]['kind'],'cost',c/1000,'nw',len(ws),'nsel',len(got),'fail',len(fl),'MISS' if miss else '')
print(tot/1000)
