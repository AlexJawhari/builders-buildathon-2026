import json,sys,os
sys.path.insert(0,'/home/alex/projects/buildathon/blast')
import mapper,patchlib
SRC='/home/alex/projects/buildathon/blast_work/src'
tests=json.load(open('/home/alex/projects/buildathon/blast/sample/tests.json'))['tests']
mp=mapper.Mapper('/home/alex/projects/buildathon/blast/data',SRC,tests)
orig=lambda p: open(SRC+'/'+p).read() if os.path.exists(SRC+'/'+p) else None
H=[n for n,t in enumerate(tests) if t['seconds']>=3]
sets=[('/home/alex/projects/buildathon/blast/sample','sample')]
for d in sys.argv[1:]: sets.append((d,'mut'))
rows=[]
for d,kind in sets:
    if kind=='sample':
        ans={k:v['failing'] for k,v in json.load(open(d+'/answers.json'))['commits'].items()}
        pf=lambda c: f'{d}/commits/{c}.patch'
    else:
        ans={m:json.load(open(f'{d}/{m}/labels.json'))['failing'] for m in sorted(os.listdir(d)) if os.path.exists(f'{d}/{m}/labels.json')}
        pf=lambda c,d=d: f'{d}/{c}/{c}.patch'
    for c,fl in ans.items():
        bits,_=mp.candidates(patchlib.parse(open(pf(c)).read()),orig)
        bits&=~mp.assumed
        cand={i for i in range(len(tests)) if bits>>i&1}
        miss=[f for f in fl if f not in cand]
        h=[x for x in cand if x in H]; r=cand-set(h)
        fr=[f for f in fl if f in r]; fh=[f for f in fl if f in h]
        rows.append((c,len(r),len(h),len(fr),len(fh),len(miss)))
        if miss: print('COVERAGE MISS',c,miss[:5],len(miss))
n=len(rows)
print('commits',n,'with heavy cand',sum(1 for r in rows if r[2]))
for r in rows:
    if r[2]: print(r)
