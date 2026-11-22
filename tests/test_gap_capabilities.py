"""Readiness is never inferred from synthetic data or official tag listings."""

from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from gap_integration.capabilities import build_readiness


def synthetic_manifest():
    return {
        "schema_version": "1.0", "case_id": "synthetic-capability-test", "evidence": "synthetic",
        "nodes": [{"id": "J1", "kind": "junction"}, {"id": "S1", "kind": "source"},
                  {"id": "B1", "kind": "boundary"}],
        "pipes": [{"id": "P1", "role": "pipeline", "parameters": {"outer_diameter_mm": 159, "wall_thickness_mm": 8}},
                  {"id": "P2", "role": "internal_connector", "parameters": {}}],
        "valves": [{"id": "V1", "pipe_id": "P1", "physical_state": "closed", "size_dn_mm": 150,
                    "pressure_rating_raw": "40"}],
    }


def validation(manifest, ready=True, issues=None):
    return {"schema_version": "1.0", "case_id": manifest["case_id"], "execution_status": "SUCCESS",
            "ready_for_transfer": ready, "issues": issues or []}


class CapabilityReadinessTests(unittest.TestCase):
    def setUp(self):
        self.manifest = synthetic_manifest()

    @staticmethod
    def codes(result):
        return {row["code"] for row in result["blockers"]}

    def test_no_validation_leaves_engineering_not_assessed(self):
        result = build_readiness(self.manifest)
        self.assertEqual(result["execution_status"], "SUCCESS")
        self.assertEqual(result["engineering_validation"]["status"], "NOT_ASSESSED")
        self.assertIsNone(result["engineering_validation"]["ready_for_transfer"])
        self.assertIn("ENGINEERING_VALIDATION_NOT_ASSESSED", self.codes(result))

    def test_even_approved_synthetic_fixture_cannot_establish_production_readiness(self):
        result = build_readiness(self.manifest, validation(self.manifest))
        self.assertEqual(result["engineering_validation"]["status"], "READY")
        self.assertTrue(result["engineering_validation"]["ready_for_transfer"])
        self.assertFalse(result["ready_for_production_build"])
        self.assertEqual(result["backend_production_status"], "UNSUPPORTED_MAPPING")
        self.assertEqual(result["runtime_verification"], "NOT_ASSESSED")
        self.assertEqual(result["assessment_status"], "NOT_ASSESSED")
        self.assertIn("CAPABILITY_PRODUCTION_BUILD", self.codes(result))

    def test_validation_issue_summary_is_kept_separate_from_api_capabilities(self):
        supplied = validation(self.manifest, False, [{"severity": "error", "code": "SOURCE_CHANGED"},
                                                   {"severity": "warning", "code": "PENDING"},
                                                   {"severity": "info", "code": "NOTE"}])
        result = build_readiness(self.manifest, supplied)
        engineering = result["engineering_validation"]
        self.assertEqual(engineering["status"], "NOT_READY")
        self.assertFalse(engineering["ready_for_transfer"])
        self.assertEqual(engineering["issue_summary"], {"error": 1, "warning": 1, "info": 1, "total": 3})
        self.assertIn("ENGINEERING_DATA_NOT_READY", self.codes(result))
        self.assertEqual(result["backend_production_status"], "UNSUPPORTED_MAPPING")

    def test_required_semantic_types_and_counts_are_explicit(self):
        result = build_readiness(self.manifest)
        self.assertEqual(result["counts"], {"nodes": 3, "pipes": 2, "valves": 1})
        required = {row["semantic_type"]: row for row in result["required_semantic_types"]}
        self.assertEqual(set(required), {"junction", "source", "boundary", "pipe", "valve"})
        self.assertEqual(required["boundary"]["native_candidates"], ["SINK", "SEP"])
        self.assertEqual(required["pipe"]["roles"], {"pipeline": 1, "internal_connector": 1})
        self.assertTrue(all(row["mapping_status"] == "unverified" for row in required.values()))

    def test_matrix_distinguishes_documentation_from_unverified_and_unsupported(self):
        result = build_readiness(self.manifest)
        matrix = {row["id"]: row for row in result["capability_matrix"]}
        self.assertEqual(matrix["create_equipment"]["status"], "documented_unverified")
        self.assertEqual(matrix["valve_state"]["status"], "unverified")
        self.assertEqual(matrix["independent_normalized_export"]["status"], "unsupported")
        self.assertEqual(matrix["production_build"]["status"], "unsupported")
        self.assertTrue(all(row["status"] in ("documented_unverified", "unverified", "unsupported")
                            for row in matrix.values()))
        self.assertTrue(all(row["official_pdf_refs"] for row in matrix.values()))
        for row in matrix.values():
            for ref in row["official_pdf_refs"]:
                self.assertTrue(ref["path"].endswith("OpenServer_Complete.pdf"))
                self.assertTrue(ref["pdf_pages"])
                self.assertTrue(all(isinstance(page, int) and page > 0 for page in ref["pdf_pages"]))

    def test_required_gaps_cover_valves_connections_pipe_units_and_readback(self):
        result = build_readiness(self.manifest)
        codes = self.codes(result)
        self.assertTrue({"CAPABILITY_NATIVE_SOURCE_SINK_VALVE_CONNECTIONS", "CAPABILITY_VALVE_STATE",
                         "CAPABILITY_VALVE_DN_AND_RATING", "CAPABILITY_PIPE_SEGMENT_INDEXING",
                         "CAPABILITY_INDEPENDENT_NORMALIZED_EXPORT", "VALVE_INSERTION_MAPPING_REQUIRED",
                         "PIPE_OD_WALL_MODEL_MODE_REQUIRED", "DOCUMENTATION_IS_NOT_RUNTIME_VERIFICATION"}.issubset(codes))
        unit_row = next(row for row in result["capability_matrix"] if row["id"] == "pipe_units")
        self.assertTrue(unit_row["required_by_manifest"])
        self.assertIn("UNITNAME", " ".join(unit_row["tags"]))
        self.assertIn("V1", next(row for row in result["blockers"] if row["code"] == "VALVE_INSERTION_MAPPING_REQUIRED")["affected_ids"])

    def test_inapplicable_valve_gaps_are_not_required_for_no_valve_fixture(self):
        self.manifest["valves"] = []
        result = build_readiness(self.manifest)
        matrix = {row["id"]: row for row in result["capability_matrix"]}
        self.assertFalse(matrix["valve_state"]["required_by_manifest"])
        self.assertNotIn("VALVE_INSERTION_MAPPING_REQUIRED", self.codes(result))
        self.assertNotIn("CAPABILITY_VALVE_STATE", self.codes(result))
        self.assertFalse(result["ready_for_production_build"])

    def test_empty_fragment_does_not_pass_vacuously(self):
        self.manifest.update(nodes=[], pipes=[], valves=[])
        result = build_readiness(self.manifest, validation(self.manifest))
        self.assertIn("EMPTY_PRODUCTION_FRAGMENT", self.codes(result))
        self.assertIn("CAPABILITY_PRODUCTION_BUILD", self.codes(result))
        self.assertFalse(result["ready_for_production_build"])

    def test_synthetic_candidate_is_reported_without_changing_inputs(self):
        manifest = synthetic_manifest()
        original = deepcopy(manifest)
        result = build_readiness(manifest)
        self.assertEqual(result["case_id"], manifest["case_id"])
        self.assertEqual(result["counts"]["nodes"], len(manifest["nodes"]))
        self.assertFalse(result["ready_for_production_build"])
        self.assertEqual(manifest, original)

    def test_malformed_documents_and_unhashable_values_return_json_errors(self):
        mutations = [None, [], "bad", {},
                     {"schema_version": "1.0", "case_id": [], "nodes": "bad", "pipes": None, "valves": {}},
                     {"schema_version": "1.0", "case_id": "bad", "nodes": [None, {"kind": []}],
                      "pipes": [{"role": {}, "parameters": []}], "valves": ["bad"]}]
        for manifest in mutations:
            with self.subTest(manifest=manifest):
                result = build_readiness(manifest)
                self.assertEqual(result["execution_status"], "ERROR")
                self.assertFalse(result["ready_for_production_build"])
                self.assertTrue(result["issues"])
                json.dumps(result, ensure_ascii=False, allow_nan=False)

    def test_malformed_validation_never_claims_engineering_ready(self):
        for value in ([], "bad", {}, {"ready_for_transfer": "true", "issues": []},
                      {"ready_for_transfer": True, "issues": [None], "execution_status": "SUCCESS"},
                      {"ready_for_transfer": True, "issues": [{"severity": []}], "execution_status": "SUCCESS"},
                      {"ready_for_transfer": True, "issues": [], "execution_status": []}):
            with self.subTest(validation=value):
                result = build_readiness(self.manifest, value)
                self.assertEqual(result["execution_status"], "ERROR")
                self.assertEqual(result["engineering_validation"]["status"], "NOT_ASSESSED")
                self.assertFalse(result["ready_for_production_build"])

    def test_validation_from_another_case_is_not_reused(self):
        supplied = validation(self.manifest)
        supplied["case_id"] = "another-case"
        result = build_readiness(self.manifest, supplied)
        self.assertEqual(result["engineering_validation"]["status"], "NOT_ASSESSED")
        self.assertIn("VALIDATION_CASE_MISMATCH", {row["code"] for row in result["issues"]})

    def test_missing_profile_reports_error_without_losing_safe_production_refusal(self):
        with patch("gap_integration.capabilities._PROFILE", Path("does-not-exist-capability-fixture.json")):
            result = build_readiness(self.manifest)
        self.assertEqual(result["execution_status"], "ERROR")
        self.assertEqual(result["backend_production_status"], "UNSUPPORTED_MAPPING")
        self.assertIn("CAPABILITY_PRODUCTION_BUILD", self.codes(result))
        self.assertFalse(result["ready_for_production_build"])

    def test_offline_and_runtime_steps_are_distinguished(self):
        result = build_readiness(self.manifest)
        steps = result["next_verification_steps"]
        self.assertEqual([row["order"] for row in steps], [1, 2, 3, 4, 5, 6])
        self.assertFalse(steps[0]["requires_live_gap"])
        self.assertFalse(steps[1]["requires_live_gap"])
        self.assertTrue(all(row["requires_live_gap"] for row in steps[2:]))

    def test_report_does_not_call_backend_create_exports_or_mutate_inputs(self):
        supplied = validation(self.manifest)
        original = deepcopy((self.manifest, supplied))
        with patch("gap_integration.backend.run_job", side_effect=AssertionError("COM/backend must not run")):
            result = build_readiness(self.manifest, supplied)
        self.assertEqual((self.manifest, supplied), original)
        self.assertNotIn("normalized_export", result)
        self.assertNotIn("snapshot", result)
        self.assertNotIn("model", result)
        self.assertIsInstance(json.dumps(result, ensure_ascii=False, allow_nan=False), str)


if __name__ == "__main__":
    unittest.main()
