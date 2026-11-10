"""Тесты режима проекта fast/full: ворота, validate, маршрутизатор."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import route  # noqa: E402
from lib import state, store, validate  # noqa: E402
from lib.store import SAError  # noqa: E402


class ModeBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._patch = mock.patch.dict(os.environ, {"SA_PROJECTS_DIR": self.tmp.name})
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.tmp.cleanup()


class TestMode(ModeBase):
    def test_default_full_and_budget(self):
        p = state.create_project("a", "A")
        st = state.load(p)
        self.assertEqual((st["mode"], st["budget_usd"]), ("full", 5.0))
        q = state.create_project("b", "B", mode="fast")
        st = state.load(q)
        self.assertEqual((st["mode"], st["budget_usd"]), ("fast", 1.5))
        self.assertIn("Главные вопросы", (q / "brief.md").read_text(encoding="utf-8"))
        self.assertEqual(state.load(state.create_project("c", "C", budget=3, mode="fast"))["budget_usd"], 3.0)
        with self.assertRaises(SAError):
            state.create_project("d", "D", mode="turbo")

    def test_fast_reaches_options_without_plan_gate(self):
        p = state.create_project("f", "F", mode="fast")
        state.set_stage(p, "collect")
        state.set_stage(p, "options")
        self.assertEqual(state.load(p)["stage"], "options")
        with self.assertRaises(SAError):  # option_chosen по-прежнему обязательно
            state.set_stage(p, "strategy")
        self.assertEqual(state.missing_gates(state.load(p), "strategy"), ["option_chosen"])

    def test_fast_strategy_gate_optional_after_option(self):
        p = state.create_project("f2", "F2", mode="fast")
        st = state.load(p)
        st["gates"]["option_chosen"] = "C001"
        state.save(p, st)
        state.set_stage(p, "strategy")
        state.set_stage(p, "test_plan")  # strategy_approved не нужен
        self.assertEqual(state.missing_gates(state.load(p), "test_plan"), [])

    def test_full_still_requires_gates(self):
        p = state.create_project("g", "G")
        with self.assertRaises(SAError):
            state.set_stage(p, "collect")
        self.assertEqual(state.missing_gates(state.load(p), "options"), ["plan_approved"])
        st = state.load(p)
        st["gates"]["plan_approved"] = True
        st["gates"]["option_chosen"] = "C001"
        state.save(p, st)
        state.set_stage(p, "strategy")
        with self.assertRaises(SAError):
            state.set_stage(p, "test_plan")  # strategy_approved в full обязателен
        # старый state.json без поля mode = full
        st = state.load(p)
        del st["mode"]
        self.assertEqual(state.mode_of(st), "full")

    def test_set_mode_history_and_switch_back(self):
        p = state.create_project("h", "H", mode="fast")
        state.set_stage(p, "collect")
        state.set_stage(p, "options")
        st = state.set_mode(p, "full")
        self.assertEqual(st["mode"], "full")
        self.assertIn("режим fast → full", st["history"][-1]["event"])
        self.assertEqual(state.missing_gates(st, "options"), ["plan_approved"])
        with self.assertRaises(SAError):
            state.set_mode(p, "x")

    def test_validate_respects_mode(self):
        p = state.create_project("v", "V", mode="fast")
        state.set_stage(p, "collect")
        state.set_stage(p, "options")
        errs = [e for e in validate.run(p).errors if "ворот" in e]
        self.assertEqual(errs, [], errs)
        self.assertFalse([w for w in validate.run(p).warnings if "пропущены" in w])
        state.set_mode(p, "full")
        self.assertTrue([e for e in validate.run(p).errors if "plan_approved" in e])

    def test_resume_and_status_show_mode(self):
        p = state.create_project("r", "R", mode="fast")
        self.assertIn("режим: fast", state.resume_lines(p)[0])
        import subprocess
        env = dict(os.environ, SA_PROJECTS_DIR=self.tmp.name)
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "sa.py"), "status", "r"],
                             capture_output=True, text=True, env=env, timeout=60).stdout
        self.assertIn("Режим: fast", out)
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "sa.py"), "mode", "r", "full"],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(state.load(p)["mode"], "full")

    def test_fast_models_cheap_or_medium(self):
        from lib import cost
        r = cost.load_routing()
        for stage in state.STAGES:
            self.assertNotEqual(cost.model_for(stage, r, "fast"), "claude-opus-5-5", stage)
        self.assertEqual(cost.model_for("synthesis", r), "claude-opus-5-5")  # full не изменился
        self.assertEqual(r["mode_tiers"]["fast"]["max_sources_per_question"], 5)


class TestRouteMode(unittest.TestCase):
    def test_fast_on_urgent(self):
        for t in ("быстро прикинь рынок самокатов", "оценка за час: стоит ли делать подписку"):
            self.assertEqual(route.recommend_mode(t)["mode"], "fast", t)

    def test_fast_on_low_budget(self):
        r = route.recommend_mode("Проанализируй рынок онлайн-образования в регионах", 1.5)
        self.assertEqual(r["mode"], "fast")
        self.assertTrue(r["reasons"])

    def test_full_on_big_decision(self):
        r = route.recommend_mode("Быстро решить: закрыть направление, необратимое решение на годы")
        self.assertEqual(r["mode"], "full")
        self.assertTrue(any("необратим" in x for x in r["reasons"]))

    def test_full_default(self):
        self.assertEqual(route.recommend_mode("Подготовь развёрнутую стратегию вывода продукта на рынок с учётом "
                                              "конкурентов, сегментов и каналов продаж")["mode"], "full")


if __name__ == "__main__":
    unittest.main()
