"""Frontmatter субагентов и скиллов должен разбираться средами (Claude Code и др.).

Ловит ошибку, из-за которой субагент critic не регистрировался: значение,
начинающееся с кавычки и продолжающееся после закрывающей кавычки
(`description: "Красная команда" для …`), — невалидный YAML.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = sorted((ROOT / ".claude" / "agents").glob("*.md")) + sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md"))


def frontmatter(text: str) -> dict:
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        raise AssertionError("нет frontmatter между строками ---")
    out = {}
    for line in m.group(1).splitlines():
        if not line.strip():
            continue
        key, sep, val = line.partition(":")
        if not sep:
            raise AssertionError(f"строка без ключа: {line!r}")
        out[key.strip()] = val.strip()
    return out


class TestFrontmatter(unittest.TestCase):
    def test_files_exist(self):
        self.assertTrue(FILES)

    def test_required_keys_and_valid_scalars(self):
        for f in FILES:
            with self.subTest(file=str(f.relative_to(ROOT))):
                fm = frontmatter(f.read_text(encoding="utf-8"))
                self.assertIn("name", fm)
                self.assertIn("description", fm)
                for key, val in fm.items():
                    if val[:1] in "\"'":
                        q = val[0]
                        self.assertTrue(val.endswith(q) and len(val) > 1,
                                        f"{key}: значение начинается с кавычки, но не заключено в неё целиком — невалидный YAML")
                    self.assertFalse(val.startswith(("*", "&", "!", "%", "@", "`")),
                                     f"{key}: значение начинается со служебного символа YAML")

    def test_agent_name_matches_file(self):
        for f in sorted((ROOT / ".claude" / "agents").glob("*.md")):
            with self.subTest(file=f.name):
                self.assertEqual(frontmatter(f.read_text(encoding="utf-8"))["name"], f.stem)


if __name__ == "__main__":
    unittest.main()


class TestImageSources(unittest.TestCase):
    """Скриншоты как источники (аудит №58/59): копия в sources/screens, факт — только с областью кадра."""

    def test_image_source_and_visual_fact(self):
        import os, subprocess, sys, tempfile, json
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, SA_PROJECTS_DIR=d, SA_ACTOR="test")
            sa = [sys.executable, str(ROOT / "scripts" / "sa.py")]
            run = lambda *a: subprocess.run(sa + list(a), env=env, capture_output=True, text=True)
            self.assertEqual(run("new", "img", "--title", "t").returncode, 0)
            png = Path(d) / "shot.png"
            png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
            r = run("source", "add", "img", "--title", "Экран", "--kind", "image", "--file", str(png))
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertTrue((Path(d) / "img" / "sources" / "screens" / "S001.png").is_file())
            r = run("evidence", "add", "img", "--claim", "На экране кнопка «Оформить»", "--type", "fact",
                    "--source", "S001", "--quote", "Оформить")
            self.assertNotEqual(r.returncode, 0, "факт со скриншота без области кадра должен отклоняться")
            r = run("evidence", "add", "img", "--claim", "На экране кнопка «Оформить»", "--type", "fact",
                    "--source", "S001", "--quote", "Оформить", "--locator", "screens/S001.png [x 10–90 %, y 20–30 %]")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            ev = [json.loads(l) for l in (Path(d) / "img" / "evidence.jsonl").read_text().splitlines() if l.strip()]
            self.assertEqual(ev[-1].get("quote_verified"), "visual")
