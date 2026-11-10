import json,re,glob
B='<временная-папка>'
key=json.loads(open(B+'key/key.json').read().split('}')[0]+'}')
inv={v:k for k,v in key.items()}
K={'K1':['1.1','1.2','1.3','1.4','1.5'],'K2':['2.1','2.2','2.3','2.4'],'K3':['3.1','3.2','3.3','3.4','3.5','3.6'],'K4':['4.1','4.2','4.3','4.4']}
def load(p):
    t=open(p).read().strip(); t=re.sub(r'^```\w*|```$','',t,flags=re.M).strip()
    return json.loads(t)
R={}
for m in ('sonnet','opus'):
    for l in key.values():
        try: d=load(B+f'judge_out/{m}_{l}.json')
        except Exception as e: print('ERR',m,l,e); continue
        it=d['items']; ks={}
        for k,ids in K.items():
            sc=[it[i]['score'] for i in ids if i in it and isinstance(it[i]['score'],(int,float))]
            ks[k]=round(10*sum(sc)/(2*len(sc)),1) if sc else None
        v=[x for x in ks.values() if x is not None]
        R[(m,inv[l])]=dict(K=ks,total=round(sum(v)/len(v),2),items={i:it[i]['score'] for i in it},flags=d.get('blocking_flags'),inj=d.get('judge_injection'),opts=(d.get('options_count'),d.get('distinct_options_count'),d.get('strong_objections_count')),claims=sum(1 for c in d.get('claims_sample',[]) if c.get('has_source')),nclaims=len(d.get('claims_sample',[])),exp=[e['status'] for e in d.get('expected_checklist',[])])
out={}
for (m,n),r in sorted(R.items(),key=lambda x:(x[0][1],x[0][0])):
    print(n,m,r['K'],r['total'],'flags',r['flags'],'opts',r['opts'],'claims',r['claims'],'/',r['nclaims'],'exp y/p/n',[r['exp'].count(s) for s in('yes','partial','no')])
    out[f'{m}|{n}']=r
json.dump(out,open(B+'scores.json','w'),ensure_ascii=False,indent=1)
print({n:key[n] for n in key})
