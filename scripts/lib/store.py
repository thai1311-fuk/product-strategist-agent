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
import time as _time
from pathlib import Path
from typing import Iterable, Iterator

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
TEMPLATES_DIR = ROOT / "templates"

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
ID_RE = re.compile(r"^([SECD])(\d{3,})$")
# Группа маркеров: [E001], [E001, C002], [S003; E004], [E146, отозвано] (единое определение; validate.py его копирует)
MARKER_GROUP_RE = re.compile(r"\[\s*[SECD]\d{3,}(?:\s*[,;]\s*(?:[SECD]\d{3,}|[^\[\]\n,;]{1,40}))*\s*\]")
MARKER_ID_RE = re.compile(r"\b[SECD]\d{3,}\b")

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


def now_precise() -> str:
    """Время с микросекундами — для сравнения «до/после» внутри одной секунды (волны пересмотра)."""
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="microseconds")


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


def load_dotenv(path: Path | None = None) -> list[str]:
    """Читает .env (только переменные SA_*; уже заданные в окружении не перезаписываются).

    Простой разбор KEY=VALUE без зависимостей: пустые строки и # игнорируются, значение может быть в кавычках,
    «export » в начале допустим. Остальные ключи (API-ключи и т.п.) намеренно игнорируются.
    Возвращает список установленных имён.
    """
    path = path or (ROOT / ".env")
    done: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return done
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if not sep or not re.fullmatch(r"SA_[A-Z0-9_]+", key):
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        elif " #" in val:
            val = val.split(" #", 1)[0].rstrip()
        if val and key not in os.environ:
            os.environ[key] = os.path.expanduser(val) if val.startswith("~") else val
            done.append(key)
    return done


load_dotenv()


def is_demo(pdir: Path) -> bool:
    try:
        return bool(read_json(pdir / "state.json").get("demo"))
    except Exception:  # noqa: BLE001
        return False


def set_demo(pdir: Path, on: bool) -> None:
    """Пометка демонстрационного проекта: "demo": true в state.json (updated_at не меняется)."""
    with locked(pdir / "state.json"):
        st = read_json(pdir / "state.json")
        if on:
            st["demo"] = True
        else:
            st.pop("demo", None)
        write_json(pdir / "state.json", st)


def active_project(include_demo: bool = False) -> Path | None:
    """Последний обновлённый проект (по updated_at в state.json, затем по mtime).

    Демонстрационные ("demo": true) и завершённые (stage_status=done) проекты пропускаются, чтобы расходы и журнал первого чата
    не попадали в бенчмарк/пример; include_demo=True — учитывать и их.
    """
    best, best_key = None, None
    for p in list_projects():
        try:
            st = read_json(p / "state.json")
            if (st.get("demo") or st.get("stage_status") == "done") and not include_demo:
                continue
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
        demos = [q.name for q in list_projects() if is_demo(q)]
        raise SAError("Рабочих проектов пока нет"
                      + (f" (есть демонстрационные: {', '.join(demos)} — укажите имя явно)" if demos else "")
                      + ". Создайте: python3 scripts/sa.py new <slug> --title \"…\"")
    return p


# ---------- блокировка и атомарная запись ----------

