import re,random,glob,json,sys
B='<временная-папка>'
rs=random.SystemRandom()
mat={'a':glob.glob(B+'inputs/a/materials/*'),'b':glob.glob(B+'inputs/b/materials/*'),'d':glob.glob(B+'inputs/d/materials/*')}
def mt(case,name):
    for f in mat[case]:
        if f.endswith('/'+name): return open(f).read()
    if name in('users.csv','events.csv'): return None
    return None
def sample(n,case):
    t=open(B+f'norm/{n}.md').read()
    body=t.split('## Источники')[0]; foot=dict(re.findall(r'\[(\d+)\] (.*)',t.split('## Источники')[1]))
    body='\n'.join(l for l in body.split('\n') if not l.startswith('#'))
    sents=[s.strip() for s in re.split(r'(?<=[.;!?])\s+|\n',body) if s.strip()]
    cl=[s for s in sents if re.search(r'\d',s) and len(s)>25]
    pick=rs.sample(cl,min(20,len(cl)))
    withsrc=0;checked=0;ok=0;bad=[]
    for s in pick:
        ms=re.findall(r'\[(\d+)\]',s)
        if ms: withsrc+=1
        for m in ms:
            nm=foot.get(m,'')
            txt=mt(case,nm)
            if txt is None: continue
            nums=re.findall(r'\d[\d\s]*(?:[.,]\d+)?',re.sub(r'\[\d+\]','',s))
            nums=[re.sub(r'\s','',x).replace(',','.') for x in nums]
            nums=[x for x in nums if len(x.replace('.',''))>=2]
            T=txt.replace(',','.').replace(' ','')
            checked+=1
            miss=[x for x in nums if x not in T]
            if not miss: ok+=1
            else: bad.append((s[:110],nm,miss))
            break
    return dict(sampled=len(pick),with_source=withsrc,checkable=checked,numbers_found_in_cited_source=ok,flagged=bad)
if __name__=='__main__':
    out={}
    for n,c in [('a_agent','a'),('a_chat','a'),('b_agent','b'),('b_chat','b'),('d1_agent','d'),('d2_agent','d'),('d_chat','d')]:
        out[n]=sample(n,c); r=out[n]; print(n,r['sampled'],r['with_source'],r['checkable'],r['numbers_found_in_cited_source'],len(r['flagged']))
    json.dump(out,open(B+'claims_sample.json','w'),ensure_ascii=False,indent=1)
