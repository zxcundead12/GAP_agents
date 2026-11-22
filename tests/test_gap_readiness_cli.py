"""The offline operator check must never activate OpenServer."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gap_integration.cli import main


class ReadinessCliTests(unittest.TestCase):
    def test_offline_report_is_written_without_starting_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            (root / "data" / "candidate_manifest.json").write_text(
                json.dumps({"schema_version": "1.0", "case_id": "synthetic-offline-test",
                            "nodes": [], "pipes": [], "valves": [], "sources": [],
                            "ambiguities": [], "selection": {}}), encoding="utf-8")
            with patch("gap_integration.backend.run_job") as backend, redirect_stdout(io.StringIO()):
                code = main(["--workspace", str(root), "--output", "outputs/readiness.json", "readiness"])
            backend.assert_not_called()
            self.assertEqual(code, 2)
            result = json.loads((root / "outputs" / "readiness.json").read_text(encoding="utf-8"))
            self.assertFalse(result["ready_for_production_build"])
            self.assertEqual(len(result["manifest_sha256"]), 64)
            self.assertFalse(any((root / "outputs").rglob("*.gap")))

    def test_missing_manifest_is_structured_error_without_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with patch("gap_integration.backend.run_job") as backend, redirect_stdout(output):
                code = main(["--workspace", directory, "readiness", "--manifest", "absent.json"])
            backend.assert_not_called()
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output.getvalue())["execution_status"], "ERROR")


if __name__ == "__main__":
    unittest.main()
