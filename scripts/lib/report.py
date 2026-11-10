"""Сборка итогового отчёта для руководителя: projects/<slug>/outputs/report.html.

Только стандартная библиотека. Вход — файлы проекта по docs/CONTRACT.md,
выход — один самодостаточный HTML (встроенный CSS/JS, без внешних ресурсов):
открывается офлайн и печатается в PDF.

Безопасность: все данные проекта (особенно тексты из источников) считаются
недоверенными и экранируются при выводе. JS в отчёте только переключает
видимость строк и не вставляет данные в DOM.

Использование:
    from lib.report import build_report
    build_report(Path("projects/my-slug"))         # -> .../outputs/report.html
    python3 scripts/lib/report.py projects/my-slug  # то же из командной строки
"""
from __future__ import annotations

import html
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from string import Template

TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "report.html"

STAGES = [
    ("intake", "Запрос"),
    ("plan", "План исследования"),
    ("collect", "Сбор данных"),
    ("synthesis", "Картина ситуации"),
    ("options", "Варианты"),
    ("strategy", "Стратегии"),
    ("test_plan", "План проверки"),
    ("decision", "Решение"),
    ("monitor", "Сопровождение"),
]
STAGE_NAMES = dict(STAGES)
STAGE_STATUS = {"in_progress": "в работе", "awaiting_human": "ждёт решения человека", "done": "завершён"}

EVIDENCE_TYPES = {
    "fact": "факт",
    "estimate": "оценка",
    "hypothesis": "гипотеза",
    "forecast": "прогноз",
    "assumption": "допущение",
    "synthetic": "синтетика",
}
# Типы, которые не доказывают сами по себе — визуально отличаются в тексте.
WEAK_TYPES = {"hypothesis", "forecast", "assumption", "synthetic"}
EV_STATUS = {"active": "действует", "superseded": "заменено", "retracted": "отозвано"}
C_STATUS = {"active": "действует", "needs_review": "на пересмотре", "revised": "пересмотрен", "retracted": "отозван"}
C_KIND = {"insight": "инсайт", "option": "вариант", "recommendation": "рекомендация", "risk": "риск"}
SRC_KIND = {"web": "сайт", "doc": "документ", "table": "таблица", "interview": "интервью",
            "synthetic": "синтетика", "image": "изображение", "user": "от пользователя"}
RELIABILITY = {"high": "высокая", "medium": "средняя", "low": "низкая"}
DEC_STATUS = {"active": "действует", "review_due": "пора пересмотреть", "revised": "пересмотрено", "closed": "закрыто"}

ACTION_LOG_LIMIT = 300
NOT_READY = "Ещё не готово"


# --------------------------------------------------------------------------- чтение данных

def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, UnicodeDecodeError):
        return {}


def _read_jsonl(path: Path) -> list[dict]:
    """Терпимое чтение: битые строки пропускаются, а не роняют отчёт."""
    rows: list[dict] = []
    text = _read_text(path)
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _strip_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return _parse_simple_yaml(lines[1:i]), "\n".join(lines[i + 1:])
    return {}, text


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def _split_inline_list(s: str) -> list[str]:
    """'[a, "b, c", d]' -> ['a', 'b, c', 'd'] с учётом кавычек."""
    inner = s.strip()[1:-1]
    items, buf, quote = [], "", None
    for ch in inner:
        if quote:
            if ch == quote:
                quote = None
            else:
                buf += ch
        elif ch in "\"'":
            quote = ch
        elif ch == ",":
            items.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        items.append(buf.strip())
    return [i for i in items if i]


def _parse_simple_yaml(lines: list[str]) -> dict:
    """Минимальный разбор frontmatter: `key: value`, `key: [a, b]`, блочные списки `- x`."""
    data: dict = {}
    key = None
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", raw)
        if m and not raw.startswith((" ", "\t")):
            key, val = m.group(1), m.group(2).strip()
            if val.startswith("[") and val.endswith("]"):
                data[key] = _split_inline_list(val)
            elif val == "":
                data[key] = []
            else:
                data[key] = _unquote(val)
            continue
        m = re.match(r"^\s*-\s+(.*)$", raw)
        if m and key is not None:
            if not isinstance(data.get(key), list):
                data[key] = []
            data[key].append(_unquote(m.group(1)))
    return data


class Project:
    def __init__(self, root: Path):
        self.root = root
        self.state = _read_json(root / "state.json")
        self.brief = _read_text(root / "brief.md")
        self.sources = _read_jsonl(root / "sources.jsonl")
        self.evidence = _read_jsonl(root / "evidence.jsonl")
        self.conclusions = _read_jsonl(root / "conclusions.jsonl")
        self.actions = _read_jsonl(root / "log" / "actions.jsonl")
        self.costs = _read_jsonl(root / "log" / "costs.jsonl")
        self.outputs: dict[str, str] = {}
        out = root / "outputs"
        for name in ("situation", "research_plan", "options", "product_strategy", "comms_strategy", "test_plan"):
            _, body = _strip_frontmatter(_read_text(out / f"{name}.md"))
            self.outputs[name] = body
        self.decisions: list[tuple[dict, str, str]] = []
        ddir = root / "decisions"
        if ddir.is_dir():
            for p in sorted(ddir.glob("*.md")):
                meta, body = _strip_frontmatter(_read_text(p))
                self.decisions.append((meta, body, p.name))
        self.src_by_id = {str(s.get("id")): s for s in self.sources if s.get("id")}
        self.ev_by_id = {str(e.get("id")): e for e in self.evidence if e.get("id")}
        self.c_by_id = {str(c.get("id")): c for c in self.conclusions if c.get("id")}
        self.d_by_id = {str(m.get("id")): m for m, _, _ in self.decisions if m.get("id")}


# --------------------------------------------------------------------------- утилиты HTML

def esc(value) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def attr(value) -> str:
    """Значение атрибута: экранирование + переносы строк для всплывающих подсказок."""
    return esc(value).replace("\r", "").replace("\n", "&#10;")


