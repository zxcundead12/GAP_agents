"""Real stdio MCP round-trip with synthetic intent; never COM/GAP or live LLM."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from gap_agent.agent import EngineeringAgent
from gap_agent.mcp_client import MCPGateway
from test_agent import SyntheticLLM, task


@unittest.skipUnless(importlib.util.find_spec('mcp'), 'MCP SDK not installed')
class AgentMCPTests(unittest.TestCase):
    def test_actual_stdio_environment_round_trip_does_not_activate_com(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='agent-wire-', dir=project / 'work') as directory:
            root = Path(directory)
            for package in ('gap_agent', 'gap_manifest', 'gap_integration'):
                shutil.copytree(project / package, root / package,
                                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            agent = EngineeringAgent(root, SyntheticLLM(task('inspect')), MCPGateway(root))
            result = agent.request('Проверь окружение')
            self.assertEqual(result['execution_status'], 'SUCCESS', result)
            evidence = json.loads(Path(result['artifacts']['environment']).read_text(encoding='utf-8'))
            self.assertFalse(evidence['com_activated'])
            self.assertFalse(evidence['automation_verified'])
            self.assertEqual(evidence['license_status'], 'NOT_ASSESSED')
            self.assertEqual(result['llm']['evidence'], 'synthetic')

