"""Offline policy simulator: cost = simulated seconds + modeled CPU, using true labels."""
import json,sys,os
sys.path.insert(0,'/home/alex/projects/buildathon/blast')
import mapper,patchlib,selector
SRC='/home/alex/projects/buildathon/blast_work/src'
tests=json.load(open('/home/alex/projects/buildathon/blast/sample/tests.json'))['tests']
ms=[round(t['seconds']*1000) for t in tests]
cpum=json.load(open('/home/alex/projects/buildathon/blast/data/cpu.json'))
cpu_est=[cpum.get(str(t['number']),t['seconds']) for t in tests]
mp=mapper.Mapper('/home/alex/projects/buildathon/blast/data',SRC,tests)
orig=lambda p: open(SRC+'/'+p).read() if os.path.exists(SRC+'/'+p) else None
def load(dirs):
    out=[]
    for d,kind in dirs:
        if kind=='sample':
            for k,v in json.load(open(d+'/answers.json'))['commits'].items(): out.append((k,v['failing'],f'{d}/commits/{k}.patch'))
        else:
            for m in sorted(os.listdir(d)):
                if os.path.exists(f'{d}/{m}/labels.json'):
                    fl=json.load(open(f'{d}/{m}/labels.json'))['failing']
                    if fl==[-1]: fl=list(range(len(tests)))
                    fl=[x for x in fl if x>=0]
                    out.append((m,fl,f'{d}/{m}/{m}.patch'))
    return out
def cost(ws): 
    ws=[w for w in ws if w]
    return (max(sum(ms[n] for n in w) for w in ws)+2000*len(ws))/1000 if ws else 0
def simulate(rows, cpu_fix=0.4, cpu_mul=1.1, heavy_gamble=True, min_ev=6):
    tot=0; miss=0; cpu=0; detail=[]
    for cid,fl,pf in rows:
        bits,_=mp.candidates(patchlib.parse(open(pf).read()),orig)
        assumed=bits_n(mp.assumed&mp.all_bits); bits&=~mp.assumed; nums=bits_n(bits)
        heavy=[n for n in nums if tests[n]['seconds']>=selector.HEAVY]; rest=[n for n in nums if n not in heavy]
        s=sum(cpu_est[n] for n in rest); printed=selector.pack(rest,ms)[1]/1000
        if not nums: final=assumed; c=0
        elif not rest or s>selector.RUN_MAX or (not heavy and s*1.1+0.4>printed): final=nums+assumed; c=0
        else:
            c=cpu_fix+cpu_mul*s
            F=[f for f in fl if f in rest]
            if heavy:
                if F or len(rest)<min_ev or not heavy_gamble:
                    final=rest+assumed+heavy; c=cpu_fix+cpu_mul*min(s, 0.5*s if F else s)  # failfast
                else: final=assumed
            else: final=F+assumed
        got=set(final); m=[f for f in fl if f not in got]
        cst=selector.pack(final,ms)[1]/1000 if final else 0
        tot+=cst+c+300*bool(m); miss+=bool(m); cpu+=c
        detail.append((cid,round(cst,1),round(c,1),'MISS' if m else ''))
    return tot,miss,cpu,detail
def bits_n(b): return selector.bits_to_nums(b)
if __name__=='__main__':
    rows=load([('/home/alex/projects/buildathon/blast/sample','sample')]+[(d,'mut') for d in sys.argv[1:]])
    for g in (True,False):
        t,m,c,d=simulate(rows,heavy_gamble=g)
        print('gamble',g,'n',len(rows),'total',round(t,1),'per commit',round(t/len(rows),2),'misses',m,'cpu',round(c,1))
