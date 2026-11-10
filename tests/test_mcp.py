"""MCP-сервер scripts/mcp_server.py: протокол JSON-RPC по stdio и инструменты на временном проекте.

Запуск: python3 -m unittest tests.test_mcp -v
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
SERVER = ROOT / "scripts" / "mcp_server.py"


class TestMCP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = dict(os.environ, SA_PROJECTS_DIR=cls.tmp.name)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "sa.py"), "new", "demo", "--title", "Демо"],
                       env=cls.env, check=True, capture_output=True)
        cls.proc = subprocess.Popen([sys.executable, str(SERVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, encoding="utf-8", env=cls.env, cwd=ROOT)
        cls.next_id = 0

    @classmethod
    def tearDownClass(cls):
        cls.proc.stdin.close()
        cls.proc.wait(timeout=10)
        cls.proc.stdout.close()
        cls.proc.stderr.close()
        cls.tmp.cleanup()

    def rpc(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            type(self).next_id += 1
            msg["id"] = self.next_id
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        if notify:
            return None
        resp = json.loads(self.proc.stdout.readline())
        self.assertEqual((resp["jsonrpc"], resp["id"]), ("2.0", msg["id"]))
        return resp

    def call(self, name, **args):
        return self.rpc("tools/call", {"name": name, "arguments": args})["result"]

    def test_1_initialize_and_list(self):
        r = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "test", "version": "0"}})["result"]
        self.assertEqual(r["protocolVersion"], "2025-06-18")
        self.assertIn("tools", r["capabilities"])
        self.assertEqual(r["serverInfo"]["name"], "strategist")
        self.rpc("notifications/initialized", notify=True)
        self.assertEqual(self.rpc("ping")["result"], {})
        tools = {t["name"]: t for t in self.rpc("tools/list")["result"]["tools"]}
        for name in ("sa_status", "sa_resume", "sa_list", "sa_source_fetch", "sa_source_add", "sa_evidence_add",
                     "sa_evidence_list", "sa_conclusion_add", "sa_impact", "sa_validate", "sa_cost_report", "sa_report"):
            self.assertIn(name, tools)
            self.assertEqual(tools[name]["inputSchema"]["type"], "object")
        for forbidden in ("sa_gate", "sa_budget", "sa_allow"):  # решения человека — не через MCP
            self.assertNotIn(forbidden, tools)

    def test_2_tools_on_project(self):
        r = self.call("sa_status", slug="demo")
        self.assertFalse(r["isError"])
        self.assertIn("Следующий шаг", r["content"][0]["text"])
        r = self.call("sa_source_add", slug="demo", title="Слова заказчика", kind="user",
                      text="Решение нужно до 15.12.")
        self.assertIn("ID: S001", r["content"][0]["text"])
        r = self.call("sa_evidence_add", slug="demo", claim="Срок 15.12", type="fact", source="S001",
                      quote="Решение нужно до 15.12")
        self.assertFalse(r["isError"], r)
        self.call("sa_conclusion_add", slug="demo", statement="Срок жёсткий", depends_on="E001")
        r = self.call("sa_validate", slug="demo")
        self.assertFalse(r["isError"], r)
        self.assertIn("Итог: проверка пройдена", r["content"][0]["text"])
        r = self.call("sa_impact", slug="demo", ids="E001", dry_run=True)
        self.assertIn("C001", r["content"][0]["text"])
        log = [json.loads(l) for l in (Path(self.tmp.name) / "demo" / "log" / "actions.jsonl").read_text().splitlines()]
        self.assertTrue(any(e["action"] == "mcp:sa_validate" and e["actor"] == "mcp" for e in log))

    def test_3_errors(self):
        r = self.rpc("tools/call", {"name": "sa_gate", "arguments": {}})
        self.assertEqual(r["error"]["code"], -32602)
        r = self.rpc("tools/call", {"name": "sa_status", "arguments": {"slug": "demo", "lishnee": 1}})
        self.assertEqual(r["error"]["code"], -32602)
        self.assertEqual(self.rpc("no/such")["error"]["code"], -32601)
        r = self.call("sa_status", slug="net-takogo")
        self.assertTrue(r["isError"])
        self.assertIn("не найден", r["content"][0]["text"])
        r = self.call("sa_source_fetch", slug="demo", url="https://evil.example.com/")
        self.assertTrue(r["isError"])
        self.assertIn("allowlist", r["content"][0]["text"])
        self.proc.stdin.write("не json\n")
        self.proc.stdin.flush()
        self.assertEqual(json.loads(self.proc.stdout.readline())["error"]["code"], -32700)

    def test_4_write_tools(self):
        tools = {t["name"] for t in self.rpc("tools/list")["result"]["tools"]}
        for n in ("sa_stage", "sa_log", "sa_cost_add", "sa_evidence_retract", "sa_evidence_supersede",
                  "sa_conclusion_update", "sa_conclusion_review_order", "sa_decision_add", "sa_decision_review",
                  "sa_decision_update", "sa_search", "sa_route"):
            self.assertIn(n, tools)
        # без --force: параметра force нет, переход без ворот отклоняется
        r = self.rpc("tools/call", {"name": "sa_stage", "arguments": {"slug": "demo", "stage": "collect", "force": True}})
        self.assertEqual(r["error"]["code"], -32602)
        r = self.call("sa_stage", slug="demo", stage="collect")
        self.assertTrue(r["isError"])
        r = self.call("sa_stage", slug="demo", stage="plan", status="awaiting_human", next="-5% ждём ответ")
        self.assertFalse(r["isError"], r)
        # значение, начинающееся с «-», не принимается за опцию
        r = self.call("sa_evidence_add", slug="demo", claim="-5% к выручке", type="hypothesis")
        self.assertFalse(r["isError"], r)
        r = self.call("sa_log", slug="demo", action="note", target="-x", key="k1")
        self.assertFalse(r["isError"], r)
        r = self.call("sa_cost_add", slug="demo", model="haiku", input_tokens=100, output_tokens=10, key="c1")
        self.assertFalse(r["isError"], r)
        r = self.call("sa_cost_add", slug="demo", model="haiku", input_tokens=100, output_tokens=10, key="c1")
        self.assertIn("уже есть", r["content"][0]["text"])
        r = self.call("sa_decision_add", slug="demo", decision="Решение", owner="Роль", based_on="C001",
                      metrics=["m: 1 → 2"], commitments=["кто: сделать к 2027-01-01"], review_date="2027-03-01")
        self.assertFalse(r["isError"], r)
        r = self.call("sa_decision_add", slug="demo", decision="Решение", owner="Роль", based_on="C001",
                      metrics=["m: 1 → 2"], commitments=["кто: сделать к 2027-01-01"], review_date="2027-03-01")
        self.assertIn("уже записано", r["content"][0]["text"])
        r = self.call("sa_decision_update", slug="demo", id="D001", review_date="2027-06-01")
        self.assertFalse(r["isError"], r)
        self.assertFalse(self.call("sa_decision_review", slug="demo", dry_run=True)["isError"])
        self.assertFalse(self.call("sa_conclusion_review_order", slug="demo")["isError"])
        r = self.call("sa_search", slug="demo", query="-5% выручке")
        self.assertIn("E00", r["content"][0]["text"])
        r = self.call("sa_search", query="интервью", methods=True)
        self.assertIn("problem-interview", r["content"][0]["text"])
        self.assertFalse(self.call("sa_route", slug="demo", text="собери источники")["isError"])


class TestMCPConfig(unittest.TestCase):
    def test_configs(self):
        cc = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["strategist"]
        self.assertEqual((cc["command"], cc["args"]), ("python3", ["scripts/mcp_server.py"]))
        cur = json.loads((ROOT / ".cursor" / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]["strategist"]
        self.assertTrue(cur["args"][0].endswith("scripts/mcp_server.py"))


if __name__ == "__main__":
    unittest.main()
