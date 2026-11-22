import os,sys,hashlib,collections,json
from concurrent.futures import ProcessPoolExecutor
import fitz
def one(p):
    out=[]
    try:
        d=fitz.open(p); seen=set()
        for pg in d:
            for im in pg.get_images(full=True):
                x=im[0]
                if x in seen: continue
                seen.add(x)
                try: b=d.xref_stream_raw(x)
                except Exception: continue
                out.append((hashlib.md5(b).hexdigest(),len(b),im[2],im[3]))
    except Exception: pass
    return p,out
if __name__=='__main__':
    root=sys.argv[1]
    files=[os.path.join(dp,f) for dp,_,fn in os.walk(root) for f in fn if f.lower().endswith('.pdf')]
    df=collections.Counter(); size={}; npdf_img=0; tot=0; per={}
    with ProcessPoolExecutor(10) as ex:
        for p,o in ex.map(one,files,chunksize=16):
            tot+=1
            if o: npdf_img+=1
            per[os.path.relpath(p,root)]=[h for h,_,_,_ in o]
            for h in {h for h,_,_,_ in o}: df[h]+=1
            for h,s,w,hh in o: size[h]=(s,w,hh)
    json.dump({'per':per,'size':size},open(sys.argv[2] if len(sys.argv)>2 else 'imgs.json','w'))
    rep=[h for h,c in df.items() if c>=3]
    print('pdfs',tot,'with images',npdf_img,'distinct images',len(df),'images in >=3 pdfs',len(rep),'pdfs containing a repeated image',sum(1 for p,hs in per.items() if any(df[h]>=3 for h in hs)))
