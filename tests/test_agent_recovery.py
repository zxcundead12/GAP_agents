"""Agent recovery/configuration checks: synthetic LLM and runner, no external calls."""
from copy import deepcopy
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from gap_agent.agent import EngineeringAgent
from gap_agent.contracts import validate_result
from gap_agent.diagnostics import doctor
from gap_integration.service import GapService


CLEAR = {'verified': True, 'gap_pids': [], 'worker_pids': [], 'uninspectable_python_pids': []}
TASK = {'operation': 'smoke', 'case_id': None, 'manifest_path': None, 'mapping_path': None,
        'export_path': None, 'model_path': None, 'tolerances_path': None, 'clarifications': []}


class SyntheticLLM:
    metadata = {'evidence': 'synthetic', 'called': True}

    def __init__(self):
        self.calls = 0

    def parse(self, text, context):
        self.calls += 1
        return deepcopy(TASK)


class Gateway:
    def __init__(self, root):
        self.status = 'SUCCESS'
        self.calls = 0
        self.service = GapService(root, runner=self.runner)

    def runner(self, job, workspace, timeout_seconds):
        self.calls += 1
        return {'execution_status': self.status, 'trace_id': 'synthetic-recovery', 'message': 'Synthetic response; no GAP'}

    def call(self, name, arguments):
        if name == 'gap_prepare_smoke':
            return self.service.prepare_smoke_plan()
        if name == 'gap_execute':
            return self.service.execute_plan(arguments['plan_id'], arguments['approval_id'])
        raise AssertionError(name)


class AgentRecoveryTests(unittest.TestCase):
    def setUp(self):
        work = Path(__file__).resolve().parents[1] / 'work'
        self.temporary = tempfile.TemporaryDirectory(prefix='agent-recovery-', dir=work)
        self.root = Path(self.temporary.name)
        self.llm = SyntheticLLM()
        self.gateway = Gateway(self.root)
        self.agent = EngineeringAgent(self.root, self.llm, self.gateway)

    def tearDown(self):
        self.temporary.cleanup()

    def prepared(self):
        result = self.agent.request('Synthetic engineering request')
        approved = self.agent.approve(result['run_id'], 'Operator', result['plan']['sha256'])
        self.assertEqual(approved['execution_status'], 'SUCCESS')
        return result['run_id']

    def check_native_error_recovery(self, status):
        first = self.prepared()
        self.gateway.status = status
        self.assertEqual(self.agent.execute(first)['execution_status'], status)
        self.assertEqual(self.agent.store.load(first)[0]['state'], 'UNKNOWN')
        second = self.prepared()
        self.assertEqual(self.agent.execute(second)['execution_status'], 'NOT_READY')
        with patch.object(GapService, '_recovery_activity', return_value=CLEAR):
            resolved = self.agent.acknowledge(first, 'Operator', 'Synthetic test: checked worker, GAP and output')
        self.assertEqual(resolved['execution_status'], 'SUCCESS', resolved)
        self.gateway.status = 'SUCCESS'
        self.assertEqual(self.agent.execute(second)['execution_status'], 'SUCCESS')
        self.assertEqual(self.agent.execute(first)['execution_status'], status)
        self.assertEqual(self.gateway.calls, 2)

    def test_api_error_is_recoverable_without_repeating_old_write(self):
        self.check_native_error_recovery('API_ERROR')

    def test_error_is_recoverable_without_repeating_old_write(self):
        self.check_native_error_recovery('ERROR')

    def test_malformed_run_identifiers_return_safe_correlated_results(self):
        for raw in ('../../private-SECRET-STRING', None, [], 'run_invalid'):
            for operation in ('approve', 'execute', 'acknowledge'):
                with self.subTest(raw=raw, operation=operation):
                    arguments = (raw, 'Operator', 'hash') if operation == 'approve' else (
                        (raw, 'Operator', 'Manual note') if operation == 'acknowledge' else (raw,))
                    result = getattr(self.agent, operation)(*arguments)
                    validate_result(result)
                    self.assertRegex(result['run_id'], r'^run_[0-9a-f]{32}$')
                    self.assertEqual(result['trace_id'], result['run_id'])
                    self.assertTrue(any(i.get('code') == 'INVALID_RUN_ID' for i in result['issues']))
                    self.assertNotIn('SECRET-STRING', json.dumps(result))
        self.assertEqual(self.gateway.calls, 0)

    def test_malformed_config_returns_results_without_calling_llm(self):
        folder = self.root / 'data'
        folder.mkdir()
        for raw in ('[]', 'null', '{"llm": []}', '{broken secret-do-not-echo'):
            with self.subTest(raw=raw):
                (folder / 'agent_config.json').write_text(raw, encoding='utf-8')
                agent = EngineeringAgent(self.root, self.llm, self.gateway)
                result = agent.request('Synthetic request')
                self.assertEqual(result['execution_status'], 'LLM_NOT_CONFIGURED')
                diagnostics = doctor(self.root)
                self.assertNotEqual(diagnostics['execution_status'], 'SUCCESS')
                self.assertTrue(any(i.get('code') == 'CONFIG_INVALID' for i in diagnostics['issues']))
                self.assertNotIn('secret-do-not-echo', json.dumps(result) + json.dumps(diagnostics))
        self.assertEqual(self.llm.calls, 0)

    def test_doctor_requires_current_gap_executable(self):
        with patch('gap_agent.diagnostics._gap_executable', return_value=(None, True)):
            result = doctor(self.root)
        self.assertEqual(result['execution_status'], 'NOT_READY')
        self.assertTrue(any(i.get('code') == 'GAP_EXECUTABLE_MISSING' for i in result['issues']))

    def test_delayed_native_claim_during_acknowledgement_keeps_agent_uncertain(self):
        run_id = self.prepared()
        self.assertEqual(self.agent.store.claim(run_id)[0], 'CLAIMED')
        result = self.agent.result(run_id, 'MCP_UNAVAILABLE', 'Synthetic transport timeout before request delivery')
        self.agent.store.finish(run_id, result)
        original = GapService._load_plan

        def claim_after_read(service, plan_id):
            row, plan = original(service, plan_id)
            if row['state'] == 'APPROVED':
                with service._connection() as db:
                    db.execute("UPDATE plans SET state='RUNNING' WHERE plan_id=?", (plan_id,))
            return row, plan

        with patch.object(GapService, '_load_plan', claim_after_read):
            resolved = self.agent.acknowledge(run_id, 'Operator', 'Synthetic race: native claim arrives after the first read')
        self.assertEqual(resolved['execution_status'], 'NOT_READY', resolved)
        self.assertEqual(self.agent.store.load(run_id)[0]['state'], 'UNKNOWN')
        self.assertEqual(self.gateway.calls, 0)


if __name__ == '__main__':
    unittest.main()
