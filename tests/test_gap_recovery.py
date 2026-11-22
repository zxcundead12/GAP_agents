"""Crash recovery is local, terminal and refuses active/unknown process state."""
from pathlib import Path
import hashlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from gap_integration.service import GapService


CLEAR = {'verified': True, 'gap_pids': [], 'worker_pids': [], 'uninspectable_python_pids': []}


class GapRecoveryTests(unittest.TestCase):
    def setUp(self):
        work = Path(__file__).resolve().parents[1] / 'work'
        self.temporary = tempfile.TemporaryDirectory(prefix='recovery-', dir=work)
        self.root = Path(self.temporary.name)
        self.calls = 0

        def runner(job, workspace, timeout_seconds):
            self.calls += 1
            return {'execution_status': 'SUCCESS', 'trace_id': 'synthetic-recovery', 'message': 'Synthetic; no GAP'}

        self.service = GapService(self.root, runner=runner)

    def tearDown(self):
        self.temporary.cleanup()

    def interrupted(self, *, with_owner=True):
        plan = self.service.prepare_smoke_plan()
        receipt = self.service.approve_plan(plan['plan_id'], 'Test operator')
        with self.service._connection() as db:
            db.execute("UPDATE plans SET state='RUNNING' WHERE plan_id=?", (plan['plan_id'],))
            if with_owner:
                db.execute('INSERT INTO execution_claims VALUES (?,?,1)', (plan['plan_id'], 123456789))
        return plan, receipt

    def test_dead_owner_and_inactive_worker_allow_terminal_reconciliation_after_restart(self):
        plan, receipt = self.interrupted()
        restarted = GapService(self.root, runner=self.service.runner)
        with patch.object(restarted, '_pid_alive', return_value=False), patch.object(
                restarted, '_recovery_activity', return_value=CLEAR):
            result = restarted.acknowledge_unknown(plan['plan_id'], 'Operator', 'Checked worker, GAP and saved output manually')
        self.assertEqual(result['execution_status'], 'SUCCESS', result)
        row, _ = restarted._load_plan(plan['plan_id'])
        self.assertEqual(row['state'], 'RESOLVED_NO_RETRY')
        previous = restarted.execute_plan(plan['plan_id'], receipt['approval_id'])
        self.assertEqual(previous['execution_status'], 'ERROR')
        self.assertTrue(previous['idempotent_reuse'])
        self.assertEqual(self.calls, 0)
        new_plan = restarted.prepare_smoke_plan()
        new_receipt = restarted.approve_plan(new_plan['plan_id'], 'Operator')
        self.assertEqual(restarted.execute_plan(new_plan['plan_id'], new_receipt['approval_id'])['execution_status'], 'SUCCESS')
        self.assertEqual(self.calls, 1)

    def test_live_owner_blocks_even_before_worker_start(self):
        plan, _ = self.interrupted()
        with patch.object(self.service, '_pid_alive', return_value=True), patch.object(
                self.service, '_recovery_activity') as activity:
            result = self.service.acknowledge_unknown(plan['plan_id'], 'Operator', 'Manual note')
        self.assertEqual(result['execution_status'], 'NOT_READY')
        self.assertEqual(result['details']['code'], 'EXECUTION_OWNER_ACTIVE')
        activity.assert_not_called()

    def test_live_gap_worker_or_uninspectable_process_block_reconciliation(self):
        plan, _ = self.interrupted()
        for key in ('gap_pids', 'worker_pids', 'uninspectable_python_pids'):
            with self.subTest(key=key), patch.object(self.service, '_pid_alive', return_value=False), patch.object(
                    self.service, '_recovery_activity', return_value={**CLEAR, key: [123]}):
                result = self.service.acknowledge_unknown(plan['plan_id'], 'Operator', 'Manual note')
                self.assertEqual(result['execution_status'], 'NOT_READY')
        self.assertEqual(self.service._load_plan(plan['plan_id'])[0]['state'], 'RUNNING')

    def test_legacy_running_without_owner_and_failed_inspection_fail_closed(self):
        legacy, _ = self.interrupted(with_owner=False)
        result = self.service.acknowledge_unknown(legacy['plan_id'], 'Operator', 'Manual note')
        self.assertEqual(result['execution_status'], 'NOT_READY')
        self.assertEqual(result['details']['code'], 'OWNERSHIP_NOT_RECORDED')
        plan, _ = self.interrupted()
        with patch.object(self.service, '_pid_alive', return_value=False), patch.object(
                self.service, '_recovery_activity', side_effect=OSError('Inspection unavailable')):
            result = self.service.acknowledge_unknown(plan['plan_id'], 'Operator', 'Manual note')
        self.assertNotEqual(result['execution_status'], 'SUCCESS')
        self.assertEqual(self.service._load_plan(plan['plan_id'])[0]['state'], 'RUNNING')

    def test_current_process_liveness_is_read_only(self):
        self.assertTrue(GapService._pid_alive(os.getpid()))

    def runtime_fixture(self):
        """Synthetic files test receipt binding only, never runtime verification."""
        model, journal, template = [self.root / name for name in ('verification.gap', 'verification.jsonl', 'template.gap')]
        for path in (model, journal, template):
            path.write_bytes(b'Synthetic artifact; never real GAP evidence')
        proof = {'model': {'path': str(model), 'sha256': hashlib.sha256(model.read_bytes()).hexdigest()},
                 'journal': {'path': str(journal), 'sha256': hashlib.sha256(journal.read_bytes()).hexdigest()}}
        proof_path = self.root / 'proof.json'
        proof_path.write_text(json.dumps(proof), encoding='utf-8')
        mapping = {'review_status': 'confirmed', 'reviewed_by': 'Synthetic reviewer', 'reviewed_at': '2026-11-22T12:00:00+03:00',
                   'verification_path': str(proof_path), 'template_path': str(template),
                   'template_sha256': hashlib.sha256(template.read_bytes()).hexdigest()}
        mapping_path = self.root / 'mapping.json'
        mapping_path.write_text(json.dumps(mapping), encoding='utf-8')
        manifest_path = self.root / 'manifest.json'
        manifest_path.write_text(json.dumps({'case_id': 'synthetic-runtime-dependencies', 'nodes': [], 'pipes': [], 'valves': []}), encoding='utf-8')
        return manifest_path, mapping_path, (proof_path, model, journal, template)

    def test_all_four_runtime_artifacts_are_bound_before_approval(self):
        manifest, mapping, artifacts = self.runtime_fixture()
        with patch('gap_integration.service.validate_manifest', return_value={'ready_for_transfer': True}):
            for artifact in artifacts:
                with self.subTest(artifact=artifact.name):
                    plan = self.service.prepare_manifest_plan(str(manifest), str(mapping))
                    self.assertEqual(plan['execution_status'], 'SUCCESS', plan)
                    frozen = json.loads(Path(plan['plan_path']).read_text(encoding='utf-8'))['inputs']['runtime_dependencies']
                    self.assertEqual(set(frozen), {'verification', 'verification_model', 'verification_journal', 'template'})
                    original = artifact.read_bytes()
                    artifact.write_bytes(original + b'\n')
                    result = self.service.approve_plan(plan['plan_id'], 'Operator')
                    self.assertEqual(result['execution_status'], 'ERROR', result)
                    artifact.write_bytes(original)
        self.assertEqual(self.calls, 0)

    def test_replacing_proof_and_its_matching_model_after_approval_is_rejected(self):
        manifest, mapping, artifacts = self.runtime_fixture()
        proof_path, model, _, _ = artifacts
        with patch('gap_integration.service.validate_manifest', return_value={'ready_for_transfer': True}):
            plan = self.service.prepare_manifest_plan(str(manifest), str(mapping))
            receipt = self.service.approve_plan(plan['plan_id'], 'Operator')
            self.assertEqual(receipt['execution_status'], 'SUCCESS', receipt)
            model.write_bytes(b'Another matching synthetic artifact')
            proof = json.loads(proof_path.read_text(encoding='utf-8'))
            proof['model']['sha256'] = hashlib.sha256(model.read_bytes()).hexdigest()
            proof_path.write_text(json.dumps(proof), encoding='utf-8')
            result = self.service.execute_plan(plan['plan_id'], receipt['approval_id'])
        self.assertEqual(result['execution_status'], 'ERROR', result)
        self.assertEqual(self.calls, 0)


if __name__ == '__main__':
    unittest.main()
