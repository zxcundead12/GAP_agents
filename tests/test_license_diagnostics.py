"""Synthetic files only: no installed license or license-manager access."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from gap_integration.license_diagnostics import MAX_LICENSE_BYTES, diagnose_license_files, inspect_license_file


SYNTHETIC = b'''# SECRET_COMMENT_CANARY
SERVER SECRET_SERVER_CANARY SECRET_HOSTID_CANARY 27000
VENDOR petex SECRET_VENDOR_PATH_CANARY
FEATURE GAP petex 2025.0 31-dec-2026 1 \\
    SIGN="SECRET_SIGNATURE_CANARY" HOSTID=SECRET_FEATURE_HOST_CANARY
INCREMENT OpenServer petex 12.4 permanent 4 SIGN=SECRET_INCREMENT_CANARY
SECRET_UNKNOWN_DIRECTIVE_CANARY SECRET_UNKNOWN_BODY_CANARY
'''


class LicenseDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(__file__).resolve().parents[1]
        work = self.project / "work"
        work.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="license-synthetic-", dir=work)
        self.root = Path(self.temp.name)
        self.supplied = self.root / "supplied.lic"
        self.installed = self.root / "installed.lic"
        self.supplied.write_bytes(SYNTHETIC)
        self.installed.write_bytes(SYNTHETIC)

    def tearDown(self):
        self.temp.cleanup()

    def assert_no_secret(self, result):
        text = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("SECRET_", text)
        self.assertNotIn("SIGN=", text)
        self.assertNotIn("HOSTID=", text)

    def test_equal_hashes_and_allowed_feature_fields_only(self):
        result = diagnose_license_files(self.supplied, self.installed)
        self.assertEqual(result["execution_status"], "SUCCESS")
        self.assertTrue(result["installed_matches_supplied"])
        self.assertEqual(result["supplied"]["sha256"], hashlib.sha256(SYNTHETIC).hexdigest())
        self.assertEqual(result["supplied"]["size_bytes"], len(SYNTHETIC))
        self.assertEqual(result["supplied"]["format"], "FLEXNET_TEXT")
        self.assertEqual(result["supplied"]["directive_count"], 5)
        self.assertEqual(result["supplied"]["directive_counts"], {"FEATURE": 1, "INCREMENT": 1, "SERVER": 1, "VENDOR": 1})
        self.assertEqual(result["supplied"]["unknown_directive_count"], 1)
        self.assertEqual(result["supplied"]["feature_metadata"], [
            {"directive": "FEATURE", "product": "GAP", "version": "2025.0", "expiry": "31-dec-2026"},
            {"directive": "INCREMENT", "product": "OpenServer", "version": "12.4", "expiry": "permanent"},
        ])
        self.assertEqual(result["license_validation_status"], "NOT_VERIFIED")
        self.assertEqual(result["api_access_status"], "NOT_VERIFIED")
        self.assertEqual(result["openserver_entitlement_status"], "NOT_VERIFIED")
        self.assertFalse(result["activation_performed"])
        self.assertFalse(result["files_modified"])
        self.assert_no_secret(result)

    def test_different_comments_mean_different_files_even_when_products_match(self):
        self.installed.write_bytes(SYNTHETIC + b"# SECRET_DIFFERENT_COMMENT_CANARY\n")
        result = diagnose_license_files(self.supplied, self.installed)
        self.assertFalse(result["installed_matches_supplied"])
        self.assertEqual(result["supplied"]["feature_metadata"], result["installed"]["feature_metadata"])
        self.assert_no_secret(result)

    def test_malformed_feature_tokens_are_not_echoed(self):
        self.supplied.write_text("FEATURE SHORT SECRET_VENDOR\nINCREMENT GAP vendor SECRET_VERSION SECRET_EXPIRY 1\n", encoding="utf-8")
        result = inspect_license_file(self.supplied)
        self.assertEqual(result["read_status"], "SUCCESS")
        self.assertEqual(result["malformed_feature_count"], 2)
        self.assertEqual(result["feature_metadata"], [])
        self.assert_no_secret(result)

    def test_binary_and_utf8_do_not_crash_or_echo_content(self):
        for data, expected in ((b"FEATURE GAP vendor 1.0 permanent 1\x00SECRET_BINARY", "BINARY_OR_UNDECODABLE"),
                               (b"\xff\xfeSECRET_BYTES", "BINARY_OR_UNDECODABLE"),
                               ("# Синтетический комментарий SECRET_UTF8\n".encode(), "TEXT_UNRECOGNIZED")):
            with self.subTest(format=expected):
                self.supplied.write_bytes(data)
                result = inspect_license_file(self.supplied)
                self.assertEqual(result["format"], expected)
                self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())
                self.assertEqual(result["feature_metadata"], [])
                self.assert_no_secret(result)

    def test_size_limit_and_fixed_error_messages_without_paths(self):
        self.supplied.write_bytes(b"x" * (MAX_LICENSE_BYTES + 1))
        self.assertEqual(inspect_license_file(self.supplied)["error_code"], "FILE_TOO_LARGE")
        self.assertEqual(inspect_license_file(self.root)["error_code"], "NOT_A_REGULAR_FILE")
        result = diagnose_license_files(self.root / "SECRET_MISSING_FILE.lic", self.installed)
        self.assertEqual(result["execution_status"], "ERROR")
        self.assertIsNone(result["installed_matches_supplied"])
        self.assert_no_secret(result)

    def test_cli_synthetic_report_and_prevention_of_input_overwrite(self):
        report = self.root / "report.json"
        command = [sys.executable, str(self.project / "scripts" / "check_license_file.py"),
                   "--license-file", str(self.supplied), "--installed-file", str(self.installed)]
        completed = subprocess.run(command + ["--output", str(report)], capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assert_no_secret(json.loads(report.read_text(encoding="utf-8")))
        completed = subprocess.run(command + ["--output", str(self.supplied)], capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(self.supplied.read_bytes(), SYNTHETIC)
        alias = self.root / "hardlink-output.json"
        try:
            os.link(self.supplied, alias)
        except OSError:
            return  # Filesystem may not support hard links; other checks stand.
        completed = subprocess.run(command + ["--output", str(alias)], capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(self.supplied.read_bytes(), SYNTHETIC)


if __name__ == "__main__":
    unittest.main()
