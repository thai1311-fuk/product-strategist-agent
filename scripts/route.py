#!/usr/bin/env python3
"""Детерминированный маршрутизатор: «запрос или этап → скилл, модель» по правилам config/routing.json.

Без моделей и сети: считаются совпадения ключевых слов (корней) из rules.list; побеждает правило с большим
числом совпадений, при равенстве — стоящее раньше. Нет совпадений — rules.default.

Эскалация: если после работы дешёвой модели валидатор или проверка цитат нашли ошибки, шаг нужно повторить
на модели уровнем выше (routing.json → escalation.order); рекомендация пишется в журнал (action=escalate_model).

Использование:
  python3 scripts/sa.py route <slug> "текст запроса"
  python3 scripts/sa.py route <slug> --stage collect
  python3 scripts/sa.py route <slug> --escalate
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import cost, store  # noqa: E402


def _h(reasons: list) -> str:
    return hashlib.md5("|".join(reasons).encode("utf-8")).hexdigest()[:8]


def classify(text: str, routing: dict | None = None) -> dict:
    """{'skill','stage','model','alias','tier','matched':[…],'basis':'rule|default'}."""
    routing = routing or cost.load_routing()
    rules = routing.get("rules", {})
    low = " ".join(str(text or "").lower().split())
    best, best_hits = None, []
    for rule in rules.get("list", []):
        hits = [k for k in rule.get("keywords", []) if k.lower() in low]
        if len(hits) > len(best_hits):
            best, best_hits = rule, hits
    basis = "rule"
    if best is None:
        best, basis = rules.get("default") or {"skill": "strategist", "stage": "intake",
                                               "model": routing.get("default_model", "")}, "default"
    return _result(best, best_hits, basis, routing)


def for_stage(stage: str, routing: dict | None = None) -> dict:
    routing = routing or cost.load_routing()
    model = cost.model_for(stage, routing)
    skill = next((r.get("skill") for r in routing.get("rules", {}).get("list", []) if r.get("stage") == stage),
                 "strategist")
    return _result({"skill": skill, "stage": stage, "model": model}, [], "stage", routing)


def _result(rule: dict, hits: list, basis: str, routing: dict) -> dict:
    model = rule.get("model") or routing.get("default_model", "")
    m = routing.get("models", {}).get(model, {})
    return {"skill": rule.get("skill"), "stage": rule.get("stage"), "model": model, "alias": m.get("alias"),
            "tier": m.get("tier"), "matched": hits, "basis": basis}


def next_model(model: str, routing: dict | None = None) -> str | None:
    routing = routing or cost.load_routing()
    order = routing.get("escalation", {}).get("order", [])
    full = cost.resolve_model(model, routing)
    if full in order and order.index(full) + 1 < len(order):
        return order[order.index(full) + 1]
    return None


def check_escalation(pdir: Path, stage: str | None = None, record: bool = True) -> dict:
    """Нужна ли эскалация: ошибки validate или цитаты fact, не найденные в источнике (quote_verified=false),
    при том, что этап вёл не самый сильный уровень. Возвращает {'escalate': bool, 'from','to','reasons',…}."""
    from lib import state, validate  # noqa: PLC0415
    routing = cost.load_routing()
    st = state.load(pdir)
    stage = stage or st.get("stage", "intake")
    rows = [r for r in store.read_jsonl(pdir / "log" / "costs.jsonl") if r.get("stage") == stage]
    used = cost.resolve_model(rows[-1]["model"], routing) if rows else None
    used = used or cost.model_for(stage, routing)
    reasons = [f"validate: {e}" for e in validate.run(pdir).errors[:5]]
    bad_quotes = [e["id"] for e in store.read_jsonl(pdir / "evidence.jsonl")
                  if e.get("status", "active") == "active" and e.get("type") == "fact"
                  and e.get("quote_verified") is False]
    if bad_quotes:
        reasons.append("цитата не найдена в источнике: " + ", ".join(bad_quotes[:8]))
    target = next_model(used, routing)
    res = {"stage": stage, "from": used, "to": target, "reasons": reasons, "escalate": False}
    if reasons and target:
        res["escalate"] = True
        if record:
            store.log_action(pdir, "escalate_model", stage, ok=True,
                             detail=f"{used} → {target}; " + "; ".join(reasons)[:350],
                             key=f"escalate_model:{stage}:{used}:{len(reasons)}:{_h(reasons)}")
    return res


def format_result(r: dict) -> list[str]:
    out = [f"Скилл: {r['skill']}; этап: {r['stage']}; модель: {r['model']} ({r.get('tier')}, {r.get('alias')})"]
    out.append("Основание: " + (("слова " + ", ".join(r["matched"])) if r["matched"] else
                                {"default": "совпадений нет — правило по умолчанию",
                                 "stage": "таблица этапов routing.json"}.get(r["basis"], "—")))
    return out
