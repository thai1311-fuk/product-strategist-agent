"""Хранилище: пути, атомарная запись JSON/JSONL, генерация ID, журнал действий.

Все записи идут через временный файл + os.replace, поэтому при сбое файл остаётся
либо старым, либо новым целиком — никогда не «полузаписанным».
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Iterable, Iterator

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
TEMPLATES_DIR = ROOT / "templates"

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
ID_RE = re.compile(r"^([SECD])(\d{3,})$")
# Группа маркеров: [E001], [E001, C002], [S003; E004]
MARKER_GROUP_RE = re.compile(r"\[((?:[SECD]\d{3,})(?:\s*[,;]\s*[SECD]\d{3,})*)\]")
MARKER_ID_RE = re.compile(r"[SECD]\d{3,}")

PROJECT_FILES = ("sources.jsonl", "evidence.jsonl", "conclusions.jsonl")
PROJECT_DIRS = ("sources", "sources/screens", "outputs", "decisions", "interviews",
                "interviews/transcripts", "interviews/synthetic", "data", "log")


class SAError(Exception):
    """Ошибка, понятная пользователю (печатается без трассировки). code — код выхода (по умолчанию 1;
    3 — страница пустая/закрытая, источник не сохранён)."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def projects_dir() -> Path:
    """Папка проектов; переопределяется переменной SA_PROJECTS_DIR (тесты, песочница)."""
    env = os.environ.get("SA_PROJECTS_DIR")
    return Path(env).expanduser().resolve() if env else ROOT / "projects"


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def today() -> _dt.date:
    return _dt.date.today()


def parse_ts(value: str | None) -> _dt.datetime | None:
    if not value:
        return None
    try:
        ts = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.astimezone()
    return ts


def check_slug(slug: str) -> str:
    if not slug or not SLUG_RE.match(slug):
        raise SAError(
            f"Некорректное имя проекта «{slug}». Допустимы строчные латинские буквы, цифры и дефис "
            "(например: megafon-family-tariff)."
        )
    return slug


def project_dir(slug: str, must_exist: bool = True) -> Path:
    path = projects_dir() / check_slug(slug)
    if must_exist and not (path / "state.json").exists():
        raise SAError(
            f"Проект «{slug}» не найден в {projects_dir()}. Список: python3 scripts/sa.py list; "
            f"создать: python3 scripts/sa.py new {slug} --title \"…\""
        )
    return path


def list_projects() -> list[Path]:
    base = projects_dir()
    if not base.exists():
        return []
    return sorted(p for p in base.iterdir() if (p / "state.json").exists())


def active_project() -> Path | None:
    """Последний обновлённый проект (по updated_at в state.json, затем по mtime)."""
    best, best_key = None, None
    for p in list_projects():
        try:
            st = read_json(p / "state.json")
            ts = parse_ts(st.get("updated_at"))
            key = ts.timestamp() if ts else (p / "state.json").stat().st_mtime
        except Exception:  # noqa: BLE001 — битый проект не должен ломать выбор
            continue
        if best_key is None or key > best_key:
            best, best_key = p, key
    return best


def resolve_project(slug: str | None) -> Path:
    if slug:
        return project_dir(slug)
    p = active_project()
    if p is None:
        raise SAError("Проектов пока нет. Создайте: python3 scripts/sa.py new <slug> --title \"…\"")
    return p


# ---------- блокировка и атомарная запись ----------

@contextlib.contextmanager
def locked(path: Path) -> Iterator[None]:
    """Межпроцессная блокировка на время «прочитать-изменить-записать» (POSIX; на Windows — без неё)."""
    lock_path = path.with_name("." + path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl  # noqa: PLC0415
    except ImportError:  # Windows
        yield
        return
    with open(lock_path, "a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SAError(f"Файл не найден: {path}") from None
    except json.JSONDecodeError as exc:
        raise SAError(f"Повреждён JSON в {path}: {exc}") from None


def write_json(path: Path, data: dict) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    """Читает JSONL; битые строки пропускает (они не должны валить работу после сбоя)."""
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    atomic_write_text(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def append_jsonl(path: Path, row: dict) -> None:
    with locked(path):
        rows = read_jsonl(path)
        rows.append(row)
        write_jsonl(path, rows)


def next_id(rows: list[dict], prefix: str) -> str:
    top = 0
    for r in rows:
        m = ID_RE.match(str(r.get("id", "")))
        if m and m.group(1) == prefix:
            top = max(top, int(m.group(2)))
    return f"{prefix}{top + 1:03d}"


def by_id(rows: list[dict]) -> dict[str, dict]:
    return {r["id"]: r for r in rows if "id" in r}


def split_ids(value: str | Iterable[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        parts = re.split(r"[,;\s]+", value)
    else:
        parts = list(value)
    out = []
    for p in parts:
        p = p.strip().strip("[]").upper()
        if p and p not in out:
            out.append(p)
    return out


def markers_in(text: str) -> list[str]:
    ids: list[str] = []
    for group in MARKER_GROUP_RE.findall(text):
        for i in MARKER_ID_RE.findall(group):
            if i not in ids:
                ids.append(i)
    return ids


def truncate(value, limit: int = 200) -> str:
    s = str(value) if value is not None else ""
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def log_action(pdir: Path, action: str, target: str = "", ok: bool = True, detail: str = "",
               actor: str = "cli", session: str | None = None, key: str | None = None) -> bool:
    """Пишет событие в log/actions.jsonl. Если key уже есть — не дублирует (идемпотентность)."""
    path = pdir / "log" / "actions.jsonl"
    row = {"ts": now_iso(), "session": session or os.environ.get("SA_SESSION", "cli"), "actor": actor,
           "action": action, "target": truncate(target, 300), "ok": bool(ok),
           "key": key or f"{action}:{target}:{now_iso()}", "detail": truncate(detail, 500)}
    with locked(path):
        rows = read_jsonl(path)
        if key and any(r.get("key") == key for r in rows):
            return False
        rows.append(row)
        write_jsonl(path, rows)
    return True
