"""Compare a reviewed manifest with an independently read GAP export.

The function deliberately does not infer tolerances, repair IDs, coerce numbers,
or claim that a synthetic export proves integration with GAP.  All validation
is local to these two JSON documents; source file integrity belongs to the
manifest validator.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import math
import re
from typing import Any


_COLLECTIONS = ("nodes", "pipes", "valves")
_PARAMETERS = {
    "length_m": "m",
    "outer_diameter_mm": "mm",
    "wall_thickness_mm": "mm",
    "inner_diameter_mm": "mm",
}
_ISO_TIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})?$"
)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _iso_time(value: Any) -> bool:
    if not isinstance(value, str) or not _ISO_TIME.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value.replace("z", "+00:00").replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _reviewed(value: dict[str, Any]) -> bool:
    return (
        value.get("review_status") == "confirmed"
        and _text(value.get("reviewed_by"))
        and _iso_time(value.get("reviewed_at"))
    )


def compare_export(
    manifest: dict,
    export: dict,
    numeric_tolerances: dict | None = None,
) -> dict:
    """Return a JSON-serializable comparison without modifying its inputs.

    ``execution_status`` describes input validity.  Object/field differences
    remain successful execution of the comparison, while their assessment is
    ``OUT_OF_TOLERANCE``.  ``NOT_ASSESSED`` always takes precedence for draft,
    synthetic, or invalid input, even when individual differences were found.

    Four pipe parameters use absolute, explicitly approved tolerances in their
    canonical units.  Valve DN is a discrete nominal size and is compared
    exactly.  Unknown expected numeric values never count as a numeric pass.
    """
    issues: list[dict[str, Any]] = []
    input_errors = 0
    structural_errors = 0
    numeric = {"expected": 0, "assessed": 0, "passed": 0, "failed": 0, "not_assessed": 0}
    counts: dict[str, dict[str, int]] = {}

    def issue(
        code: str,
        severity: str,
        object_id: str | None,
        message: str,
        refs: Any = None,
        *,
        invalid: bool = False,
        structural: bool = False,
    ) -> None:
        nonlocal input_errors, structural_errors
        issues.append({
            "code": code,
            "severity": severity,
            "object_id": object_id if _text(object_id) else None,
            "message": message,
            "source_refs": refs if isinstance(refs, list) else [],
        })
        if severity == "error":
            input_errors += int(invalid)
            structural_errors += int(structural)

    def invalid(code: str, oid: str | None, message: str, refs: Any = None) -> None:
        issue(code, "error", oid, message, refs, invalid=True, structural=True)

    if not isinstance(manifest, dict):
        invalid("INVALID_MANIFEST", None, "Manifest must be a JSON object.")
        manifest = {}
    if not isinstance(export, dict):
        invalid("INVALID_EXPORT", None, "GAP export must be a JSON object.")
        export = {}

    for label, document in (("manifest", manifest), ("export", export)):
        if document.get("schema_version") != "1.0":
            invalid("INVALID_SCHEMA_VERSION", None, f"{label}.schema_version must equal '1.0'.")
        if not _text(document.get("case_id")):
            invalid("INVALID_CASE_ID", None, f"{label}.case_id must be a non-empty string.")
    if _text(manifest.get("case_id")) and _text(export.get("case_id")):
        if manifest["case_id"] != export["case_id"]:
            issue("CASE_ID_MISMATCH", "error", None,
                  f"Expected case_id {manifest['case_id']!r}; exported {export['case_id']!r}.",
                  structural=True)

    model = export.get("model")
    if not isinstance(model, dict):
        invalid("INVALID_MODEL", None, "export.model must be an object.")
        model = {}
    else:
        for field in ("path", "software_version"):
            if not _text(model.get(field)):
                invalid("INVALID_MODEL_FIELD", None, f"export.model.{field} must be a non-empty string.")
        if not _iso_time(model.get("exported_at")):
            invalid("INVALID_EXPORT_TIME", None, "export.model.exported_at must be an ISO 8601 timestamp.")
        if model.get("evidence") not in ("real", "synthetic"):
            invalid("INVALID_EVIDENCE", None, "export.model.evidence must be 'real' or 'synthetic'.")
    real_export = model.get("evidence") == "real"
    if not real_export:
        issue("REAL_EXPORT_REQUIRED", "warning", None,
              "A synthetic or missing GAP export cannot establish a confirmed assessment.")

    selection = manifest.get("selection")
    if not isinstance(selection, dict):
        invalid("INVALID_SELECTION", None, "manifest.selection must be an object.")
        selection = {}
    selection_confirmed = _reviewed(selection)
    if not selection_confirmed:
        issue("SELECTION_NOT_CONFIRMED", "warning", selection.get("id") if _text(selection.get("id")) else None,
              "Selection requires confirmed status, reviewer and valid ISO 8601 review time.")
    if selection.get("review_status") not in ("pending", "confirmed", "rejected"):
        invalid("INVALID_REVIEW_STATUS", None, "selection.review_status is invalid.")
    if selection.get("review_status") == "confirmed" and not selection_confirmed:
        invalid("INVALID_REVIEW_METADATA", None, "Confirmed selection has missing or invalid review metadata.")

    ambiguities = manifest.get("ambiguities")
    no_open_ambiguities = isinstance(ambiguities, list)
    if not isinstance(ambiguities, list):
        invalid("INVALID_AMBIGUITIES", None, "manifest.ambiguities must be a list.")
        ambiguities = []
    ambiguity_ids: set[str] = set()
    for index, ambiguity in enumerate(ambiguities):
        if not isinstance(ambiguity, dict):
            invalid("INVALID_AMBIGUITY", None, f"ambiguities[{index}] must be an object.")
            no_open_ambiguities = False
            continue
        aid = ambiguity.get("id") if _text(ambiguity.get("id")) else None
        refs = ambiguity.get("source_refs")
        if aid is None:
            invalid("INVALID_ID", None, f"ambiguities[{index}].id must be a non-empty string.", refs)
        elif aid in ambiguity_ids:
            invalid("DUPLICATE_ID", aid, f"Duplicate ambiguity ID at ambiguities[{index}].", refs)
        else:
            ambiguity_ids.add(aid)
        if ambiguity.get("status") == "open":
            no_open_ambiguities = False
            issue("OPEN_AMBIGUITY", "warning", aid,
                  str(ambiguity.get("description", "Unresolved ambiguity.")), refs)
        elif ambiguity.get("status") == "resolved":
            if not (_text(ambiguity.get("resolution")) and _text(ambiguity.get("reviewed_by"))
                    and _iso_time(ambiguity.get("reviewed_at"))):
                no_open_ambiguities = False
                invalid("INVALID_RESOLUTION", aid,
                        "Resolved ambiguity requires resolution, reviewer and ISO 8601 review time.", refs)
        else:
            no_open_ambiguities = False
            invalid("INVALID_AMBIGUITY_STATUS", aid, "Ambiguity status must be 'open' or 'resolved'.", refs)

    # Lists and multisets are retained.  Only IDs occurring exactly once are
    # eligible for a comparison pair; duplicate IDs are never silently collapsed.
    documents: dict[str, dict[str, list[dict[str, Any]]]] = {"manifest": {}, "export": {}}
    by_id: dict[str, dict[str, dict[str, list[dict[str, Any]]]]] = {"manifest": {}, "export": {}}
    collection_sizes: dict[str, dict[str, int]] = {"manifest": {}, "export": {}}
    all_reviewed = True
    for label, document in (("manifest", manifest), ("export", export)):
        global_locations: dict[str, list[str]] = defaultdict(list)
        for collection in _COLLECTIONS:
            raw = document.get(collection)
            if not isinstance(raw, list):
                invalid("INVALID_COLLECTION", None, f"{label}.{collection} must be a list.")
                raw = []
                if label == "manifest":
                    all_reviewed = False
            collection_sizes[label][collection] = len(raw)
            entries = []
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for index, obj in enumerate(raw):
                if not isinstance(obj, dict):
                    invalid("INVALID_OBJECT", None, f"{label}.{collection}[{index}] must be an object.")
                    if label == "manifest":
                        all_reviewed = False
                    continue
                entries.append(obj)
                oid = obj.get("id") if _text(obj.get("id")) else None
                refs = obj.get("source_refs")
                if oid is None:
                    invalid("INVALID_ID", None, f"{label}.{collection}[{index}].id must be a non-empty string.", refs)
                else:
                    grouped[oid].append(obj)
                    global_locations[oid].append(f"{collection}[{index}]")
                if label == "manifest":
                    if obj.get("review_status") not in ("pending", "confirmed", "rejected"):
                        invalid("INVALID_REVIEW_STATUS", oid, f"Invalid review status in {label}.{collection}[{index}].", refs)
                    if not _reviewed(obj):
                        all_reviewed = False
                        issue("OBJECT_NOT_CONFIRMED", "warning", oid,
                              "Object requires confirmed status, reviewer and valid ISO 8601 review time.", refs)
                        if obj.get("review_status") == "confirmed":
                            invalid("INVALID_REVIEW_METADATA", oid,
                                    "Confirmed object has missing or invalid review metadata.", refs)
                    if not isinstance(refs, list):
                        invalid("INVALID_SOURCE_REFS", oid, "Manifest source_refs must be a list.")
                    if not isinstance(obj.get("original_label"), str):
                        invalid("INVALID_LABEL", oid, "Manifest original_label must be a string.", refs)
                if collection == "nodes":
                    if obj.get("kind") not in ("junction", "source", "boundary"):
                        invalid("INVALID_NODE_KIND", oid, f"Invalid node kind in {label}.{collection}[{index}].", refs)
                    if label == "export" and not isinstance(obj.get("original_label"), str):
                        invalid("INVALID_LABEL", oid, "Export node original_label must be a string.", refs)
                elif collection == "pipes":
                    for field in ("from_node", "to_node"):
                        if not _text(obj.get(field)):
                            invalid("INVALID_REFERENCE", oid, f"{label}.{collection}[{index}].{field} must be an ID string.", refs)
                    if obj.get("active_state") not in ("active", "inactive", "unknown"):
                        invalid("INVALID_ACTIVE_STATE", oid, "Pipe active_state must be active, inactive or unknown.", refs)
                    parameters = obj.get("parameters")
                    if not isinstance(parameters, dict):
                        invalid("INVALID_PARAMETERS", oid, f"{label} pipe parameters must be an object.", refs)
                    else:
                        for field in _PARAMETERS:
                            if field not in parameters:
                                invalid("MISSING_PARAMETER", oid, f"{label} pipe parameters.{field} is required, possibly null.", refs)
                            elif parameters[field] is not None and not _number(parameters[field]):
                                invalid("INVALID_NUMBER", oid, f"{label} pipe parameters.{field} must be finite numeric or null; bool is forbidden.", refs)
                else:
                    if not _text(obj.get("pipe_id")):
                        invalid("INVALID_REFERENCE", oid, f"{label} valve pipe_id must be an ID string.", refs)
                    if obj.get("physical_state") not in ("open", "closed", "unknown"):
                        invalid("INVALID_VALVE_STATE", oid, "Valve physical_state must be open, closed or unknown.", refs)
                    if "size_dn_mm" not in obj:
                        invalid("MISSING_PARAMETER", oid, f"{label} valve size_dn_mm is required, possibly null.", refs)
                    elif obj["size_dn_mm"] is not None and not _number(obj["size_dn_mm"]):
                        invalid("INVALID_NUMBER", oid, f"{label} valve size_dn_mm must be finite numeric or null; bool is forbidden.", refs)
                    if "pressure_rating_raw" not in obj:
                        invalid("MISSING_PARAMETER", oid, f"{label} valve pressure_rating_raw is required, possibly null.", refs)
                    elif obj["pressure_rating_raw"] is not None and not isinstance(obj["pressure_rating_raw"], str):
                        invalid("INVALID_PRESSURE_RATING", oid, f"{label} valve pressure_rating_raw must be a string or null.", refs)
            documents[label][collection] = entries
            by_id[label][collection] = dict(grouped)
        for oid, locations in global_locations.items():
            if len(locations) > 1:
                invalid("DUPLICATE_ID", oid,
                        f"{label} contains {len(locations)} occurrences of ID {oid!r}: {', '.join(locations)}.",
                        next((obj.get("source_refs") for entries in documents[label].values()
                              for obj in entries if obj.get("id") == oid), []))
        for collection in ("pipes", "valves"):
            for obj in documents[label][collection]:
                oid = obj.get("id") if _text(obj.get("id")) else None
                refs = obj.get("source_refs")
                references = (("from_node", "nodes"), ("to_node", "nodes")) if collection == "pipes" else (("pipe_id", "pipes"),)
                for field, target in references:
                    reference = obj.get(field)
                    if _text(reference):
                        candidates = by_id[label][target].get(reference, [])
                        if len(candidates) != 1:
                            invalid("BROKEN_REFERENCE", oid,
                                    f"{label}.{collection}.{field}={reference!r} resolves to {len(candidates)} {target}; expected one.", refs)

    if not all_reviewed:
        issue("MANIFEST_NOT_CONFIRMED", "warning", None,
              "Every manifest object must be confirmed by a named reviewer with an ISO 8601 timestamp.")

    tolerances: dict[str, float | int] = {}
    if numeric_tolerances is not None and not isinstance(numeric_tolerances, dict):
        invalid("INVALID_TOLERANCES", None, "numeric_tolerances must be an object or null.")
        numeric_tolerances = {}
    for field, unit in _PARAMETERS.items():
        config = (numeric_tolerances or {}).get(field)
        if config is None:
            issue("NO_APPROVED_TOLERANCE", "warning", None,
                  f"{field} ({unit}) is not assessed without an explicitly approved absolute tolerance.")
            continue
        if not isinstance(config, dict):
            invalid("INVALID_TOLERANCE", None, f"Tolerance {field} must be an object.")
            continue
        if (config.get("unit") != unit or not _number(config.get("absolute"))
                or config.get("absolute", -1) < 0 or not _text(config.get("approved_by"))
                or not _iso_time(config.get("approved_at")) or not _text(config.get("source"))):
            invalid("INVALID_TOLERANCE", None,
                    f"Tolerance {field} requires unit={unit!r}, finite nonnegative absolute, approver, ISO 8601 time and source.")
            continue
        tolerances[field] = config["absolute"]
    if isinstance(numeric_tolerances, dict):
        for field in numeric_tolerances:
            if field not in _PARAMETERS:
                invalid("UNKNOWN_TOLERANCE", None, f"Unknown numeric parameter in tolerance configuration: {field!r}.")

    def exact(expected: dict, actual: dict, field: str, collection: str) -> None:
        if field not in expected or field not in actual:
            # A missing field must not accidentally compare equal to explicit null.
            issue("MISSING_FIELD", "error", expected.get("id"),
                  f"{collection}.{field} is absent from {'manifest' if field not in expected else 'export'}.",
                  expected.get("source_refs"), structural=True)
        elif expected[field] != actual[field]:
            issue("FIELD_MISMATCH", "error", expected.get("id"),
                  f"{collection}.{field}: expected {expected[field]!r}; exported {actual[field]!r}.",
                  expected.get("source_refs"), structural=True)

    for collection in _COLLECTIONS:
        expected_index = by_id["manifest"][collection]
        actual_index = by_id["export"][collection]
        unique_expected = {oid: values[0] for oid, values in expected_index.items() if len(values) == 1}
        unique_actual = {oid: values[0] for oid, values in actual_index.items() if len(values) == 1}
        shared = sorted(unique_expected.keys() & unique_actual.keys())
        missing = sorted(expected_index.keys() - actual_index.keys())
        extra = sorted(actual_index.keys() - expected_index.keys())
        ambiguous = sorted(oid for oid in expected_index.keys() | actual_index.keys()
                           if len(expected_index.get(oid, [])) > 1 or len(actual_index.get(oid, [])) > 1)
        counts[collection] = {
            "expected": collection_sizes["manifest"][collection],
            "actual": collection_sizes["export"][collection],
            "matched": len(shared), "missing": len(missing), "extra": len(extra),
            "ambiguous": len(ambiguous),
        }
        for oid in missing:
            issue("MISSING_OBJECT", "error", oid, f"Expected {collection} object is absent from GAP export.",
                  expected_index[oid][0].get("source_refs"), structural=True)
        for oid in extra:
            issue("EXTRA_OBJECT", "error", oid, f"Unexpected {collection} object exists in GAP export.", structural=True)
        for oid in ambiguous:
            issue("AMBIGUOUS_OBJECT_ID", "error", oid,
                  "Object comparison is skipped because its ID is duplicated.",
                  expected_index.get(oid, [{}])[0].get("source_refs"), structural=True)
        for oid in shared:
            expected, actual = unique_expected[oid], unique_actual[oid]
            if collection == "nodes":
                exact(expected, actual, "kind", collection)
                exact(expected, actual, "original_label", collection)
            elif collection == "valves":
                for field in ("pipe_id", "physical_state", "size_dn_mm", "pressure_rating_raw"):
                    exact(expected, actual, field, collection)
                numeric["expected"] += 1
                expected_dn, actual_dn = expected.get("size_dn_mm"), actual.get("size_dn_mm")
                if expected_dn is None:
                    numeric["not_assessed"] += 1
                    issue("UNKNOWN_NOMINAL_SIZE", "warning", oid,
                          "Expected valve DN is unknown; null/null is not verification of a nominal size.",
                          expected.get("source_refs"))
                elif not (_number(expected_dn) and _number(actual_dn)):
                    numeric["not_assessed"] += 1
                else:
                    numeric["assessed"] += 1
                    numeric["passed" if expected_dn == actual_dn else "failed"] += 1
            else:
                expected_ends = (expected.get("from_node"), expected.get("to_node"))
                actual_ends = (actual.get("from_node"), actual.get("to_node"))
                # Tuple membership also preserves repeated ends for a self-loop.
                if actual_ends not in (expected_ends, expected_ends[::-1]):
                    issue("CONNECTION_MISMATCH", "error", oid,
                          f"Pipe endpoints: expected {expected_ends!r}; exported {actual_ends!r} (order ignored).",
                          expected.get("source_refs"), structural=True)
                exact(expected, actual, "active_state", collection)
                expected_params = expected.get("parameters")
                actual_params = actual.get("parameters")
                expected_params = expected_params if isinstance(expected_params, dict) else {}
                actual_params = actual_params if isinstance(actual_params, dict) else {}
                for field, unit in _PARAMETERS.items():
                    numeric["expected"] += 1
                    expected_value, actual_value = expected_params.get(field), actual_params.get(field)
                    if expected_value is None:
                        numeric["not_assessed"] += 1
                        issue("UNKNOWN_EXPECTED_VALUE", "warning", oid,
                              f"{field} ({unit}) is unknown in the manifest; no numeric pass can be claimed.",
                              expected.get("source_refs"))
                    elif actual_value is None:
                        numeric["not_assessed"] += 1
                        issue("MISSING_NUMERIC_VALUE", "error", oid,
                              f"Export has no numeric {field} ({unit}); expected {expected_value!r}.",
                              expected.get("source_refs"))
                    elif not (_number(expected_value) and _number(actual_value)) or field not in tolerances:
                        numeric["not_assessed"] += 1
                    else:
                        difference = abs(actual_value - expected_value)
                        numeric["assessed"] += 1
                        if difference <= tolerances[field]:
                            numeric["passed"] += 1
                        else:
                            numeric["failed"] += 1
                            issue("NUMERIC_OUT_OF_TOLERANCE", "error", oid,
                                  f"{field}: expected {expected_value} {unit}; exported {actual_value} {unit}; "
                                  f"absolute difference {difference} {unit} exceeds approved {tolerances[field]} {unit}.",
                                  expected.get("source_refs"))
        if collection == "pipes":
            # Parameters on absent, invalid-ID or duplicate pipes were not
            # compared.  Include them in totals so an empty intersection cannot
            # accidentally turn into an all-numeric success.
            unpaired = collection_sizes["manifest"][collection] - len(shared)
            numeric["expected"] += unpaired * len(_PARAMETERS)
            numeric["not_assessed"] += unpaired * len(_PARAMETERS)
        elif collection == "valves":
            unpaired = collection_sizes["manifest"][collection] - len(shared)
            numeric["expected"] += unpaired
            numeric["not_assessed"] += unpaired

    if structural_errors:
        structural_status = "OUT_OF_TOLERANCE"
    elif all(documents["manifest"][name] or documents["export"][name] for name in _COLLECTIONS):
        structural_status = "OK"
    else:
        structural_status = "NOT_ASSESSED"
        issue("EMPTY_COLLECTION", "warning", None,
              "Structural assessment requires nodes, pipes and valves; at least one collection is empty.")
    if numeric["failed"]:
        numeric_status = "OUT_OF_TOLERANCE"
    elif numeric["expected"] and not numeric["not_assessed"] and numeric["assessed"] == numeric["expected"]:
        numeric_status = "OK"
    else:
        numeric_status = "NOT_ASSESSED"

    readiness = {
        "confirmed_selection": selection_confirmed,
        "confirmed_entities": all_reviewed,
        "no_open_ambiguities": no_open_ambiguities,
        "real_export": real_export,
    }
    if input_errors or not all(readiness.values()):
        assessment_status = "NOT_ASSESSED"
    elif structural_status == "OUT_OF_TOLERANCE" or numeric_status == "OUT_OF_TOLERANCE":
        assessment_status = "OUT_OF_TOLERANCE"
    elif structural_status == "OK" and numeric_status == "OK":
        assessment_status = "OK"
    else:
        assessment_status = "NOT_ASSESSED"
    severities = Counter(item["severity"] for item in issues)
    return {
        "schema_version": "1.0",
        "case_id": manifest.get("case_id") if _text(manifest.get("case_id")) else None,
        "execution_status": "ERROR" if input_errors else "SUCCESS",
        "assessment_status": assessment_status,
        "structural_status": structural_status,
        "numeric_status": numeric_status,
        "issues": issues,
        "summary": {
            "counts": counts,
            "issues": {**{name: severities[name] for name in ("error", "warning", "info")}, "total": len(issues)},
            "numeric": numeric,
            "readiness": readiness,
        },
        "model": dict(model),
    }
