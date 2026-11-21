#!/usr/bin/env python3
"""Сборка docs/DESCRIPTION.pdf: текст docs/DESCRIPTION.md + скриншоты отчёта и терминала (docs/img/).

Запуск из корня репозитория:  python3 scripts/build_description_pdf.py [slug]   (по умолчанию family-connect)
Нужен Google Chrome / Chromium (headless). Только стандартная библиотека Python.
"""
from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from lib.report import Markdown  # noqa: E402

GH = "https://github.com/thai1311-fuk/product-strategist-agent/blob/main/"
CHROME_CANDIDATES = [
    os.environ.get("CHROME", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    shutil.which("google-chrome") or "", shutil.which("chromium") or "", shutil.which("chromium-browser") or "",
]

REPORT_SECTIONS = [  # (файл, id раздела в report.html, подпись)
    ("01-decision.png", None, "Отчёт: «Решение на одной странице» — выбранный вариант, трудность, отказы, North Star, главный риск; маркеры [E…]/[C…] ведут к доказательствам."),
    ("08-progress.png", "sec-progress", "Прогресс по этапам: все этапы и ворота."),
    ("02-brief-answer.png", "sec-brief-answer", "Документ по формату брифа МегаФона: пять частей, расчёты рядом с решениями."),
    ("04-situation.png", "sec-situation", "Картина ситуации: что известно, откуда, чего не знаем; блоки «Пересмотрено»."),
    ("03-options.png", "sec-options", "Варианты: блоки «Пересмотрено» после возражений critic и после новых данных; диагноз с маркерами."),
    ("06-evidence.png", "sec-evidence", "Реестр доказательств: тип (факт / оценка / допущение / прогноз / синтетика), цитата, источник, статус."),
    ("07-decisions.png", "sec-decisions", "Решение D001: метрики, условия пересмотра, обязательства, история пересмотров."),
    ("05-costs.png", "sec-costs", "Расходы по этапам и моделям, с пометкой «оценка»."),
]
TERMINAL = [  # (файл, команда sa.py, высота окна, подпись)
    ("09-resume.png", ["resume", "{slug}"], 720, "Продолжение после перерыва: новый чат, `sa.py resume` — этап, ворота, открытые вопросы, ближайшие пересмотры из файлов."),
    ("10-impact.png", ["impact", "{slug}", "E048"], 420, "Новые данные → `sa.py impact`: какие выводы, документы и решения затронуты."),
    ("11-cost.png", ["cost", "report", "{slug}"], 640, "`sa.py cost report`: расходы по этапам, моделям и способу учёта, время работы."),
    ("12-validate.png", ["validate", "{slug}"], 220, "`sa.py validate`: проверка цитирования — 0 ошибок."),
]


def chrome() -> str:
    for c in CHROME_CANDIDATES:
        if c and Path(c).exists():
            return c
    sys.exit("Не найден Chrome/Chromium: задайте путь в переменной CHROME.")


def shot(ch: str, url: str, out: Path, w: int, h: int) -> None:
    subprocess.run([ch, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={w},{h}",
                    f"--screenshot={out}", url], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=120)


def main() -> int:
    slug = sys.argv[1] if len(sys.argv) > 1 else "family-connect"
    ch = chrome()
    img = ROOT / "docs" / "img"
    img.mkdir(parents=True, exist_ok=True)
    report = ROOT / "projects" / slug / "outputs" / "report.html"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "sa.py"), "report", slug], check=True,
                   stdout=subprocess.DEVNULL, cwd=ROOT)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = report.read_text(encoding="utf-8")
        for name, sid, _ in REPORT_SECTIONS:
            if sid is None:
                shot(ch, report.as_uri(), img / name, 1280, 1600)
                continue
            css = (f"<style>section{{display:none!important}} #{sid}{{display:block!important}}"
                   " .toc,.no-print{display:none!important}</style>")
            f = tmp / f"{sid}.html"
            f.write_text(src.replace("</head>", css + "</head>", 1), encoding="utf-8")
            shot(ch, f.as_uri(), img / name, 1280, 1700)
        for name, args, h, _ in TERMINAL:
            args = [a.format(slug=slug) for a in args]
            r = subprocess.run([sys.executable, str(ROOT / "scripts" / "sa.py"), *args], capture_output=True,
                               text=True, cwd=ROOT)
            out = (r.stdout + r.stderr).strip()
            if args[0] == "cost":
                out = "\n".join(out.splitlines()[:22])
            if args[0] == "validate":
                out = "\n".join(l for l in out.splitlines() if not l.startswith("  !"))
            page = ("<!doctype html><meta charset='utf-8'><style>body{margin:0;background:#1e1e1e;"
                    "font:15px/1.45 Menlo,monospace;color:#e6e6e6}.bar{background:#333;padding:8px 14px;color:#aaa;"
                    "font:13px -apple-system,sans-serif}pre{margin:0;padding:16px 18px;white-space:pre-wrap}"
                    ".p{color:#7ec97e}</style><div class='bar'>Терминал — product-strategist-agent</div>"
                    f"<pre><span class='p'>$ python3 scripts/sa.py {html.escape(' '.join(args))}</span>\n"
                    f"{html.escape(out)}</pre>")
            f = tmp / f"{name}.html"
            f.write_text(page, encoding="utf-8")
            shot(ch, f.as_uri(), img / name, 1100, h)

        md = (ROOT / "docs" / "DESCRIPTION.md").read_text(encoding="utf-8")
        md = re.split(r"\n## Приложение: какие скриншоты вставить при сборке PDF", md)[0]

        def fix(m: re.Match) -> str:
            text, href = m.group(1), m.group(2)
            if href.startswith(("http", "#", "mailto:")):
                return m.group(0)
            parts: list[str] = []
            for seg in ("docs/" + href).split("/"):
                if seg == "..":
                    if parts:
                        parts.pop()
                elif seg and seg != ".":
                    parts.append(seg)
            return f"[{text}]({GH}{'/'.join(parts)})"

        md = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", fix, md)
        body = Markdown(None, heading_shift=0).render(md)
        caps = [(n, c) for n, _, c in REPORT_SECTIONS] + [(n, c) for n, _, _, c in TERMINAL]
        figs = "".join(f'<figure><img src="{(img / n).as_uri()}"><figcaption>{Markdown(None).inline(c)}</figcaption></figure>'
                       for n, c in caps if (img / n).exists())
        doc = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Стратег Фабрики продуктов — описание работы</title>
