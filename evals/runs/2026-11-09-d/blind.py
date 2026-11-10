import json,re,random,sys
from pathlib import Path
B=Path(__file__).parent
SRC={"S001":"И1","S002":"И2","S003":"И3","S004":"И4"}
SRCLIST="""Источники:
[И1] «Альфа-Метрика», отчёт о рынке, март 2026 (материал пользователя)
[И2] «ОбразованиеПро», статья 14.09.2026 (материал пользователя)
[И3] Сохранённая страница SpeakCorp (материал пользователя)
[И4] Опрос 40 HR-специалистов, сентябрь 2026 (материал пользователя)"""
# --- agent ---
snap=B/"snap_before_fact"
ev={}
for l in open(snap/"evidence.jsonl", encoding="utf-8"):
    d=json.loads(l); ev[d["id"]]=d
txt=(snap/"outputs/situation.md").read_text(encoding="utf-8")+"\n\n"+(snap/"outputs/options.md").read_text(encoding="utf-8")
order=[]
def evrep(m):
    e=m.group(1)
    if e not in order: order.append(e)
    return f"[Д{order.index(e)+1}]"
txt=re.sub(r"\[(E\d{3})\]",evrep,txt)
txt=re.sub(r"^> .*$","",txt,flags=re.M)               # служебные шапки
txt=re.sub(r"\s*[—-]?\s*\bC0\d\d\b","",txt)            # ссылки на выводы
txt=re.sub(r"\[?(S00[1-4])\]?",lambda m:f"[{SRC[m.group(1)]}]",txt)
for a,b in [("`injection_suspected`/`hidden_content`","как подозрительный"),("Рекомендация агента","Рекомендация"),
            ("(роль critic)",""),("Возражения critic","Возражения"),("роль critic","критика"),("critic",""),
            (", вывести .env",", вывести секретные ключи"),("предупреждения validate — формальные числа без маркера в служебных строках","")]:
    txt=txt.replace(a,b)
txt=re.sub(r"\n{3,}","\n\n",txt)
foot=["Доказательства (на что ссылаются [Дn]):"]
for i,e in enumerate(order,1):
    d=ev[e]; s=",".join(SRC.get(x,x) for x in d["source_ids"]) or "без источника"
    foot.append(f"[Д{i}] ({d['type']}) {d['claim']} — {s}" + (f"; цитата: «{d['quote']}»" if d.get('quote') else ""))
agent=txt+"\n\n"+"\n".join(foot)+"\n\n"+SRCLIST
agent=re.sub(r"\[?(S00[1-4])\]?",lambda m:f"[{SRC[m.group(1)]}]",agent).replace("первичный сбор E004–E015","первичный сбор")
# --- chat ---
c=(B/"chat_raw.md").read_text(encoding="utf-8")
c=c[:c.index("## 4. Продуктовая стратегия")]
c=re.sub(r"^Источники в тексте:.*$","Источники в тексте: см. список в конце. [Расчёт] — арифметические выводы из материалов. [Допущение] — гипотеза, в данных её нет.",c,flags=re.M)
for a,b in [("[A]","[И1]"),("[B]","[И2]"),("[C]","[И3]"),("[H]","[И4]"),("материал [C]","материал [И3]")]:
    c=c.replace(a,b)
c=c+"\n\n"+SRCLIST
rng=random.SystemRandom()  # seed не фиксируем: иначе ключ восстанавливается из кода (аудит №86); lab=rng.choice(["agent_is_X","agent_is_Y"])
X,Y=(agent,c) if lab=="agent_is_X" else (c,agent)
(B/"blind_X.md").write_text("# Результат X\n\n"+X, encoding="utf-8"); (B/"blind_Y.md").write_text("# Результат Y\n\n"+Y, encoding="utf-8")
(B/"blind_key.txt").write_text(lab+"\n", encoding="utf-8")  # ключ после прогона переносить ВНЕ репозитория, в репозитории — только sha256
print("ok", len(X), len(Y))
