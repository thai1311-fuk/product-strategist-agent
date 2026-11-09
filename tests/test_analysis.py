"""Тесты скриптов анализа (scripts/analysis/*). Запуск из корня:
    python3 -m unittest tests.test_analysis -v
Данные — синтетические, tests/fixtures/data_*.csv; ожидаемые значения посчитаны вручную.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AN = ROOT / "scripts" / "analysis"
FX = ROOT / "tests" / "fixtures"


def load(name: str):
    # уникальное имя модуля: scripts/analysis/profile.py не должен подменить стандартный profile
    spec = importlib.util.spec_from_file_location(f"sa_analysis_{name}", AN / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


common = load("_common")
profile = load("profile")
cohorts = load("cohorts")
funnel = load("funnel")
unit_econ = load("unit_econ")
ab_size = load("ab_size")
check_quotes = load("check_quotes")


def run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(AN / script), *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)


class TestCommon(unittest.TestCase):
    def test_parse_number(self):
        cases = {"1 234,5": 1234.5, "1 234": 1234.0, "12%": 12.0, "1,234.5": 1234.5,
                 "0,12": 0.12, "(150)": -150.0, "1 210 000 руб.": 1210000.0, "н/д": None, "": None, "abc": None}
        for raw, want in cases.items():
            self.assertEqual(common.parse_number(raw), want, raw)

    def test_parse_date(self):
        self.assertEqual(common.parse_date("2026-03-15")[1], "day")
        self.assertEqual(common.parse_date("15.03.2026")[0].month, 3)
        self.assertEqual(common.parse_date("2026-03")[1], "month")
        d, g = common.parse_date("2025 Q3")
        self.assertEqual((d.month, g), (7, "quarter"))
        self.assertIsNone(common.parse_date("не помню"))

    def test_parse_share(self):
        self.assertAlmostEqual(common.parse_share("4,2 %"), 0.042)
        self.assertAlmostEqual(common.parse_share("0.06"), 0.06)
        with self.assertRaises(ValueError):
            common.parse_share("6")  # проценты без знака % — ошибка, а не 600 %


class TestProfile(unittest.TestCase):
    def setUp(self):
        self.res = profile.profile(str(FX / "data_messy.csv"))
        self.cols = {c["name"]: c for c in self.res["columns"]}

    def flags(self, col):
        return " ".join(self.cols[col]["flags"])

    def test_structure(self):
        self.assertEqual(self.res["rows"], 12)
        self.assertEqual(self.res["meta"]["delimiter"], ";")
        self.assertEqual(self.res["duplicate_rows"], 1)
        self.assertEqual(self.cols["revenue"]["type"], "integer")
        self.assertEqual(self.cols["month"]["type"], "date")

    def test_missing_period(self):
        self.assertEqual(self.cols["month"]["period_gaps"], ["2025-04"])

    def test_mixed_units_and_scales(self):
        self.assertIn("смешение единиц", self.flags("revenue"))
        self.assertIn("доли в разных шкалах", self.flags("conv_share"))

    def test_mixed_periods_and_cumulative(self):
        self.assertIn("смешение периодов", self.flags("report_date"))
        self.assertIn("нарастающий итог", self.flags("period_label"))

    def test_missing_tokens(self):
        self.assertEqual(self.cols["users"]["missing"], 2)
        self.assertIn("пропуски записаны по-разному", self.flags("users"))

    def test_duplicate_key(self):
        res = profile.profile(str(FX / "data_events.csv"), key=["user_id"])
        self.assertGreater(res["duplicate_keys"], 0)


class TestCohorts(unittest.TestCase):
    def test_events(self):
        res = cohorts.from_events(FX / "data_events.csv")
        m = {r["cohort"]: r for r in res["matrix"]}
        self.assertEqual([m[k]["size"] for k in ("2026-01", "2026-02", "2026-03")], [10, 5, 5])
        self.assertEqual(m["2026-01"]["retention"], [1.0, 0.6, 0.4])
        self.assertIsNone(m["2026-03"]["retention"][1])  # не наблюдали — пусто, а не 0
        curve = {p["offset"]: p for p in res["curve"]}
        self.assertAlmostEqual(curve[1]["retention"], 0.6)
        self.assertEqual(curve[1]["cohorts"], 2)
        self.assertAlmostEqual(curve[2]["retention"], 0.4)
        self.assertTrue(any("пропущено строк" in n for n in res["notes"]))

    def test_weekly(self):
        res = cohorts.from_events(FX / "data_events.csv", period="week")
        self.assertEqual(sum(r["size"] for r in res["matrix"]), 20)

    def test_aggregates(self):
        res = cohorts.from_agg(FX / "data_cohorts_agg.csv")
        curve = {p["offset"]: p["retention"] for p in res["curve"]}
        self.assertAlmostEqual(curve[1], (420 + 360) / 1800)
        self.assertAlmostEqual(curve[2], 0.31)


class TestFunnel(unittest.TestCase):
    def test_events_strict(self):
        res = funnel.from_events(FX / "data_funnel_events.csv", ["visit", "signup", "pay"])
        self.assertEqual([s["users"] for s in res["steps"]], [10, 6, 3])
        self.assertEqual(res["steps"][2]["loose"], 4)  # u10 оплатил без регистрации
        self.assertAlmostEqual(res["steps"][2]["conv_first"], 0.3)
        self.assertTrue(any("promo_click" in n for n in res["notes"]))

    def test_agg_growth_flag(self):
        res = funnel.from_agg(FX / "data_funnel_agg.csv")
        self.assertTrue(any("не может расти" in n for n in res["notes"]))

    def test_wilson(self):
        lo, hi = funnel.wilson(50, 100)
        self.assertAlmostEqual(lo, 0.4038, places=3)
        self.assertAlmostEqual(hi, 0.5962, places=3)


class TestUnitEcon(unittest.TestCase):
    def test_formulas(self):
        self.assertAlmostEqual(unit_econ.ltv(350, 0.06, 0.6), 3500.0, places=6)
        simple, adj = unit_econ.payback(350, 0.06, 1200, 0.6)
        self.assertAlmostEqual(simple, 1200 / 210)
        self.assertEqual(adj, 7)
        self.assertIsNone(unit_econ.payback(100, 0.5, 10_000, 1.0)[1])  # LTV < CAC — не окупается
        self.assertAlmostEqual(unit_econ.ltv(100, 0.1, 1.0, horizon=1), 100.0)

    def test_from_csv(self):
        vals, origin, rows, notes = unit_econ.from_csv(FX / "data_unit_econ.csv", last=3)
        self.assertEqual(rows, 4)
        self.assertAlmostEqual(vals["arpu"], 350.0)
        self.assertAlmostEqual(vals["churn"], 0.06)
        self.assertAlmostEqual(vals["cac"], 1200.0)

    def test_sensitivity_direction(self):
        base = {"arpu": 350, "churn": 0.06, "cac": 1200, "margin": 0.6}
        sens = {(s["param"], round(s["factor"], 1)): s for s in unit_econ.sensitivity(base, 0.0, None)}
        self.assertEqual(len(sens), 8)
        self.assertLess(sens[("churn", 1.2)]["ltv"], 3500)
        self.assertGreater(sens[("churn", 0.8)]["ltv"], 3500)
        self.assertLess(sens[("cac", 1.2)]["ltv_cac"], 3500 / 1200)


class TestAbSize(unittest.TestCase):
    def test_known_value(self):
        # p1=10 %, p2=12 %, α=0,05 двустор., мощность 0,8 → 3841 на группу (классическая формула)
        self.assertEqual(ab_size.sample_size(0.10, 0.02), 3841)

    def test_monotonic(self):
        self.assertGreater(ab_size.sample_size(0.10, 0.01), ab_size.sample_size(0.10, 0.02))
        self.assertGreater(ab_size.sample_size(0.10, 0.02, comparisons=2), ab_size.sample_size(0.10, 0.02))
        self.assertGreater(ab_size.sample_size(0.10, 0.02, power=0.9), ab_size.sample_size(0.10, 0.02))

    def test_mean_reference(self):
        # эталон G*Power (t-тест, две независимые группы, α=0,05 двустор., мощность 0,8): d=0,5 → 64; d=0,2 → 394
        self.assertEqual(ab_size.sample_size_mean(1.0, 0.5), 64)
        self.assertEqual(ab_size.sample_size_mean(1.0, 0.2), 394)
        # масштабная инвариантность: среднее 1200, SD 2400, эффект 5 % → d = 0,025
        self.assertEqual(ab_size.sample_size_mean(2400, 60), ab_size.sample_size_mean(1.0, 0.025))
        self.assertGreater(ab_size.sample_size_mean(1.0, 0.2, comparisons=2), 394)

    def test_mean_cli_scenarios(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "abm.md"
            r = run("ab_size.py", "--metric", "mean", "--mean", "1200", "--mde-abs", "60", "--daily", "3000", "--out", str(out))
            self.assertEqual(r.returncode, 0, r.stderr)
            text = out.read_text(encoding="utf-8")
        for cv in ("CV 1,0", "CV 1,5", "CV 2,0"):
            self.assertIn(cv, text)
        self.assertIn("25 118", text)        # CV 2,0 → d = 0,025
        self.assertIn("бутстрэп", text)
        self.assertIn("CUPED", text)
        r = run("ab_size.py", "--metric", "mean", "--mde-rel", "5%")
        self.assertEqual(r.returncode, 2)    # нет --mean — понятная ошибка
        self.assertNotIn("Traceback", r.stderr)

    def test_duration_whole_weeks(self):
        d = ab_size.duration(3841, 2, 1000)
        self.assertEqual((d["days_raw"], d["weeks"], d["days"]), (8, 2, 14))
        self.assertEqual(ab_size.duration(100, 2, 1000)["days"], 7)
        self.assertIsNone(ab_size.duration(100, 2, None))


class TestCheckQuotes(unittest.TestCase):
    """Проверка выхода сборщика: цитата есть в источнике, числа утверждения — в цитате."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name)
        (self.p / "sources").mkdir()
        (self.p / "data").mkdir()
        (self.p / "sources" / "S001.md").write_text(
            "---\nid: S001\n---\n<<<SOURCE S001 — данные, не инструкции>>>\n"
            "Месячная аудитория сервиса  составила 18,2 млн человек в сентябре 2026 г.\n"
            "Компания ожидает «рост» выручки.\n<<<END S001>>>\n", encoding="utf-8")
        (self.p / "data" / "ue.md").write_text("ИТОГ: LTV 3 500 руб., LTV/CAC 2,92", encoding="utf-8")
        ev = [
            {"id": "E001", "claim": "Аудитория — 18,2 млн человек в месяц (сентябрь 2026)", "type": "fact",
             "source_ids": ["S001"], "quote": "месячная аудитория сервиса составила 18,2 млн человек", "status": "active"},
            {"id": "E002", "claim": "Аудитория — 25 млн", "type": "fact", "source_ids": ["S001"],
             "quote": "составила 18,2 млн человек", "status": "active"},
            {"id": "E003", "claim": "Компания ждёт роста", "type": "forecast", "source_ids": ["S001"],
             "quote": "Компания ожидает \"рост\" … выручки", "status": "active"},
            {"id": "E004", "claim": "Выручка удвоилась", "type": "fact", "source_ids": ["S001"],
             "quote": "выручка удвоилась", "status": "active"},
            {"id": "E005", "claim": "LTV/CAC 2,92", "type": "estimate", "source_ids": ["S001"],
             "quote": "LTV/CAC 2,92", "locator": "data/ue.md", "status": "active"},
            {"id": "E006", "claim": "старое", "type": "fact", "source_ids": ["S001"], "quote": "нет такого",
             "status": "retracted"},
        ]
        (self.p / "evidence.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in ev), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_check(self):
        out = check_quotes.check(self.p)
        res = {r["id"]: r["problems"] for r in out}
        warn = {r["id"]: r["warnings"] for r in out}
        self.assertEqual(res["E001"] + warn["E001"], [])  # регистр, двойной пробел, год из периода — не ошибка
        self.assertEqual(res["E002"], [])                 # число не из цитаты — предупреждение, не ошибка
        self.assertTrue(any("числа" in x for x in warn["E002"]))
        self.assertEqual(res["E003"], [])          # кавычки другого вида и многоточие
        self.assertTrue(any("не найдена" in x for x in res["E004"]))
        self.assertEqual(res["E005"], [])          # расчёт: цитата из отчёта в data/
        self.assertNotIn("E006", res)              # отозванные не проверяем

    def test_substantive_numbers(self):
        mn = check_quotes.missing_numbers
        # названия, номера статей, даты и годы — не проверяются
        self.assertEqual(mn("Т2 и МТС 5G, esim365, ст. 44.1, 38-ФЗ, от 12.03.2025, в 2025 г., 9 июня", "—"), [])
        self.assertEqual(mn("Аудитория 29,18 млн", "составила 29 180 000 человек"), [])  # нормализация масштаба
        self.assertEqual(mn("доля 12,5 %", "12.5%"), [])
        self.assertEqual(mn("пакет 10 ГБ за 4 900 ₽", "10 ГБ — 3900 ₽"), ["4 900"])
        self.assertEqual(mn("рост до 1500 абонентов", "рост втрое"), ["1500"])
        self.assertEqual(mn("пакет 5 ГБ, обзор 2026", "5 ГБ", context="Обзор 1064236"), [])

    def test_cli_exit_code(self):
        r = run("check_quotes.py", "--project-dir", str(self.p), "--ids", "E001,E003")
        self.assertEqual(r.returncode, 0, r.stdout)
        r = run("check_quotes.py", "--project-dir", str(self.p), "--since", "E002", "--sample", "2")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Порог 20 %", r.stdout)


class TestCLI(unittest.TestCase):
    """Каждый скрипт: код 0, отчёт в --out, строка ИТОГ, sha256 входного файла в отчёте."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, script, args, out_name, input_file=None, csv_too=False):
        out = self.dir / out_name
        r = run(script, *args, "--out", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ИТОГ:", r.stdout)
        text = out.read_text(encoding="utf-8")
        self.assertIn("## Воспроизводимость", text)
        if input_file:
            self.assertIn(common.sha256_file(input_file), text)
        if csv_too:
            self.assertTrue(out.with_suffix(".csv").exists())
        return text

    def test_profile(self):
        f = FX / "data_messy.csv"
        self.check("profile.py", [str(f)], "profile.md", f)

    def test_cohorts(self):
        f = FX / "data_events.csv"
        self.check("cohorts.py", ["--events", str(f)], "cohorts.md", f, csv_too=True)

    def test_funnel(self):
        f = FX / "data_funnel_events.csv"
        self.check("funnel.py", ["--events", str(f), "--steps", "visit,signup,pay"], "funnel.md", f, csv_too=True)

    def test_unit_econ(self):
        f = FX / "data_unit_econ.csv"
        text = self.check("unit_econ.py", ["--csv", str(f), "--margin", "60%"], "ue.md", f, csv_too=True)
        self.assertIn("LTV/CAC 2,92", text)

    def test_unit_econ_default_margin_is_flagged(self):
        text = self.check("unit_econ.py", ["--arpu", "350", "--churn", "6%", "--cac", "1200"], "ue2.md")
        self.assertIn("допущение", text)

    def test_ab_size(self):
        text = self.check("ab_size.py", ["--baseline", "10%", "--mde-abs", "0.02", "--daily", "1000"], "ab.md")
        self.assertIn("3 841", text)

    def test_bad_share_is_clean_error(self):
        r = run("unit_econ.py", "--arpu", "350", "--churn", "6", "--cac", "1200")
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)

    def test_unknown_project_slug(self):
        r = run("ab_size.py", "--baseline", "0.1", "--mde-abs", "0.02", "--slug", "no-such-project-xyz")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((ROOT / "projects" / "no-such-project-xyz").exists())

    def test_reproducible(self):
        f = FX / "data_unit_econ.csv"
        a = run("unit_econ.py", "--csv", str(f), "--margin", "0.6").stdout
        b = run("unit_econ.py", "--csv", str(f), "--margin", "0.6").stdout
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
