"""Решения на воротах по поручению человека (SA_ACTOR=delegated) и их последующее подтверждение человеком."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SA = ROOT / "scripts" / "sa.py"


class DelegatedGatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ, SA_PROJECTS_DIR=self.tmp.name)
        self.env.pop("SA_ACTOR", None)
        self.sa("new", "demo", "--title", "Проверка ворот")
        self.sa("stage", "demo", "plan", "--next", "план")

    def tearDown(self):
        self.tmp.cleanup()

    def sa(self, *args, actor=None):
        env = dict(self.env)
        if actor:
            env["SA_ACTOR"] = actor
        r = subprocess.run([sys.executable, str(SA), *args], capture_output=True, text=True, env=env,
                           stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout + r.stderr

    def gate_actors(self):
        log = Path(self.tmp.name) / "demo" / "log" / "actions.jsonl"
        return [json.loads(x)["actor"] for x in log.read_text(encoding="utf-8").splitlines()
                if x.strip() and json.loads(x).get("action") == "gate"]

    def test_delegated_is_logged_and_warned(self):
        self.sa("gate", "demo", "plan_approved", actor="delegated")
        self.assertEqual(self.gate_actors(), ["delegated"])
        out = self.sa("validate", "demo")
        self.assertIn("закрыты автором «delegated»", out)
        plan = (Path(self.tmp.name) / "demo" / "outputs" / "research_plan.md").read_text(encoding="utf-8")
        if "Статус:" in plan:
            self.assertIn("по прямому поручению человека", plan)

    def test_human_confirmation_clears_warning(self):
        self.sa("gate", "demo", "plan_approved", actor="delegated")
        self.sa("gate", "demo", "plan_approved", actor="human")
        self.assertEqual(self.gate_actors(), ["delegated", "human"])
        self.assertNotIn("закрыты автором «delegated»", self.sa("validate", "demo"))

    def test_budget_logs_real_actor(self):
        self.sa("budget", "demo", "7", "--reason", "тест", actor="agent")
        log = Path(self.tmp.name) / "demo" / "log" / "actions.jsonl"
        rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
        self.assertEqual([r["actor"] for r in rows if r.get("action") == "budget"], ["agent"])


if __name__ == "__main__":
    unittest.main()
