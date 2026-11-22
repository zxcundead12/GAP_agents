"""Limited, read-only license-file metadata; this is never license validation.

No activation, COM, network, vendor API, or license-manager calls are made.
Only hashes, counts, and tightly constrained FEATURE/INCREMENT metadata leave
the parser. License lines, comments, signatures, host IDs, vendor paths, and
server names are deliberately not included in results or exception messages.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAX_LICENSE_BYTES = 1_048_576
_DIRECTIVES = frozenset({
    "FEATURE", "INCREMENT", "SERVER", "VENDOR", "DAEMON", "USE_SERVER",
    "PACKAGE", "UPGRADE", "INCLUDE", "EXCLUDE", "RESERVE", "MAX",
    "BORROW_LOWWATER", "SUPERSEDE",
})
_PRODUCT = re.compile(r"[A-Za-z][A-Za-z0-9_.+-]{0,95}\Z")
_VERSION = re.compile(r"[0-9]{1,8}(?:\.[0-9]{1,8}){0,3}\Z")
_EXPIRY = re.compile(r"(?:permanent|[0-9]{1,2}-(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)-[0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2})\Z", re.IGNORECASE)
_NOTICE = (
    "Это диагностика метаданных и совпадения файлов, а не проверка лицензии. "
    "SHA-256, директивы и названия продуктов не подтверждают подлинность, действительность, "
    "доступ к API или право использования OpenServer. Ничего не активировано и не изменено."
)


def _failure(code: str) -> dict[str, Any]:
    # No str(exc), input path, input bytes, or unrecognised tokens are exposed.
    return {"read_status": "ERROR", "error_code": code, "sha256": None, "size_bytes": None,
            "format": "NOT_READ", "directive_count": 0, "directive_counts": {},
            "unknown_directive_count": 0, "feature_metadata": [], "malformed_feature_count": 0}


def _parse_metadata(data: bytes) -> dict[str, Any]:
    result: dict[str, Any] = {
        "format": "BINARY_OR_UNDECODABLE", "encoding": None, "directive_count": 0,
        "directive_counts": {}, "unknown_directive_count": 0, "feature_metadata": [],
        "malformed_feature_count": 0,
    }
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return result
    if any(ord(char) < 32 and char not in "\t\r\n" for char in text) or "\x7f" in text:
        return result
    result["encoding"] = "ascii" if text.isascii() else "utf-8"
    counts: Counter = Counter()
    unknown = 0
    metadata = []
    pending = ""
    # Continuations are assembled in memory only and never returned or logged.
    logical_lines = []
    for physical_line in text.splitlines():
        line = physical_line.strip()
        if not line or line.startswith(("#", ";", "//")):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        logical_lines.append(pending + line)
        pending = ""
    if pending:
        logical_lines.append(pending)
    for line in logical_lines:
        # Only the first five tokens matter. Everything following expiry is
        # intentionally opaque, including seat counts, hostid and SIGN fields.
        tokens = line.split(None, 5)
        if not tokens:
            continue
        directive = tokens[0].upper()
        result["directive_count"] += 1
        if directive not in _DIRECTIVES:
            unknown += 1
            continue
        counts[directive] += 1
        if directive not in {"FEATURE", "INCREMENT"}:
            continue
        if len(tokens) < 5:
            result["malformed_feature_count"] += 1
            continue
        product, version, expiry = tokens[1], tokens[3], tokens[4]
        if not (_PRODUCT.fullmatch(product) and _VERSION.fullmatch(version) and _EXPIRY.fullmatch(expiry)):
            result["malformed_feature_count"] += 1
            continue
        record = {"directive": directive, "product": product, "version": version, "expiry": expiry}
        if record not in metadata:
            metadata.append(record)
    result.update(
        format="FLEXNET_TEXT" if counts else "TEXT_UNRECOGNIZED",
        directive_counts=dict(sorted(counts.items())), unknown_directive_count=unknown,
        feature_metadata=metadata,
    )
    return result


def inspect_license_file(path: Path | str) -> dict[str, Any]:
    """Read at most 1 MiB from one explicit regular file; return safe metadata.

    Format identification is heuristic. Binary/unrecognised input can still be
    hashed for an exact byte comparison but is never interpreted as valid.
    Returned data intentionally omits the input path and all full license text.
    """
    try:
        file = Path(path)
        initial = file.stat()
        if not stat.S_ISREG(initial.st_mode):
            return _failure("NOT_A_REGULAR_FILE")
        if initial.st_size > MAX_LICENSE_BYTES:
            return _failure("FILE_TOO_LARGE")
        with file.open("rb") as stream:
            current = os.fstat(stream.fileno())
            if not stat.S_ISREG(current.st_mode):
                return _failure("NOT_A_REGULAR_FILE")
            if current.st_size > MAX_LICENSE_BYTES:
                return _failure("FILE_TOO_LARGE")
            data = stream.read(MAX_LICENSE_BYTES + 1)
        if len(data) > MAX_LICENSE_BYTES:
            return _failure("FILE_TOO_LARGE")
    except FileNotFoundError:
        return _failure("FILE_NOT_FOUND")
    except (OSError, ValueError, TypeError):
        return _failure("FILE_UNREADABLE")
    return {"read_status": "SUCCESS", "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data), **_parse_metadata(data)}


def diagnose_license_files(license_file: Path | str, installed_file: Path | str) -> dict[str, Any]:
    """Compare supplied and installed bytes without installing or validating."""
    supplied = inspect_license_file(license_file)
    installed = inspect_license_file(installed_file)
    both_read = supplied["read_status"] == installed["read_status"] == "SUCCESS"
    return {
        "schema_version": "1.0", "execution_status": "SUCCESS" if both_read else "ERROR",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "supplied": supplied, "installed": installed,
        "installed_matches_supplied": supplied["sha256"] == installed["sha256"] if both_read else None,
        "license_validation_status": "NOT_VERIFIED", "api_access_status": "NOT_VERIFIED",
        "openserver_entitlement_status": "NOT_VERIFIED",
        "activation_performed": False, "software_started": False, "files_modified": False,
        "notice": _NOTICE,
    }
