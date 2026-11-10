import re,json,glob,sys
B='<временная-папка>'
SECS=['Ситуация','Варианты','Рекомендация','Риски','Пробелы']

def parse(text):
    """-> list of (level,title,body_lines) flat; body until next heading any level"""
    out=[];cur=None
    for ln in text.split('\n'):
        m=re.match(r'^(#{1,4})\s+(.*)',ln)
        if m: cur=[len(m.group(1)),m.group(2),[]];out.append(cur)
        elif cur: cur[2].append(ln)
    return out
def sect(text,level,pat,sub=True):
    """body of heading level matching pat, including deeper subsections (rendered as ### lines)"""
    P=parse(text);res=[];on=False
    for lv,t,b in P:
        if on and lv<=level: break
        if not on and lv==level and re.search(pat,t): on=True; res+=b; continue
        if on: res.append('### '+t); res+=b
    return '\n'.join(res)

def tables(s):
    out=[];hdr=None
    for ln in s.split('\n'):
        if ln.strip().startswith('|') and not re.match(r'^\s*\|[\s\-:|]+\|\s*$',ln):
            cells=[c.strip() for c in ln.strip().strip('|').split('|')]
            if hdr is None and any(c.startswith('Тип') for c in cells) and any('Откуда' in c or 'Уверенность' in c for c in cells):
                hdr=cells; continue
            if hdr is not None and len(cells)==len(hdr):
                d=dict(zip(hdr,cells)); src=next((v for k,v in d.items() if 'Откуда' in k),'')
                tp=d.get('Тип',''); cf=next((v for k,v in d.items() if 'Уверенность' in k),'')
                out.append(f"- {cells[0]} {src} ({tp}; уверенность: {cf})"); continue
        elif not ln.strip().startswith('|'): hdr=None
        elif re.match(r'^\s*\|[\s\-:|]+\|\s*$',ln): continue
        if re.match(r'^\s*\|[\s\-:|]+\|\s*$',ln): continue
        if ln.strip().startswith('|'):
            cells=[c.strip() for c in ln.strip().strip('|').split('|')]
            out.append('- '+'; '.join(c for c in cells if c))
        else: out.append(ln)
    return '\n'.join(out)

# ---- markers
def agent_maps(repo):
    pd=repo if repo.startswith('/') else glob.glob(B+repo+'/projects/*')[0]
    ev={json.loads(l)['id']:json.loads(l) for l in open(pd+'/evidence.jsonl')}
    so={json.loads(l)['id']:json.loads(l) for l in open(pd+'/sources.jsonl')}
    return ev,so
def srcname(s):
    loc=s.get('locator','')
    if 'd7_segments' in loc: return 'расчёт автора'
    if 'brief' in loc: return 'запрос руководителя (бриф)'
    if loc.startswith('http'): return loc
    return re.sub(r'^S\d+-','',loc.split('/')[-1]) or s['title']

class Foot:
    def __init__(s): s.l=[]
    def n(s,name):
        if name not in s.l: s.l.append(name)
        return s.l.index(name)+1

def conv_agent(text,repo,F):
    ev,so=agent_maps(repo)
    def one(i):
        if i.startswith('E') and i in ev:
            e=ev[i]
            if e['type'] in('estimate','calc'): return 'расчёт автора'
            ids=e.get('source_ids') or []
            if not ids: return None
            return srcname(so[ids[0]]) if ids[0] in so else None
        if i.startswith('S') and i in so: return srcname(so[i])
        return None
    def rep(m):
        ids=re.findall(r'[ECSH]\d{1,3}',m.group(0))
        ns=[]
        for i in ids:
            nm=one(i)
            if nm:
                k=F.n(nm)
                if k not in ns: ns.append(k)
        return ''.join(f'[{k}]' for k in ns)
    return re.sub(r'(?:\[[ECSH]\d{1,3}\]\s*)+',lambda m:rep(m)+' ' if rep(m) else '',text)

def conv_chat(text,legend,F):
    def rep(m):
        inner=m.group(1)
        lab=re.split(r'[,;:№ ]',inner.strip())[0]
        if inner.strip() in legend: key=inner.strip()
        else: key=lab
        v=legend.get(key,'?')
        if v is None: return ''
        if v=='?': return m.group(0)
        if v.startswith('('): return v
        # combos like [Альфа, Бета]
        parts=[p.strip() for p in re.split(r'[,;]',inner)]
        ks=[]
        for p in parts:
            k0=re.split(r'[:№ ]',p)[0]
            vv=legend.get(p,legend.get(k0))
            if vv and not vv.startswith('('): 
                n=F.n(vv)
                if n not in ks: ks.append(n)
        return ''.join(f'[{n}]' for n in ks) if ks else (v if v.startswith('(') else '')
    return re.sub(r'\[([^\]\n]{1,40})\]',lambda m: rep(m) if re.split(r'[,;:№ ]',m.group(1).strip())[0] in legend or m.group(1).strip() in legend else m.group(0),text)

def clean(s):
    s=tables(s)
    s=re.sub(r'\*\*(.+?)\*\*',r'\1',s); s=re.sub(r'(?<!\w)\*(?!\s)(.+?)\*',r'\1',s); s=s.replace('`','')
    s=re.sub(r'(?i)\bcritic\b','оппонент',s)
    s=s.replace('красная команда, opus; ','').replace('opus','')
    kill=[r'^\s*[-*]\s*(Возражения оппонент|Возражения critic)[^\n]*см\. ниже[^\n]*$',r'^\s*[-*]\s*Ответ на возражения:?\s*см\. ниже[^\n]*$',r'^>\s.*$',r'^\s*[-*]\s*(Откуда|Источник)[^\n]*$']
    for k in kill: s=re.sub(k,'',s,flags=re.M|re.I)
    s=re.sub(r'Гипотеза (критика|оппонента)','Предположение',s)
    s=re.sub(r'\[(Г|Суждение)(?:[^\]]*)\]','(гипотеза)',s); s=re.sub(r'\[ОЗ[^\]]*\]','(общие знания, не проверено)',s)
    s=s.replace('[E]','')
    s=re.sub(r'\s+([.,;:])',r'\1',s); s=re.sub(r'(?m)^Ответ:\s*','',s); s=re.sub(r'(?m)^(#+ .*?)\s*[—-]\s*$',r'\1',s)
    s=re.sub(r'\]\s*,\s*\[',r'][',s)
    s=re.sub(r'\n{3,}','\n\n',s)
    s=re.sub(r'[ \t]+\n','\n',s); s=re.sub(r' {2,}',' ',s)
    return s.strip()

def build(sections,conv):
    F=Foot();out={}
    for k in SECS:
        out[k]=clean(conv(sections.get(k,''),F))
    return out,F
def render(out,F):
    t=''
    for k in SECS: t+=f'## {k}\n\n{out[k]}\n\n'
    t+='## Источники\n\n'+'\n'.join(f'[{i+1}] {n}' for i,n in enumerate(F.l))+'\n'
    return t
def trunc_blocks(s,budget):
    if len(s)<=budget: return s
    blocks=re.split(r'\n(?=### |- |\n)',s);o='';
    for b in blocks:
        if len(o)+len(b)>budget and o: break
        o+=b+'\n'
    return o.strip()
