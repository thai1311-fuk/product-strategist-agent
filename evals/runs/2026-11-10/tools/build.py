import sys,re,json;sys.path.insert(0,'/private/tmp/claude-501/-Users-thai/fa2237a0-5433-4537-b2e8-397673a95c10/scratchpad/bench-iso/tools')
from norm import *
R=lambda p:open(B+p).read()
def ag(repo,slug_dir=None):
    pd=B+(slug_dir or '')
    import glob
    if slug_dir is None: pd=glob.glob(B+repo+'/projects/*')[0]
    sit=open(pd+'/outputs/situation.md').read(); op=open(pd+'/outputs/options.md').read()
    return pd,sit,op
def agent_sections(sit,op):
    S={}
    s=sect(sit,2,r'Коротко')+'\n'+sect(sit,2,r'Что известно')+'\n'+sect(sit,2,r'Противоречи')
    S['Ситуация']=s
    S['Варианты']=sect(op,2,r'^Варианты$')
    S['Рекомендация']=sect(op,2,r'Рекомендаци')
    S['Риски']=sect(op,2,r'(Возражения|Pre-mortem)')
    if not S['Риски'].strip():
        v=S['Варианты'].split('\n'); keep=[];rk=[]
        for ln in v:
            (rk if re.match(r'^\s*-\s*(\*\*)?(Возражения|Pre-mortem)',ln) and 'см. ниже' not in ln else keep).append(ln)
        S['Варианты']='\n'.join(keep); S['Риски']='\n'.join(rk)
    S['Пробелы']=sect(sit,2,r'Чего мы не знаем')+'\n'+sect(op,2,r'Чего мы не знаем')
    return S
def chat_sections(t,case):
    S={}
    if case=='a':
        S['Ситуация']=sect(t,2,r'1\. Картина')
        gaps=sect(t,3,r'Чего мы не знаем'); S['Ситуация']=S['Ситуация'].replace(gaps,'') if False else S['Ситуация']
        S['Пробелы']=gaps
        S['Варианты']=sect(t,2,r'2\. Варианты'); S['Рекомендация']=sect(t,2,r'3\. Рекомендация'); S['Риски']=sect(t,3,r'^Риски')
    if case=='b':
        S['Ситуация']=sect(t,3,r'1\.1')+'\n'+sect(t,3,r'1\.2')
        S['Пробелы']=sect(t,3,r'1\.3')+'\n'+sect(t,3,r'1\.4')
        S['Варианты']=sect(t,2,r'2\. Варианты'); S['Рекомендация']=sect(t,2,r'3\. Рекомендация')
        m=re.search(r'\*\*Риски:\*\*(.*?)(?=\n## )',t,re.S); S['Риски']=m.group(1) if m else ''
    if case=='d':
        S['Ситуация']='\n'.join(sect(t,3,x) for x in (r'1\.1',r'1\.2',r'1\.3',r'1\.4'))
        S['Пробелы']=sect(t,3,r'1\.5'); S['Варианты']=sect(t,2,r'2\. Варианты'); S['Рекомендация']=sect(t,2,r'3\. Рекомендация')
        S['Риски']=sect(t,3,r'^Риски')
    return S
LEG={
 'a':{'U':'users.csv','E':'events.csv','DD':'data_dictionary.md','RN':'release_notes.md','MN':'marketing_note.md','AR':'app_reviews.md','Расчёт':'расчёт автора','Ограничения':'запрос руководителя (бриф)','Г':'(гипотеза)'},
 'b':{'З':'запрос руководителя (бриф)','О':'product_onepager.md','CSV':'pilot_signups.csv','Расчёт':'расчёт автора',**{f'В{i}':'field_notes.md' for i in range(1,7)},'В1–В6':'field_notes.md','В1–В5':'field_notes.md'},
 'd':{'Альфа':'report_alpha.md','Альфы':'report_alpha.md','Беты':'news_beta.md','Бета':'news_beta.md','SpeakCorp':'competitor_page.html','Опрос':'hr_survey.md','Расчёт':'расчёт автора','Расчёт и суждение':'расчёт автора'}}
out={}
def do_agent(name,repo,slugdir=None):
    pd,sit,op=ag(repo,slugdir)
    o,F=build(agent_sections(sit,op),lambda t,F:conv_agent(t,pd,F)); out[name]=(o,F)
def do_chat(name,case,f):
    t=open(B+f).read()
    o,F=build(chat_sections(t,case),lambda t,F:conv_chat(t,LEG[case],F)); out[name]=(o,F)
do_agent('a_agent','repo'); do_chat('a_chat','a','raw/chat_a.md')
do_agent('b_agent','repo_b'); do_chat('b_chat','b','raw/chat_b.md')
do_agent('d1_agent',None,'snap/d1_before_fact'); do_agent('d2_agent',None,'snap/d2_before_fact'); do_chat('d_chat','d','raw/chat_d.md')
pairs=[('a_agent','a_chat'),('b_agent','b_chat'),('d1_agent','d_chat'),('d2_agent','d_chat')]
# truncation: each text <= 1.2 * min of its pair; chat d shared -> use min across d
limit={}
def L(n): return sum(len(v) for v in out[n][0].values())
for a,c in pairs:
    lim=min(L(a),L(c))*1.2
    for n in (a,c): limit[n]=min(limit.get(n,1e9),lim)
for n,(o,F) in out.items():
    tot=L(n);lim=limit[n]
    if tot>lim:
        k=lim/tot
        for s in SECS: o[s]=trunc_blocks(o[s],int(len(o[s])*k))
    open(B+f'norm/{n}.md','w').write(render(o,F))
    print(n,'before',tot,'after',L(n),'lim',int(lim),{s:len(o[s]) for s in SECS})
