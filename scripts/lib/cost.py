"""Расходы: цены и маршрутизация из config/routing.json, журнал log/costs.jsonl, отчёт и время."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from . import store
from .store import SAError

ACTIVE_GAP_MIN = 30  # промежуток без действий длиннее — считается перерывом


def load_routing() -> dict:
    path = store.CONFIG_DIR / "routing.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SAError(f"Нет {path} — таблица моделей и цен.") from None
    except json.JSONDecodeError as exc:
        raise SAError(f"Повреждён {path}: {exc}") from None


def resolve_model(name: str, routing: dict | None = None) -> str | None:
    """Полное имя модели по алиасу/префиксу (claude-opus-5-5-20261101 → claude-opus-5-5, opus → claude-opus-5-5)."""
    routing = routing or load_routing()
    models = routing.get("models", {})
    if not name:
        return None
    n = name.strip().lower()
    if n in models:
        return n
    for full, m in models.items():
        if n == m.get("alias") or n.startswith(full):
            return full
    for full, m in models.items():  # грубое совпадение по семейству
        if m.get("alias") and m["alias"] in n:
            return full
    return None


def price(model: str, inp: int, out: int, cache_write: int = 0, cache_read: int = 0,
          routing: dict | None = None) -> float | None:
    routing = routing or load_routing()
    full = resolve_model(model, routing)
    if not full:
        return None
    m = routing["models"][full]
    mult = routing.get("cache_multipliers", {})
    pin = m["input_per_mtok"]
    cw = m.get("cache_write_per_mtok", pin * mult.get("write", 1.25))
    cr = m.get("cache_read_per_mtok", pin * mult.get("read", 0.1))
    usd = (inp * pin + out * m["output_per_mtok"] + cache_write * cw + cache_read * cr) / 1e6
    return round(usd, 6)


def model_for(stage_or_agent: str, routing: dict | None = None) -> str:
    routing = routing or load_routing()
    return (routing.get("stages", {}).get(stage_or_agent) or routing.get("subagents", {}).get(stage_or_agent)
            or routing.get("default_model", ""))


def add(pdir: Path, model: str, inp: int, out: int, stage: str | None = None, note: str = "",
        source: str = "manual", cache_read: int = 0, cache_write: int = 0) -> dict:
    from . import state  # noqa: PLC0415
    if min(inp, out, cache_read, cache_write) < 0:
        raise SAError("Число токенов не может быть отрицательным.")
    routing = load_routing()
    full = resolve_model(model, routing)
    if not full:
        raise SAError(f"Модель «{model}» не найдена в config/routing.json. Доступны: {', '.join(routing['models'])}")
    stage = stage or state.load(pdir).get("stage", "intake")
    rec = {"ts": store.now_iso(), "stage": stage, "model": full,
           "input_tokens": int(inp) + int(cache_read) + int(cache_write), "output_tokens": int(out),
           "usd": price(full, inp, out, cache_write, cache_read, routing=routing), "source": source, "note": note}
    if cache_read or cache_write:
        rec.update(raw_input=int(inp), cache_read=int(cache_read), cache_write=int(cache_write))
    store.append_jsonl(pdir / "log" / "costs.jsonl", rec)
    return rec


def upsert_session(pdir: Path, session: str, stage: str, per_model: dict[str, dict]) -> list[dict]:
    """Идемпотентная запись расходов сессии (хук Stop).

    per_model — накопленный итог сессии по моделям (из транскрипта). Часть, уже записанная
    на другие этапы, вычитается; остаток записывается на текущий этап. Повторный вызов
    перезаписывает запись (session, stage, model), а не дублирует её.
    """
    routing = load_routing()
    path = pdir / "log" / "costs.jsonl"
    written = []
    with store.locked(path):
        rows = store.read_jsonl(path)
        mine = [r for r in rows if r.get("source") == "hook" and r.get("session") == session]
        rest = [r for r in rows if not (r.get("source") == "hook" and r.get("session") == session
                                        and r.get("stage") == stage)]
        keys = {"input_tokens": "raw_input", "output_tokens": "output_tokens",
                "cache_write": "cache_write", "cache_read": "cache_read"}
        for model, tot in per_model.items():
            other = [r for r in mine if r.get("stage") != stage and r.get("model") == model]
            cur = {k: max(0, int(tot.get(k, 0)) - sum(int(r.get(rk, 0) or 0) for r in other))
                   for k, rk in keys.items()}
            if not any(cur.values()):
                continue
            usd = price(model, cur["input_tokens"], cur["output_tokens"], cur["cache_write"], cur["cache_read"], routing)
            rec = {"ts": store.now_iso(), "stage": stage, "model": model,
                   "input_tokens": cur["input_tokens"] + cur["cache_write"] + cur["cache_read"],
                   "output_tokens": cur["output_tokens"], "usd": usd if usd is not None else 0.0,
                   "source": "hook",
                   "note": ("модель не найдена в routing.json — цена 0" if usd is None else
                            f"из транскрипта; вход без кэша {cur['input_tokens']}, "
                            f"кэш-запись {cur['cache_write']}, кэш-чтение {cur['cache_read']}"),
                   "session": session, "key": f"hook:{session}:{stage}:{model}",
                   "raw_input": cur["input_tokens"], "cache_write": cur["cache_write"],
                   "cache_read": cur["cache_read"]}
            rest.append(rec)
            written.append(rec)
        store.write_jsonl(path, rest)
    return written


def _active_minutes(actions: list[dict]) -> float:
    by_session: dict[str, list] = defaultdict(list)
    for a in actions:
        ts = store.parse_ts(a.get("ts"))
        if ts:
            by_session[a.get("session", "?")].append(ts)
    total = 0.0
    for stamps in by_session.values():
        stamps.sort()
        for a, b in zip(stamps, stamps[1:]):
            gap = (b - a).total_seconds() / 60
            if gap <= ACTIVE_GAP_MIN:
                total += gap
    return total


def stage_intervals(st: dict) -> list[tuple[str, object, object, bool]]:
    """Интервалы этапов по history: (этап, вход, выход, открыт ли ещё). Вход — первая запись с этим этапом
    после смены, выход — первая запись следующего этапа; текущий этап открыт до «сейчас» (или до done)."""
    import datetime as _dt  # noqa: PLC0415
    hist = [(store.parse_ts(h.get("ts")), h.get("stage")) for h in st.get("history", [])]
    hist = [h for h in hist if h[0] and h[1]]
    out: list = []
    for ts, stage in hist:
        if not out or out[-1][0] != stage:
            if out:
                out[-1][2] = ts
            out.append([stage, ts, None])
    res = []
    for i, (stage, t_in, t_out) in enumerate(out):
        is_open = t_out is None
        if is_open:
            t_out = (hist[-1][0] if st.get("stage_status") == "done" else _dt.datetime.now().astimezone())
        res.append((stage, t_in, t_out, is_open and st.get("stage_status") != "done"))
    return res


def _stage_durations(st: dict) -> dict[str, float]:
    """Суммарная длительность этапов (часы) по истории: вход в этап → выход из него."""
    dur: dict[str, float] = defaultdict(float)
    for stage, t_in, t_out, _ in stage_intervals(st):
        dur[stage] += max(0.0, (t_out - t_in).total_seconds() / 3600)
    return dur


def summary(pdir: Path) -> dict:
    from . import state  # noqa: PLC0415
    st = state.load(pdir)
    rows = store.read_jsonl(pdir / "log" / "costs.jsonl")
    total = sum(float(r.get("usd") or 0) for r in rows)
    budget = float(st.get("budget_usd") or 0)
    return {"total": total, "budget": budget, "share": (total / budget) if budget else 0.0, "rows": rows}


def report(pdir: Path) -> list[str]:
    from . import state  # noqa: PLC0415
    st = state.load(pdir)
    routing = load_routing()
    s = summary(pdir)
    rows = s["rows"]
    by_stage: dict[str, list] = defaultdict(lambda: [0, 0, 0.0])
    by_model: dict[str, list] = defaultdict(lambda: [0, 0, 0.0])
    by_source: dict[str, float] = defaultdict(float)
    for r in rows:
        for agg, k in ((by_stage, r.get("stage", "?")), (by_model, r.get("model", "?"))):
            agg[k][0] += int(r.get("input_tokens") or 0)
            agg[k][1] += int(r.get("output_tokens") or 0)
            agg[k][2] += float(r.get("usd") or 0)
        by_source[r.get("source", "?")] += float(r.get("usd") or 0)
    out = [f"Расходы проекта {pdir.name} (цены условные, см. config/routing.json):"]
    if not rows:
        out.append("  записей о расходах пока нет (sa.py cost add … или хук Stop в Claude Code)")
    else:
        out.append("  По этапам:")
        order = {s: i for i, s in enumerate(state.STAGES)}
        for k in sorted(by_stage, key=lambda x: order.get(x, 99)):
            i, o, u = by_stage[k]
            out.append(f"    {k:<11} ${u:8.4f}  вход {i:>10,}  выход {o:>9,}  (рекоменд. модель: {model_for(k, routing)})")
        out.append("  По моделям:")
        for k, (i, o, u) in sorted(by_model.items(), key=lambda x: -x[1][2]):
            out.append(f"    {k:<20} ${u:8.4f}  вход {i:>10,}  выход {o:>9,}")
        out.append("  По способу учёта: " + ", ".join(f"{k} ${v:.4f}" for k, v in by_source.items()))
    pct = s["share"] * 100
    warn = routing.get("budget", {}).get("warn_share", 0.8) * 100
    out.append(f"  Итого: ${s['total']:.4f} из бюджета ${s['budget']:.2f} ({pct:.0f}%)"
               + ("  ⚠ превышено 80% бюджета — предупредите пользователя" if pct >= warn else ""))
    created, updated = store.parse_ts(st.get("created_at")), store.parse_ts(st.get("updated_at"))
    actions = store.read_jsonl(pdir / "log" / "actions.jsonl")
    if created and updated:
        days = (updated - created).total_seconds() / 86400
        out.append(f"  Время: с начала проекта {days:.1f} дн.; активная работа ≈ {_active_minutes(actions):.0f} мин "
                   f"(по журналу действий, перерывы >{ACTIVE_GAP_MIN} мин не считаются)")
    iv = stage_intervals(st)
    if iv:
        out.append("  Время на этапах (по истории state.json: вход → выход):")
        for stage, t_in, t_out, is_open in iv:
            mins = (t_out - t_in).total_seconds() / 60
            dur_s = f"{mins:.0f} мин" if mins < 120 else f"{mins / 60:.1f} ч"
            out.append(f"    {stage:<11} {t_in.strftime('%d.%m %H:%M')} → "
                       f"{'сейчас' if is_open else t_out.strftime('%d.%m %H:%M')}  {dur_s}")
    return out
