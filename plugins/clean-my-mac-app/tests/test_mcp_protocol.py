from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).parents[1]
SERVER = PLUGIN_ROOT / "mcp/server.mjs"


class McpProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = subprocess.Popen(
            ["node", str(SERVER)],
            cwd=PLUGIN_ROOT,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def tearDown(self) -> None:
        self.server.terminate()
        try:
            self.server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.server.kill()
            self.server.wait(timeout=3)
        for stream in (self.server.stdin, self.server.stdout, self.server.stderr):
            if stream:
                stream.close()

    def request(self, request_id: int, method: str, params: dict | None = None) -> dict:
        assert self.server.stdin and self.server.stdout
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        self.server.stdin.write(json.dumps(payload) + "\n")
        self.server.stdin.flush()
        return json.loads(self.server.stdout.readline())

    def test_tools_and_deep_scan(self) -> None:
        initialize = self.request(1, "initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(initialize["result"]["serverInfo"]["version"], "0.2.0")
        tools = self.request(2, "tools/list")["result"]["tools"]
        names = {tool["name"] for tool in tools}
        self.assertIn("clean_my_mac_deep_scan", names)
        self.assertIn("clean_my_mac_tool_status", names)
        for action in ("scan", "plan", "clean"):
            self.assertIn(f"clean_my_mac_codex_{action}", names)
        codex_clean = next(tool for tool in tools if tool["name"] == "clean_my_mac_codex_clean")
        self.assertEqual(codex_clean["inputSchema"]["required"], ["ids", "yes"])
        self.assertEqual(codex_clean["inputSchema"]["properties"]["ids"]["minItems"], 1)
        self.assertTrue(codex_clean["annotations"]["destructiveHint"])

        resource = self.request(6, "resources/read", {"uri": "ui://clean-my-mac/app-v2.html"})
        html = resource["result"]["contents"][0]["text"]
        self.assertIn('data-action="deep"', html)
        self.assertIn('clean_my_mac_deep_scan', html)
        self.assertIn('data-action="codex"', html)
        self.assertIn('clean_my_mac_codex_${action}', html)

        status = self.request(3, "tools/call", {"name": "clean_my_mac_tool_status", "arguments": {}})
        self.assertFalse(status["result"].get("isError", False))
        self.assertFalse(status["result"]["structuredContent"]["result"]["policy"]["auto_install"])

        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "sample.bin").write_bytes(b"sample")
            scan = self.request(
                4,
                "tools/call",
                {
                    "name": "clean_my_mac_deep_scan",
                    "arguments": {"path": directory, "maxEntries": 100, "timeoutSeconds": 10, "top": 5},
                },
            )
        result = scan["result"]
        self.assertFalse(result.get("isError", False))
        evidence = result["structuredContent"]["result"]
        self.assertEqual(evidence["mode"], "read-only")
        self.assertIn("allocated_bytes", evidence["summary"])
        self.assertIn(evidence["adapters"]["gdu"]["status"], {"available", "partial", "unavailable", "failed"})
        self.assertTrue(result["content"][0]["text"].startswith("Deep scan complete"))

        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "a.bin").write_bytes(b"a")
            Path(directory, "b.bin").write_bytes(b"b")
            partial_scan = self.request(
                7,
                "tools/call",
                {
                    "name": "clean_my_mac_deep_scan",
                    "arguments": {"path": directory, "maxEntries": 1, "timeoutSeconds": 10, "top": 5},
                },
            )
        self.assertTrue(partial_scan["result"]["content"][0]["text"].startswith("Deep scan partial (max_entries)"))

        invalid = self.request(
            5,
            "tools/call",
            {"name": "clean_my_mac_deep_scan", "arguments": {"path": "relative/path"}},
        )
        self.assertTrue(invalid["result"]["isError"])

    def test_codex_requires_explicit_nonempty_selection_and_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory, "keep.txt")
            marker.write_text("unchanged")
            cases = [
                ("clean_my_mac_codex_clean", {"ids": ["candidate"], "roots": [directory]}, "codex_clean_requires_yes_true"),
                ("clean_my_mac_codex_clean", {"ids": ["candidate"], "yes": False}, "codex_clean_requires_yes_true"),
                ("clean_my_mac_codex_clean", {"ids": [], "yes": True}, "codex_requires_nonempty_ids"),
                ("clean_my_mac_codex_plan", {"ids": []}, "codex_requires_nonempty_ids"),
                ("clean_my_mac_codex_plan", {"ids": [" "]}, "codex_requires_nonempty_ids"),
                ("clean_my_mac_codex_scan", {"roots": ["relative"]}, "codex_requires_absolute_roots"),
            ]
            for request_id, (name, arguments, error) in enumerate(cases, 20):
                with self.subTest(name=name, arguments=arguments):
                    response = self.request(request_id, "tools/call", {"name": name, "arguments": arguments})["result"]
                    self.assertTrue(response["isError"])
                    self.assertEqual(response["structuredContent"]["error"], error)
                    self.assertNotIn("command", response["structuredContent"])
                    self.assertEqual(marker.read_text(), "unchanged")

    def test_codex_scan_is_read_only_and_has_selection_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "task"
            root.mkdir()
            subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
            (root / ".gitignore").write_text("work/*\n")
            work = root / "work"
            work.mkdir()
            (work / "package.json").write_text('{"dependencies":{"example":"1.0.0"}}')
            (work / "package-lock.json").write_text('{"lockfileVersion":3}')
            generated = work / "node_modules/package"
            generated.mkdir(parents=True)
            (generated / "index.js").write_text("module.exports = 1;")
            marker = root / "keep.txt"
            marker.write_text("unchanged")
            response = self.request(30, "tools/call", {
                "name": "clean_my_mac_codex_scan", "arguments": {"roots": [str(root)]},
            })["result"]
            self.assertFalse(response.get("isError", False), response)
            payload = response["structuredContent"]["result"]
            self.assertIn("status", payload)
            self.assertIn(str(root), payload["roots"])
            self.assertIsInstance(payload["targets"], list)
            self.assertTrue(payload["targets"])
            self.assertIn("coverage", payload)
            for row in payload["targets"]:
                for key in ("id", "path", "label", "eligible", "blocked_reason", "logical_bytes", "allocated_bytes", "provenance"):
                    self.assertIn(key, row)
            self.assertEqual(marker.read_text(), "unchanged")


if __name__ == "__main__":
    unittest.main()