def _short(s, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _fmt_ts(ts) -> str:
    if not ts:
        return ""
    s = str(ts)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.strftime("%d.%m.%Y %H:%M") if "T" in s else dt.strftime("%d.%m.%Y")
    except ValueError:
        return s


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _int(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _tok(n: int) -> str:
    return f"{n:,}".replace(",", "\u202f")


def _usd(v: float) -> str:
    return f"${v:,.2f}" if v >= 0.01 or v == 0 else f"${v:.4f}"


def _safe_href(url: str) -> str | None:
    """Разрешаем только http(s), mailto, якоря и относительные пути."""
    u = html.unescape(url).strip()
    low = u.lower()
    if re.match(r"^[a-z][a-z0-9+.-]*:", low):
        if not low.startswith(("http://", "https://", "mailto:")):
            return None
    return u


def not_ready(hint: str = "") -> str:
    extra = f" <span class=\"muted\">{esc(hint)}</span>" if hint else ""
    return f'<p class="not-ready">{NOT_READY}.{extra}</p>'


def _list_values(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    s = str(v).strip()
    return [s] if s else []


# --------------------------------------------------------------------------- ссылки на реестры

REF_GROUP_RE = re.compile(r"\[((?:[ECSD]\d{3,})(?:\s*[,;]\s*[ECSD]\d{3,})*)\](?!\()")


class RefRenderer:
    def __init__(self, project: Project):
        self.p = project
        self.used: Counter = Counter()

    def tip_and_class(self, rid: str) -> tuple[str, list[str], str]:
        """-> (текст подсказки, css-классы, короткая метка или '')."""
        p = self.p
        kind = rid[0]
        if kind == "E" and rid in p.ev_by_id:
            e = p.ev_by_id[rid]
            t = str(e.get("type") or "")
            st = str(e.get("status") or "active")
            parts = [f"{EVIDENCE_TYPES.get(t, t or 'тип не указан')} · уверенность: {RELIABILITY.get(str(e.get('confidence')), e.get('confidence') or '—')}"]
            if st != "active":
                parts[0] += f" · {EV_STATUS.get(st, st).upper()}"
            parts.append(_short(e.get("claim"), 300))
            if e.get("quote"):
                parts.append(f"«{_short(e.get('quote'), 300)}»")
            srcs = []
            for sid in _list_values(e.get("source_ids")):
                s = p.src_by_id.get(sid)
                if s:
                    srcs.append(f"{sid}: {_short(s.get('title'), 90)}" + (f" ({_short(s.get('publisher'), 40)})" if s.get("publisher") else ""))
                else:
                    srcs.append(sid)
            if srcs:
                parts.append("Источник: " + "; ".join(srcs))
            elif t in WEAK_TYPES:
                parts.append("Без источника — помечено как " + EVIDENCE_TYPES.get(t, t))
            if e.get("data_date"):
                parts.append(f"Дата данных: {e.get('data_date')}")
            cls = [f"t-{t}" if t else "t-unknown"]
            if st != "active":
                cls.append("st-inactive")
            label = EVIDENCE_TYPES.get(t, "") if t in WEAK_TYPES else ""
            if st != "active":
                label = EV_STATUS.get(st, st)
            return "\n".join(parts), cls, label
        if kind == "C" and rid in p.c_by_id:
            c = p.c_by_id[rid]
            st = str(c.get("status") or "active")
            parts = [f"Вывод · {C_KIND.get(str(c.get('kind')), c.get('kind') or '—')} · {C_STATUS.get(st, st)}",
                     _short(c.get("statement"), 320)]
            deps = _list_values(c.get("depends_on"))
            if deps:
                parts.append("Опирается на: " + ", ".join(deps))
            if c.get("note"):
                parts.append("Примечание: " + _short(c.get("note"), 200))
            cls = ["t-conclusion"]
            label = ""
            if st == "needs_review":
                cls.append("st-review")
                label = "на пересмотре"
            elif st in ("retracted", "revised"):
                cls.append("st-inactive")
                label = C_STATUS.get(st, st)
            return "\n".join(parts), cls, label
        if kind == "S" and rid in p.src_by_id:
            s = p.src_by_id[rid]
            parts = [_short(s.get("title"), 200),
                     " · ".join(x for x in [SRC_KIND.get(str(s.get("kind")), s.get("kind") or ""),
                                           str(s.get("publisher") or ""),
                                           "надёжность: " + RELIABILITY.get(str(s.get("reliability")), str(s.get("reliability") or "—"))] if x),
                     _short(s.get("locator"), 160)]
            cls = ["t-source"]
            label = ""
            if "injection_suspected" in _list_values(s.get("flags")):
                parts.append("⚠ В тексте источника найдены подозрительные инструкции")
                cls.append("st-flag")
            if s.get("allowed") is False:
                cls.append("st-inactive")
                label = "не допущен"
            return "\n".join(p_ for p_ in parts if p_), cls, label
        if kind == "D" and rid in p.d_by_id:
            d = p.d_by_id[rid]
            st = str(d.get("status") or "active")
            return (f"Решение · {d.get('date', '')} · {DEC_STATUS.get(st, st)}\n{_short(d.get('decision'), 300)}",
                    ["t-decision"], "")
        return "Нет в реестре — ссылка не подтверждена", ["missing"], "нет в реестре"

    def one(self, rid: str) -> str:
        self.used[rid] += 1
        tip, cls, label = self.tip_and_class(rid)
        tag = f'<span class="ref-tag">{esc(label)}</span>' if label else ""
        aria = attr(f"{rid}: " + tip.replace("\n", ". "))
        if "missing" in cls:
            return f'<span class="ref missing" tabindex="0" data-tip="{attr(tip)}" aria-label="{aria}">{esc(rid)}{tag}</span>'
        return (f'<a class="ref {" ".join(cls)}" href="#{esc(rid)}" data-tip="{attr(tip)}" '
                f'aria-label="{aria}">{esc(rid)}{tag}</a>')

    def group(self, ids: list[str]) -> str:
        return '<span class="refs">' + "".join(self.one(i) for i in ids) + "</span>"


# --------------------------------------------------------------------------- markdown → HTML

LABEL_RE = re.compile(r"(?<![\w>])(Гипотеза|Прогноз|Допущение|Оценка|Синтетика|Синтетические данные)(\s*:)")
LABEL_CLASS = {"Гипотеза": "hypothesis", "Прогноз": "forecast", "Допущение": "assumption",
               "Оценка": "estimate", "Синтетика": "synthetic", "Синтетические данные": "synthetic"}


class Markdown:
    """Небольшой конвертер markdown → HTML: заголовки, абзацы, списки (вложенные),
    таблицы, цитаты, код, жирный/курсив, ссылки, маркеры [E012]/[C003]/[S004].
    Всё содержимое экранируется; сырые HTML-теги из markdown не пропускаются."""

    def __init__(self, refs: RefRenderer | None, heading_shift: int = 2):
        self.refs = refs
        self.shift = heading_shift

    # ---- inline
    def inline(self, text: str) -> str:
        tokens: list[str] = []

        def keep(fragment: str) -> str:
            tokens.append(fragment)
            return f"\x00{len(tokens) - 1}\x00"

        text = text.replace("\x00", "")
        text = re.sub(r"`([^`]+)`", lambda m: keep(f"<code>{esc(m.group(1))}</code>"), text)

        def link(m: re.Match) -> str:
            label, url = m.group(1), m.group(2)
            href = _safe_href(url)
            inner = self._emphasis(esc(label))
            if href is None:
                return keep(inner)
            ext = ' rel="noopener noreferrer"' if href.lower().startswith("http") else ""
            return keep(f'<a href="{esc(href)}"{ext}>{inner}</a>')

        text = re.sub(r"\[([^\]\[]+)\]\(((?:[^()\s]|\([^()\s]*\))+)(?:\s+\"[^\"]*\")?\)", link, text)
        text = re.sub(r"<(https?://[^>\s]+)>", lambda m: keep(
            f'<a href="{esc(m.group(1))}" rel="noopener noreferrer">{esc(m.group(1))}</a>'), text)
        if self.refs is not None:
            text = REF_GROUP_RE.sub(
                lambda m: keep(self.refs.group(re.split(r"\s*[,;]\s*", m.group(1)))), text)
        text = esc(text)
        text = LABEL_RE.sub(lambda m: keep(
            f'<span class="lbl lbl-{LABEL_CLASS[m.group(1)]}">{esc(m.group(1))}{m.group(2).strip()}</span>'), text)
        text = self._emphasis(text)
        text = re.sub(r"\bTODO\b", '<span class="todo">TODO</span>', text)
        # восстановление вложенных токенов (до двух уровней достаточно)
        for _ in range(3):
            if "\x00" not in text:
                break
            text = re.sub(r"\x00(\d+)\x00", lambda m: tokens[int(m.group(1))], text)
        return text

    @staticmethod
    def _emphasis(t: str) -> str:
        t = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)", r"<strong>\1</strong>", t)
        t = re.sub(r"(?<![\*\w])\*(?=\S)([^*]+?)(?<=\S)\*(?!\*)", r"<em>\1</em>", t)
        t = re.sub(r"(?<!\w)_(?=\S)([^_]+?)(?<=\S)_(?!\w)", r"<em>\1</em>", t)
        t = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<del>\1</del>", t)
        return t

    # ---- blocks
    LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
    TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")

    def render(self, text: str) -> str:
        lines = text.replace("\r\n", "\n").replace("\t", "    ").split("\n")
        out: list[str] = []
        i, n = 0, len(lines)
        para: list[str] = []

        def flush_para():
            if para:
                parts = []
                for j, ln in enumerate(para):
                    hard = ln.endswith("  ") and j < len(para) - 1
                    parts.append(self.inline(ln.strip()) + ("<br>" if hard else ""))
                out.append("<p>" + " ".join(parts) + "</p>")
                para.clear()

        while i < n:
            line = lines[i]
            stripped = line.strip()
            if not stripped:
                flush_para()
                i += 1
                continue
            if stripped.startswith("```") or stripped.startswith("~~~"):
                flush_para()
                fence = stripped[:3]
                buf = []
                i += 1
                while i < n and not lines[i].strip().startswith(fence):
                    buf.append(lines[i])
                    i += 1
                i += 1
                out.append("<pre><code>" + esc("\n".join(buf)) + "</code></pre>")
                continue
            m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", stripped)
            if m:
                flush_para()
                level = min(6, len(m.group(1)) + self.shift)
                out.append(f"<h{level}>{self.inline(m.group(2))}</h{level}>")
                i += 1
                continue
            if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
                flush_para()
                out.append("<hr>")
                i += 1
                continue
            if stripped.startswith("|") and i + 1 < n and self.TABLE_SEP_RE.match(lines[i + 1]):
                flush_para()
                rows = [stripped]
                i += 2
                while i < n and lines[i].strip().startswith("|"):
                    rows.append(lines[i].strip())
                    i += 1
                out.append(self._table(rows))
                continue
            if stripped.startswith(">"):
                flush_para()
                buf = []
                while i < n and lines[i].strip().startswith(">"):
                    buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                    i += 1
                out.append("<blockquote>" + self.render("\n".join(buf)) + "</blockquote>")
                continue
            if self.LIST_RE.match(line):
                flush_para()
                buf = []
                while i < n:
                    ln = lines[i]
                    if self.LIST_RE.match(ln):
                        buf.append(ln)
                    elif ln.strip() and ln.startswith("  ") and buf:
                        buf.append(ln)  # продолжение пункта
                    elif not ln.strip() and i + 1 < n and self.LIST_RE.match(lines[i + 1]):
                        pass  # пустая строка между пунктами
                    else:
                        break
                    i += 1
                out.append(self._list(buf))
                continue
            para.append(line)
            i += 1
        flush_para()
        return "\n".join(out)

    @staticmethod
    def _cells(row: str) -> list[str]:
        row = row.strip()
        if row.startswith("|"):
            row = row[1:]
        if row.endswith("|") and not row.endswith("\\|"):
            row = row[:-1]
        cells = re.split(r"(?<!\\)\|", row)
        return [c.strip().replace("\\|", "|") for c in cells]

    def _table(self, rows: list[str]) -> str:
        head = self._cells(rows[0])
        body = [self._cells(r) for r in rows[1:]]
        h = "".join(f"<th>{self.inline(c)}</th>" for c in head)
        b = []
        for r in body:
            r = (r + [""] * len(head))[: max(len(head), len(r))]
            b.append("<tr>" + "".join(f"<td>{self.inline(c)}</td>" for c in r) + "</tr>")
        return ('<div class="table-wrap"><table><thead><tr>' + h + "</tr></thead><tbody>"
                + "".join(b) + "</tbody></table></div>")

    def _list(self, lines: list[str]) -> str:
        # элементы: (indent, ordered, text)
        items: list[list] = []
        for ln in lines:
            m = self.LIST_RE.match(ln)
            if m:
                items.append([len(m.group(1)), m.group(2)[0].isdigit(), m.group(3)])
            elif items:
                items[-1][2] += " " + ln.strip()
        html_parts: list[str] = []
        stack: list[tuple[int, str]] = []  # (indent, tag)
        for indent, ordered, text in items:
            tag = "ol" if ordered else "ul"
            while stack and indent < stack[-1][0]:
                html_parts.append(f"</li></{stack.pop()[1]}>")
            if not stack or indent > stack[-1][0]:
                html_parts.append(f"<{tag}>")
                stack.append((indent, tag))
            else:
                html_parts.append("</li>")
            text = re.sub(r"^\[ \]\s+", "☐ ", text)
            text = re.sub(r"^\[[xX]\]\s+", "☑ ", text)
            html_parts.append("<li>" + self.inline(text))
        while stack:
            html_parts.append(f"</li></{stack.pop()[1]}>")
        return "".join(html_parts)


def _drop_title(md: str) -> str:
    """Убираем ведущий H1: раздел отчёта уже имеет свой заголовок."""
    lines = md.lstrip("\n").split("\n")
    if lines and re.match(r"^#\s+", lines[0]):
        lines = lines[1:]
    # служебные строки шаблона («> Правила: …») руководителю не нужны
    out, head = [], True
    for ln in lines:
        if head and ln.strip() and not ln.lstrip().startswith(">"):
            head = False
        if head and re.match(r"^\s*>\s*Правила\b", ln):
            continue
        out.append(ln)
    return "\n".join(out)


def _sections(md: str) -> list[tuple[str, int, str]]:
    """Разбиение markdown по заголовкам: [(заголовок, уровень, тело)]."""
    res: list[tuple[str, int, str]] = []
    cur_title, cur_level, buf = "", 0, []
    in_code = False
    for ln in md.split("\n"):
        if ln.strip().startswith("```"):
            in_code = not in_code
        m = None if in_code else re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", ln.strip())
        if m:
            res.append((cur_title, cur_level, "\n".join(buf).strip()))
            cur_title, cur_level, buf = m.group(2), len(m.group(1)), []
        else:
            buf.append(ln)
    res.append((cur_title, cur_level, "\n".join(buf).strip()))
    return [r for r in res if r[0] or r[2]]


def _find_section(md: str, keywords: list[str], skip_todo: bool = False) -> str:
    """Тело первого раздела, заголовок которого содержит одно из ключевых слов.
    skip_todo: незаполненный раздел шаблона (с TODO) считается отсутствующим."""
    for title, _, body in _sections(md):
        t = title.lower()
        if title and any(k in t for k in keywords) and body:
            if skip_todo and re.search(r"\bTODO\b", body):
                continue
            return body
    return ""


def _limit_lines(md: str, max_lines: int) -> tuple[str, bool]:
    lines = [ln for ln in md.split("\n")]
    if len(lines) <= max_lines:
        return md, False
    return "\n".join(lines[:max_lines]), True


# --------------------------------------------------------------------------- разделы

class ReportBuilder:
    def __init__(self, project: Project):
        self.p = project
        self.refs = RefRenderer(project)
        self.md = Markdown(self.refs, heading_shift=1)

    # ---- шапка
    def brief_fields(self) -> dict:
        md = self.p.brief
        fields = {}
        for key, kws in (("question", ["вопрос", "запрос", "задач"]),
                         ("recipient", ["получател", "для кого", "адресат"]),
                         ("goal", ["цель"]),
                         ("constraints", ["ограничен"])):
            body = _find_section(md, kws)
            if not body:
                # вариант «**Вопрос:** текст» или «Вопрос: текст» в одну строку
                for kw in kws:
                    m = re.search(rf"(?im)^[\s\-*]*\**\s*{kw}[^:\n]*:\**\s*(.+)$", md)
                    if m and m.group(1).strip():
                        body = m.group(1).strip()
                        break
            fields[key] = body
        return fields

    def header(self) -> str:
        st = self.p.state
        bf = self.brief_fields()
        stage = str(st.get("stage") or "")
        stage_txt = STAGE_NAMES.get(stage, stage or "не начат")
        # нумерация этапов 0–8, как в таблице AGENTS.md («Этап 2/8 — сбор»)
        idx = [s for s, _ in STAGES].index(stage) if stage in STAGE_NAMES else 0
        status = STAGE_STATUS.get(str(st.get("stage_status")), str(st.get("stage_status") or ""))
        q = bf["question"] or ""
        q_html = self.md.render(_limit_lines(q, 8)[0]) if q else not_ready("Вопрос появится после этапа «Запрос» (brief.md).")
        rows = [
            ("Вопрос", q_html),
            ("Получатель", self.md.inline(_short(bf["recipient"], 300)) if bf["recipient"] else '<span class="muted">не указан</span>'),
            ("Этап", f"{idx}/{len(STAGES) - 1} — {esc(stage_txt)}" + (f' <span class="pill pill-{esc(st.get("stage_status"))}">{esc(status)}</span>' if status else "")),
            ("Следующий шаг", esc(st.get("next_step") or "—")),
            ("Обновлено", esc(_fmt_ts(st.get("updated_at"))) or "—"),
        ]
        dl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
        return f'<dl class="meta">{dl}</dl>'

    # ---- решение на одной странице
    def one_pager(self) -> str:
        p = self.p
        ps, opt = p.outputs.get("product_strategy", ""), p.outputs.get("options", "")
        sit = p.outputs.get("situation", "")

        def pick(*pairs) -> str:
            for md, kws in pairs:
                found = _find_section(md, kws, skip_todo=True)
                if found:
                    return found
            return ""

        # Заголовки — по шаблонам templates/{options,product_strategy}.md, с запасными вариантами.
        reco = pick((ps, ["коротко", "рекомендац"]), (opt, ["рекомендац", "наш выбор", "предлагаем"]))
        reco_from_strategy = bool(_find_section(ps, ["коротко", "рекомендац"], skip_todo=True))
        why_pairs = [(ps, ["почему", "обоснован", "диагноз"]), (opt, ["почему", "обоснован", "диагноз"])]
        if reco_from_strategy:  # обоснование выбора — в рекомендации из options.md
            why_pairs.append((opt, ["рекомендац"]))
        why = pick(*why_pairs)
        risks = pick((ps, ["риск"]), (opt, ["риск"]))
        unknown = pick((ps, ["чего мы не знаем", "чего не знаем"]), (opt, ["чего мы не знаем", "чего не знаем"]),
                       (sit, ["чего мы не знаем", "чего не знаем", "пробел"]))
        now = pick((opt, ["решить сейчас", "что решить", "нужно решить", "решение человека", "требуется решение"]),
                   (ps, ["решить сейчас", "что решить", "нужно решить"]))

        gates = p.state.get("gates") or {}
        chosen = gates.get("option_chosen")
        blocks = []

        def block(title: str, body_html: str, cls: str = "") -> None:
            blocks.append(f'<div class="op-block {cls}"><h3>{title}</h3>{body_html}</div>')

        def md_block(md: str) -> str:
            text, cut = _limit_lines(md, 18)
            more = '<p class="muted small">Полностью — в разделах ниже.</p>' if cut else ""
            return self.md.render(text) + more

        if chosen:
            c = p.c_by_id.get(str(chosen))
            chosen_html = (f'<p class="chosen">Выбран вариант {self.refs.one(str(chosen))}'
                           + (f": {self.md.inline(_short(c.get('statement'), 400))}" if c else "") + "</p>")
        else:
            chosen_html = ""
        if reco:
            block("Рекомендация", chosen_html + md_block(reco), "op-reco")
        elif chosen_html:
            block("Рекомендация", chosen_html, "op-reco")
        else:
            recs = [c for c in p.conclusions if c.get("kind") == "recommendation" and c.get("status") in ("active", "needs_review")]
            if recs:
                block("Рекомендация", "<ul>" + "".join(
                    f"<li>{self.md.inline(_short(c.get('statement'), 400))} {self.refs.one(str(c.get('id')))}</li>" for c in recs) + "</ul>", "op-reco")
            else:
                block("Рекомендация", not_ready("Появится на этапе «Варианты» (options.md, раздел «Рекомендация»)."), "op-reco")

        block("Почему", md_block(why) if why else not_ready("Обоснование — в options.md / product_strategy.md (раздел «Почему»)."))

        if risks:
            block("Главные риски", md_block(risks))
        else:
            rk = [c for c in p.conclusions if c.get("kind") == "risk" and c.get("status") in ("active", "needs_review")]
            block("Главные риски", ("<ul>" + "".join(
                f"<li>{self.md.inline(_short(c.get('statement'), 300))} {self.refs.one(str(c.get('id')))}</li>" for c in rk[:5]) + "</ul>")
                if rk else not_ready("Риски появятся вместе с вариантами."))

        if unknown:
            block("Чего мы не знаем", md_block(unknown))

        now_parts = []
        if now:
            now_parts.append(md_block(now))
        if p.state.get("stage_status") == "awaiting_human":
            now_parts.append(f'<p class="callout">Агент ждёт вашего решения: {esc(p.state.get("next_step") or "см. открытые вопросы")}</p>')
        oq = _list_values(p.state.get("open_questions"))
        if oq:
            now_parts.append("<p><strong>Открытые вопросы:</strong></p><ul>" + "".join(f"<li>{self.md.inline(q)}</li>" for q in oq) + "</ul>")
        block("Что решить сейчас", "".join(now_parts) if now_parts else not_ready("Нет вопросов, требующих решения прямо сейчас."), "op-now")
        return '<div class="one-pager">' + "".join(blocks) + "</div>"

    def review_banner(self) -> str:
        nr = [c for c in self.p.conclusions if c.get("status") == "needs_review"]
        due = [m for m, _, _ in self.p.decisions if m.get("status") == "review_due"]
        if not nr and not due:
            return ""
        parts = []
        if nr:
            parts.append(f"{len(nr)} вывод(ов) требуют пересмотра после новых данных: " + self.refs.group([str(c.get("id")) for c in nr]))
        if due:
            parts.append(f"{len(due)} решение(й) пора пересмотреть: " + self.refs.group([str(m.get("id")) for m in due if m.get("id")]))
        return '<div class="banner-review" role="alert"><strong>Внимание.</strong> ' + " ".join(parts) + "</div>"

    # ---- прогресс
    def progress(self) -> str:
        p = self.p
        st = p.state
        stage = str(st.get("stage") or "")
        keys = [s for s, _ in STAGES]
        cur = keys.index(stage) if stage in keys else -1
        status = str(st.get("stage_status") or "")
        gates = st.get("gates") or {}
        gate_at = {"plan": ("plan_approved", "план одобрен"), "options": ("option_chosen", "вариант выбран"),
                   "strategy": ("strategy_approved", "стратегия одобрена")}
        # ✓ только у этапов, реально пройденных по history (поле stage и левая часть «a/b → c/d»)
        seen = set()
        for h in (st.get("history") or []):
            if isinstance(h, dict):
                seen.add(str(h.get("stage") or ""))
                m = re.match(r"\s*(\w+)/\w+\s*→\s*(\w+)/", str(h.get("event") or ""))
                if m:
                    seen.update(m.groups())
        items = []
        for i, (key, name) in enumerate(STAGES):
            if (i < cur or (i == cur and status == "done")) and key in seen:
                cls, mark = "done", "✓"
            elif i < cur:
                cls, mark = "todo", "⊘"  # этап пропущен: в истории нет перехода на него
            elif i == cur:
                cls, mark = ("wait" if status == "awaiting_human" else "now"), "●"
            else:
                cls, mark = "todo", "○"
            gate = ""
            if key in gate_at:
                g, gname = gate_at[key]
                val = gates.get(g)
                ok = bool(val)
                gate = f'<span class="gate {"gate-ok" if ok else ""}">⏸ {gname}: {"да" if ok else "нет"}{(" (" + esc(val) + ")") if isinstance(val, str) and val else ""}</span>'
            items.append(f'<li class="st-{cls}"><span class="mark" aria-hidden="true">{mark}</span>'
                         f'<span class="st-name">{i}. {esc(name)}{" (пропущен)" if mark == "⊘" else ""}</span>{gate}</li>')
        ev_types = Counter(str(e.get("type")) for e in p.evidence if e.get("status", "active") == "active")
        nr = sum(1 for c in p.conclusions if c.get("status") == "needs_review")
        spent = sum(_num(c.get("usd")) for c in p.costs)
        budget = _num(st.get("budget_usd"))
        stats = [
            ("Источники", str(len(p.sources))),
            ("Доказательства", f"{sum(ev_types.values())} действующих"
             + (f" ({', '.join(f'{EVIDENCE_TYPES.get(t, t)}: {n}' for t, n in ev_types.most_common())})" if ev_types else "")),
            ("Выводы", f"{len(p.conclusions)}" + (f", на пересмотре: {nr}" if nr else "")),
            ("Решения", str(len(p.decisions))),
            ("Расходы", _usd(spent) + (f" из {_usd(budget)} ({spent / budget * 100:.0f} %)" if budget else "")),
        ]
        stat_html = '<dl class="stats">' + "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in stats) + "</dl>"
        hist = st.get("history") or []
        hist_html = ""
        if isinstance(hist, list) and hist:
            rows = "".join(
                f"<tr><td class=\"nowrap\">{esc(_fmt_ts(h.get('ts')))}</td><td>{esc(STAGE_NAMES.get(str(h.get('stage')), h.get('stage') or ''))}</td><td>{self.md.inline(str(h.get('event') or ''))}</td></tr>"
                for h in hist if isinstance(h, dict))
            hist_html = (f'<details><summary>История этапов ({len(hist)})</summary><div class="table-wrap"><table>'
                         f"<thead><tr><th>Когда</th><th>Этап</th><th>Событие</th></tr></thead><tbody>{rows}</tbody></table></div></details>")
        return f'<ol class="stages">{"".join(items)}</ol>{stat_html}{hist_html}'

    # ---- разделы из outputs
    def output_section(self, name: str, hint: str) -> str:
        md = self.p.outputs.get(name, "")
        if not md.strip():
            return not_ready(hint)
        return '<div class="prose">' + self.md.render(_drop_title(md)) + "</div>"

    # ---- решения и пересмотры
    def decisions(self) -> str:
        p = self.p
        parts = []
        if p.decisions:
            for meta, body, fname in p.decisions:
                did = str(meta.get("id") or fname.split("-")[0])
                st = str(meta.get("status") or "active")
                rows = []
                for key, label in (("date", "Дата"), ("owner", "Ответственный")):
                    if meta.get(key):
                        rows.append(f"<dt>{label}</dt><dd>{esc(meta.get(key))}</dd>")
                based = _list_values(meta.get("based_on"))
                if based:
                    rows.append(f"<dt>Основания</dt><dd>{self.refs.group(based)}</dd>")
                for key, label in (("metrics", "Метрики"), ("review_triggers", "Условия пересмотра"), ("commitments", "Обязательства")):
                    vals = _list_values(meta.get(key))
                    if vals:
                        rows.append(f"<dt>{label}</dt><dd><ul>" + "".join(f"<li>{self.md.inline(v)}</li>" for v in vals) + "</ul></dd>")
                body_html = f'<details><summary>Полный текст записи</summary><div class="prose">{self.md.render(_drop_title(body))}</div></details>' if body.strip() else ""
                parts.append(
                    f'<article class="decision dec-{esc(st)}" id="{esc(did)}"><h3>{esc(did)} · {self.md.inline(str(meta.get("decision") or fname))}'
                    f' <span class="pill pill-{esc(st)}">{esc(DEC_STATUS.get(st, st))}</span></h3>'
                    f'<dl class="meta">{"".join(rows)}</dl>{body_html}</article>')
        else:
            parts.append(not_ready("Запись решения появится на этапе «Решение» (decisions/D###.md)."))

        # пересмотры: что изменилось в доказательствах и выводах
        rev = []
        for e in p.evidence:
            st = e.get("status")
            if st in ("superseded", "retracted"):
                txt = f"{EV_STATUS.get(st, st).capitalize()}: {self.refs.one(str(e.get('id')))} {self.md.inline(_short(e.get('claim'), 200))}"
                if e.get("superseded_by"):
                    txt += f" → заменено на {self.refs.one(str(e.get('superseded_by')))}"
                if e.get("reason"):
                    txt += f' <span class="muted">Причина: {self.md.inline(_short(e.get("reason"), 200))}</span>'
                rev.append((str(e.get("added_at") or ""), txt))
        for c in p.conclusions:
            st = c.get("status")
            if st in ("needs_review", "revised", "retracted"):
                txt = f"Вывод {C_STATUS.get(st, st)}: {self.refs.one(str(c.get('id')))} {self.md.inline(_short(c.get('statement'), 200))}"
                if c.get("note"):
                    txt += f' <span class="muted">{self.md.inline(_short(c.get("note"), 200))}</span>'
                rev.append((str(c.get("updated_at") or ""), txt))
        if rev:
            rev.sort(key=lambda x: x[0], reverse=True)
            parts.append("<h3>Пересмотры</h3><ul class=\"revisions\">" + "".join(
                f'<li><span class="muted nowrap">{esc(_fmt_ts(ts))}</span> {t}</li>' for ts, t in rev) + "</ul>")
        else:
            parts.append('<h3>Пересмотры</h3><p class="muted">Пересмотров пока не было.</p>')
        return "".join(parts)

    # ---- приложение: реестры
    def evidence_table(self) -> str:
        p = self.p
        if not p.evidence:
            return not_ready("Доказательства появятся на этапе «Сбор данных».")
        types = Counter(str(e.get("type") or "") for e in p.evidence)
        buttons = ['<button type="button" class="flt is-on" data-filter="all" aria-pressed="true">Все ({})</button>'.format(len(p.evidence))]
        for t in list(EVIDENCE_TYPES) + sorted(set(types) - set(EVIDENCE_TYPES)):
            if types.get(t):
                buttons.append(f'<button type="button" class="flt" data-filter="{esc(t)}" aria-pressed="false">'
                               f'<span class="tbadge t-{esc(t)}">{esc(EVIDENCE_TYPES.get(t, t or "?"))}</span> {types[t]}</button>')
        toggle = ('<label class="small"><input type="checkbox" id="show-inactive" checked> показывать заменённые и отозванные</label>'
                  if any(e.get("status", "active") != "active" for e in p.evidence) else "")
        rows = []
        for e in sorted(p.evidence, key=lambda x: str(x.get("id"))):
            eid = str(e.get("id") or "")
            t = str(e.get("type") or "")
            st = str(e.get("status") or "active")
            srcs = _list_values(e.get("source_ids"))
            src_html = self.refs.group(srcs) if srcs else '<span class="muted">—</span>'
            quote = f'<blockquote class="q">«{esc(_short(e.get("quote"), 400))}»</blockquote>' if e.get("quote") else ""
            loc = f'<div class="muted small">{esc(e.get("locator"))}</div>' if e.get("locator") else ""
            status_html = esc(EV_STATUS.get(st, st))
            if e.get("superseded_by"):
                status_html += " → " + self.refs.one(str(e.get("superseded_by")))
            if e.get("reason"):
                status_html += f'<div class="muted small">{esc(_short(e.get("reason"), 200))}</div>'
            rows.append(
                f'<tr id="{esc(eid)}" data-type="{esc(t)}" data-status="{esc(st)}" class="{"row-inactive" if st != "active" else ""}">'
                f'<td class="nowrap"><strong>{esc(eid)}</strong></td>'
                f'<td><span class="tbadge t-{esc(t)}">{esc(EVIDENCE_TYPES.get(t, t or "?"))}</span></td>'
                f'<td>{esc(e.get("claim"))}{quote}{loc}</td>'
                f'<td>{src_html}</td>'
                f'<td class="nowrap">{esc(e.get("data_date") or "—")}</td>'
                f'<td>{esc(RELIABILITY.get(str(e.get("confidence")), e.get("confidence") or "—"))}</td>'
                f'<td>{status_html}</td></tr>')
        return (f'<div class="filters no-print" role="group" aria-label="Фильтр по типу">{"".join(buttons)} {toggle}</div>'
                '<div class="table-wrap"><table class="registry" id="evidence-table"><thead><tr>'
                "<th>ID</th><th>Тип</th><th>Утверждение и цитата</th><th>Источник</th><th>Дата данных</th><th>Уверенность</th><th>Статус</th>"
                f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
                '<p class="muted small" id="evidence-empty" hidden>Нет доказательств выбранного типа.</p>')

    def conclusions_table(self) -> str:
        p = self.p
        if not p.conclusions:
            return not_ready("Выводы появятся на этапе «Картина ситуации».")
        rows = []
        for c in sorted(p.conclusions, key=lambda x: str(x.get("id"))):
            cid = str(c.get("id") or "")
            st = str(c.get("status") or "active")
            deps = _list_values(c.get("depends_on"))
            note = f'<div class="muted small">{esc(_short(c.get("note"), 300))}</div>' if c.get("note") else ""
            rows.append(
                f'<tr id="{esc(cid)}" class="{"row-review" if st == "needs_review" else ("row-inactive" if st in ("retracted", "revised") else "")}">'
                f'<td class="nowrap"><strong>{esc(cid)}</strong></td>'
                f'<td>{esc(C_KIND.get(str(c.get("kind")), c.get("kind") or "—"))}</td>'
                f'<td>{esc(c.get("statement"))}{note}</td>'
                f'<td>{self.refs.group(deps) if deps else "—"}</td>'
                f'<td><span class="pill pill-{esc(st)}">{esc(C_STATUS.get(st, st))}</span></td>'
                f'<td class="nowrap">{esc(_fmt_ts(c.get("updated_at")))}</td></tr>')
        return ('<div class="table-wrap"><table class="registry"><thead><tr><th>ID</th><th>Вид</th><th>Вывод</th>'
                f'<th>Опирается на</th><th>Статус</th><th>Обновлён</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')

    def sources_table(self) -> str:
        p = self.p
        if not p.sources:
            return not_ready("Источники появятся на этапе «Сбор данных».")
        used = Counter()
        for e in p.evidence:
            for sid in _list_values(e.get("source_ids")):
                used[sid] += 1
        rows = []
        for s in sorted(p.sources, key=lambda x: str(x.get("id"))):
            sid = str(s.get("id") or "")
            loc = str(s.get("locator") or "")
            href = _safe_href(loc) if loc.lower().startswith(("http://", "https://")) else None
            loc_html = (f'<a href="{esc(href)}" rel="noopener noreferrer" class="break">{esc(_short(loc, 80))}</a>'
                        if href else f'<span class="break">{esc(_short(loc, 80))}</span>')
            flags = _list_values(s.get("flags"))
            flag_html = ""
            if "injection_suspected" in flags:
                flag_html += '<div class="warn small">⚠ подозрение на внедрённые инструкции — текст использован только как данные</div>'
            other = [f for f in flags if f != "injection_suspected"]
            if other:
                flag_html += f'<div class="muted small">флаги: {esc(", ".join(other))}</div>'
            if s.get("allowed") is False:
                flag_html += '<div class="warn small">не допущен к использованию</div>'
            notes = f'<div class="muted small">{esc(_short(s.get("notes"), 200))}</div>' if s.get("notes") else ""
            rows.append(
                f'<tr id="{esc(sid)}"><td class="nowrap"><strong>{esc(sid)}</strong></td>'
                f'<td>{esc(s.get("title"))}{notes}{flag_html}</td>'
                f'<td>{esc(s.get("publisher") or "—")}</td>'
                f'<td>{esc(SRC_KIND.get(str(s.get("kind")), s.get("kind") or "—"))}</td>'
                f'<td>{esc(RELIABILITY.get(str(s.get("reliability")), s.get("reliability") or "—"))}</td>'
                f'<td>{loc_html}<div class="muted small">получен {esc(_fmt_ts(s.get("retrieved_at")) or "—")}</div></td>'
                f'<td class="num">{used.get(sid, 0)}</td></tr>')
        return ('<div class="table-wrap"><table class="registry"><thead><tr><th>ID</th><th>Название</th><th>Издатель</th>'
                '<th>Вид</th><th>Надёжность</th><th>Где взят</th><th class="num">Доказ.</th></tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table></div>')

    def actions_table(self) -> str:
        acts = self.p.actions
        if not acts:
            return not_ready("Журнал пуст: действия записываются хуками Claude Code или командой sa.py log.")
        shown = list(reversed(acts))[:ACTION_LOG_LIMIT]
        by_actor = Counter(str(a.get("actor") or "—") for a in acts)
        fails = sum(1 for a in acts if a.get("ok") is False)
        summary = (f'<p class="small">Всего действий: {len(acts)}; по исполнителям: '
                   + ", ".join(f"{esc(k)} — {v}" for k, v in by_actor.most_common())
                   + (f'; <span class="warn">с ошибкой: {fails}</span>' if fails else "") + ".</p>")
        rows = "".join(
            f'<tr class="{"row-fail" if a.get("ok") is False else ""}"><td class="nowrap">{esc(_fmt_ts(a.get("ts")))}</td>'
            f'<td>{esc(a.get("actor"))}</td><td>{esc(a.get("action"))}</td><td class="break">{esc(_short(a.get("target"), 80))}</td>'
            f'<td>{"✓" if a.get("ok") is not False else "✗"}</td><td class="small">{esc(_short(a.get("detail"), 200))}</td></tr>'
            for a in shown)
        more = f'<p class="muted small">Показаны последние {ACTION_LOG_LIMIT}; полный журнал — log/actions.jsonl.</p>' if len(acts) > ACTION_LOG_LIMIT else ""
        return (summary + '<details><summary>Показать журнал</summary><div class="table-wrap"><table class="registry"><thead><tr><th>Когда</th>'
                '<th>Кто</th><th>Действие</th><th>Объект</th><th>Успех</th><th>Детали</th></tr></thead>'
                f"<tbody>{rows}</tbody></table></div>{more}</details>")

    def costs(self) -> str:
        p = self.p
        rows = p.costs
        budget = _num(p.state.get("budget_usd"))
        if not rows:
            return not_ready("Расходы записываются хуками Claude Code или командой sa.py cost add.")
        total = sum(_num(r.get("usd")) for r in rows)
        tin = sum(_int(r.get("input_tokens")) for r in rows)
        tout = sum(_int(r.get("output_tokens")) for r in rows)

        def agg(key: str, names: dict | None = None) -> str:
            g: dict = defaultdict(lambda: [0.0, 0, 0, 0])
            for r in rows:
                k = str(r.get(key) or "—")
                g[k][0] += _num(r.get("usd"))
                g[k][1] += _int(r.get("input_tokens"))
                g[k][2] += _int(r.get("output_tokens"))
                g[k][3] += 1
            items = sorted(g.items(), key=lambda kv: -kv[1][0])
            body = ""
            for k, (usd, i, o, n) in items:
                share = usd / total * 100 if total else 0
                label = names.get(k, k) if names else k
                body += (f'<tr><td>{esc(label)}</td><td class="num">{_tok(i)}</td><td class="num">{_tok(o)}</td>'
                         f'<td class="num">{n}</td><td class="num">{esc(_usd(usd))}</td>'
                         f'<td class="bar-cell"><span class="bar" style="width:{share:.1f}%"></span><span class="small">{share:.0f} %</span></td></tr>')
            return body

        head = '<thead><tr><th>{}</th><th class="num">Вход, ток.</th><th class="num">Выход, ток.</th><th class="num">Записей</th><th class="num">USD</th><th>Доля</th></tr></thead>'
        src = Counter(str(r.get("source") or "—") for r in rows)
        src_names = {"hook": "хук (автоматически)", "manual": "вручную", "estimate": "оценка"}
        budget_html = ""
        if budget:
            pct = total / budget * 100
            budget_html = (f'<div class="budget{" over" if pct >= 80 else ""}"><span class="bar" style="width:{min(pct, 100):.1f}%"></span></div>'
                           f'<p class="small">Израсходовано {esc(_usd(total))} из бюджета {esc(_usd(budget))} ({pct:.0f} %)'
                           + (" — <strong>превышен порог 80 %</strong>" if pct >= 80 else "") + ".</p>")
        note = ""
        if src.get("estimate"):
            note = f'<p class="muted small">{src["estimate"]} запис(ей) — оценка, а не точный учёт: их стоимость приблизительна.</p>'
        return (f'<p class="lead-num">{esc(_usd(total))} <span class="small muted">· вход {_tok(tin)} ток. · выход {_tok(tout)} ток.</span></p>'
                + budget_html
                + f'<h3>По этапам</h3><div class="table-wrap"><table class="costs">{head.format("Этап")}<tbody>{agg("stage", STAGE_NAMES)}</tbody></table></div>'
                + f'<h3>По моделям</h3><div class="table-wrap"><table class="costs">{head.format("Модель")}<tbody>{agg("model")}</tbody></table></div>'
                + '<p class="small">Способ учёта: ' + ", ".join(f"{esc(src_names.get(k, k))} — {v}" for k, v in src.most_common()) + ".</p>"
                + note)

    # ---- сборка
    def build(self) -> dict:
        st = self.p.state
        title = str(st.get("title") or st.get("project") or self.p.root.name)

        def safe(fn, *a) -> str:
            try:
                return fn(*a)
            except Exception as exc:  # отчёт не должен падать из-за одного раздела
                return f'<p class="warn">Раздел не собран: {esc(type(exc).__name__)}: {esc(_short(exc, 200))}</p>'

        sections = [
            ("decision", "Решение на одной странице", safe(self.one_pager), True),
            ("progress", "Прогресс по этапам", safe(self.progress), True),
            ("situation", "Картина ситуации", safe(self.output_section, "situation", "Появится на этапе 3 «Картина ситуации»: что известно, откуда, чего не знаем."), True),
            ("options", "Варианты", safe(self.output_section, "options", "Появится на этапе 4 «Варианты»: не меньше трёх разных вариантов с возражениями."), True),
            ("product", "Продуктовая стратегия", safe(self.output_section, "product_strategy", "Появится после выбора варианта."), True),
            ("comms", "Коммуникационная стратегия", safe(self.output_section, "comms_strategy", "Появится после выбора варианта."), True),
            ("test", "План проверки", safe(self.output_section, "test_plan", "Появится на этапе 6: ожидаемое изменение, с чем сравниваем, срок."), True),
            ("decisions", "Решения и пересмотры", safe(self.decisions), True),
            ("research-plan", "План исследования", safe(self.output_section, "research_plan", "Появится на этапе 1."), False),
            ("evidence", "Реестр доказательств", safe(self.evidence_table), False),
            ("conclusions", "Выводы", safe(self.conclusions_table), False),
            ("sources", "Источники", safe(self.sources_table), False),
            ("actions", "Журнал действий", safe(self.actions_table), False),
            ("costs", "Расходы", safe(self.costs), False),
        ]
        toc_main = "".join(f'<li><a href="#sec-{sid}">{esc(name)}</a></li>' for sid, name, _, main in sections if main)
        toc_app = "".join(f'<li><a href="#sec-{sid}">{esc(name)}</a></li>' for sid, name, _, main in sections if not main)
        body_main = "".join(f'<section id="sec-{sid}"><h2>{esc(name)}</h2>{html_}</section>' for sid, name, html_, main in sections if main)
        body_app = "".join(f'<section id="sec-{sid}"><h2>{esc(name)}</h2>{html_}</section>' for sid, name, html_, main in sections if not main)
        legend = ('<p class="legend small no-print">Ссылки в тексте: '
                  '<a class="ref t-fact" href="#sec-evidence" data-tip="Подтверждённый факт с источником и цитатой">E001</a> факт · '
                  '<a class="ref t-hypothesis" href="#sec-evidence" data-tip="Гипотеза: требует проверки">E002<span class="ref-tag">гипотеза</span></a> гипотеза, прогноз, допущение, синтетика · '
                  '<a class="ref t-conclusion" href="#sec-conclusions" data-tip="Вывод, опирающийся на доказательства">C001</a> вывод · '
                  '<a class="ref t-source" href="#sec-sources" data-tip="Источник">S001</a> источник. Наведите курсор, чтобы увидеть утверждение, цитату и источник.</p>')
        return {
            "title": esc(title),
            "lang": "ru",
            "generated": esc(datetime.now().strftime("%d.%m.%Y %H:%M")),
            "project": esc(st.get("project") or self.p.root.name),
            "header": safe(self.header),
            "banner": safe(self.review_banner),
            "legend": legend,
            "toc_main": toc_main,
            "toc_appendix": toc_app,
            "main": body_main,
            "appendix": body_app,
        }


FALLBACK_TEMPLATE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>$title</title></head>
<body><h1>$title</h1>$header $banner $legend <nav><ol>$toc_main</ol><ol>$toc_appendix</ol></nav>
<main>$main</main><hr><div>$appendix</div><p>Сформировано $generated · проект $project</p></body></html>"""


def render_html(project_dir: Path) -> str:
    values = ReportBuilder(Project(Path(project_dir))).build()
    tpl_text = _read_text(TEMPLATE_PATH) or FALLBACK_TEMPLATE
    return Template(tpl_text).safe_substitute(values)


def build_report(project_dir: Path) -> Path:
    """Собрать outputs/report.html; вернуть путь к файлу. Отсутствующие файлы проекта не ошибка."""
    project_dir = Path(project_dir)
    out = project_dir / "outputs" / "report.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".html.tmp")
    tmp.write_text(render_html(project_dir), encoding="utf-8")
    tmp.replace(out)
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Использование: python3 scripts/lib/report.py projects/<slug>", file=sys.stderr)
        sys.exit(2)
    print(build_report(Path(sys.argv[1])))
