"""Кодировка загруженных страниц: windows-1251 без charset в заголовке (pravo.gov.ru и др.)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib.evidence import _detect_charset  # noqa: E402


class CharsetTest(unittest.TestCase):
    def test_header_wins(self):
        self.assertEqual(_detect_charset("текст".encode("koi8-r"), "koi8-r"), "koi8-r")

    def test_meta_charset(self):
        body = '<html><head><meta charset="windows-1251"></head>Закон</html>'.encode("cp1251")
        self.assertEqual(_detect_charset(body, ""), "windows-1251")

    def test_utf8_without_header(self):
        self.assertEqual(_detect_charset("Федеральный закон".encode("utf-8"), ""), "utf-8")

    def test_cp1251_fallback(self):
        body = "Федеральный закон о связи".encode("cp1251")
        enc = _detect_charset(body, "")
        self.assertEqual(body.decode(enc), "Федеральный закон о связи")

    def test_unknown_header_ignored(self):
        self.assertEqual(_detect_charset("abc".encode(), "x-unknown-enc"), "utf-8")


if __name__ == "__main__":
    unittest.main()
