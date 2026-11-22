from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import tempfile
import unittest

from gap_manifest.validation import components, validate_manifest


def fixture():
    approved = {"review_status": "confirmed", "reviewed_by": "test engineer (synthetic fixture)",
                "reviewed_at": "2026-01-01T00:00:00+00:00"}
    ref = {"source_id": "pdf", "page": 1, "bbox": [1, 1, 20, 20], "raw_text": "synthetic fixture",
           "method": "visual", "fields": ["length_m", "outer_diameter_mm", "wall_thickness_mm", "inner_diameter_mm"]}
    nodes = [{"id": f"N{i}", "original_label": f"N{i}", "kind": "source" if i < 2 else "junction",
              "context": "fixture", "position": [5 + i, 5], "source_refs": [deepcopy(ref)], **approved} for i in range(12)]
    pipes = [{"id": f"P{i}", "original_label": f"P{i}", "from_node": f"N{i}", "to_node": f"N{(i+1)%12}",
              "role": "pipeline", "active_state": "active", "line_class": "test",
              "parameters": {"length_m": 10, "outer_diameter_mm": 159, "wall_thickness_mm": 8, "inner_diameter_mm": 143},
              "source_refs": [deepcopy(ref)], **approved} for i in range(12)]
    valves = [{"id": f"V{i}", "original_label": f"V{i}", "pipe_id": f"P{i}", "physical_state": "closed" if i == 0 else "open",
               "size_dn_mm": 150, "pressure_rating_raw": "40", "source_refs": [deepcopy(ref)], **approved} for i in range(4)]
    ambiguities = [{"id": kind, "kind": kind, "description": "synthetic", "status": "resolved", "affected_ids": [],
                    "source_refs": [deepcopy(ref)], "resolution": "test only", "reviewed_by": approved["reviewed_by"],
                    "reviewed_at": approved["reviewed_at"]} for kind in ("crossing", "detail_mapping")]
    return {"schema_version": "1.0", "case_id": "test", "sources": [{"id": "pdf", "path": "test.pdf", "sha256": "0"*64,
            "pages": [{"number": 1, "width_pt": 100, "height_pt": 100}]}],
            "selection": {"id": "selection", "regions": [deepcopy(ref)], "required_minima": {"nodes": 10,"pipes": 12,"sources": 2,"valves": 4}, **approved},
            "nodes": nodes, "pipes": pipes, "valves": valves, "ambiguities": ambiguities}


