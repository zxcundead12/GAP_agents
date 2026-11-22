"""Local, receipt-bound workflow around the constrained GAP backend.

Creating or approving a plan never starts COM.  SQLite claims a plan before the
runner starts, so another process cannot repeat an in-progress or uncertain
write.  Receipts authorize execution only; they do not confirm engineering data.
"""

from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
import sys
import uuid

from gap_manifest.validation import confirmed, validate_manifest
from gap_manifest.sources import resolve_source


_IDENTIFIER = re.compile(r"^(?:plan|approval)_[0-9a-f]{32}$")
_STATUSES = {
    "SUCCESS", "ERROR", "NOT_READY", "CONFIRMATION_REQUIRED", "SESSION_BUSY",
    "COM_UNAVAILABLE", "API_ERROR", "TIMEOUT", "UNSUPPORTED_MAPPING", "LICENSE_BUSY", "LICENSE_UNAVAILABLE",
}
_KNOWN_REFUSALS = {
    "NOT_READY", "CONFIRMATION_REQUIRED", "SESSION_BUSY", "COM_UNAVAILABLE",
    "UNSUPPORTED_MAPPING", "LICENSE_BUSY", "LICENSE_UNAVAILABLE",
}
_SECRET_KEYS = ("password", "secret", "token", "license_key", "licence_key", "serial", "authorization", "api_key")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _redact(value):
    if isinstance(value, dict):
        return {str(key): "[REDACTED]" if any(part in str(key).lower() for part in _SECRET_KEYS)
                else _redact(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_redact(item) for item in value]
    return value


class GapService:
    """The only write entry point is ``execute_plan`` with a local receipt."""

    def __init__(self, workspace: Path | str, runner=None):
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise ValueError("Workspace must be an existing directory.")
        self.runner = runner
        self.output_dir = self._path("outputs/gap")
        self.plan_dir = self._path("outputs/gap/plans")
        self.receipt_dir = self._path("outputs/gap/receipts")
        self.model_dir = self._path("outputs/gap/models")
        self.artifact_dir = self._path("outputs/gap/artifacts")
        for directory in (self.output_dir, self.plan_dir, self.receipt_dir, self.model_dir, self.artifact_dir):
            directory.mkdir(parents=True, exist_ok=True)
            self._path(directory)
        state_dir = self._path("work/gap_service")
        state_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self._path(state_dir / "ledger.sqlite3")
        self.journal_path = self._path(self.output_dir / "service_journal.jsonl")
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id TEXT PRIMARY KEY, plan_path TEXT NOT NULL,
                    plan_sha256 TEXT NOT NULL, operation TEXT NOT NULL,
                    target_model_path TEXT NOT NULL, state TEXT NOT NULL,
                    approval_id TEXT, result_json TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL,
                    plan_sha256 TEXT NOT NULL, receipt_path TEXT NOT NULL,
                    receipt_sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_claims (
                    plan_id TEXT PRIMARY KEY, owner_pid INTEGER NOT NULL,
                    active INTEGER NOT NULL
                );
            """)

    def _path(self, raw):
        """Resolve existing symlinks before applying the workspace boundary."""
        if not isinstance(raw, (str, os.PathLike)) or not str(raw).strip() or "\0" in str(raw):
            raise ValueError("A non-empty workspace path is required.")
        path = Path(raw)
        if ".." in path.parts:
            raise ValueError("Parent traversal is not accepted for workspace paths.")
        if os.name == "nt":
            if path.drive and not path.is_absolute():
                raise ValueError("Drive-relative paths are not accepted.")
            if any(":" in part for part in path.parts if part != path.anchor):
                raise ValueError("Alternate data streams are not accepted.")
        if not path.is_absolute():
            path = self.workspace / path
        path = path.resolve()
        if not path.is_relative_to(self.workspace):
            raise ValueError("Path must remain inside the workspace after symlink resolution.")
        return path

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self._path(self.ledger_path), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _read(self, raw):
        path = self._path(raw)
        data = path.read_bytes()
        value = json.loads(data.decode("utf-8-sig"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON number.")))
        _bytes(value)
        return path, value, _hash(data)

    def _save(self, raw, value, *, exclusive=False):
        path = self._path(raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path(path)
        data = _bytes(value)
        if exclusive:
            with path.open("xb") as stream:
                stream.write(data)
        else:
            temporary = self._path(path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp"))
            with temporary.open("xb") as stream:
                stream.write(data)
            os.replace(temporary, self._path(path))
        return path, _hash(data)

    def _result(self, status, message, **details):
        return {"execution_status": status, "trace_id": uuid.uuid4().hex,
                "message": message, **details}

    def _failure(self, exc, code="INVALID_REQUEST"):
        # Exception messages may contain secrets from a third-party runner.
        return self._result("ERROR", "Request was rejected before a confirmed result was available.",
                            details={"code": code, "error_type": type(exc).__name__})

    def _journal(self, event, trace_id, **details):
        path = self._path(self.journal_path)
        record = _redact({"timestamp": _now(), "trace_id": trace_id, "event": event, **details})
        with path.open("ab") as stream:
            stream.write((json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))

    def _invoke(self, job):
        trace = uuid.uuid4().hex
        self._journal("backend_request", trace, operation=job["operation"],
                      model_path=job.get("model_path"))
        try:
            runner = self.runner
            if runner is None:
                from .backend import run_job
                runner = run_job
            response = runner(deepcopy(job), self.workspace, timeout_seconds=60)
            if not isinstance(response, dict):
                raise TypeError("Backend response must be an object.")
            _bytes(response)
            if (response.get("execution_status") not in _STATUSES
                    or not _text(response.get("trace_id")) or not isinstance(response.get("message"), str)):
                raise ValueError("Backend response violates the service contract.")
            response = _redact(response)
        except TimeoutError:
            response = self._result("TIMEOUT", "Runner timed out; GAP state must be checked before another write.",
                                    trace_id=trace, manual_review_required=True)
        except Exception as exc:
            response = self._failure(exc, "RUNNER_FAILED")
            response["manual_review_required"] = True
        self._journal("backend_response", trace, response=response)
        return response

    def inspect_environment(self):
        """Inspect module specs and the saved inventory, without COM activation."""
        try:
            libraries = {}
            for name in ("win32com", "pythoncom", "mcp"):
                try:
                    spec = importlib.util.find_spec(name)
                    libraries[name] = {"available": spec is not None,
                                       "origin": str(spec.origin) if spec and spec.origin else None}
                except (ImportError, ValueError, AttributeError):
                    libraries[name] = {"available": False, "origin": None}
            try:
                mcp_version = importlib.metadata.version("mcp")
            except importlib.metadata.PackageNotFoundError:
                mcp_version = None
            inventory_path = self._path("work/gap_inventory/installed_gap_inventory.json")
            inventory = self._read(inventory_path)[1] if inventory_path.is_file() else None
            result = self._result(
                "SUCCESS", "Read-only environment inventory; COM and license availability were not tested.",
                workspace=str(self.workspace), runtime={"executable": sys.executable, "version": sys.version.split()[0],
                                                       "bits": struct.calcsize("P") * 8},
                libraries=libraries, mcp_version=mcp_version,
                inventory_path=str(inventory_path), recorded_inventory=_redact(inventory),
                com_activated=False, license_status="NOT_ASSESSED", automation_verified=False,
            )
            self._journal("environment_inspection", result["trace_id"], inventory_present=inventory is not None)
            return result
        except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
            return self._failure(exc)

    def probe(self):
        try:
            return self._invoke({"operation": "probe"})
        except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
            return self._failure(exc)

    def _source_bindings(self, manifest):
        """Original evidence may be read outside workspace, but is never written."""
        bindings = []
        for source in manifest.get("sources", []):
            raw = source["path"]
            source_path = resolve_source(source, self.workspace)
            digest = _hash(source_path.read_bytes())
            if digest != source["sha256"]:
                raise ValueError("Source SHA-256 changed.")
            bindings.append({"source_id": source["id"], "declared_path": raw,
                             "resolved_path": str(source_path), "sha256": digest})
        return bindings

    def _runtime_bindings(self, mapping):
        """Freeze declared profile evidence without confusing it with real verification.

        A mapping with no runtime profile is still an offline plan; its backend
        must refuse unsupported mapping before COM. Once a profile is declared,
        all of its files are hash/path-bound to the reviewed plan.
        """
        if 'verification_path' not in mapping and 'template_path' not in mapping:
            return {}
        proof_path, proof, proof_digest = self._read(mapping.get('verification_path'))
        if not isinstance(proof, dict):
            raise ValueError('Runtime verification must be a JSON object.')
        bindings = {'verification': {'path': str(proof_path), 'sha256': proof_digest}}
        for role in ('model', 'journal'):
            binding = proof.get(role)
            if not isinstance(binding, dict):
                raise ValueError('Runtime proof has an invalid artifact binding.')
            path = self._path(binding.get('path'))
            digest = _hash(path.read_bytes())
            if digest != binding.get('sha256'):
                raise ValueError('Runtime proof artifact content changed.')
            bindings['verification_' + role] = {'path': str(path), 'sha256': digest}
        template = self._path(mapping.get('template_path'))
        digest = _hash(template.read_bytes())
        if template.suffix.lower() != '.gap' or digest != mapping.get('template_sha256'):
            raise ValueError('Runtime template content changed.')
        bindings['template'] = {'path': str(template), 'sha256': digest}
        return bindings

    def _prepare(self, operation, summary, inputs=None, manifest=None, mapping=None):
        plan_id = "plan_" + uuid.uuid4().hex
        target = self._path(self.model_dir / (plan_id + ".gap"))
        job = {"operation": operation, "model_path": str(target)}
        if operation == "build":
            job.update(manifest=manifest, mapping=mapping)
        plan = {
            "schema_version": "1.0", "plan_id": plan_id, "created_at": _now(),
            "workspace": str(self.workspace), "operation": operation,
            "target_model_path": str(target), "summary": summary,
            "inputs": inputs or {}, "job": job,
            "authorization_scope": "Execution only; does not confirm engineering data.",
        }
        path, digest = self._save(self.plan_dir / (plan_id + ".json"), plan, exclusive=True)
        with self._connection() as db:
            db.execute("INSERT INTO plans VALUES (?,?,?,?,?,?,NULL,NULL,?,?)",
                       (plan_id, str(path), digest, operation, str(target), "PREPARED", plan["created_at"], plan["created_at"]))
        result = self._result("SUCCESS", "Plan prepared; local execution approval is required.",
                              plan_id=plan_id, plan_path=str(path), plan_sha256=digest, summary=summary,
                              target_model_path=str(target), plan_state="PREPARED",
                              authorization_status="CONFIRMATION_REQUIRED")
        self._journal("plan_prepared", result["trace_id"], plan_id=plan_id, plan_sha256=digest, operation=operation)
        return result

    def prepare_smoke_plan(self):
        try:
            return self._prepare("smoke", {
                "purpose": "Artificial integration check, not a production fragment.",
                "nodes": ["AG_J1", "AG_J2"], "pipes": ["AG_P1"],
                "actions": ["create separate model", "connect two JOINT objects", "save", "reopen", "read back", "check duplicate-free repeat"],
            })
        except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
            return self._failure(exc)

    def prepare_manifest_plan(self, manifest_path: str, mapping_path: str | None = None):
        try:
            path, manifest, digest = self._read(manifest_path)
            validation = validate_manifest(manifest, self.workspace, check_sources=True)
            if not validation.get("ready_for_transfer"):
                return self._result("NOT_READY", "Manifest is not ready; execution approval cannot confirm its engineering data.",
                                    validation=validation, plan_state="NOT_PREPARED")
            if mapping_path is None:
                return self._result("NOT_READY", "An explicitly reviewed GAP mapping and units are required.",
                                    details={"code": "MAPPING_REQUIRED"}, plan_state="NOT_PREPARED")
            map_path, mapping, map_digest = self._read(mapping_path)
            if not confirmed(mapping):
                return self._result("NOT_READY", "Mapping must be confirmed with reviewer and ISO 8601 time.",
                                    details={"code": "MAPPING_NOT_CONFIRMED"}, plan_state="NOT_PREPARED")
            inputs = {"manifest": {"path": str(path), "sha256": digest},
                      "mapping": {"path": str(map_path), "sha256": map_digest},
                      "sources": self._source_bindings(manifest),
                      "runtime_dependencies": self._runtime_bindings(mapping)}
            if _hash(path.read_bytes()) != digest or _hash(map_path.read_bytes()) != map_digest:
                raise ValueError("Input changed while the plan was being prepared.")
            return self._prepare("build", {"case_id": manifest["case_id"],
                                          "counts": {name: len(manifest[name]) for name in ("nodes", "pipes", "valves")},
                                          "mapping_support": "Backend must verify support for this installed GAP version."},
                                 inputs, manifest, mapping)
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            return self._failure(exc)

    def _load_plan(self, plan_id):
        if not isinstance(plan_id, str) or not _IDENTIFIER.fullmatch(plan_id) or not plan_id.startswith("plan_"):
            raise ValueError("Invalid plan ID.")
        with self._connection() as db:
            row = db.execute("SELECT * FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown plan ID.")
        path, plan, digest = self._read(row["plan_path"])
        if (digest != row["plan_sha256"] or not isinstance(plan, dict) or plan.get("plan_id") != plan_id
                or plan.get("workspace") != str(self.workspace) or plan.get("operation") != row["operation"]):
            raise ValueError("Plan content or binding has changed.")
        target = self._path(plan.get("target_model_path"))
        job = plan.get("job")
        if (str(target) != row["target_model_path"] or not isinstance(job, dict)
                or job.get("operation") != row["operation"] or self._path(job.get("model_path")) != target
                or job["operation"] not in ("smoke", "build")):
            raise ValueError("Plan job does not match its reserved target.")
        return dict(row), plan

    def _check_inputs(self, plan):
        if plan["operation"] != "build":
            return
        inputs = plan["inputs"]
        loaded = {}
        for role in ("manifest", "mapping"):
            _, value, digest = self._read(inputs[role]["path"])
            if digest != inputs[role]["sha256"] or value != plan["job"][role]:
                raise ValueError("A frozen plan input has changed.")
            loaded[role] = value
        validation = validate_manifest(loaded["manifest"], self.workspace, check_sources=True)
        if not validation.get("ready_for_transfer") or not confirmed(loaded["mapping"]):
            raise ValueError("Manifest or mapping is no longer ready.")
        if self._source_bindings(loaded["manifest"]) != inputs["sources"]:
            raise ValueError("Source path or content has changed.")
        if self._runtime_bindings(loaded['mapping']) != inputs.get('runtime_dependencies', {}):
            raise ValueError('Runtime profile evidence path or content has changed.')

    def approve_plan(self, plan_id: str, approved_by: str):
        """Local human/CLI method.  It must never be registered as an MCP tool."""
        try:
            if not _text(approved_by):
                raise ValueError("A named local approver is required.")
            row, plan = self._load_plan(plan_id)
            self._check_inputs(plan)
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute("SELECT * FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
                if current["approval_id"]:
                    receipt = db.execute("SELECT * FROM approvals WHERE approval_id=?", (current["approval_id"],)).fetchone()
                    self._check_receipt(dict(current), current["approval_id"], dict(receipt) if receipt else None)
                    return self._result("SUCCESS", "Existing local execution receipt returned.",
                                        approval_id=current["approval_id"], receipt_path=receipt["receipt_path"],
                                        plan_id=plan_id, plan_sha256=row["plan_sha256"], idempotent_reuse=True)
                if current["state"] != "PREPARED":
                    return self._result("NOT_READY", "Plan state does not permit a new approval.", plan_state=current["state"])
                approval_id = "approval_" + uuid.uuid4().hex
                receipt_value = {"schema_version": "1.0", "approval_id": approval_id, "plan_id": plan_id,
                                 "plan_sha256": row["plan_sha256"], "approved_by": approved_by.strip(), "approved_at": _now(),
                                 "authorization_scope": "Execution only; engineering reviews remain separate."}
                path, receipt_digest = self._save(self.receipt_dir / (approval_id + ".json"), receipt_value, exclusive=True)
                db.execute("INSERT INTO approvals VALUES (?,?,?,?,?)",
                           (approval_id, plan_id, row["plan_sha256"], str(path), receipt_digest))
                db.execute("UPDATE plans SET approval_id=?,state='APPROVED',updated_at=? WHERE plan_id=?",
                           (approval_id, _now(), plan_id))
            result = self._result("SUCCESS", "Local execution approved; engineering data were not changed.",
                                  approval_id=approval_id, receipt_path=str(path), plan_id=plan_id,
                                  plan_sha256=row["plan_sha256"], plan_state="APPROVED")
            self._journal("execution_approved", result["trace_id"], plan_id=plan_id,
                          approval_id=approval_id, plan_sha256=row["plan_sha256"])
            return result
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            return self._failure(exc, "APPROVAL_REJECTED")

    def _check_receipt(self, row, approval_id, receipt_row):
        if (not isinstance(approval_id, str) or not _IDENTIFIER.fullmatch(approval_id)
                or not approval_id.startswith("approval_") or row["approval_id"] != approval_id
                or not receipt_row or receipt_row["plan_id"] != row["plan_id"]
                or receipt_row["plan_sha256"] != row["plan_sha256"]):
            raise PermissionError("A matching local execution receipt is required.")
        _, receipt, digest = self._read(receipt_row["receipt_path"])
        if (digest != receipt_row["receipt_sha256"] or not isinstance(receipt, dict)
                or receipt.get("plan_id") != row["plan_id"] or receipt.get("approval_id") != approval_id
                or receipt.get("plan_sha256") != row["plan_sha256"]):
            raise PermissionError("Execution receipt content or binding has changed.")

    def _artifacts(self, response, tag):
        response = deepcopy(response)
        for field, suffix in (("snapshot", "snapshot"), ("normalized_export", "normalized")):
            if field in response and response[field] is not None:
                if not isinstance(response[field], (dict, list)):
                    raise ValueError("Backend evidence must be a structured JSON value.")
                path, digest = self._save(self.artifact_dir / (tag + "." + suffix + ".json"), response[field], exclusive=True)
                response[field + "_path"] = str(path)
                response[field + "_sha256"] = digest
        return response

    def execute_plan(self, plan_id: str, approval_id: str):
        try:
            row, plan = self._load_plan(plan_id)
            with self._connection() as db:
                receipt_row = db.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id if isinstance(approval_id, str) else "",)).fetchone()
            try:
                self._check_receipt(row, approval_id, dict(receipt_row) if receipt_row else None)
            except (PermissionError, OSError, ValueError, TypeError, KeyError):
                return self._result("CONFIRMATION_REQUIRED", "A valid local receipt for this exact plan is required.", plan_id=plan_id)
            self._check_inputs(plan)
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute("SELECT * FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
                if current["result_json"]:
                    result = json.loads(current["result_json"])
                    result["idempotent_reuse"] = True
                    return result
                if current["state"] in ("RUNNING", "UNKNOWN"):
                    return self._result("NOT_READY", "Execution is in progress or uncertain; automatic retry is blocked.",
                                        plan_id=plan_id, plan_state=current["state"], manual_review_required=True)
                blocker = db.execute("SELECT plan_id,state FROM plans WHERE plan_id<>? AND state IN ('RUNNING','UNKNOWN') LIMIT 1", (plan_id,)).fetchone()
                if blocker:
                    return self._result("NOT_READY", "Another write has unresolved execution state; inspect GAP before continuing.",
                                        blocking_plan_id=blocker["plan_id"], plan_state=blocker["state"], manual_review_required=True)
                if current["state"] != "APPROVED":
                    return self._result("CONFIRMATION_REQUIRED", "Plan is not approved for execution.", plan_id=plan_id)
                # Existing files are never overwritten by a new planned write.
                if self._path(plan["target_model_path"]).exists():
                    return self._result("NOT_READY", "Reserved target already exists; no model was overwritten.",
                                        plan_id=plan_id, details={"code": "TARGET_EXISTS"})
                db.execute("UPDATE plans SET state='RUNNING',updated_at=? WHERE plan_id=?", (_now(), plan_id))
                db.execute("INSERT OR REPLACE INTO execution_claims VALUES (?,?,1)", (plan_id, os.getpid()))
            response = self._invoke(plan["job"])
            uncertain = response["execution_status"] not in ({"SUCCESS"} | _KNOWN_REFUSALS)
            state = "UNKNOWN" if uncertain else "COMPLETED"
            response.update(plan_id=plan_id, approval_id=approval_id, plan_sha256=row["plan_sha256"],
                            target_model_path=plan["target_model_path"], plan_state=state, idempotent_reuse=False)
            if uncertain:
                response["manual_review_required"] = True
            response = self._artifacts(response, plan_id)
            result_path, _ = self._save(self.artifact_dir / (plan_id + ".result.json"), response, exclusive=True)
            response["result_path"] = str(result_path)
            with self._connection() as db:
                db.execute("UPDATE plans SET state=?,result_json=?,updated_at=? WHERE plan_id=?",
                           (state, _bytes(response).decode("utf-8"), _now(), plan_id))
                db.execute("UPDATE execution_claims SET active=0 WHERE plan_id=?", (plan_id,))
            self._journal("plan_finished", response["trace_id"], plan_id=plan_id,
                          plan_state=state, execution_status=response["execution_status"])
            return response
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            return self._failure(exc, "EXECUTION_REJECTED")

    @staticmethod
    def _pid_alive(pid):
        """Check liveness without sending a signal or terminating a process."""
        if not isinstance(pid, int) or pid <= 0:
            raise ValueError("Invalid execution owner PID.")
        if os.name != "nt":
            if Path("/proc").is_dir():
                return Path("/proc", str(pid)).exists()
            raise OSError("Process liveness could not be established.")
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE, no mutation rights
        if not handle:
            if ctypes.get_last_error() == 87:  # ERROR_INVALID_PARAMETER: PID no longer exists
                return False
            raise OSError("Execution owner liveness could not be inspected.")
        try:
            result = kernel.WaitForSingleObject(handle, 0)
            if result not in (0, 258):
                raise OSError("Execution owner liveness could not be inspected.")
            return result == 258
        finally:
            kernel.CloseHandle(handle)

    @staticmethod
    def _recovery_activity():
        """Read process metadata only; never starts COM/GAP or kills a worker.

        Fail closed when a Python command line cannot be inspected. Returned
        evidence contains PIDs, never arbitrary command lines or their secrets.
        """
        processes = []
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class ProcessEntry(ctypes.Structure):
                _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

            class UnicodeString(ctypes.Structure):
                _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
            kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            kernel.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
            kernel.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
            kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            query = ctypes.WinDLL("ntdll").NtQueryInformationProcess
            query.argtypes = (wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG))
            query.restype = wintypes.LONG
            snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
            if snapshot == ctypes.c_void_p(-1).value:
                raise OSError("Process inspection failed.")
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            try:
                present = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
                while present:
                    name, pid = entry.szExeFile, int(entry.th32ProcessID)
                    if name.lower() == "gap.exe" or name.lower().startswith("python"):
                        command = "" if pid == os.getpid() else None
                        handle = None
                        if name.lower() != "gap.exe" and pid != os.getpid():
                            handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION only
                        if handle:
                            try:
                                size = wintypes.ULONG()
                                query(handle, 60, None, 0, ctypes.byref(size))  # ProcessCommandLineInformation
                                if 0 < size.value <= 1_048_576:
                                    buffer = ctypes.create_string_buffer(size.value)
                                    if query(handle, 60, buffer, size.value, ctypes.byref(size)) == 0:
                                        value = UnicodeString.from_buffer(buffer)
                                        start, end = ctypes.addressof(buffer), ctypes.addressof(buffer) + len(buffer)
                                        if value.Buffer and start <= value.Buffer <= end and value.Buffer + value.Length <= end:
                                            command = ctypes.wstring_at(value.Buffer, value.Length // 2)
                            finally:
                                kernel.CloseHandle(handle)
                        processes.append({"ProcessId": pid, "Name": name, "CommandLine": command})
                    present = kernel.Process32NextW(snapshot, ctypes.byref(entry))
            finally:
                kernel.CloseHandle(snapshot)
        elif Path("/proc").is_dir():
            for entry in Path("/proc").iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    name = (entry / "comm").read_text().strip()
                    if name.lower() == "gap.exe" or name.lower().startswith("python"):
                        command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
                        processes.append({"ProcessId": int(entry.name), "Name": name, "CommandLine": command})
                except FileNotFoundError:
                    continue  # process exited during the read-only inventory
        else:
            raise OSError("Process inspection is unsupported on this platform.")
        if not isinstance(processes, list):
            raise ValueError("Invalid process inspection result.")
        evidence = {"verified": True, "gap_pids": [], "worker_pids": [], "uninspectable_python_pids": []}
        for item in processes:
            if not isinstance(item, dict) or not isinstance(item.get("ProcessId"), int):
                raise ValueError("Invalid process inspection entry.")
            pid, name, command = item["ProcessId"], item.get("Name", ""), item.get("CommandLine")
            if name.lower() == "gap.exe":
                evidence["gap_pids"].append(pid)
            elif pid != os.getpid() and not isinstance(command, str):
                evidence["uninspectable_python_pids"].append(pid)
            elif isinstance(command, str) and re.search(r"(?:^|\s)-m\s+gap_integration\.worker(?:\s|$)", command):
                evidence["worker_pids"].append(pid)
        return evidence

    def acknowledge_unknown(self, plan_id: str, reviewed_by: str, note: str):
        """Locally record an operator's investigation; never retry the old plan.

        This method is deliberately not an MCP tool.  The operator must first
        inspect GAP/worker state and document the facts in ``note``.  Acknowledging
        the uncertain execution allows a new, separately approved plan; it does
        not change the observed result or repair a license/API error.
        Interrupted RUNNING claims additionally require a recorded, inactive
        owner. A read-only process check must find no GAP or worker activity;
        this method never stops processes or claims to cancel native work.
        """
        try:
            if not _text(reviewed_by) or not _text(note):
                raise ValueError("Named reviewer and investigation note are required.")
            self._load_plan(plan_id)
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
                if row["state"] not in ("UNKNOWN", "RUNNING"):
                    return self._result("NOT_READY", "Only an uncertain execution may be acknowledged.",
                                        plan_id=plan_id, plan_state=row["state"])
                claim = db.execute("SELECT * FROM execution_claims WHERE plan_id=?", (plan_id,)).fetchone()
                if row["state"] == "RUNNING" and claim is None:
                    return self._result("NOT_READY", "Execution ownership was not recorded; automatic reconciliation is unsafe.",
                                        plan_id=plan_id, details={"code": "OWNERSHIP_NOT_RECORDED"})
                if claim is not None and claim["active"] and self._pid_alive(claim["owner_pid"]):
                    return self._result("NOT_READY", "Execution owner is still active; wait for it to finish before reconciliation.",
                                        plan_id=plan_id, details={"code": "EXECUTION_OWNER_ACTIVE"})
                activity = self._recovery_activity()
                if (activity.get("verified") is not True or any(activity.get(key) for key in
                        ("gap_pids", "worker_pids", "uninspectable_python_pids"))):
                    return self._result("NOT_READY", "GAP or a worker may still be active; reconciliation was refused.",
                                        plan_id=plan_id, details={"code": "EXECUTION_ACTIVITY_PRESENT", "activity": activity})
                prior = json.loads(row["result_json"]) if row["result_json"] else self._result(
                    "ERROR", "Interrupted execution has no recorded outcome; its success is not established.",
                    plan_id=plan_id, plan_state="RUNNING", manual_review_required=True)
                acknowledgement = {
                    "schema_version": "1.0", "acknowledgement_id": "ack_" + uuid.uuid4().hex,
                    "plan_id": plan_id, "plan_sha256": row["plan_sha256"],
                    "reviewed_by": reviewed_by.strip(), "reviewed_at": _now(), "note": note.strip(),
                    "prior_state": row["state"], "new_state": "RESOLVED_NO_RETRY",
                    "prior_execution_result": prior,
                    "process_inspection": activity,
                    "automatic_retry_permitted": False,
                }
                path, digest = self._save(
                    self.artifact_dir / (acknowledgement["acknowledgement_id"] + ".json"),
                    acknowledgement, exclusive=True,
                )
                resolved = deepcopy(prior)
                resolved.update(plan_state="RESOLVED_NO_RETRY", manual_review_required=False,
                                automatic_retry_permitted=False, prior_execution_result=prior,
                                acknowledgement={key: acknowledgement[key] for key in
                                                 ("acknowledgement_id", "reviewed_by", "reviewed_at", "note")})
                resolved["acknowledgement"].update(path=str(path), sha256=digest)
                db.execute("UPDATE plans SET state='RESOLVED_NO_RETRY',result_json=?,updated_at=? WHERE plan_id=?",
                           (_bytes(resolved).decode("utf-8"), _now(), plan_id))
                db.execute("UPDATE execution_claims SET active=0 WHERE plan_id=?", (plan_id,))
            result = self._result("SUCCESS", "Operator investigation recorded; old plan remains terminal and cannot be retried.",
                                  plan_id=plan_id, plan_state="RESOLVED_NO_RETRY", acknowledgement_path=str(path),
                                  prior_execution_status=prior["execution_status"], automatic_retry_permitted=False)
            self._journal("unknown_execution_acknowledged", result["trace_id"], plan_id=plan_id,
                          acknowledgement_path=str(path), plan_state="RESOLVED_NO_RETRY")
            return result
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            return self._failure(exc, "ACKNOWLEDGEMENT_REJECTED")

    def export_model(self, model_path: str):
        try:
            path = self._path(model_path)
            if not path.is_file():
                return self._result("NOT_READY", "A saved model inside workspace is required for export.",
                                    details={"code": "MODEL_NOT_FOUND"})
            response = self._invoke({"operation": "export", "model_path": str(path)})
            if response["execution_status"] == "SUCCESS" and not any(
                response.get(field) is not None for field in ("snapshot", "normalized_export")
            ):
                return self._result("ERROR", "Backend returned no model evidence; no export was fabricated.",
                                    details={"code": "MISSING_EXPORT_EVIDENCE", "backend_trace_id": response["trace_id"]})
            response = self._artifacts(response, "export_" + uuid.uuid4().hex)
            return response
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            return self._failure(exc, "EXPORT_REJECTED")
