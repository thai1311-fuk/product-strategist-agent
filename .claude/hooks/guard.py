#!/usr/bin/env python3
"""PreToolUse-хук Claude Code: защита секретов, правил агента и сети.

Вход: JSON в stdin ({"tool_name", "tool_input", "cwd", ...}).
Решения:
  блок      → exit 2, причина в stderr (Claude видит её и не выполняет действие);
  спросить  → JSON {"hookSpecificOutput": {"permissionDecision": "ask", ...}} в stdout, exit 0;
  пропустить → exit 0 без вывода (обычные правила разрешений Claude Code).
Внутренняя ошибка или таймаут → fail-closed: ask (подтверждение человека), а не allow. Только стандартная библиотека.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or HERE.parents[2]).resolve()
SANITIZE_PATH = HERE.parents[2] / "scripts" / "lib" / "sanitize.py"

# Файлы правил агента и защиты: менять можно, но только с подтверждением человека.
PROTECTED_FILES = {"agents.md", "claude.md", ".claude/settings.json", ".claude/settings.local.json",
                   "config/allowed_sources.json", "scripts/lib/sanitize.py", ".gitignore", ".mcp.json",
                   "scripts/sa.py", "scripts/mcp_server.py"}
PROTECTED_DIRS = (".claude/hooks/", ".claude/skills/", ".claude/agents/", ".cursor/", "scripts/lib/", "config/")
ENV_OK = {".env.example", ".env.sample", ".env.template"}
SENSITIVE_READ = re.compile(r"(^|/)(\.env(\.[\w.-]+)?|[^/]*\.env|[^/]*\.(pem|key|p12|pfx)|id_(rsa|ed25519|ecdsa)[^/]*"
                            r"|\.netrc|\.git-credentials|\.aws/(credentials|config)|\.ssh/[^/]+)$", re.I)


def norm_path(path: str) -> str:
    """Windows-пути (C:\\Users\\x\\.env, .\\.env) → косые черты, чтобы проверки работали одинаково."""
    return path.replace("\\", "/") if isinstance(path, str) else path


def load_sanitize():
    # грузим строго по пути внутри проекта — без поиска по sys.path (защита от подмены модуля)
    spec = importlib.util.spec_from_file_location("sa_sanitize_guard", SANITIZE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Decision:
    def __init__(self) -> None:
        self.deny: list[str] = []
        self.ask: list[str] = []


def is_env_file(path: str) -> bool:
    name = Path(norm_path(path)).name.lower()
    if name in ENV_OK:
        return False
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


def rel_to_root(path: str, cwd: str) -> str | None:
    p = Path(norm_path(path)).expanduser()
    if not p.is_absolute():
        p = Path(cwd or ROOT) / p
    p = Path(os.path.realpath(p))
    try:
        return p.relative_to(ROOT).as_posix().lower()
    except ValueError:
        return None


def is_protected(path: str, cwd: str) -> bool:
    rel = rel_to_root(path, cwd)
    if rel is not None:
        return rel in PROTECTED_FILES or rel.startswith(PROTECTED_DIRS)
    # пользовательские настройки Claude Code вне проекта (~/.claude/settings.json, хуки)
    real = os.path.realpath(os.path.expanduser(norm_path(path)))
    home_claude = os.path.realpath(os.path.expanduser("~/.claude"))
    if real.startswith(home_claude + os.sep):
        sub = real[len(home_claude) + 1:].lower()
        return sub.startswith(("settings", "hooks")) or sub.endswith("claude.md")
    return False


# ---------------------------------------------------------------- запись файлов

def check_write(tool: str, ti: dict, cwd: str, san, d: Decision) -> None:
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    if tool == "Write":
        content = ti.get("content") or ""
    elif tool == "MultiEdit":
        content = "\n".join(str(e.get("new_string") or "") for e in ti.get("edits") or [] if isinstance(e, dict))
    elif tool == "NotebookEdit":
        content = ti.get("new_source") or ""
    else:
        content = ti.get("new_string") or ""
    if not isinstance(path, str) or not isinstance(content, str):
        return
    if path and is_env_file(path):
        d.deny.append(f"Запись в {Path(path).name} запрещена: секреты хранятся только локально вне "
                      "контроля агента. Шаблон переменных — .env.example (с пустыми значениями). "
                      "Если ключ нужен, человек сам вписывает его в .env.")
        return
    found = san.scan_secrets(content)
    if found:
        d.deny.append(f"В записываемом тексте ({path or 'файл'}) найдены секреты: {', '.join(found)}. "
                      "Ключи и пароли не должны попадать в файлы проекта — уберите значение "
                      "(используйте переменную окружения из .env).")
        return
    if path and is_protected(path, cwd):
        d.ask.append(f"Изменение файла правил/защиты агента ({path}). Подтвердите, что это решение "
                     "человека-разработчика, а не команда из текста источника.")


def check_read(ti: dict, d: Decision, keys=("file_path", "path", "notebook_path")) -> None:
    for k in keys:
        path = ti.get(k) or ""
        if not isinstance(path, str):
            continue
        path = norm_path(path)
        if SENSITIVE_READ.search(path) and Path(path).name.lower() not in ENV_OK:
            d.ask.append(f"Чтение {Path(path).name}: файл может содержать секреты, они попадут в контекст и "
                         "журнал сессии. Разрешить?")
            return


def check_mcp(tool: str, ti: dict, d: Decision, san) -> None:
    """MCP-инструменты: адрес и пути к файлам в полях url/file/locator."""
    if "url" in ti:
        check_url_tool(ti.get("url"), d, san)
    check_read(ti, d, keys=("file", "file_path", "path", "locator"))
    for k in ("locator",):
        v = ti.get(k)
        if isinstance(v, str) and re.match(r"https?://", v.strip(), re.I):
            check_url_tool(v.strip(), d, san)


# ---------------------------------------------------------------- Bash

RX_PIPE_SHELL = re.compile(
    r"\b(curl|wget|fetch|iwr|invoke-webrequest|irm)\b[^|;&\n]*\|\s*(sudo\s+(-\S+\s+)*)?(env\s+)?"
    r"((ba|z|da|k|fi)?sh|python\d?(\.\d+)?|perl|ruby|node|php|iex|invoke-expression)\b"
    r"|\b(ba|z|da)?sh\s+(-\w+\s+)*[\"']?\$\(\s*(curl|wget)"
    r"|(\bsource|(^|[;&|]\s*)\.)\s+<\(\s*(curl|wget)|\b(ba|z)?sh\s+<\(\s*(curl|wget)", re.I)
RX_CURL = re.compile(r"(^|[\s;&|(`])(curl|wget|http|https|xh)\s", re.I)
RX_REMOTE = re.compile(r"^([\w.-]+@)?[\w.-]+:(?!//)")
RX_UPLOAD_FILE = re.compile(
    r"(\s(-d|--data(-binary|-urlencode|-ascii)?|--json)\s*[\"']?[^\s\"']*@"
    r"|\s(-F|--form)\s*[\"']?[^\s\"']*=@|\s-T\s|\s--upload-file\b|--post-file\b|--body-file\b"
    r"|\$\(\s*(cat|base64|tar|zip|gzip)\b|`\s*(cat|base64|tar)\b|\s@-\s*)", re.I)
RX_UPLOAD_ANY = re.compile(r"\s(-d|--data[\w-]*|-F|--form|--json|--post-data|-X\s*(POST|PUT|PATCH))\b", re.I)
RX_NET_TOOLS = re.compile(r"(^|[\s;&|(])(nc|ncat|netcat|socat|telnet|sftp|ftp|tftp)\s", re.I)
RX_DEVTCP = re.compile(r"/dev/(tcp|udp)/")
RX_URL = re.compile(r"https?://[^\s'\"<>|;)`]+", re.I)
RX_PROTECTED_IN_CMD = re.compile(
    r"(agents\.md|claude\.md|\.claude/(settings(\.local)?\.json|hooks|skills|agents)|config/|\.mcp\.json"
    r"|scripts/lib/|(?<!python3 )(?<!python )scripts/sa\.py|scripts/mcp_server\.py|\.cursor/|\.gitignore|~/\.claude|\$home/\.claude)", re.I)
RX_ALLOW_API = re.compile(r"\b(allow_domain|allow_domain_check)\b|allowed_sources", re.I)
RX_INTERP = re.compile(r"\b(python\d?(?:\.\d+)?|node|nodejs|perl|ruby|php|deno|bun)\b", re.I)
RX_INLINE = re.compile(r"\s-[A-Za-z]*[cepE]\b|\s--eval\b|<<|\s-\s|\|\s*(python|node)", re.I)
RX_NET_CODE = re.compile(
    r"urllib|urlopen|\brequests\b|http\.client|\bhttpx\b|aiohttp|\bsocket\b|XMLHttpRequest|\bfetch\s*\(|"
    r"require\(\s*[\"'](?:node:)?(?:https?|net|tls|http2)[\"']|node:https?|\bimport\s+(?:https?|net)\b|"
    r"\bhttps?\.(?:get|request)\b|ftplib|smtplib|\bpycurl\b|\bWebSocket\b", re.I)
RX_WRITE_OP = re.compile(
    r"(?<![0-9&>-])>{1,2}(?![&>])|\btee\b|\bsed\b[^|;&]*\s-i|\bperl\b[^|;&]*\s-[a-z]*i|\b(mv|cp|rm|truncate|chmod|"
    r"ln|dd|install|unlink|rsync)\b|open\(|write_text|\bgit\s+(checkout|restore|rm|mv|reset|stash)\b", re.I)
RX_SA_ALLOW = re.compile(r"\bsa\.py[\"']?\s+(?:--?\S+\s+)*allow\b", re.I)
RX_SA_GATE = re.compile(r"\bsa\.py[\"']?\s+(?:--?\S+\s+)*gate\b", re.I)
RX_SA_BUDGET = re.compile(r"\bsa\.py[\"']?\s+(?:--?\S+\s+)*budget\b", re.I)
RX_SA_FORCE = re.compile(r"\bsa\.py[\"']?\s+(?:--?\S+\s+)*stage\b[^;&|\n]*--force\b", re.I)
RX_ENV_REF = re.compile(r"(^|[\s/'\"=<])\.env(\.[\w-]+)?(?=$|[\s'\";|&)>])")


def _segments(cmd: str) -> list[list[str]]:
    segs = []
    for part in re.split(r"&&|\|\||;|\||\n", cmd):
        try:
            toks = shlex.split(part, posix=True)
        except ValueError:
            toks = part.split()
        if toks:
            segs.append(toks)
    return segs


def _scan_file(path: Path, san) -> list[str]:
    try:
        if not path.is_file() or path.stat().st_size > 2_000_000:
            return []
        data = path.read_bytes()
    except OSError:
        return []
    if b"\x00" in data[:8192]:
        return []
    return san.scan_secrets(data.decode("utf-8", errors="replace"))


def check_git_add(toks: list[str], cwd: str, san, d: Decision, deadline: float) -> None:
    # git [-C dir] add [opts] [pathspec…]
    i, gdir = 1, cwd
    while i < len(toks) and toks[i] != "add":
        if toks[i] == "-C" and i + 1 < len(toks):
            gdir = str(Path(cwd) / toks[i + 1])
            i += 1
        elif not toks[i].startswith("-"):
            return  # другая подкоманда git
        i += 1
    if i >= len(toks):
        return
    args = toks[i + 1:]
    force = any(a in ("-f", "--force") or (re.fullmatch(r"-[a-zA-Z]*f[a-zA-Z]*", a) is not None) for a in args)
    specs = [a for a in args if not a.startswith("-")]
    for s in specs:
        if is_env_file(s):
            d.deny.append(f"git add {s}: файл с секретами нельзя добавлять в репозиторий (он в .gitignore).")
            return
    files: list[Path] = []
    try:
        cmd = ["git", "-C", gdir, "ls-files", "-z", "--others", "--modified", "--exclude-standard", "--"]
        res = subprocess.run(cmd + (specs or ["."]), capture_output=True, timeout=1.0)
        if res.returncode == 0:
            files = [Path(gdir) / f for f in res.stdout.decode("utf-8", "replace").split("\0") if f]
    except (OSError, subprocess.SubprocessError):
        files = []
    if force or not files:
        files += [Path(gdir) / s for s in specs if (Path(gdir) / s).is_file()]
    bad = []
    for f in files[:500]:
        if time.monotonic() > deadline:
            break
        if is_env_file(f.name):
            bad.append(f"{f.name} (файл .env)")
            continue
        found = _scan_file(f, san)
        if found:
            bad.append(f"{f.name}: {', '.join(found)}")
    if bad:
        d.deny.append("git add остановлен — в файлах найдены секреты: " + "; ".join(bad[:5]) +
                      ". Уберите значения из файлов (ключи держите в .env) и повторите.")


_DANGEROUS_RM = {"/", "/*", "~", "~/", "~/*", "$home", "$home/", "${home}", "${home}/", "$home/*", "..", "../", "/users",
                 "/home", "/etc", "/usr", "/var", "/bin", "/system"}
_DYN = re.compile(r"[$`*?\[]")
_WRITERS = {"tee", "cp", "mv", "ln", "install", "dd", "truncate", "touch", "chmod", "rsync"}


def _expand_assignments(cmd: str) -> str:
    """Подставляет значения простых присваиваний VAR=value из самой команды (f=AGENTS; … $f.md)."""
    vals = {m.group(1): m.group(2).strip("'\"") for m in re.finditer(r"(?:^|[\s;&|])([A-Za-z_]\w*)=([^\s;&|]*)", cmd)}
    if not vals:
        return cmd
    out = cmd
    for _ in range(2):
        out = re.sub(r"\$\{(\w+)\}|\$(\w+)", lambda m: vals.get(m.group(1) or m.group(2), m.group(0)), out)
    return out


def _secret_parts(cmd: str, san) -> list[str]:
    """Секрет, разрезанный на части (кавычки, переменные, конкатенация): склеиваем и сканируем."""
    variants = [re.sub(r"[\'\"\\+]|\$\{?\w+\}?", "", cmd)]
    literals = [a or b for a, b in re.findall(r"\"([^\"]*)\"|'([^']*)'", cmd)]
    values = [m.group(1).strip("'\"") for m in re.finditer(r"\b[A-Za-z_]\w*=([^\s;&|]+)", cmd)]
    for parts in (literals, values, literals + values):
        if len(parts) > 1:
            variants.append(" " + "".join(parts))
    found: list[str] = []
    for v in variants:
        for k in san.scan_secrets(v):
            if k not in found:
                found.append(k)
    return found


def _write_targets(cmd: str) -> list[str]:
    out = [m.group(1).strip("'\"") for m in re.finditer(r"(?<![0-9&>-])>{1,2}\s*([^\s;&|<>]+)", cmd)]
    for toks in _segments(cmd):
        names = [Path(t).name for t in toks]
        if toks and (names[0] in _WRITERS or (names[0] == "sed" and any(t.startswith("-i") for t in toks))):
            out += [t for t in toks[1:] if not t.startswith("-")]
    return out


def _dynamic_write_target(cmd: str) -> bool:
    return any(_DYN.search(t) for t in _write_targets(cmd))


def check_bash(ti: dict, cwd: str, san, d: Decision, deadline: float) -> None:
    cmd = ti.get("command") or ""
    if not isinstance(cmd, str) or not cmd.strip():
        return
    cmd = cmd.replace("\\\n", " ")
    # rm -rf / и подобное: блок
    for toks in _segments(cmd):
        names = [Path(t).name for t in toks]
        if "rm" not in names:
            continue
        args = toks[names.index("rm") + 1:]
        recursive = any(a == "--recursive" or (a.startswith("-") and not a.startswith("--") and re.search("[rR]", a))
                        for a in args)
        targets = [a for a in args if not a.startswith("-")]
        home = os.path.expanduser("~")
        bad = "--no-preserve-root" in args
        for t in targets:
            tl = t.lower().rstrip("/") + ("/" if t.endswith("/") and len(t) == 1 else "")
            if t.lower() in _DANGEROUS_RM or tl in _DANGEROUS_RM:
                bad = True
            else:
                try:
                    rp = os.path.realpath(os.path.expanduser(t))
                    if rp in ("/", home, str(ROOT)) or Path(rp) in ROOT.parents:
                        bad = True
                except (OSError, ValueError):
                    pass
        if bad and (recursive or "--no-preserve-root" in args):
            d.deny.append("Рекурсивное удаление корня, домашней папки или проекта (rm -rf /) запрещено.")
    # сетевой код в одной строке интерпретатора: python -c "urllib…", node -e "https…", heredoc
    if RX_INTERP.search(cmd) and RX_INLINE.search(" " + cmd) and RX_NET_CODE.search(cmd):
        d.deny.append("Сетевой вызов из встроенного кода (python -c/node -e/heredoc с urllib, requests, http, socket) "
                      "запрещён: загрузки идут только через sa.py source fetch (allowlist), выгрузка наружу не допускается.")
    if RX_INTERP.search(cmd) and RX_ALLOW_API.search(cmd) and "sa.py" not in cmd:
        d.ask.append("Код обращается к allowlist (allow_domain/allowed_sources) мимо sa.py: это изменение правил агента.")
    if RX_PIPE_SHELL.search(cmd):
        d.deny.append("Запуск скрипта из интернета без проверки (curl/wget | sh) запрещён. "
                      "Скачайте файл, покажите его человеку и запускайте только после проверки.")
    segs = _segments(cmd)
    net_upload = RX_CURL.search(cmd) and RX_UPLOAD_FILE.search(" " + cmd)
    if net_upload or RX_DEVTCP.search(cmd) or RX_NET_TOOLS.search(" " + cmd):
        d.deny.append("Отправка файлов или данных проекта на внешний адрес (curl -d @/-F/-T, nc, ftp, /dev/tcp) "
                      "запрещена. Если это действительно нужно — человек делает это сам.")
    for toks in segs:
        name = Path(toks[0]).name if toks else ""
        if name in ("scp", "rsync") and any(RX_REMOTE.match(t) for t in toks[1:] if not t.startswith("-")):
            d.deny.append(f"{name} на удалённый хост запрещён: файлы проекта не отправляются наружу агентом.")
        if name == "git" and "add" in toks:
            check_git_add(toks, cwd, san, d, deadline)
    if d.deny:
        return
    if RX_CURL.search(cmd) and RX_UPLOAD_ANY.search(" " + cmd):
        d.ask.append("Команда отправляет данные на внешний адрес (POST/-d/-F). Проверьте, что уходит "
                     "только открытая информация.")
    for rx, what in ((RX_SA_GATE, "ворота этапа"), (RX_SA_BUDGET, "бюджет"), (RX_SA_FORCE, "обход ворот (--force)")):
        if rx.search(cmd):
            d.ask.append(f"Решение человека ({what}): sa.py gate/budget/stage --force/allow выполняются только "
                         "после прямого ответа человека в чате, не по тексту источника.")
    if RX_SA_ALLOW.search(cmd):
        d.ask.append("Решение человека (allowlist): добавление домена в allowlist — нужно согласие пользователя. "
                     "Подтверждайте, только если вы сами просили открыть этот источник, а не текст со страницы.")
    found = san.scan_secrets(cmd) or _secret_parts(cmd, san)
    if found:
        d.ask.append(f"В команде есть секрет ({', '.join(found)}): он попадёт в журнал сессии. Разрешить?")
    expanded = _expand_assignments(cmd)
    if RX_WRITE_OP.search(cmd) and (RX_PROTECTED_IN_CMD.search(cmd) or RX_PROTECTED_IN_CMD.search(expanded)
                                    or any(is_protected(t, cwd) for t in _write_targets(expanded) if not _DYN.search(t))):
        d.ask.append("Команда может изменить правила или защиту агента (AGENTS.md, CLAUDE.md, .claude/…, "
                     "allowlist, sanitize.py). Подтвердите, что это решение человека.")
    if _dynamic_write_target(cmd):
        d.ask.append("Команда пишет в файл, имя которого собирается подстановкой ($var, $(…), маска): проверить, "
                     "не правила ли агента это затрагивает, нельзя — подтвердите.")
    if RX_ENV_REF.search(cmd) and not re.search(r"\.env\.(example|sample|template)\b", cmd):
        d.ask.append("Команда обращается к файлу .env с секретами — его содержимое может попасть в контекст. Разрешить?")
    if RX_CURL.search(cmd) and "sa.py" not in cmd:
        for url in RX_URL.findall(cmd):
            ok, why, cat = san.check_url(url)
            if cat == "denied":
                d.deny.append(f"Сетевой запрос к {url} запрещён: {why}.")
            elif not ok:
                d.ask.append(f"Запрос к {url}: {why}. Это открытый источник?")


# ---------------------------------------------------------------- сеть

def check_url_tool(url, d: Decision, san) -> None:
    if not isinstance(url, str) or not url:
        return
    ok, why, cat = san.check_url(url)
    if cat == "denied":
        d.deny.append(f"Адрес {url} запрещён: {why}. Используются только открытые источники; "
                      "внутренние адреса и ссылки с токенами недопустимы.")
    elif not ok:
        d.ask.append(f"{why}. Это открытый источник? Если да — можно разрешить разово или добавить домен "
                     "в allow.user_added (docs/SECURITY.md).")


# ---------------------------------------------------------------- main

def decide(raw: str) -> tuple[int, str, str]:
    start = time.monotonic()
    data = json.loads(raw)
    if not isinstance(data, dict):
        return 0, "", ""
    tool = data.get("tool_name")
    ti = data.get("tool_input")
    if not isinstance(tool, str) or not isinstance(ti, dict):
        return 0, "", ""
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else str(ROOT)
    san = load_sanitize()
    d = Decision()
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        check_write(tool, ti, cwd, san, d)
    elif tool == "Read":
        check_read(ti, d)
    elif tool == "Bash":
        check_bash(ti, cwd, san, d, start + 0.15)
    elif tool == "WebFetch":
        check_url_tool(ti.get("url"), d, san)
    elif tool.startswith("mcp__"):
        check_mcp(tool, ti, d, san)
    if d.deny:
        return 2, "", "Заблокировано защитой проекта (guard.py): " + " ".join(dict.fromkeys(d.deny))
    if d.ask:
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                      "permissionDecisionReason": "Защита проекта: " + " ".join(dict.fromkeys(d.ask))}}
        return 0, json.dumps(out, ensure_ascii=False), ""
    return 0, "", ""


TIME_LIMIT = 7  # секунд; settings.json даёт хуку 10 — отвечаем «ask» раньше, чем хук оборвут


class _Timeout(Exception):
    pass


def _on_alarm(_sig, _frm):
    raise _Timeout()


RISKY_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "WebFetch", "Read")


def fail_closed(why: str, raw: str = "") -> tuple[int, str, str]:
    m = re.search(r'"tool_name"\s*:\s*"([^"]+)"', raw or "")
    tool = m.group(1) if m else ""
    if tool not in RISKY_TOOLS and not tool.startswith("mcp__"):
        return 0, "", ""  # ввод без распознаваемого инструмента: нечего защищать
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                  "permissionDecisionReason": f"Защита проекта: хук не смог проверить действие ({why}). "
                                                              "Подтвердите вручную, только если это ваша просьба."}}
    return 0, json.dumps(out, ensure_ascii=False), ""


def main() -> None:
    raw = ""
    try:
        raw = sys.stdin.read()
        if hasattr(signal, "SIGALRM"):
            signal.signal(signal.SIGALRM, _on_alarm)
            signal.alarm(TIME_LIMIT)
        code, out, err = decide(raw)
    except _Timeout:
        code, out, err = fail_closed("таймаут", raw)
    except Exception as exc:  # noqa: BLE001 — fail-closed: при ошибке спрашиваем человека, а не разрешаем
        code, out, err = fail_closed(type(exc).__name__, raw)
    finally:
        if hasattr(signal, "alarm"):
            signal.alarm(0)
    if out:
        sys.stdout.write(out + "\n")
    if err:
        sys.stderr.write(err + "\n")
    sys.exit(code)


if __name__ == "__main__":
    main()
