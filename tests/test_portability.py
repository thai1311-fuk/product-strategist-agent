"""Переносимость: конфиги сред, уровни моделей, неизвестные модели, session start/end, MCP-инструменты сессии.

Запуск: python3 -m unittest tests.test_portability -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SA = ROOT / "scripts" / "sa.py"
SERVER = ROOT / "scripts" / "mcp_server.py"


class TestPortability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = dict(os.environ, SA_PROJECTS_DIR=cls.tmp.name)
        cls.sa("new", "p1", "--title", "Тест", "--budget", "10")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def sa(cls, *args, env=None):
        return subprocess.run([sys.executable, str(SA), *args], env=env or cls.env, capture_output=True,
                              text=True, encoding="utf-8")

    def rows(self, name):
        p = Path(self.tmp.name) / "p1" / "log" / name
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]

    def last_action(self, action):
        return [r for r in self.rows("actions.jsonl") if r["action"] == action][-1]

    # --- конфиги ---
    def test_env_configs_valid(self):
        for f, key in ((".vscode/mcp.json", "servers"), (".gemini/settings.json", "mcpServers"),
                       (".cursor/mcp.json", "mcpServers"), (".mcp.json", "mcpServers")):
            d = json.loads((ROOT / f).read_text(encoding="utf-8"))
            self.assertIn("strategist", d[key], f)
            self.assertIn("mcp_server.py", json.dumps(d[key]["strategist"]), f)
        oc = json.loads((ROOT / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(oc["mcp"]["servers"]["strategist"]["command"], ["python3", "scripts/mcp_server.py"])
        try:
            import tomllib
        except ImportError:  # Python < 3.11
            tomllib = None
        if tomllib:
            t = tomllib.loads((ROOT / ".codex" / "config.toml").read_text(encoding="utf-8"))
            self.assertEqual(t["mcp_servers"]["strategist"]["args"], ["scripts/mcp_server.py"])
        for f in (".github/copilot-instructions.md", ".windsurf/rules/strategist.md", "GEMINI.md"):
            self.assertIn("AGENTS.md", (ROOT / f).read_text(encoding="utf-8"), f)

    def test_routing_tiers(self):
        r = json.loads((ROOT / "config" / "routing.json").read_text(encoding="utf-8"))
        for blk, base in (("stage_tiers", "stages"), ("subagent_tiers", "subagents")):
            self.assertEqual(set(r[blk]), set(r[base]))
            for k, lvl in r[blk].items():
                self.assertEqual(r["models"][r[base][k]]["level"], lvl, k)
        for prov in ("anthropic", "gigachat", "yandexgpt", "openai", "google", "qwen"):
            self.assertEqual(set(r["providers"][prov]), {"cheap", "medium", "strong"})
        for prov in ("gigachat", "yandexgpt", "openai", "google", "qwen"):
            for m in r["providers"][prov].values():
                self.assertIsNone(m["input_per_mtok"])
                self.assertIsNone(m["output_per_mtok"])

    # --- неизвестные модели ---
    def test_unknown_model_with_tier(self):
        res = self.sa("cost", "add", "p1", "--model", "GigaChat-2-Pro", "--in", "1000000", "--out", "0",
                      "--tier", "medium")
        self.assertEqual(res.returncode, 0, res.stderr)
        row = self.rows("costs.jsonl")[-1]
        self.assertEqual(row["model"], "GigaChat-2-Pro")
        self.assertAlmostEqual(row["usd"], 3.0)  # цена уровня medium = как у sonnet

    def test_unknown_model_without_tier_warns(self):
        res = self.sa("cost", "add", "p1", "--model", "some-local-llm", "--in", "5000", "--out", "100")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("$0", res.stdout)
        self.assertEqual(self.rows("costs.jsonl")[-1]["usd"], 0.0)

    def test_known_model_unchanged(self):
        self.sa("cost", "add", "p1", "--model", "opus", "--in", "1000000", "--out", "0")
        row = self.rows("costs.jsonl")[-1]
        self.assertEqual(row["model"], "claude-opus-5-5")
        self.assertAlmostEqual(row["usd"], 5.0)

    # --- session ---
    def test_session_start_end(self):
        env = dict(self.env, SA_ACTOR="windsurf")
        r = self.sa("session", "start", "p1", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("p1", r.stdout)
        self.assertEqual(self.last_action("session_start")["actor"], "windsurf")
        r = self.sa("session", "end", "p1", "--model", "claude-haiku-5-5", "--in", "2000000", "--out", "0",
                    "--source", "provider", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.last_action("session_end")["actor"], "windsurf")
        c = self.rows("costs.jsonl")[-1]
        self.assertEqual((c["model"], c["source"]), ("claude-haiku-5-5", "provider"))
        self.assertAlmostEqual(c["usd"], 2.0)

    def test_session_default_actor_and_no_cost(self):
        env = {k: v for k, v in self.env.items() if k != "SA_ACTOR"}
        self.assertEqual(self.sa("session", "start", "p1", env=env).returncode, 0)
        self.assertEqual(self.last_action("session_start")["actor"], "agent")
        n = len(self.rows("costs.jsonl"))
        self.assertEqual(self.sa("session", "end", "p1", env=env).returncode, 0)
        self.assertEqual(len(self.rows("costs.jsonl")), n)

    def test_session_end_needs_model_with_tokens(self):
        self.assertNotEqual(self.sa("session", "end", "p1", "--in", "5", "--out", "5").returncode, 0)
        self.assertNotEqual(self.sa("session", "end", "p1", "--model", "x", "--in", "5").returncode, 0)

    # --- MCP ---
    def test_mcp_session_tools(self):
        p = subprocess.Popen([sys.executable, str(SERVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding="utf-8", env=self.env, cwd=ROOT)
        try:
            def rpc(i, method, params=None):
                msg = {"jsonrpc": "2.0", "id": i, "method": method}
                if params is not None:
                    msg["params"] = params
                p.stdin.write(json.dumps(msg) + "\n")
                p.stdin.flush()
                return json.loads(p.stdout.readline())
            rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "t", "version": "0"}})
            names = {t["name"] for t in rpc(2, "tools/list")["result"]["tools"]}
            self.assertTrue({"sa_session_start", "sa_session_end"} <= names)
            r = rpc(3, "tools/call", {"name": "sa_session_start", "arguments": {"slug": "p1"}})["result"]
            self.assertFalse(r["isError"], r)
            r = rpc(4, "tools/call", {"name": "sa_session_end", "arguments": {
                "slug": "p1", "model": "my-model", "tier": "cheap", "input_tokens": 1000000, "output_tokens": 0,
                "source": "estimate"}})["result"]
            self.assertFalse(r["isError"], r)
            self.assertAlmostEqual(self.rows("costs.jsonl")[-1]["usd"], 1.0)
        finally:
            p.stdin.close()
            p.wait(timeout=10)
            p.stdout.close()
            p.stderr.close()


if __name__ == "__main__":
    unittest.main()
