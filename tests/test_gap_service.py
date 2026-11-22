"""Service tests use injected runners only: no COM, GAP or license access."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from gap_integration.service import GapService


STAMP = "2026-11-22T12:00:00+03:00"


class FakeRunner:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or {"execution_status": "SUCCESS", "trace_id": "injected-trace", "message": "Injected runner only",
                                     "snapshot": {"origin": "unit-test", "objects": ["AG_J1", "AG_J2", "AG_P1"]}}

    def __call__(self, job, workspace, timeout_seconds):
        self.calls.append((deepcopy(job), Path(workspace), timeout_seconds))
        return deepcopy(self.response)


class GapServiceTests(unittest.TestCase):
    def setUp(self):
        work = Path(__file__).resolve().parents[1] / "work"
        work.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="gap-service-tests-", dir=work)
        self.base = Path(self.temporary.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.runner = FakeRunner()
        self.service = GapService(self.workspace, runner=self.runner)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")

    def approved_smoke(self, service=None):
        service = service or self.service
        plan = service.prepare_smoke_plan()
        self.assertEqual(plan["execution_status"], "SUCCESS", plan)
        receipt = service.approve_plan(plan["plan_id"], "Local test operator")
        self.assertEqual(receipt["execution_status"], "SUCCESS", receipt)
        return plan, receipt

    def ready_inputs(self):
        # This is deliberately synthetic evidence for service contract tests.
        source = self.base / "synthetic-evidence.pdf"
        source.write_bytes(b"Synthetic unit fixture; not a real engineering drawing.")
        reviewed = {"review_status": "confirmed", "reviewed_by": "Synthetic fixture reviewer", "reviewed_at": STAMP}
        ref = {"source_id": "fixture", "page": 1, "bbox": [1, 1, 20, 20], "raw_text": "synthetic fixture",
               "method": "visual", "fields": ["length_m", "outer_diameter_mm", "wall_thickness_mm", "inner_diameter_mm"]}
        manifest = {
            "schema_version": "1.0", "case_id": "synthetic-service-test",
            "sources": [{"id": "fixture", "path": "../synthetic-evidence.pdf", "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                         "pages": [{"number": 1, "width_pt": 100, "height_pt": 100}]}],
            "selection": {"id": "selection", "regions": [deepcopy(ref)],
                          "required_minima": {"nodes": 10, "pipes": 12, "sources": 2, "valves": 4}, **reviewed},
            "nodes": [{"id": f"N{i}", "original_label": f"N{i}", "kind": "source" if i < 2 else "junction",
                       "context": "synthetic fixture", "position": [i + 5, 5], "source_refs": [deepcopy(ref)], **reviewed}
                      for i in range(12)],
            "pipes": [{"id": f"P{i}", "original_label": f"P{i}", "from_node": f"N{i}", "to_node": f"N{(i + 1) % 12}",
                       "role": "pipeline", "active_state": "active", "line_class": "test",
                       "parameters": {"length_m": 10, "outer_diameter_mm": 159, "wall_thickness_mm": 8, "inner_diameter_mm": 143},
                       "source_refs": [deepcopy(ref)], **reviewed} for i in range(12)],
            "valves": [{"id": f"V{i}", "original_label": f"V{i}", "pipe_id": f"P{i}", "physical_state": "closed" if i == 0 else "open",
                        "size_dn_mm": 150, "pressure_rating_raw": "40", "source_refs": [deepcopy(ref)], **reviewed}
                       for i in range(4)],
            "ambiguities": [{"id": kind, "kind": kind, "description": "synthetic fixture", "status": "resolved",
                             "affected_ids": [], "source_refs": [deepcopy(ref)], "resolution": "test only",
                             "reviewed_by": reviewed["reviewed_by"], "reviewed_at": STAMP}
                            for kind in ("crossing", "detail_mapping")],
        }
        mapping = {"schema_version": "1.0", "units": {"length_m": "m"}, "node_mapping": {"junction": "JOINT"}, **reviewed}
        manifest_path = self.workspace / "manifest.json"
        mapping_path = self.workspace / "mapping.json"
        self.write(manifest_path, manifest)
        self.write(mapping_path, mapping)
        return manifest_path, mapping_path, source

    def test_environment_inspection_does_not_call_runner_or_claim_license(self):
        result = self.service.inspect_environment()
        self.assertEqual(result["execution_status"], "SUCCESS")
        self.assertFalse(result["com_activated"])
        self.assertFalse(result["automation_verified"])
        self.assertEqual(result["license_status"], "NOT_ASSESSED")
        self.assertEqual(set(result["libraries"]), {"win32com", "pythoncom", "mcp"})
        self.assertEqual(self.runner.calls, [])

    def test_inventory_secrets_are_redacted(self):
        inventory = self.workspace / "work/gap_inventory/installed_gap_inventory.json"
        self.write(inventory, {"mode": "unit fixture", "api_key": "not-for-output", "nested": {"password": "also-hidden"}})
        result = self.service.inspect_environment()
        self.assertEqual(result["recorded_inventory"]["api_key"], "[REDACTED]")
        self.assertNotIn("not-for-output", json.dumps(result))
        self.assertNotIn("also-hidden", json.dumps(result))

    def test_probe_is_an_exact_readonly_job(self):
        self.assertEqual(self.service.probe()["execution_status"], "SUCCESS")
        self.assertEqual(self.runner.calls, [({"operation": "probe"}, self.workspace.resolve(), 60)])
        self.assertEqual(list(self.service.plan_dir.iterdir()), [])

    def test_prepare_creates_immutable_hash_bound_plan_without_running_gap(self):
        result = self.service.prepare_smoke_plan()
        path = Path(result["plan_path"])
        self.assertTrue(path.is_relative_to(self.workspace))
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), result["plan_sha256"])
        self.assertFalse(Path(result["target_model_path"]).exists())
        self.assertEqual(result["authorization_status"], "CONFIRMATION_REQUIRED")
        self.assertEqual(self.runner.calls, [])

    def test_execute_without_receipt_cannot_reach_runner(self):
        plan = self.service.prepare_smoke_plan()
        result = self.service.execute_plan(plan["plan_id"], "")
        self.assertEqual(result["execution_status"], "CONFIRMATION_REQUIRED")
        self.assertEqual(self.runner.calls, [])

    def test_approval_is_bound_to_plan_hash_and_reused(self):
        plan, receipt = self.approved_smoke()
        saved = json.loads(Path(receipt["receipt_path"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["plan_sha256"], plan["plan_sha256"])
        self.assertIn("engineering", saved["authorization_scope"])
        again = self.service.approve_plan(plan["plan_id"], "Different local operator")
        self.assertEqual(again["approval_id"], receipt["approval_id"])
        self.assertTrue(again["idempotent_reuse"])
        self.assertEqual(self.runner.calls, [])

    def test_blank_approver_is_rejected(self):
        plan = self.service.prepare_smoke_plan()
        self.assertEqual(self.service.approve_plan(plan["plan_id"], " ")["execution_status"], "ERROR")
        self.assertEqual(list(self.service.receipt_dir.iterdir()), [])

    def test_receipt_for_another_plan_is_rejected(self):
        first, receipt = self.approved_smoke()
        second = self.service.prepare_smoke_plan()
        self.assertNotEqual(first["plan_id"], second["plan_id"])
        result = self.service.execute_plan(second["plan_id"], receipt["approval_id"])
        self.assertEqual(result["execution_status"], "CONFIRMATION_REQUIRED")
        self.assertEqual(self.runner.calls, [])

    def test_changed_plan_or_receipt_cannot_be_executed(self):
        plan, receipt = self.approved_smoke()
        path = Path(plan["plan_path"])
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        self.assertEqual(self.service.execute_plan(plan["plan_id"], receipt["approval_id"])["execution_status"], "ERROR")
        path.write_bytes(original)
        receipt_path = Path(receipt["receipt_path"])
        receipt_path.write_bytes(receipt_path.read_bytes() + b" ")
        self.assertEqual(self.service.execute_plan(plan["plan_id"], receipt["approval_id"])["execution_status"], "CONFIRMATION_REQUIRED")
        self.assertEqual(self.runner.calls, [])

    def test_completed_result_is_reused_after_service_restart(self):
        plan, receipt = self.approved_smoke()
        first = self.service.execute_plan(plan["plan_id"], receipt["approval_id"])
        self.assertEqual(first["execution_status"], "SUCCESS")
        second_runner = FakeRunner()
        restarted = GapService(self.workspace, runner=second_runner)
        repeated = restarted.execute_plan(plan["plan_id"], receipt["approval_id"])
        self.assertTrue(repeated["idempotent_reuse"])
        self.assertEqual(repeated["trace_id"], first["trace_id"])
        self.assertEqual(repeated["snapshot"], first["snapshot"])
        self.assertEqual(len(self.runner.calls), 1)
        self.assertEqual(second_runner.calls, [])

    def test_concurrent_execute_is_claimed_once(self):
        plan, receipt = self.approved_smoke()
        entered, finish = threading.Event(), threading.Event()

        def waiting(job, workspace, timeout_seconds):
            entered.set()
            if not finish.wait(3):
                raise TimeoutError()
            return self.runner(job, workspace, timeout_seconds)

        self.service.runner = waiting
        other = GapService(self.workspace, runner=self.runner)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.service.execute_plan, plan["plan_id"], receipt["approval_id"])
            try:
                self.assertTrue(entered.wait(3))
                blocked = other.execute_plan(plan["plan_id"], receipt["approval_id"])
                self.assertEqual(blocked["execution_status"], "NOT_READY")
                self.assertEqual(blocked["plan_state"], "RUNNING")
            finally:
                finish.set()
            self.assertEqual(future.result(timeout=3)["execution_status"], "SUCCESS")
        self.assertEqual(len(self.runner.calls), 1)

    def test_timeout_is_not_retried_and_blocks_new_writes(self):
        self.runner.response = {"execution_status": "TIMEOUT", "trace_id": "timeout-trace", "message": "Injected timeout"}
        plan, receipt = self.approved_smoke()
        first = self.service.execute_plan(plan["plan_id"], receipt["approval_id"])
        self.assertEqual(first["plan_state"], "UNKNOWN")
        self.assertTrue(first["manual_review_required"])
        repeated = self.service.execute_plan(plan["plan_id"], receipt["approval_id"])
        self.assertEqual(repeated["execution_status"], "TIMEOUT")
        self.assertTrue(repeated["idempotent_reuse"])
        second, second_receipt = self.approved_smoke()
        blocked = self.service.execute_plan(second["plan_id"], second_receipt["approval_id"])
        self.assertEqual(blocked["execution_status"], "NOT_READY")
        self.assertEqual(blocked["blocking_plan_id"], plan["plan_id"])
        self.assertEqual(len(self.runner.calls), 1)

    def test_confirmed_license_unavailable_is_a_terminal_refusal_without_unknown_state(self):
        self.runner.response = {"execution_status": "LICENSE_UNAVAILABLE", "trace_id": "license-refusal",
                                "message": "Observed native error 22: No OpenServer license available"}
        plan, receipt = self.approved_smoke()
        result = self.service.execute_plan(plan["plan_id"], receipt["approval_id"])
        self.assertEqual(result["execution_status"], "LICENSE_UNAVAILABLE")
        self.assertEqual(result["plan_state"], "COMPLETED")
        self.assertNotIn("manual_review_required", result)
        self.runner.response = {"execution_status": "SUCCESS", "trace_id": "new-attempt", "message": "Injected result"}
        next_plan, next_receipt = self.approved_smoke()
        next_result = self.service.execute_plan(next_plan["plan_id"], next_receipt["approval_id"])
        self.assertEqual(next_result["execution_status"], "SUCCESS")
        self.assertEqual(len(self.runner.calls), 2)

    def test_acknowledgement_requires_reviewer_and_investigation_note(self):
        self.runner.response = {"execution_status": "TIMEOUT", "trace_id": "timeout", "message": "Injected timeout"}
        plan, receipt = self.approved_smoke()
        self.service.execute_plan(plan["plan_id"], receipt["approval_id"])
        for reviewer, note in (("", "Investigated"), ("Operator", " "), (None, "Investigated")):
            with self.subTest(reviewer=reviewer, note=note):
                result = self.service.acknowledge_unknown(plan["plan_id"], reviewer, note)
                self.assertEqual(result["execution_status"], "ERROR")
        repeated = self.service.execute_plan(plan["plan_id"], receipt["approval_id"])
        self.assertEqual(repeated["plan_state"], "UNKNOWN")
        self.assertEqual(len(self.runner.calls), 1)

    def test_acknowledgement_preserves_prior_result_allows_new_plan_and_never_retries_old(self):
        self.runner.response = {"execution_status": "API_ERROR", "trace_id": "prior-failure", "message": "Injected uncertain failure"}
        plan, receipt = self.approved_smoke()
        prior = self.service.execute_plan(plan["plan_id"], receipt["approval_id"])
        acknowledged = self.service.acknowledge_unknown(plan["plan_id"], "Local operator", "Inspected worker and GAP; no active job remains.")
        self.assertEqual(acknowledged["execution_status"], "SUCCESS")
        self.assertEqual(acknowledged["plan_state"], "RESOLVED_NO_RETRY")
        artifact = json.loads(Path(acknowledged["acknowledgement_path"]).read_text(encoding="utf-8"))
        self.assertEqual(artifact["prior_execution_result"], prior)
        repeated = self.service.execute_plan(plan["plan_id"], receipt["approval_id"])
        self.assertEqual(repeated["execution_status"], "API_ERROR")
        self.assertEqual(repeated["plan_state"], "RESOLVED_NO_RETRY")
        self.assertTrue(repeated["idempotent_reuse"])
        self.assertEqual(repeated["prior_execution_result"], prior)
        self.assertEqual(len(self.runner.calls), 1)
        self.runner.response = {"execution_status": "SUCCESS", "trace_id": "new-plan-result", "message": "Injected result"}
        next_plan, next_receipt = self.approved_smoke()
        self.assertEqual(self.service.execute_plan(next_plan["plan_id"], next_receipt["approval_id"])["execution_status"], "SUCCESS")
        self.assertEqual(len(self.runner.calls), 2)

    def test_runner_exception_is_uncertain_and_does_not_leak_exception_secret(self):
        def failing(*args, **kwargs):
            raise RuntimeError("password=DO_NOT_LOG_THIS")

        self.service.runner = failing
        plan, receipt = self.approved_smoke()
        result = self.service.execute_plan(plan["plan_id"], receipt["approval_id"])
        self.assertEqual(result["plan_state"], "UNKNOWN")
        self.assertEqual(result["execution_status"], "ERROR")
        self.assertNotIn("DO_NOT_LOG_THIS", json.dumps(result))
        self.assertNotIn("DO_NOT_LOG_THIS", self.service.journal_path.read_text(encoding="utf-8"))

    def test_process_interrupt_leaves_a_persistent_running_block(self):
        def interrupted(*args, **kwargs):
            raise KeyboardInterrupt()

        self.service.runner = interrupted
        plan, receipt = self.approved_smoke()
        with self.assertRaises(KeyboardInterrupt):
            self.service.execute_plan(plan["plan_id"], receipt["approval_id"])
        restarted = GapService(self.workspace, runner=self.runner)
        result = restarted.execute_plan(plan["plan_id"], receipt["approval_id"])
        self.assertEqual(result["execution_status"], "NOT_READY")
        self.assertEqual(result["plan_state"], "RUNNING")
        self.assertEqual(self.runner.calls, [])

    def test_existing_reserved_model_is_never_overwritten(self):
        plan, receipt = self.approved_smoke()
        target = Path(plan["target_model_path"])
        target.write_bytes(b"pre-existing model")
        result = self.service.execute_plan(plan["plan_id"], receipt["approval_id"])
        self.assertEqual(result["execution_status"], "NOT_READY")
        self.assertEqual(target.read_bytes(), b"pre-existing model")
        self.assertEqual(self.runner.calls, [])

    def test_parent_traversal_and_external_requested_paths_are_rejected(self):
        outside = self.base / "external.gap"
        outside.write_bytes(b"external model")
        for path in ("../external.gap", str(outside)):
            with self.subTest(path=path):
                self.assertEqual(self.service.export_model(path)["execution_status"], "ERROR")
                self.assertEqual(self.service.prepare_manifest_plan(path)["execution_status"], "ERROR")
        self.assertEqual(self.runner.calls, [])

    def test_symlink_escape_is_rejected_after_resolution(self):
        outside = self.base / "external.gap"
        outside.write_bytes(b"external model")
        link = self.workspace / "linked.gap"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Creating symlinks is not permitted by this Windows account.")
        self.assertEqual(self.service.export_model(str(link))["execution_status"], "ERROR")
        self.assertEqual(self.runner.calls, [])

    def test_pending_manifest_is_not_prepared_or_implicitly_confirmed(self):
        manifest_path, mapping_path, _ = self.ready_inputs()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["selection"].update(review_status="pending", reviewed_by=None, reviewed_at=None)
        self.write(manifest_path, manifest)
        original = manifest_path.read_bytes()
        result = self.service.prepare_manifest_plan(str(manifest_path), str(mapping_path))
        self.assertEqual(result["execution_status"], "NOT_READY", result)
        self.assertFalse(result["validation"]["ready_for_transfer"])
        self.assertEqual(manifest_path.read_bytes(), original)
        self.assertEqual(list(self.service.plan_dir.iterdir()), [])
        self.assertEqual(self.runner.calls, [])

    def test_mapping_must_be_present_and_reviewed(self):
        manifest_path, mapping_path, _ = self.ready_inputs()
        result = self.service.prepare_manifest_plan(str(manifest_path))
        self.assertEqual(result["execution_status"], "NOT_READY", result)
        self.assertEqual(result["details"]["code"], "MAPPING_REQUIRED")
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        mapping["reviewed_by"] = None
        self.write(mapping_path, mapping)
        result = self.service.prepare_manifest_plan(str(manifest_path), str(mapping_path))
        self.assertEqual(result["execution_status"], "NOT_READY")
        self.assertEqual(result["details"]["code"], "MAPPING_NOT_CONFIRMED")

    def test_manifest_mapping_and_source_bytes_are_frozen_to_approval(self):
        manifest_path, mapping_path, source_path = self.ready_inputs()
        for path in (manifest_path, mapping_path, source_path):
            with self.subTest(path=path.name):
                plan = self.service.prepare_manifest_plan(str(manifest_path), str(mapping_path))
                self.assertEqual(plan["execution_status"], "SUCCESS", plan)
                receipt = self.service.approve_plan(plan["plan_id"], "Local operator")
                self.assertEqual(receipt["execution_status"], "SUCCESS", receipt)
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b" ")
                    result = self.service.execute_plan(plan["plan_id"], receipt["approval_id"])
                    self.assertEqual(result["execution_status"], "ERROR", result)
                finally:
                    path.write_bytes(original)
        self.assertEqual(self.runner.calls, [])

    def test_approved_mapping_can_still_be_refused_by_backend_without_partial_success(self):
        manifest_path, mapping_path, _ = self.ready_inputs()
        self.runner.response = {"execution_status": "UNSUPPORTED_MAPPING", "trace_id": "mapping-refusal", "message": "Installed GAP mapping not supported"}
        plan = self.service.prepare_manifest_plan(str(manifest_path), str(mapping_path))
        self.assertEqual(plan["execution_status"], "SUCCESS", plan)
        receipt = self.service.approve_plan(plan["plan_id"], "Local operator")
        result = self.service.execute_plan(plan["plan_id"], receipt["approval_id"])
        self.assertEqual(result["execution_status"], "UNSUPPORTED_MAPPING")
        self.assertEqual(result["plan_state"], "COMPLETED")
        self.assertEqual(self.runner.calls[0][0]["operation"], "build")
        self.assertIn("manifest", self.runner.calls[0][0])
        self.assertIn("mapping", self.runner.calls[0][0])

    def test_snapshot_and_normalized_export_are_saved_separately_without_invention(self):
        target = self.workspace / "saved.gap"
        target.write_bytes(b"unit-test saved model placeholder")
        self.runner.response["normalized_export"] = {"model": {"evidence": "real"}, "adapter_read_value": 17}
        result = self.service.export_model(str(target))
        self.assertEqual(result["execution_status"], "SUCCESS")
        self.assertEqual(json.loads(Path(result["snapshot_path"]).read_text(encoding="utf-8")), self.runner.response["snapshot"])
        self.assertEqual(json.loads(Path(result["normalized_export_path"]).read_text(encoding="utf-8")), self.runner.response["normalized_export"])
        self.assertNotEqual(result["snapshot_path"], result["normalized_export_path"])
        self.assertNotIn("nodes", result["normalized_export"])

    def test_raw_snapshot_does_not_generate_a_fabricated_normalized_export(self):
        target = self.workspace / "saved.gap"
        target.write_bytes(b"unit-test model placeholder")
        result = self.service.export_model(str(target))
        self.assertIn("snapshot_path", result)
        self.assertNotIn("normalized_export", result)
        self.assertNotIn("normalized_export_path", result)

    def test_success_without_any_export_evidence_is_rejected(self):
        target = self.workspace / "saved.gap"
        target.write_bytes(b"unit-test model placeholder")
        self.runner.response = {"execution_status": "SUCCESS", "trace_id": "empty-export", "message": "No data"}
        result = self.service.export_model(str(target))
        self.assertEqual(result["execution_status"], "ERROR")
        self.assertEqual(result["details"]["code"], "MISSING_EXPORT_EVIDENCE")
        self.assertEqual(list(self.service.artifact_dir.iterdir()), [])

    def test_ledger_and_journal_are_inside_workspace_and_traceable(self):
        plan, receipt = self.approved_smoke()
        result = self.service.execute_plan(plan["plan_id"], receipt["approval_id"])
        self.assertTrue(self.service.ledger_path.is_relative_to(self.workspace))
        self.assertTrue(self.service.journal_path.is_relative_to(self.workspace))
        records = [json.loads(line) for line in self.service.journal_path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(all("timestamp" in item and "trace_id" in item for item in records))
        self.assertIn("backend_response", {item["event"] for item in records})
        with closing(sqlite3.connect(self.service.ledger_path)) as db:
            state = db.execute("SELECT state FROM plans WHERE plan_id=?", (plan["plan_id"],)).fetchone()[0]
        self.assertEqual(state, "COMPLETED")
        self.assertEqual(result["plan_state"], state)


if __name__ == "__main__":
    unittest.main()
