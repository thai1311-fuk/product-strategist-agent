import json,sys
T='/private/tmp/claude-501/-Users-thai/8988dd67-6b93-4927-8f95-a547ddb685a9/tasks/%s.output'
PR={'in':3.0,'out':15.0,'cw':3.75,'cr':0.3}
def load(aid):
    return [json.loads(l) for l in open(T%aid)]
def final_text(aid):
    last=None
    for d in load(aid):
        if d.get('type')=='assistant':
            t=''.join(c.get('text','') for c in d['message']['content'] if c.get('type')=='text')
            for c in d['message']['content']:
                if c.get('type')=='tool_use' and c.get('name')=='SubagentHandback': t=c['input'].get('message','')
            if t.strip(): last=t
    return last
def usage(aid, price=PR):
    u=dict(i=0,o=0,cw=0,cr=0); seen=set(); ts=[]
    for d in load(aid):
        ts.append(d.get('timestamp'))
        if d.get('type')=='assistant':
            m=d['message']; mid=m.get('id') or d['uuid']
            if mid in seen: continue
            seen.add(mid); x=m.get('usage',{})
            u['i']+=x.get('input_tokens',0);u['o']+=x.get('output_tokens',0)
            u['cw']+=x.get('cache_creation_input_tokens',0);u['cr']+=x.get('cache_read_input_tokens',0)
    c=(u['i']*price['in']+u['o']*price['out']+u['cw']*price['cw']+u['cr']*price['cr'])/1e6
    return u,c,ts[0],ts[-1]
if __name__=='__main__':
    aid=sys.argv[1]
    if sys.argv[2]=='text': print(final_text(aid))
    else:
        u,c,a,b=usage(aid); print(u,round(c,3),a,b)
