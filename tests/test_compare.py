"""Contract tests for independent manifest/export comparison."""

from copy import deepcopy
import json
import unittest

from gap_manifest.compare import compare_export


STAMP = "2026-11-22T12:00:00+03:00"
PARAMETERS = {
    "length_m": 100.0,
    "outer_diameter_mm": 159.0,
    "wall_thickness_mm": 8.0,
    "inner_diameter_mm": 143.0,
}


def fixture():
    """Small comparison fixture, not a claim to meet project selection minima."""
    source_ref = {
        "source_id": "scheme", "page": 1, "bbox": [10, 20, 30, 40],
        "raw_text": "Узел", "method": "visual", "fields": ["original_label"],
    }

    def common(oid, label):
        return {
            "id": oid, "original_label": label, "review_status": "confirmed",
            "reviewed_by": "Инженер", "reviewed_at": STAMP,
            "source_refs": [deepcopy(source_ref)],
        }

    manifest = {
        "schema_version": "1.0", "case_id": "contract-unit-case",
        "sources": [{"id": "scheme", "path": "../scheme.pdf", "sha256": "a" * 64,
                     "pages": [{"number": 1, "width_pt": 400, "height_pt": 500}]}],
        "selection": {"id": "fragment", "review_status": "confirmed", "reviewed_by": "Инженер",
                      "reviewed_at": STAMP, "regions": [], "required_minima": {}, "notes": "Unit fixture"},
        "nodes": [
            {**common("S1", "Куст 1"), "kind": "source", "context": "тест", "position": [20, 30]},
            {**common("J1", "Узел 1"), "kind": "junction", "context": "тест", "position": [40, 30]},
            {**common("B1", "Граница"), "kind": "boundary", "context": "тест", "position": [60, 30],
             "boundary_reason": "Фрагмент заканчивается"},
        ],
        "pipes": [
            {**common("P1", "Труба 1"), "from_node": "S1", "to_node": "J1", "role": "pipeline",
             "line_class": "Нефтесбор", "parameters": deepcopy(PARAMETERS), "active_state": "active"},
            {**common("P2", "Труба 2"), "from_node": "J1", "to_node": "B1", "role": "pipeline",
             "line_class": "Нефтесбор", "parameters": deepcopy(PARAMETERS), "active_state": "inactive"},
        ],
        "valves": [{**common("V1", "Задвижка 1"), "pipe_id": "P2", "physical_state": "closed",
                    "size_dn_mm": 150, "pressure_rating_raw": "40"}],
        "ambiguities": [],
    }
    export = {
        "schema_version": "1.0", "case_id": manifest["case_id"],
        "model": {"path": "model.gap", "software_version": "test-version", "exported_at": STAMP,
                  "evidence": "real"},
        "nodes": [{key: node[key] for key in ("id", "kind", "original_label")} for node in manifest["nodes"]],
        "pipes": [{key: deepcopy(pipe[key]) for key in ("id", "from_node", "to_node", "parameters", "active_state")}
                  for pipe in manifest["pipes"]],
        "valves": [{key: deepcopy(valve[key]) for key in ("id", "pipe_id", "physical_state", "size_dn_mm", "pressure_rating_raw")}
                   for valve in manifest["valves"]],
    }
    tolerances = {
        parameter: {"unit": "m" if parameter == "length_m" else "mm", "absolute": 0.25,
                    "approved_by": "Преподаватель", "approved_at": STAMP, "source": "Unit test approval"}
        for parameter in PARAMETERS
    }
    return manifest, export, tolerances


