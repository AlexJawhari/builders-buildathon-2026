import json,time,sys
sys.path.insert(0,'/home/alex/projects/buildathon/blast')
import mapper,patchlib
SRC='/home/alex/projects/buildathon/blast_work/src'
ev=sys.argv[1]
tests=json.load(open(ev+'/tests.json'))['tests']
ans=json.load(open(ev+'/answers.json'))['commits']
mp=mapper.Mapper('/home/alex/projects/buildathon/blast/data',SRC,tests)
def orig(p):
    try: return open(SRC+'/'+p).read()
    except OSError: return None
nmiss=0
for cid,a in sorted(ans.items()):
    fds=patchlib.parse(open(f'{ev}/commits/{cid}.patch').read())
    bits,notes=mp.candidates(fds,orig)
    nums=[i for i in range(len(tests)) if bits>>i&1]
    s=sum(tests[i]['seconds'] for i in nums)
    miss_t=[f for f in a['failing'] if not bits>>f&1]
    nmiss+=bool(miss_t)
    print(cid,a['kind'],'fail',len(a['failing']),'cand',len(nums),round(s,1),'MISS' if miss_t else '',miss_t[:6],[fd.path.split('/')[-1] for fd in fds][:2])
print('misses',nmiss)
