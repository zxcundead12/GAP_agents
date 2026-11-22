"""Actual subprocess protocol check; never COM, GAP or engineering writes."""
import asyncio
import importlib.util
from pathlib import Path
import tempfile
import unittest


@unittest.skipUnless(importlib.util.find_spec('mcp'), 'MCP SDK is not installed')
class StdioProtocolTests(unittest.TestCase):
    def test_real_stdio_discovery_environment_and_plan_preparation(self):
        root = Path(__file__).resolve().parents[1]
        work = root / 'work'
        work.mkdir(exist_ok=True)
        spec = importlib.util.spec_from_file_location('integration_stdio_check', root / 'scripts/check_mcp_stdio.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix='stdio-unit-', dir=work) as directory:
            result = asyncio.run(asyncio.wait_for(module.check_protocol(root, Path(directory)), timeout=40))
            self.assertEqual(result['execution_status'], 'SUCCESS')
            self.assertEqual(len(result['tool_names']), 6)
            self.assertFalse(result['com_called'])
            self.assertFalse(result['gap_plan_executed'])
            self.assertFalse(result['approval_created'])
            self.assertFalse(list(Path(directory).rglob('*.gap')))
