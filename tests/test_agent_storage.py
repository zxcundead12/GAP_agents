"""Synthetic durable-state and redaction tests without native software."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from gap_agent.storage import RunStore


class AgentStorageTests(unittest.TestCase):
    def setUp(self):
        work = Path(__file__).resolve().parents[1] / 'work'
        work.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='agent-storage-', dir=work)
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def approved(self, store):
        run_id = 'run_' + uuid.uuid4().hex
        sha = store.create_plan({'run_id': run_id, 'synthetic': True})
        store.approve(run_id, {'plan_sha256': sha, 'approved_by': 'synthetic-test-only'})
        return run_id

    def test_escaped_and_custom_named_secret_is_redacted_before_serialization(self):
        secret = 'synthetic-"escaped"-\\key\ncanary'
        run_id = 'run_' + uuid.uuid4().hex
        store = RunStore(self.root, secret_env_names=['AUTH_FOR_LLM'])
        fields = {'nested': [{'text': 'before ' + secret + ' after', secret: secret}]}
        original = deepcopy(fields)
        with patch.dict(os.environ, {'AUTH_FOR_LLM': secret}, clear=True):
            store.journal(run_id, 'synthetic', fields=fields)
        text = (store.run_folder(run_id) / 'journal.jsonl').read_text(encoding='utf-8')
        row = json.loads(text)
        self.assertNotIn(secret, text)
        self.assertEqual(row['fields']['nested'][0]['text'], 'before [REDACTED] after')
        self.assertEqual(row['fields']['nested'][0]['[REDACTED]'], '[REDACTED]')
        self.assertEqual(fields, original)
        self.assertEqual(row['trace_id'], run_id)

    def test_generic_api_key_and_explicit_short_secret_are_redacted(self):
        store = RunStore(self.root, secret_env_names='CUSTOM_AUTH')
        run_id = 'run_' + uuid.uuid4().hex
        with patch.dict(os.environ, {'OTHER_API_KEY': 'synthetic-generic-key', 'CUSTOM_AUTH': 'xy!'}, clear=True):
            store.journal(run_id, 'synthetic', values=['synthetic-generic-key', 'xy!'])
        row = json.loads((store.run_folder(run_id) / 'journal.jsonl').read_text(encoding='utf-8'))
        self.assertEqual(row['values'], ['[REDACTED]', '[REDACTED]'])

    def test_uncertain_errors_remain_cached_and_block_other_writes_after_restart(self):
        for status in ('ERROR', 'API_ERROR', 'TIMEOUT', 'MCP_UNAVAILABLE'):
            with self.subTest(status=status):
                root = self.root / status
                store = RunStore(root)
                first = self.approved(store)
                self.assertEqual(store.claim(first)[0], 'CLAIMED')
                result = {'execution_status': status, 'issues': []}
                store.finish(first, result)
                restarted = RunStore(root)
                self.assertEqual(restarted.load(first)[0]['state'], 'UNKNOWN')
                self.assertEqual(restarted.claim(first), ('CACHED', result))
                second = self.approved(restarted)
                self.assertEqual(restarted.claim(second)[0], 'BLOCKED')
                restarted.resolve(first, 'Synthetic operator', 'Synthetic review only; no native operation')
                self.assertEqual(restarted.claim(second)[0], 'CLAIMED')
                self.assertEqual(restarted.claim(first), ('CACHED', result))

    def test_known_refusals_do_not_block_other_plans(self):
        for status in ('COM_UNAVAILABLE', 'LICENSE_UNAVAILABLE', 'LICENSE_BUSY', 'SESSION_BUSY',
                       'UNSUPPORTED_MAPPING', 'NOT_READY', 'CONFIRMATION_REQUIRED'):
            with self.subTest(status=status):
                store = RunStore(self.root / status)
                first = self.approved(store)
                store.claim(first)
                store.finish(first, {'execution_status': status, 'issues': []})
                self.assertEqual(store.load(first)[0]['state'], 'DONE')
                second = self.approved(store)
                self.assertEqual(store.claim(second)[0], 'CLAIMED')

    def test_native_uncertainty_overrides_even_a_known_refusal(self):
        for native in ({'manual_review_required': True}, {'plan_state': 'UNKNOWN'}, {'plan_state': 'RUNNING'}):
            with self.subTest(native=native):
                store = RunStore(self.root / uuid.uuid4().hex)
                run_id = self.approved(store)
                store.claim(run_id)
                store.finish(run_id, {'execution_status': 'NOT_READY', 'issues': []}, native_result=native)
                self.assertEqual(store.load(run_id)[0]['state'], 'UNKNOWN')

    def test_result_uncertainty_issue_is_preserved_without_native_dict(self):
        store = RunStore(self.root)
        run_id = self.approved(store)
        store.claim(run_id)
        store.finish(run_id, {'execution_status': 'SUCCESS', 'issues': [{'code': 'NATIVE_EXECUTION_UNCERTAIN'}]})
        self.assertEqual(store.load(run_id)[0]['state'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
