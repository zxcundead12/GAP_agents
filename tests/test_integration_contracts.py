"""The standalone participant-2 package must not impersonate engineering approval."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from gap_integration.contracts import confirmed, finite_number, validate_manifest, workspace_path
from gap_integration.service import GapService
from synthetic_engineering import fixture


class IntegrationContractsTests(unittest.TestCase):
    def test_missing_peer_validator_blocks_production_without_com(self):
        with patch('gap_integration.contracts.importlib.import_module', side_effect=ModuleNotFoundError):
            result = validate_manifest(fixture())
        self.assertFalse(result['ready_for_transfer'])
        self.assertEqual(result['issues'][0]['code'], 'ENGINEERING_VALIDATOR_UNAVAILABLE')

    def test_invalid_peer_reply_cannot_grant_readiness(self):
        for reply in ({'ready_for_transfer': 'true'}, {'execution_status': 'SUCCESS', 'ready_for_transfer': True,
                      'case_id': 'synthetic-unit-only', 'issues': [None]}):
            with self.subTest(reply=reply), patch('gap_integration.contracts.importlib.import_module',
                 return_value=SimpleNamespace(validate_manifest=lambda *a, **kw: reply)):
                self.assertFalse(validate_manifest(fixture())['ready_for_transfer'])

    def test_peer_errors_override_claimed_readiness(self):
        reply = {'execution_status': 'ERROR', 'case_id': 'synthetic-unit-only',
                 'ready_for_transfer': True, 'issues': []}
        with patch('gap_integration.contracts.importlib.import_module',
                   return_value=SimpleNamespace(validate_manifest=lambda *a, **kw: reply)):
            self.assertFalse(validate_manifest(fixture())['ready_for_transfer'])

    def test_common_helpers_reject_nonfinite_bool_and_escaped_paths(self):
        for value in (True, float('nan'), float('inf'), None, '1'):
            self.assertFalse(finite_number(value))
        self.assertTrue(finite_number(1.5))
        self.assertFalse(confirmed({'review_status': 'confirmed', 'reviewed_by': 'test', 'reviewed_at': '2026-11-22T12:00:00'}))
        with tempfile.TemporaryDirectory() as directory:
            for raw in ('../model.gap', 'model.gap:stream', 'bad\nname'):
                with self.subTest(raw=raw), self.assertRaises(ValueError):
                    workspace_path(directory, raw)

    def test_smoke_plan_does_not_require_peer_validator_or_call_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            service = GapService(directory, runner=lambda *args, **kwargs: calls.append(args))
            with patch('gap_integration.contracts.importlib.import_module', side_effect=ModuleNotFoundError):
                result = service.prepare_smoke_plan()
            self.assertEqual(result['execution_status'], 'SUCCESS')
            self.assertFalse(calls)