<style>
@page {{ size: A4; margin: 16mm 14mm; }}
body {{ font: 10.5pt/1.45 -apple-system, "Helvetica Neue", Arial, sans-serif; color: #111; }}
h1 {{ font-size: 20pt; margin: 0 0 8pt; }} h2 {{ font-size: 14pt; margin: 18pt 0 6pt; border-bottom: 1px solid #ccc; padding-bottom: 3pt; }}
h3 {{ font-size: 11.5pt; margin: 12pt 0 4pt; }}
table {{ border-collapse: collapse; width: 100%; font-size: 9pt; margin: 6pt 0; }}
th, td {{ border: 1px solid #ccc; padding: 3pt 5pt; vertical-align: top; text-align: left; }} th {{ background: #f2f2f2; }}
code {{ font: 9pt Menlo, monospace; background: #f4f4f4; padding: 0 2pt; }} pre {{ background: #f4f4f4; padding: 6pt; font-size: 8.5pt; white-space: pre-wrap; }}
blockquote {{ border-left: 3px solid #bbb; margin: 6pt 0; padding: 2pt 8pt; color: #333; }}
a {{ color: #1a55b0; text-decoration: none; }}
figure {{ page-break-inside: avoid; margin: 10pt 0 14pt; }} figure img {{ width: 100%; border: 1px solid #999; }}
figcaption {{ font-size: 9pt; color: #333; margin-top: 3pt; }}
.shots h2 {{ page-break-before: always; }}
</style></head><body>{body}<div class="shots"><h2>Скриншоты</h2>{figs}</div></body></html>"""
        page = tmp / "description.html"
        page.write_text(doc, encoding="utf-8")
        pdf = ROOT / "docs" / "DESCRIPTION.pdf"
        subprocess.run([ch, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={pdf}",
                        page.as_uri()], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
    print(f"PDF: {pdf.relative_to(ROOT)}; скриншоты: docs/img/ ({len(caps)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