class CompareExportTests(unittest.TestCase):
    def setUp(self):
        self.manifest, self.export, self.tolerances = fixture()

    def compare(self):
        return compare_export(self.manifest, self.export, self.tolerances)

    def codes(self, result):
        return {issue["code"] for issue in result["issues"]}

    def test_confirmed_real_complete_export_can_pass(self):
        result = self.compare()
        self.assertEqual(result["execution_status"], "SUCCESS")
        self.assertEqual(result["assessment_status"], "OK")
        self.assertEqual(result["structural_status"], "OK")
        self.assertEqual(result["numeric_status"], "OK")
        self.assertEqual(result["summary"]["numeric"],
                         {"expected": 9, "assessed": 9, "passed": 9, "failed": 0, "not_assessed": 0})
        self.assertEqual(result["issues"], [])

    def test_input_documents_are_not_mutated(self):
        original = deepcopy((self.manifest, self.export, self.tolerances))
        self.compare()
        self.assertEqual(original, (self.manifest, self.export, self.tolerances))

    def test_pipe_end_order_is_irrelevant(self):
        for pipe in self.export["pipes"]:
            pipe["from_node"], pipe["to_node"] = pipe["to_node"], pipe["from_node"]
        self.assertEqual(self.compare()["assessment_status"], "OK")

    def test_self_loop_cannot_hide_in_a_set_of_ends(self):
        self.export["pipes"][0]["to_node"] = "S1"
        result = self.compare()
        self.assertIn("CONNECTION_MISMATCH", self.codes(result))
        self.assertEqual(result["assessment_status"], "OUT_OF_TOLERANCE")

    def test_no_tolerance_does_not_assume_exact_numeric_acceptance(self):
        result = compare_export(self.manifest, self.export)
        self.assertEqual(result["execution_status"], "SUCCESS")
        self.assertEqual(result["structural_status"], "OK")
        self.assertEqual(result["numeric_status"], "NOT_ASSESSED")
        self.assertEqual(result["assessment_status"], "NOT_ASSESSED")
        self.assertEqual(result["summary"]["numeric"]["not_assessed"], 8)
        self.assertIn("NO_APPROVED_TOLERANCE", self.codes(result))

    def test_unknown_expected_number_is_not_a_pass_even_with_tolerance(self):
        self.manifest["pipes"][0]["parameters"]["length_m"] = None
        self.export["pipes"][0]["parameters"]["length_m"] = None
        result = self.compare()
        self.assertEqual(result["numeric_status"], "NOT_ASSESSED")
        self.assertEqual(result["assessment_status"], "NOT_ASSESSED")
        self.assertEqual(result["summary"]["numeric"]["passed"], 8)
        self.assertIn("UNKNOWN_EXPECTED_VALUE", self.codes(result))

    def test_unknown_nominal_dn_cannot_pass(self):
        self.manifest["valves"][0]["size_dn_mm"] = None
        self.export["valves"][0]["size_dn_mm"] = None
        result = self.compare()
        self.assertEqual(result["assessment_status"], "NOT_ASSESSED")
        self.assertIn("UNKNOWN_NOMINAL_SIZE", self.codes(result))

    def test_exact_nominal_dn_is_not_a_continuous_tolerance(self):
        self.export["valves"][0]["size_dn_mm"] = 150.01
        result = self.compare()
        self.assertEqual(result["assessment_status"], "OUT_OF_TOLERANCE")
        self.assertEqual(result["summary"]["numeric"]["failed"], 1)
        self.assertTrue(any(i["code"] == "FIELD_MISMATCH" and "size_dn_mm" in i["message"]
                            for i in result["issues"]))

    def test_missing_actual_number_is_not_assessed(self):
        self.export["pipes"][0]["parameters"]["length_m"] = None
        result = self.compare()
        self.assertEqual(result["assessment_status"], "NOT_ASSESSED")
        self.assertIn("MISSING_NUMERIC_VALUE", self.codes(result))

    def test_tolerance_boundary_is_inclusive_and_units_are_reported(self):
        self.export["pipes"][0]["parameters"]["length_m"] += 0.25
        self.assertEqual(self.compare()["assessment_status"], "OK")
        self.export["pipes"][0]["parameters"]["length_m"] += 0.25
        result = self.compare()
        self.assertEqual(result["execution_status"], "SUCCESS")
        self.assertEqual(result["numeric_status"], "OUT_OF_TOLERANCE")
        issue = next(i for i in result["issues"] if i["code"] == "NUMERIC_OUT_OF_TOLERANCE")
        self.assertIn("0.5 m", issue["message"])
        self.assertEqual(issue["source_refs"], self.manifest["pipes"][0]["source_refs"])

    def test_synthetic_export_cannot_pass_and_still_reports_differences(self):
        self.export["model"]["evidence"] = "synthetic"
        self.export["valves"][0]["physical_state"] = "open"
        result = self.compare()
        self.assertEqual(result["assessment_status"], "NOT_ASSESSED")
        self.assertEqual(result["structural_status"], "OUT_OF_TOLERANCE")
        self.assertIn("FIELD_MISMATCH", self.codes(result))
        self.assertIn("REAL_EXPORT_REQUIRED", self.codes(result))

    def test_pending_selection_or_entity_cannot_pass(self):
        for location in ("selection", "entity"):
            with self.subTest(location=location):
                manifest, export, tolerances = fixture()
                target = manifest["selection"] if location == "selection" else manifest["nodes"][0]
                target.update(review_status="pending", reviewed_by=None, reviewed_at=None)
                result = compare_export(manifest, export, tolerances)
                self.assertEqual(result["execution_status"], "SUCCESS")
                self.assertEqual(result["assessment_status"], "NOT_ASSESSED")

    def test_confirmed_review_needs_reviewer_and_valid_iso_timestamp(self):
        for value in (None, "", "2026-02-30T00:00:00Z", "2026-11-22", "yesterday"):
            with self.subTest(value=value):
                manifest, export, tolerances = fixture()
                manifest["nodes"][0]["reviewed_at"] = value
                result = compare_export(manifest, export, tolerances)
                self.assertEqual(result["execution_status"], "ERROR")
                self.assertEqual(result["assessment_status"], "NOT_ASSESSED")
                self.assertIn("INVALID_REVIEW_METADATA", self.codes(result))
        self.manifest["selection"]["reviewed_by"] = " "
        self.assertEqual(self.compare()["assessment_status"], "NOT_ASSESSED")

    def test_open_and_unreviewed_resolutions_cannot_pass(self):
        self.manifest["ambiguities"] = [{"id": "A1", "status": "open", "description": "Crossing?",
                                          "source_refs": [], "resolution": None,
                                          "reviewed_by": None, "reviewed_at": None}]
        result = self.compare()
        self.assertEqual(result["assessment_status"], "NOT_ASSESSED")
        self.assertIn("OPEN_AMBIGUITY", self.codes(result))
        self.manifest["ambiguities"][0]["status"] = "resolved"
        result = self.compare()
        self.assertEqual(result["execution_status"], "ERROR")
        self.assertIn("INVALID_RESOLUTION", self.codes(result))
        self.manifest["ambiguities"][0].update(resolution="Not connected", reviewed_by="Инженер", reviewed_at=STAMP)
        self.assertEqual(self.compare()["assessment_status"], "OK")

    def test_missing_extra_and_broken_references_are_all_visible(self):
        self.export["nodes"].pop(0)
        self.export["nodes"].append({"id": "EXTRA", "kind": "source", "original_label": "Extra"})
        result = self.compare()
        self.assertEqual(result["execution_status"], "ERROR")
        self.assertTrue({"MISSING_OBJECT", "EXTRA_OBJECT", "BROKEN_REFERENCE"}.issubset(self.codes(result)))
        counts = result["summary"]["counts"]["nodes"]
        self.assertEqual((counts["expected"], counts["actual"], counts["missing"], counts["extra"]), (3, 3, 1, 1))

    def test_duplicate_ids_are_not_silently_collapsed_or_paired(self):
        duplicate = deepcopy(self.export["pipes"][0])
        duplicate["parameters"]["length_m"] = 999
        self.export["pipes"].append(duplicate)
        result = self.compare()
        counts = result["summary"]["counts"]["pipes"]
        self.assertEqual((counts["expected"], counts["actual"], counts["matched"], counts["ambiguous"]), (2, 3, 1, 1))
        self.assertEqual(result["summary"]["numeric"]["not_assessed"], 4)
        self.assertTrue({"DUPLICATE_ID", "AMBIGUOUS_OBJECT_ID"}.issubset(self.codes(result)))
        self.assertFalse(any(i["code"] == "NUMERIC_OUT_OF_TOLERANCE" for i in result["issues"]))
        self.assertEqual(result["assessment_status"], "NOT_ASSESSED")

    def test_duplicate_ids_in_manifest_and_across_collections_are_invalid(self):
        self.manifest["nodes"].append(deepcopy(self.manifest["nodes"][0]))
        self.manifest["valves"][0]["id"] = "P1"
        result = self.compare()
        duplicates = [i for i in result["issues"] if i["code"] == "DUPLICATE_ID"]
        self.assertEqual({i["object_id"] for i in duplicates}, {"S1", "P1"})
        self.assertEqual(result["execution_status"], "ERROR")

    def test_non_finite_and_bool_are_rejected_everywhere_numeric(self):
        for value in (float("nan"), float("inf"), -float("inf"), True):
            for where in ("manifest", "export", "valve", "tolerance"):
                with self.subTest(value=value, where=where):
                    manifest, export, tolerances = fixture()
                    if where == "manifest":
                        manifest["pipes"][0]["parameters"]["length_m"] = value
                    elif where == "export":
                        export["pipes"][0]["parameters"]["length_m"] = value
                    elif where == "valve":
                        export["valves"][0]["size_dn_mm"] = value
                    else:
                        tolerances["length_m"]["absolute"] = value
                    result = compare_export(manifest, export, tolerances)
                    self.assertEqual(result["execution_status"], "ERROR")
                    self.assertEqual(result["assessment_status"], "NOT_ASSESSED")

    def test_unapproved_negative_or_wrong_unit_tolerance_is_rejected(self):
        for key, value in (("unit", "ft"), ("absolute", -0.1), ("approved_by", ""),
                           ("approved_at", "today"), ("source", None)):
            with self.subTest(key=key):
                manifest, export, tolerances = fixture()
                tolerances["length_m"][key] = value
                result = compare_export(manifest, export, tolerances)
                self.assertEqual(result["execution_status"], "ERROR")
                self.assertEqual(result["numeric_status"], "NOT_ASSESSED")
                self.assertIn("INVALID_TOLERANCE", self.codes(result))

    def test_required_field_absence_is_distinct_from_explicit_null(self):
        self.manifest["valves"][0]["pressure_rating_raw"] = None
        del self.export["valves"][0]["pressure_rating_raw"]
        result = self.compare()
        self.assertEqual(result["execution_status"], "ERROR")
        self.assertIn("MISSING_FIELD", self.codes(result))

    def test_exact_labels_states_and_raw_rating_are_compared(self):
        self.export["nodes"][0]["original_label"] = "Куст 2"
        self.export["pipes"][1]["active_state"] = "active"
        self.export["valves"][0]["pressure_rating_raw"] = "40 bar"
        result = self.compare()
        messages = " ".join(i["message"] for i in result["issues"])
        self.assertIn("original_label", messages)
        self.assertIn("active_state", messages)
        self.assertIn("pressure_rating_raw", messages)
        self.assertEqual(result["assessment_status"], "OUT_OF_TOLERANCE")

    def test_wrong_case_is_not_accepted(self):
        self.export["case_id"] = "other-case"
        result = self.compare()
        self.assertEqual(result["assessment_status"], "OUT_OF_TOLERANCE")
        self.assertIn("CASE_ID_MISMATCH", self.codes(result))

    def test_invalid_top_level_and_collections_return_issues_without_crashing(self):
        for manifest, export, tolerance in ((None, None, None), ([], {}, []), ({}, "bad", 4)):
            with self.subTest(manifest=manifest, export=export):
                result = compare_export(manifest, export, tolerance)
                self.assertEqual(result["execution_status"], "ERROR")
                self.assertEqual(result["assessment_status"], "NOT_ASSESSED")
                self.assertTrue(result["issues"])
        self.export["nodes"].append("not an object")
        result = self.compare()
        self.assertEqual(result["summary"]["counts"]["nodes"]["actual"], 4)
        self.assertIn("INVALID_OBJECT", self.codes(result))

    def test_issue_schema_and_summary_totals_are_consistent(self):
        self.export["model"]["evidence"] = "synthetic"
        self.export["pipes"][0]["from_node"] = "BAD"
        result = self.compare()
        for issue in result["issues"]:
            self.assertEqual(set(issue), {"code", "severity", "object_id", "message", "source_refs"})
            self.assertIn(issue["severity"], ("error", "warning", "info"))
            self.assertIsInstance(issue["source_refs"], list)
        totals = result["summary"]["issues"]
        self.assertEqual(totals["total"], len(result["issues"]))
        self.assertEqual(totals["total"], sum(totals[key] for key in ("error", "warning", "info")))
        self.assertIsInstance(json.dumps(result, ensure_ascii=False, allow_nan=False), str)


if __name__ == "__main__":
    unittest.main()
