"""MCP 2.x discovery and typed delegation with no COM/GAP activation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    from mcp import Client
    from mcp.server import MCPServer
except ImportError:
    Client = None
    MCPServer = None

from gap_integration.mcp_server import TOOL_NAMES, create_server


class FakeService:
    """Explicit fake: records service calls without importing the COM backend."""

    def __init__(self):
        self.calls = []

    def result(self, method, *args):
        self.calls.append((method, args))
        return {"execution_status": "SUCCESS", "trace_id": "fake-only", "method": method, "arguments": list(args)}

    def inspect_environment(self):
        return self.result("inspect_environment")

    def probe(self):
        return self.result("probe")

    def prepare_smoke_plan(self):
        return self.result("prepare_smoke_plan")

    def prepare_manifest_plan(self, manifest_path, mapping_path=None):
        return self.result("prepare_manifest_plan", manifest_path, mapping_path)

    def execute_plan(self, plan_id, approval_id):
        result = self.result("execute_plan", plan_id, approval_id)
        if not approval_id:
            result["execution_status"] = "CONFIRMATION_REQUIRED"
        return result

    def export_model(self, model_path):
        return self.result("export_model", model_path)


@unittest.skipUnless(Client is not None and MCPServer is not None, "Official MCP 2.x SDK is optional; use .venv/Scripts/python.exe")
class McpServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        work = Path(__file__).resolve().parents[1] / "work"
        work.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="mcp-unit-", dir=work)
        self.service = FakeService()
        self.server = create_server(Path(self.temp.name), service=self.service)

    def tearDown(self):
        self.temp.cleanup()

    async def test_discovery_exposes_exactly_six_tools_and_no_approval_or_raw_commands(self):
        async with Client(self.server, mode='legacy', read_timeout_seconds=5) as client:
            result = await client.list_tools()
        tools = {tool.name: tool for tool in result.tools}
        self.assertEqual(set(tools), TOOL_NAMES)
        self.assertEqual(self.service.calls, [])
        self.assertNotIn("approve_plan", tools)
        self.assertNotIn("execute_command", tools)
        self.assertTrue(tools["gap_environment"].annotations.read_only_hint)
        self.assertTrue(tools["gap_execute"].annotations.destructive_hint)
        self.assertEqual(tools["gap_prepare_manifest"].input_schema["properties"]["manifest_path"]["type"], "string")
        self.assertIn("manifest_path", tools["gap_prepare_manifest"].input_schema["required"])
        self.assertIn("description", tools["gap_export"].input_schema["properties"]["model_path"])
        self.assertIsNotNone(tools["gap_execute"].output_schema)

    async def test_all_tools_delegate_only_to_their_named_service_method(self):
        cases = [
            ("gap_environment", {}, "inspect_environment", ()),
            ("gap_probe", {}, "probe", ()),
            ("gap_prepare_smoke", {}, "prepare_smoke_plan", ()),
            ("gap_prepare_manifest", {"manifest_path": "data/candidate.json"}, "prepare_manifest_plan", ("data/candidate.json", None)),
            ("gap_prepare_manifest", {"manifest_path": "data/candidate.json", "mapping_path": "data/mapping.json"},
             "prepare_manifest_plan", ("data/candidate.json", "data/mapping.json")),
            ("gap_execute", {"plan_id": "plan-1", "approval_id": "approval-1"}, "execute_plan", ("plan-1", "approval-1")),
            ("gap_export", {"model_path": "outputs/gap/test.gap"}, "export_model", ("outputs/gap/test.gap",)),
        ]
        async with Client(self.server, mode='legacy', read_timeout_seconds=5) as client:
            for name, arguments, expected, values in cases:
                with self.subTest(tool=name):
                    result = await client.call_tool(name, arguments)
                    self.assertFalse(result.is_error)
                    self.assertEqual(result.structured_content["method"], expected)
                    self.assertEqual(self.service.calls[-1], (expected, values))

    async def test_execute_without_receipt_cannot_approve_itself(self):
        async with Client(self.server, mode='legacy', read_timeout_seconds=5) as client:
            result = await client.call_tool("gap_execute", {"plan_id": "plan-1"})
            self.assertFalse(result.is_error)
            self.assertEqual(result.structured_content["execution_status"], "CONFIRMATION_REQUIRED")
            self.assertEqual(self.service.calls, [("execute_plan", ("plan-1", ""))])
            result = await client.call_tool("approve_plan", {"plan_id": "plan-1", "approved_by": "agent"})
            self.assertTrue(result.is_error)
            self.assertEqual(len(self.service.calls), 1)

    async def test_invalid_argument_types_and_empty_paths_fail_before_service(self):
        async with Client(self.server, mode='legacy', read_timeout_seconds=5) as client:
            for arguments in ({}, {"manifest_path": 123}, {"manifest_path": []}, {"manifest_path": ""},
                              {"manifest_path": "ok.json", "mapping_path": 7}):
                with self.subTest(arguments=arguments):
                    result = await client.call_tool("gap_prepare_manifest", arguments)
                    self.assertTrue(result.is_error)
        self.assertEqual(self.service.calls, [])


if __name__ == "__main__":
    unittest.main()
