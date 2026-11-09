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


class TestMCPConfig(unittest.TestCase):
    def test_configs(self):
        cc = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["strategist"]
        self.assertEqual((cc["command"], cc["args"]), ("python3", ["scripts/mcp_server.py"]))
        cur = json.loads((ROOT / ".cursor" / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]["strategist"]
        self.assertTrue(cur["args"][0].endswith("scripts/mcp_server.py"))


if __name__ == "__main__":
    unittest.main()