@contextlib.contextmanager
def locked(path: Path) -> Iterator[None]:
    """Межпроцессная блокировка на время «прочитать-изменить-записать»: fcntl.flock (POSIX) или
    msvcrt.locking на lock-файле (Windows)."""
    lock_path = path.with_name("." + path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl  # noqa: PLC0415
    except ImportError:
        fcntl = None
    if fcntl is not None:
        with open(lock_path, "a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return
    try:
        import msvcrt  # noqa: PLC0415
    except ImportError:  # неизвестная платформа — без блокировки
        yield
        return
    with open(lock_path, "a+b") as fh:
        fh.seek(0)
        if not fh.read(1):
            fh.write(b"\0")
            fh.flush()
        deadline = _time.monotonic() + 30
        while True:
            fh.seek(0)
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if _time.monotonic() > deadline:
                    raise SAError(f"Не удалось получить блокировку {lock_path} за 30 с") from None
                _time.sleep(0.05)
        try:
            yield
        finally:
            fh.seek(0)
            with contextlib.suppress(OSError):
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


def _file_mode() -> int:
    """Права новых файлов: 0666 с учётом umask (mkstemp создаёт 0600)."""
    old = os.umask(0)
    os.umask(old)
    return 0o666 & ~old


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            mode = (path.stat().st_mode & 0o777) if path.exists() else _file_mode()
            os.chmod(tmp, mode)
        except OSError:
            pass
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


class Rows(list):
    """Список строк JSONL + то, что не удалось разобрать: corrupt (сырые строки) и reserved_ids
    (ID из битых строк и из <файл>.corrupt — они не переиспользуются)."""

    corrupt: list
    reserved_ids: set


_ID_IN_LINE_RE = re.compile(r'"id"\s*:\s*"([SECD]\d{3,})"')


def corrupt_path(path: Path) -> Path:
    return path.with_name(path.name + ".corrupt")


def _scan(path: Path) -> tuple[Rows, list[str]]:
    rows = Rows()
    rows.corrupt, rows.reserved_ids = [], set()
    if not path.exists():
        return rows, []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            rows.corrupt.append(line)
            rows.reserved_ids.update(_ID_IN_LINE_RE.findall(line))
            continue
        if isinstance(obj, dict):
            rows.append(obj)
        else:
            rows.corrupt.append(line)
    cp = corrupt_path(path)
    saved = cp.read_text(encoding="utf-8", errors="replace").splitlines() if cp.exists() else []
    for line in saved:
        rows.reserved_ids.update(_ID_IN_LINE_RE.findall(line))
    return rows, saved


def read_jsonl(path: Path) -> list[dict]:
    """Читает JSONL; битые строки не валят работу, но и не теряются: см. write_jsonl (сохраняет их в
    <файл>.corrupt); их ID попадают в reserved_ids и не выдаются повторно (next_id)."""
    return _scan(path)[0]


def corrupt_lines(path: Path) -> list[str]:
    """Битые строки: ещё лежащие в файле и уже сохранённые в <файл>.corrupt."""
    rows, saved = _scan(path)
    return rows.corrupt + [x for x in saved if x not in rows.corrupt]


def corrupt_report(pdir: Path) -> list[tuple[str, int, str]]:
    """Для validate/doctor: [(имя файла, число битых строк, пример)] по всем jsonl проекта."""
    out = []
    for rel in (*PROJECT_FILES, "log/actions.jsonl", "log/costs.jsonl"):
        bad = corrupt_lines(pdir / rel)
        if bad:
            out.append((rel, len(bad), truncate(bad[0], 80)))
    return out


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    # битые строки старого файла сначала уходят в <файл>.corrupt, и только потом файл перезаписывается
    if path.exists():
        _rows, saved = _scan(path)
        fresh = [x for x in _rows.corrupt if x not in saved]
        if fresh:
            cp = corrupt_path(path)
            atomic_write_text(cp, "".join(x + "\n" for x in saved + fresh))
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
    for i in getattr(rows, "reserved_ids", ()):
        m = ID_RE.match(i)
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


def rel_path(value) -> str:
    """Путь/текст без абсолютного префикса проекта и домашней папки (имя пользователя не попадает в журнал)."""
    text = str(value) if value is not None else ""
    for base, repl in ((str(ROOT), "."), (str(Path.home()), "~")):
        if base and base not in ("/", "."):
            text = text.replace(base + os.sep, "" if repl == "." else repl + "/").replace(base, repl)
    return text.replace("\\", "/") if os.sep == "\\" else text


def log_action(pdir: Path, action: str, target: str = "", ok: bool = True, detail: str = "",
               actor: str = "cli", session: str | None = None, key: str | None = None) -> bool:
    """Пишет событие в log/actions.jsonl. Если key уже есть — не дублирует (идемпотентность)."""
    path = pdir / "log" / "actions.jsonl"
    if actor == "cli":  # кто вызвал CLI-функции: cli по умолчанию, mcp — из MCP-сервера
        actor = os.environ.get("SA_ACTOR", "cli")
    row = {"ts": now_iso(), "session": session or os.environ.get("SA_SESSION", "cli"), "actor": actor,
           "action": action, "target": truncate(rel_path(target), 300), "ok": bool(ok),
           "key": key or f"{action}:{target}:{now_iso()}", "detail": truncate(rel_path(detail), 500)}
    with locked(path):
        rows = read_jsonl(path)
        if key and any(r.get("key") == key for r in rows):
            return False
        rows.append(row)
        write_jsonl(path, rows)
    return True