class ValidationTests(unittest.TestCase):
    def validate(self, manifest):
        return validate_manifest(manifest, check_sources=False)

    def test_confirmed_complete_fixture_and_closed_valve_preserves_topology(self):
        result = self.validate(fixture())
        self.assertTrue(result["ready_for_transfer"], result["issues"])
        self.assertEqual(result["topology"]["physical_edge_count"], 12)
        self.assertEqual(result["topology"]["guaranteed_flow_edge_count"], 11)

    def test_unknown_valve_distinguishes_possible_and_guaranteed_flow(self):
        manifest = fixture()
        manifest["valves"][1]["physical_state"] = "unknown"
        result = self.validate(manifest)
        self.assertEqual(len(result["topology"]["physical_components"]), 1)
        self.assertEqual(len(result["topology"]["guaranteed_flow_components"]), 2)
        self.assertEqual(len(result["topology"]["possible_flow_components"]), 1)
        self.assertFalse(result["ready_for_transfer"])

    def test_unknown_line_status_prevents_ready(self):
        manifest = fixture()
        manifest["pipes"][0]["active_state"] = "unknown"
        self.assertFalse(self.validate(manifest)["ready_for_transfer"])

    def test_missing_geometry_never_fills_a_value(self):
        manifest = fixture()
        manifest["pipes"][0]["parameters"]["length_m"] = None
        original = deepcopy(manifest)
        result = self.validate(manifest)
        self.assertFalse(result["ready_for_transfer"])
        self.assertEqual(manifest, original)
        self.assertIn("MISSING_PARAMETER", {i["code"] for i in result["issues"]})

    def test_pending_fragment_blocks_transfer(self):
        manifest = fixture()
        manifest["selection"]["review_status"] = "pending"
        self.assertFalse(self.validate(manifest)["ready_for_transfer"])

    def test_duplicate_dangling_and_bad_diameter_detected(self):
        manifest = fixture()
        manifest["nodes"].append(deepcopy(manifest["nodes"][0]))
        manifest["pipes"][0]["to_node"] = "absent"
        manifest["pipes"][1]["parameters"]["inner_diameter_mm"] = 159
        codes = {i["code"] for i in self.validate(manifest)["issues"]}
        self.assertTrue({"DUPLICATE_ID", "PIPE_ENDPOINT", "INNER_DIAMETER"}.issubset(codes))

    def test_bool_and_nan_are_not_physical_parameters(self):
        for value in (True, float("nan"), float("inf"), -1, 0, "10"):
            with self.subTest(value=value):
                manifest = fixture()
                manifest["pipes"][0]["parameters"]["length_m"] = value
                self.assertIn("PARAMETER_RANGE", {i["code"] for i in self.validate(manifest)["issues"]})

    def test_evidence_must_have_valid_area(self):
        manifest = fixture()
        manifest["nodes"][0]["source_refs"][0]["bbox"] = [-1, 1, 101, 20]
        self.assertIn("BBOX_OUTSIDE_PAGE", {i["code"] for i in self.validate(manifest)["issues"]})

    def test_confirmation_requires_person_and_timezone(self):
        manifest = fixture()
        manifest["nodes"][0]["reviewed_at"] = "2026-01-01"
        self.assertFalse(self.validate(manifest)["ready_for_transfer"])

    def test_source_change_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "test.pdf"
            file.write_bytes(b"source")
            manifest = fixture()
            manifest["sources"][0]["sha256"] = hashlib.sha256(b"source").hexdigest()
            self.assertTrue(validate_manifest(manifest, directory)["ready_for_transfer"])
            file.write_bytes(b"changed")
            self.assertIn("SOURCE_CHANGED", {i["code"] for i in validate_manifest(manifest, directory)["issues"]})

    def test_unresolved_crossing_blocks_acceptance(self):
        manifest = fixture()
        manifest["ambiguities"][0]["status"] = "open"
        self.assertFalse(self.validate(manifest)["ready_for_transfer"])

    def test_components_do_not_invent_crossing_connection(self):
        self.assertEqual(components(["a","b","c","d"], [("a","b"),("c","d")]), [["a","b"],["c","d"]])

    def test_malformed_evidence_and_metadata_return_issues_without_crashing(self):
        mutations = [
            lambda m: m["nodes"][0]["source_refs"][0].update(source_id=[]),
            lambda m: m["nodes"][0].update(context=[]),
            lambda m: m["nodes"][0].update(original_label=[]),
            lambda m: m["pipes"][0].update(source_refs=None),
            lambda m: m["ambiguities"][0].update(affected_ids=None),
            lambda m: m["ambiguities"][0].update(affected_ids=[{}]),
            lambda m: m["ambiguities"][0].update(kind=[]),
            lambda m: m["nodes"][0]["source_refs"][0].update(method="not-a-method"),
            lambda m: m["sources"][0].update(kind="banana"),
            lambda m: m["sources"][0]["pages"][0].update(number=1.5),
            lambda m: m["nodes"][0]["source_refs"][0].update(page=1.5),
            lambda m: m["selection"].pop("id"),
            lambda m: m["nodes"][0].update(kind="boundary", boundary_reason=["invented"]),
            lambda m: m["nodes"][0].update(position=[-1, 5]),
            lambda m: m["sources"][0].update(path="bad\0.pdf"),
        ]
        for number, mutation in enumerate(mutations):
            with self.subTest(number=number):
                manifest = fixture()
                mutation(manifest)
                result = self.validate(manifest)
                self.assertFalse(result["ready_for_transfer"])
                self.assertTrue(any(i["severity"] == "error" for i in result["issues"]))


if __name__ == "__main__":
    unittest.main()
