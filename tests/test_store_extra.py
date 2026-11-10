"""Хранилище и CLI: demo-проекты, битые строки jsonl, идемпотентность, решения, поиск, маршрутизация, .env, права.

Запуск: python3 -m unittest tests.test_store_extra -v
"""
from __future__ import annotations

import datetime as dt
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import cost, decisions, search, state, store  # noqa: E402
from lib.store import SAError  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("SA_PROJECTS_DIR")
        os.environ["SA_PROJECTS_DIR"] = self.tmp.name
        self.pdir = state.create_project("p1", "Тест")

    def tearDown(self):
        if self.old is None:
            os.environ.pop("SA_PROJECTS_DIR", None)
        else:
            os.environ["SA_PROJECTS_DIR"] = self.old
        self.tmp.cleanup()

    def sa(self, *args):
        env = dict(os.environ, SA_PROJECTS_DIR=self.tmp.name, PYTHONIOENCODING="utf-8")
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "sa.py"), *args], env=env,
                              capture_output=True, text=True, encoding="utf-8")

    def add_concl(self):
        from lib import evidence  # noqa: PLC0415
        s = evidence.add_source(self.pdir, "Док", "x", "user", "high", text="Тариф стоит 100 рублей в месяц.")
        e = evidence.add_evidence(self.pdir, "Тариф стоит 100 рублей", "fact", s["id"], "Тариф стоит 100 рублей", "", "high", "2026", "analyst")
        c = evidence.add_conclusion(self.pdir, "Тариф дешёвый для массового сегмента", e["id"], "insight", None)
        return e["id"], c["id"]


class TestDemo(Base):
    def test_demo_skipped(self):
        state.create_project("p2", "Второй")
        store.set_demo(store.project_dir("p2"), True)
        self.assertEqual(store.active_project().name, "p1")
        self.assertEqual(store.active_project(include_demo=True).name in ("p1", "p2"), True)
        store.set_demo(store.project_dir("p1"), True)
        self.assertIsNone(store.active_project())
        with self.assertRaises(SAError):
            store.resolve_project(None)
        r = self.sa("demo", "p1", "off")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(store.active_project().name, "p1")

    def test_new_demo_flag_and_done_skipped(self):
        r = self.sa("new", "d1", "--title", "Демо", "--demo")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(store.is_demo(store.project_dir("d1")))
        state.set_stage(self.pdir, "intake", "done")
        self.assertIsNone(store.active_project())


class TestCorrupt(Base):
    def test_corrupt_preserved_ids_not_reused(self):
        e_id, _ = self.add_concl()
        path = self.pdir / "evidence.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write('{"id": "E002", "claim": "оборванная запись\n')
        from lib import evidence  # noqa: PLC0415
        e2 = evidence.add_evidence(self.pdir, "Другое", "hypothesis", "", "", "", "low", "", "analyst")
        self.assertEqual(e2["id"], "E003")
        saved = (self.pdir / "evidence.jsonl.corrupt").read_text(encoding="utf-8")
        self.assertIn("оборванная запись", saved)
        evidence.add_evidence(self.pdir, "Третье", "hypothesis", "", "", "", "low", "", "analyst")
        self.assertEqual(saved.count("оборванная"), (self.pdir / "evidence.jsonl.corrupt").read_text(encoding="utf-8").count("оборванная"))
        rep = dict((r[0], r[1]) for r in store.corrupt_report(self.pdir))
        self.assertEqual(rep.get("evidence.jsonl"), 1)

    def test_doctor_shows_corrupt(self):
        (self.pdir / "log" / "costs.jsonl").write_text("{битая\n", encoding="utf-8")
        r = self.sa("doctor")
        self.assertIn("битых строк 1", r.stdout)


class TestIdempotent(Base):
    def test_cost_key(self):
        a = cost.add(self.pdir, "haiku", 1000, 100, key="k1")
        b = cost.add(self.pdir, "haiku", 1000, 100, key="k1")
        self.assertTrue(b.get("duplicate"))
        self.assertEqual(len(store.read_jsonl(self.pdir / "log" / "costs.jsonl")), 1)
        self.assertEqual(a["usd"], b["usd"])

    def test_decision_dedupe_and_review_date(self):
        _, c = self.add_concl()
        kw = dict(decision="Запустить пилот", owner="Продакт", based_on=c, metrics=["m: 1 → 2 к 2027-01-01"],
                  triggers=["дата: 2027-02-01"], commitments=["Аналитик: отчёт к 2020-01-01"], review_date="2027-02-01")
        f1 = decisions.add(self.pdir, **kw)
        f2 = decisions.add(self.pdir, **kw)
        self.assertEqual(f1, f2)
        self.assertEqual(len(list((self.pdir / "decisions").glob("D*.md"))), 1)
        text = f1.read_text(encoding="utf-8")
        self.assertEqual(text.count("дата: 2027-02-01"), 1)
        res = decisions.review(self.pdir, apply=False)
        self.assertEqual(res["overdue_commitments"][0][0], "D001")

    def test_decision_update_review_date_and_review_due(self):
        _, c = self.add_concl()
        f = decisions.add(self.pdir, "Решение", "Роль", c, ["m: 1 → 2"], [], ["Кто: сделать к 15.12.2026"],
                          review_date="2020-01-01")
        res = decisions.review(self.pdir)
        self.assertTrue(res["due"])
        res = decisions.review(self.pdir)  # review_due без причин не должен давать «нечего пересматривать»
        self.assertTrue(res["due"])
        future = (dt.date.today() + dt.timedelta(days=90)).isoformat()
        decisions.update(self.pdir, "D001", "active", "в силе", future)
        text = f.read_text(encoding="utf-8")
        self.assertEqual(text.count("дата:"), 1)
        self.assertIn(future, text)
        self.assertFalse(decisions.review(self.pdir)["due"])
        with self.assertRaises(SAError):
            decisions.update(self.pdir, "D001")


