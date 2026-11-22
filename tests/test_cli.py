"""CLI boundaries: no invented exports, explicit draft status, saved evidence."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gap_manifest import cli


STAMP = "2026-11-22T12:00:00+03:00"


class CLITests(unittest.TestCase):
    def setUp(self):
        work = Path(__file__).resolve().parents[1] / "work"
        work.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="cli-tests-", dir=work)
        self.root = Path(self.temporary.name)
        common = {"review_status": "pending", "reviewed_by": None, "reviewed_at": None, "source_refs": []}
        parameters = {"length_m": 1.0, "outer_diameter_mm": 159.0,
                      "wall_thickness_mm": 8.0, "inner_diameter_mm": 143.0}
        self.manifest = {
            "schema_version": "1.0", "case_id": "cli-test",
            "selection": {"id": "fragment", **common}, "sources": [], "ambiguities": [],
            "nodes": [{"id": "N1", "original_label": "Source", "kind": "source", **common},
                      {"id": "N2", "original_label": "Boundary", "kind": "boundary", **common}],
            "pipes": [{"id": "P1", "original_label": "Pipe", "from_node": "N1", "to_node": "N2",
                       "parameters": parameters, "active_state": "active", **common}],
            "valves": [{"id": "V1", "original_label": "Valve", "pipe_id": "P1", "physical_state": "open",
                        "size_dn_mm": 150, "pressure_rating_raw": "40", **common}],
        }
        self.export = {
            "schema_version": "1.0", "case_id": "cli-test",
            "model": {"path": "test.gap", "software_version": "test", "exported_at": STAMP, "evidence": "synthetic"},
            "nodes": [{key: node[key] for key in ("id", "original_label", "kind")} for node in self.manifest["nodes"]],
            "pipes": [{key: deepcopy(pipe[key]) for key in ("id", "from_node", "to_node", "parameters", "active_state")}
                      for pipe in self.manifest["pipes"]],
            "valves": [{key: valve[key] for key in ("id", "pipe_id", "physical_state", "size_dn_mm", "pressure_rating_raw")}
                       for valve in self.manifest["valves"]],
        }
        self.manifest_path = self.root / "manifest.json"
        self.export_path = self.root / "export.json"
        self.output_path = self.root / "result.json"
        self.write(self.manifest_path, self.manifest)
        self.write(self.export_path, self.export)
        self.validation = {"schema_version": "1.0", "case_id": "cli-test", "execution_status": "SUCCESS",
                           "ready_for_transfer": False, "stats": {"nodes": 2}, "issues": [], "topology": {}}

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")

    def run_cli(self, command, *additional):
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = [command, "--manifest", str(self.manifest_path), "--root", str(self.root),
                "--output", str(self.output_path), *map(str, additional)]
        with patch.object(cli, "validate_manifest", return_value=self.validation), redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_validate_draft_saves_false_readiness_and_returns_nonready_code(self):
        code, stdout, stderr = self.run_cli("validate")
        self.assertEqual(code, 2)
        self.assertEqual(stderr, "")
        self.assertFalse(json.loads(stdout)["ready_for_transfer"])
        self.assertEqual(json.loads(self.output_path.read_text(encoding="utf-8")), self.validation)

    def test_compare_requires_an_actual_export_file(self):
        with patch("gap_manifest.compare.compare_export") as compare:
            code, _, stderr = self.run_cli("compare")
        self.assertEqual(code, 1)
        self.assertIn("--export", stderr)
        self.assertFalse(self.output_path.exists())
        compare.assert_not_called()

    def test_missing_export_path_is_not_replaced_by_manifest(self):
        code, _, stderr = self.run_cli("compare", "--export", self.root / "does-not-exist.json")
        self.assertEqual(code, 1)
        self.assertIn("Ошибка", stderr)
        self.assertFalse(self.output_path.exists())

    def test_draft_synthetic_comparison_keeps_not_assessed_and_source_evidence(self):
        original_export = self.export_path.read_bytes()
        code, stdout, stderr = self.run_cli("compare", "--export", self.export_path)
        result = json.loads(self.output_path.read_text(encoding="utf-8"))
        self.assertEqual((code, stderr), (2, ""))
        self.assertIn("NOT_ASSESSED", stdout)
        self.assertEqual(result["assessment_status"], "NOT_ASSESSED")
        self.assertEqual(result["model"]["evidence"], "synthetic")
        self.assertIn("MANIFEST_NOT_READY", {issue["code"] for issue in result["issues"]})
        self.assertEqual(self.export_path.read_bytes(), original_export)

    def test_cli_added_readiness_issue_is_included_in_summary_totals(self):
        self.run_cli("compare", "--export", self.export_path)
        result = json.loads(self.output_path.read_text(encoding="utf-8"))
        summary = result["summary"]["issues"]
        self.assertEqual(summary["total"], len(result["issues"]))
        for severity in ("error", "warning", "info"):
            self.assertEqual(summary[severity], sum(i["severity"] == severity for i in result["issues"]))

    def test_report_without_export_passes_none_and_does_not_call_comparator(self):
        with patch("gap_manifest.report.write_html_report") as report, patch("gap_manifest.compare.compare_export") as compare:
            code, _, stderr = self.run_cli("report", "--image", self.root / "missing.png")
        self.assertEqual((code, stderr), (0, ""))
        compare.assert_not_called()
        report.assert_called_once()
        args = report.call_args.args
        self.assertEqual(args[0], self.manifest)
        self.assertEqual(args[1], self.validation)
        self.assertIsNone(args[3])
        self.assertIsNone(args[4])

    def test_flat_tolerance_configuration_is_loaded_without_rewriting(self):
        tolerances = {name: {"unit": "m" if name == "length_m" else "mm", "absolute": 0,
                             "approved_by": "Engineer", "approved_at": STAMP, "source": "approval"}
                      for name in self.manifest["pipes"][0]["parameters"]}
        tolerance_path = self.root / "tolerances.json"
        self.write(tolerance_path, tolerances)
        code, _, stderr = self.run_cli("compare", "--export", self.export_path, "--tolerances", tolerance_path)
        result = json.loads(self.output_path.read_text(encoding="utf-8"))
        self.assertEqual((code, stderr), (2, ""))
        self.assertEqual(result["numeric_status"], "OK")
        self.assertEqual(result["assessment_status"], "NOT_ASSESSED")
        self.assertEqual(result["summary"]["numeric"]["not_assessed"], 0)

    def test_malformed_nonfinite_json_is_rejected_before_validation(self):
        for raw in ("NaN", "Infinity", "-Infinity", '{"nodes": [NaN]}'):
            with self.subTest(raw=raw):
                self.manifest_path.write_text(raw, encoding="utf-8")
                code, _, stderr = self.run_cli("validate")
                self.assertEqual(code, 1)
                self.assertIn("Non-finite JSON", stderr)
                self.assertFalse(self.output_path.exists())


if __name__ == "__main__":
    unittest.main()
