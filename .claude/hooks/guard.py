#!/usr/bin/env python3
"""PreToolUse-хук Claude Code: защита секретов, правил агента и сети.

Вход: JSON в stdin ({"tool_name", "tool_input", "cwd", ...}).
Решения:
  блок      → exit 2, причина в stderr (Claude видит её и не выполняет действие);
  спросить  → JSON {"hookSpecificOutput": {"permissionDecision": "ask", ...}} в stdout, exit 0;
  пропустить → exit 0 без вывода (обычные правила разрешений Claude Code).
Любая внутренняя ошибка → exit 0 (хук никогда не ломает работу). Только стандартная библиотека.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or HERE.parents[2]).resolve()
SANITIZE_PATH = HERE.parents[2] / "scripts" / "lib" / "sanitize.py"

# Файлы правил агента и защиты: менять можно, но только с подтверждением человека.
PROTECTED_FILES = {"agents.md", "claude.md", ".claude/settings.json", ".claude/settings.local.json",
                   "config/allowed_sources.json", "scripts/lib/sanitize.py", ".gitignore"}
PROTECTED_DIRS = (".claude/hooks/", ".cursor/rules/")
ENV_OK = {".env.example", ".env.sample", ".env.template"}
SENSITIVE_READ = re.compile(r"(^|/)(\.env(\.[\w.-]+)?|[^/]*\.env|[^/]*\.(pem|key|p12|pfx)|id_(rsa|ed25519|ecdsa)[^/]*)$", re.I)


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
    name = Path(path).name.lower()
    if name in ENV_OK:
        return False
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


def rel_to_root(path: str, cwd: str) -> str | None:
    p = Path(path).expanduser()
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
    real = os.path.realpath(os.path.expanduser(path))
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


def check_read(ti: dict, d: Decision) -> None:
    path = ti.get("file_path") or ""
    if isinstance(path, str) and SENSITIVE_READ.search(path) and Path(path).name.lower() not in ENV_OK:
        d.ask.append(f"Чтение {Path(path).name}: файл может содержать секреты, они попадут в контекст и "
                     "журнал сессии. Разрешить?")


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
    r"(agents\.md|claude\.md|\.claude/settings(\.local)?\.json|\.claude/hooks|config/allowed_sources\.json"
    r"|scripts/lib/sanitize\.py|\.cursor/rules|\.gitignore|~/\.claude|\$home/\.claude)", re.I)
RX_WRITE_OP = re.compile(
    r"(?<![0-9&>-])>{1,2}(?![&>])|\btee\b|\bsed\b[^|;&]*\s-i|\bperl\b[^|;&]*\s-[a-z]*i|\b(mv|cp|rm|truncate|chmod|"
    r"ln|dd|install|unlink|rsync)\b|open\(|write_text|\bgit\s+(checkout|restore|rm|mv|reset|stash)\b", re.I)
RX_SA_ALLOW = re.compile(r"\bsa\.py[\"']?\s+(?:--?\S+\s+)*allow\b", re.I)
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


def check_bash(ti: dict, cwd: str, san, d: Decision, deadline: float) -> None:
    cmd = ti.get("command") or ""
    if not isinstance(cmd, str) or not cmd.strip():
        return
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
    if RX_SA_ALLOW.search(cmd):
        d.ask.append("Изменение правил агента: добавление домена в allowlist — нужно согласие пользователя. "
                     "Подтверждайте, только если вы сами просили открыть этот источник, а не текст со страницы.")
    found = san.scan_secrets(cmd)
    if found:
        d.ask.append(f"В команде есть секрет ({', '.join(found)}): он попадёт в журнал сессии. Разрешить?")
    if RX_PROTECTED_IN_CMD.search(cmd) and RX_WRITE_OP.search(cmd):
        d.ask.append("Команда может изменить правила или защиту агента (AGENTS.md, CLAUDE.md, .claude/…, "
                     "allowlist, sanitize.py). Подтвердите, что это решение человека.")
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
    elif tool == "WebFetch" or (tool.startswith("mcp__") and "url" in ti):
        check_url_tool(ti.get("url"), d, san)
    if d.deny:
        return 2, "", "Заблокировано защитой проекта (guard.py): " + " ".join(dict.fromkeys(d.deny))
    if d.ask:
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                      "permissionDecisionReason": "Защита проекта: " + " ".join(dict.fromkeys(d.ask))}}
        return 0, json.dumps(out, ensure_ascii=False), ""
    return 0, "", ""


def main() -> None:
    try:
        code, out, err = decide(sys.stdin.read())
    except Exception:  # noqa: BLE001 — хук не должен ломать работу при неожиданном вводе
        code, out, err = 0, "", ""
    if out:
        sys.stdout.write(out + "\n")
    if err:
        sys.stderr.write(err + "\n")
    sys.exit(code)


if __name__ == "__main__":
    main()