class TestSearchRoute(Base):
    def test_search_links(self):
        e, c = self.add_concl()
        decisions.add(self.pdir, "Запуск", "Роль", c, ["m: 1 → 2"], ["дата: 2027-01-01"], ["кто: сделать"])
        out = "\n".join(search.search_project(self.pdir, "тариф стоит"))
        self.assertIn(e, out)
        self.assertIn("решения: D001", out)
        r = self.sa("search", "p1", "тариф")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_methods_search_and_index(self):
        out = "\n".join(search.search_methods("интервью без наводящих вопросов"))
        self.assertIn("problem-interview", out)
        self.assertTrue((ROOT / "knowledge" / "INDEX.md").exists())

    def test_route(self):
        import route  # noqa: PLC0415
        self.assertEqual(route.classify("собери источники про тарифы")["model"], "claude-haiku-5-5")
        self.assertEqual(route.classify("предложи варианты стратегии")["model"], "claude-opus-5-5")
        self.assertEqual(route.classify("абракадабра")["basis"], "default")
        self.assertEqual(route.for_stage("synthesis")["model"], "claude-opus-5-5")

    def test_escalation_logged(self):
        import route  # noqa: PLC0415
        state.set_stage(self.pdir, "intake", "in_progress")
        # лишняя ссылка на несуществующий маркер в outputs → ошибка валидатора
        (self.pdir / "outputs" / "situation.md").write_text("# Ситуация\n\nТариф дорог [E099].\n", encoding="utf-8")
        cost.add(self.pdir, "haiku", 10, 10, stage="intake")
        r = route.check_escalation(self.pdir)
        self.assertTrue(r["escalate"])
        self.assertEqual(r["to"], "claude-sonnet-5-5")
        acts = store.read_jsonl(self.pdir / "log" / "actions.jsonl")
        self.assertTrue(any(a["action"] == "escalate_model" for a in acts))


class TestMisc(Base):
    def test_search_cost_and_human_minutes(self):
        store.log_action(self.pdir, "websearch", "q1")
        store.log_action(self.pdir, "websearch", "q2")
        n, per, usd = cost.search_cost(self.pdir)
        self.assertEqual(n, 2)
        self.assertAlmostEqual(usd, 2 * per)
        st = state.load(self.pdir)
        t0 = dt.datetime.now().astimezone() - dt.timedelta(minutes=30)
        st["history"] = [
            {"ts": t0.isoformat(), "stage": "plan", "event": "intake/done → plan/awaiting_human"},
            {"ts": (t0 + dt.timedelta(minutes=20)).isoformat(), "stage": "plan", "event": "ворота plan_approved = True (решение человека)"}]
        st["stage_status"] = "in_progress"
        w = cost.human_gate_minutes(st)
        self.assertEqual(len(w), 1)
        self.assertAlmostEqual(w[0][1], 20, delta=0.1)
        self.assertIn("Поиск: 2 запросов", "\n".join(cost.report(self.pdir)))

    def test_session_not_double_counted_across_projects(self):
        p2 = state.create_project("p2", "Второй")
        tot = {"claude-haiku-5-5": {"input_tokens": 1000, "output_tokens": 500, "cache_write": 0, "cache_read": 0}}
        cost.upsert_session(self.pdir, "s1", "intake", tot)
        tot2 = {"claude-haiku-5-5": {"input_tokens": 1600, "output_tokens": 800, "cache_write": 0, "cache_read": 0}}
        rows = cost.upsert_session(p2, "s1", "intake", tot2)
        self.assertEqual(rows[0]["raw_input"], 600)
        self.assertEqual(rows[0]["output_tokens"], 300)

    def test_dotenv_only_sa_vars(self):
        env = Path(self.tmp.name) / "x.env"
        env.write_text("# c\nSA_TEST_A=1\nexport SA_TEST_B='two words'\nOPENAI_API_KEY=sk-x\nSA_TEST_C=\n", encoding="utf-8")
        for k in ("SA_TEST_A", "SA_TEST_B", "OPENAI_API_KEY"):
            os.environ.pop(k, None)
        got = store.load_dotenv(env)
        try:
            self.assertEqual(sorted(got), ["SA_TEST_A", "SA_TEST_B"])
            self.assertEqual(os.environ["SA_TEST_B"], "two words")
            self.assertNotIn("OPENAI_API_KEY", os.environ)
        finally:
            os.environ.pop("SA_TEST_A", None)
            os.environ.pop("SA_TEST_B", None)

    @unittest.skipIf(os.name == "nt", "права POSIX")
    def test_file_mode_follows_umask(self):
        old = os.umask(0o022)
        try:
            p = Path(self.tmp.name) / "f.txt"
            store.atomic_write_text(p, "x")
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o644)
        finally:
            os.umask(old)

    def test_relative_paths_in_log(self):
        store.log_action(self.pdir, "x", str(store.ROOT / "projects" / "a" / "f.md"), detail=f"{Path.home()}/секрет")
        row = store.read_jsonl(self.pdir / "log" / "actions.jsonl")[-1]
        self.assertNotIn(str(store.ROOT), row["target"])
        self.assertNotIn(str(Path.home()), row["detail"])


if __name__ == "__main__":
    unittest.main()
