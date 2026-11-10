"""Общие функции для скриптов анализа (только стандартная библиотека).

Чтение CSV (кодировка и разделитель определяются сами), разбор чисел и дат
в русских и международных форматах, пути вывода (--out / --slug) и блок
«Воспроизводимость» (входной файл + sha256, параметры, команда).
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import re
import shlex
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION = "1.0"

MISSING_TOKENS = {
    "", "na", "n/a", "nan", "null", "none", "-", "—", "–", "н/д", "нд", "#н/д",
    "#n/a", "нет данных", "?",
}

_NUM_STRIP = re.compile(r"(?i)\s| | |руб\.?|₽|\$|€|тыс\.?|млн\.?|млрд\.?|шт\.?")


def is_missing(value: str | None) -> bool:
    return value is None or value.strip().lower() in MISSING_TOKENS


def parse_number(value: str | None) -> float | None:
    """'1 234,5' → 1234.5; '12%' → 12.0; '1,234.5' → 1234.5; иначе None.

    Единицы (руб., тыс.) отбрасываются: их наличие проверяет profile.py отдельно.
    Одиночная запятая трактуется как десятичная (российский формат).
    """
    if is_missing(value):
        return None
    s = _NUM_STRIP.sub("", value.strip())
    s = s.rstrip("%")
    if s.startswith("(") and s.endswith(")"):  # бухгалтерская запись отрицательного
        s = "-" + s[1:-1]
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        if s.count(",") > 1:
            return None
        s = s.replace(",", ".")
    try:
        x = float(s)
    except ValueError:
        return None
    if x != x or x in (float("inf"), float("-inf")):
        return None
    return x


_AMBIG = re.compile(r"^[-+(]?\d{1,3},\d{3}\)?$")


def is_ambiguous_number(value: str | None) -> bool:
    """'1,234' — тысячи (1234) или десятичная дробь (1,234)? Парсер читает как 1.234; такие значения надо проверять."""
    if is_missing(value):
        return False
    return bool(_AMBIG.match(_NUM_STRIP.sub("", value.strip()).rstrip("%")))


def count_ambiguous(values) -> int:
    return sum(1 for v in values if is_ambiguous_number(v))


def parse_share(value: str | float) -> float:
    """Доля из '4.2%', '4,2 %', '0.042' → 0.042. Число > 1 без % — ошибка (вероятно, проценты)."""
    if isinstance(value, (int, float)):
        x, pct = float(value), False
    else:
        pct = "%" in value
        x = parse_number(value)
        if x is None:
            raise ValueError(f"не число: {value!r}")
    if pct:
        x /= 100.0
    if not 0.0 <= x <= 1.0:
        raise ValueError(f"доля {value!r} вне [0; 1]: укажите долю (0.05) или проценты со знаком % (5%)")
    return x


def share_type(value: str) -> float:
    """Тип для argparse: доля или проценты со знаком % (понятная ошибка вместо трассировки)."""
    import argparse
    try:
        return parse_share(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


_QUARTER = [
    re.compile(r"^(\d{4})\s*[-_ ]?\s*[QqКк]([1-4])$"),
    re.compile(r"^[QqКк]([1-4])\s*[-_ ]?\s*(\d{4})$"),
    re.compile(r"^([1-4])\s*кв\.?\s*(\d{4})", re.I),
]
_DATE_FORMATS = [
    ("%Y-%m-%d", "day"), ("%d.%m.%Y", "day"), ("%d/%m/%Y", "day"), ("%Y/%m/%d", "day"),
    ("%Y-%m-%d %H:%M:%S", "day"), ("%Y-%m-%dT%H:%M:%S", "day"), ("%d.%m.%Y %H:%M", "day"),
    ("%Y-%m-%d %H:%M", "day"), ("%Y-%m-%dT%H:%M", "day"), ("%d.%m.%Y %H:%M:%S", "day"),
    ("%Y-%m", "month"), ("%m.%Y", "month"), ("%m/%Y", "month"),
]


def parse_date(value: str | None) -> tuple[dt.date, str] | None:
    """Возвращает (дата, гранулярность: day|month|quarter) или None."""
    if is_missing(value):
        return None
    s = value.strip()
    s = re.sub(r"(?<=\d{2}:\d{2}:\d{2})[.,]\d+", "", s)  # доли секунды
    s = re.sub(r"(Z|[+-]\d{2}:?\d{2})$", "", s) if re.search(r"\d{2}:\d{2}", s) else s  # часовой пояс
    for i, rx in enumerate(_QUARTER):
        m = rx.match(s)
        if m:
            if i == 0:
                year, q = int(m.group(1)), int(m.group(2))
            else:
                q, year = int(m.group(1)), int(m.group(2))
            return dt.date(year, 3 * (q - 1) + 1, 1), "quarter"
    for fmt, gran in _DATE_FORMATS:
        try:
            d = dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
        if 1900 <= d.year <= 2100:
            return d, gran
    return None


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv(path: str | Path) -> tuple[list[str], list[dict[str, str]], dict]:
    """Читает CSV: utf-8 (с BOM или без), иначе cp1251; разделитель , ; tab |.

    Возвращает (заголовок, строки-словари, метаданные: encoding, delimiter, ragged_rows).
    """
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit(f"Ошибка: {path} не читается как utf-8 или cp1251. Пересохраните файл в utf-8 (CSV UTF-8).")
    if not text.strip():
        raise SystemExit(f"Ошибка: {path} пустой файл.")
    sample = text[:20000]
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delim = ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delim)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise SystemExit(f"Ошибка: {path}: в файле нет строк данных")
    header = [h.strip() for h in rows[0]]
    if len(set(header)) != len(header):
        raise ValueError(f"{path}: повторяющиеся названия столбцов {header}")
    out, ragged = [], 0
    for r in rows[1:]:
        if len(r) != len(header):
            ragged += 1
        r = (r + [""] * len(header))[: len(header)]
        out.append({h: v.strip() for h, v in zip(header, r)})
    return header, out, {"encoding": enc, "delimiter": delim, "ragged_rows": ragged}


def require_columns(header: list[str], needed: list[str], path) -> None:
    missing = [c for c in needed if c not in header]
    if missing:
        raise SystemExit(f"Ошибка: в {path} нет столбцов {missing}. Есть: {header}. "
                         f"Укажите имена через параметры --*-col.")


def add_output_args(p) -> None:
    p.add_argument("--out", help="путь отчёта .md (рядом пишется .csv с тем же именем, если есть таблица); "
                                 "по правилам проекта — projects/<slug>/data/…")
    p.add_argument("--slug", help="проект: отчёт пишется в projects/<slug>/data/<имя по умолчанию>")


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def project_path(slug: str) -> Path:
    """projects/<slug> с проверкой имени (без «..» и слэшей); учитывает SA_PROJECTS_DIR."""
    import os
    if not slug or not _SLUG_RE.match(slug):
        raise SystemExit(f"Ошибка: некорректное имя проекта «{slug}» (строчные латинские буквы, цифры, дефис).")
    env = os.environ.get("SA_PROJECTS_DIR")
    base = Path(env).expanduser().resolve() if env else REPO_ROOT / "projects"
    return base / slug


def resolve_out(args, default_name: str) -> Path | None:
    if getattr(args, "out", None):
        out = Path(args.out)
        if out.is_dir() or args.out.endswith(("/", "\\")):
            out = out / default_name
    elif getattr(args, "slug", None):
        proj = project_path(args.slug)
        if not proj.exists():
            raise SystemExit(f"Ошибка: проект {proj} не найден (создайте: python3 scripts/sa.py new {args.slug})")
        out = proj / "data" / default_name
    else:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def params_hash(params: dict) -> str:
    blob = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def provenance_md(script: str, inputs: list[tuple[str | Path, int | None]], params: dict) -> str:
    """Блок «Воспроизводимость»: что запускали, на каком файле (sha256), с какими параметрами."""
    lines = ["## Воспроизводимость", f"- Скрипт: `scripts/analysis/{script}` (версия {VERSION})",
             f"- Команда: `python3 {shlex.join([_rel(sys.argv[0])] + sys.argv[1:])}`"]
    if inputs:
        for path, n in inputs:
            extra = f", строк данных: {n}" if n is not None else ""
            lines.append(f"- Входной файл: `{_rel(path)}` — sha256 `{sha256_file(path)}`{extra}")
    else:
        lines.append("- Входной файл: нет (только параметры командной строки)")
    clean = {k: v for k, v in params.items() if k not in ("out", "slug")}
    lines.append(f"- Параметры (sha256 `{params_hash(clean)[:16]}`): `{json.dumps(clean, ensure_ascii=False, sort_keys=True, default=str)}`")
    lines.append(f"- Запуск: {dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()}, Python {sys.version.split()[0]}")
    lines.append("- Повтор: та же команда на файле с тем же sha256 даёт те же числа.")
    return "\n".join(lines) + "\n"


def _rel(path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def fmt(x: float | None, digits: int = 2) -> str:
    """Число для отчёта: пробел-разделитель тысяч, запятая — десятичная."""
    if x is None:
        return "—"
    if abs(x) >= 1000:
        digits = 0
    s = f"{x:,.{digits}f}".replace(",", " ").replace(".", ",")
    return s


def pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else fmt(100.0 * x, digits) + " %"


def finish(out: Path | None, report: str, summary: str) -> None:
    """Пишет отчёт (если задан путь) и печатает краткий итог."""
    if out:
        out.write_text(report, encoding="utf-8")
        print(f"Отчёт: {_rel(out)}")
    print(summary)
