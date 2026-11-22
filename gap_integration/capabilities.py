"""Read-only production readiness against documented, unverified IPM 7.5 API.

No COM import, backend call, source-file validation or GAP export is performed.
The profile is an evidence inventory, not an executable command allowlist.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path


_PROFILE = Path(__file__).resolve().parent / "profiles" / "ipm75_capabilities.json"
_CAPABILITY_STATUSES = {"documented_unverified", "unverified", "unsupported"}


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def build_readiness(manifest, validation=None):
    """Describe capability gaps without claiming production readiness.

    ``validation`` may be the first participant's existing validation result.
    Omitting it explicitly leaves engineering validation ``NOT_ASSESSED``.
    Even an approved synthetic fixture cannot establish a working production API.
    """
    issues = []

    def issue(code, message, object_id=None):
        issues.append({"code": code, "severity": "error", "object_id": object_id,
                       "message": message, "source_refs": []})

    try:
        profile = json.loads(_PROFILE.read_text(encoding="utf-8"))
        if not isinstance(profile, dict) or profile.get("schema_version") != "1.0":
            raise ValueError("Unsupported profile schema")
        if not isinstance(profile.get("official_references"), dict) or not isinstance(profile.get("capabilities"), list):
            raise ValueError("Invalid profile collections")
        for row in profile["capabilities"]:
            if not isinstance(row, dict) or row.get("status") not in _CAPABILITY_STATUSES or not _text(row.get("id")):
                raise ValueError("Invalid capability row")
    except (OSError, ValueError, TypeError) as exc:
        issue("CAPABILITY_PROFILE_UNAVAILABLE", f"Профиль возможностей недоступен или повреждён: {type(exc).__name__}.")
        profile = {"profile_id": None, "software": {}, "official_references": {}, "capabilities": []}

    if not isinstance(manifest, dict):
        issue("INVALID_MANIFEST", "Манифест должен быть объектом JSON.")
        manifest = {}
    if manifest.get("schema_version") != "1.0":
        issue("INVALID_MANIFEST_SCHEMA", "Ожидается schema_version=1.0 манифеста.")
    case_id = manifest.get("case_id") if _text(manifest.get("case_id")) else None
    if case_id is None:
        issue("INVALID_CASE_ID", "Не указан строковый case_id манифеста.")
    groups = {}
    counts = {}
    for name in ("nodes", "pipes", "valves"):
        raw = manifest.get(name)
        if not isinstance(raw, list):
            issue("INVALID_COLLECTION", f"Поле {name} должно быть списком.")
            raw = []
        counts[name] = len(raw)
        groups[name] = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                issue("INVALID_OBJECT", f"{name}[{index}] должен быть объектом JSON.")
            else:
                groups[name].append(item)

    kinds = Counter()
    for node in groups["nodes"]:
        kind = node.get("kind")
        if isinstance(kind, str) and kind in ("junction", "source", "boundary"):
            kinds[kind] += 1
        else:
            issue("UNKNOWN_NODE_KIND", "Узел имеет неизвестный семантический тип.", node.get("id") if _text(node.get("id")) else None)
    pipe_roles = Counter()
    for pipe in groups["pipes"]:
        role = pipe.get("role")
        if isinstance(role, str) and role in ("pipeline", "internal_connector"):
            pipe_roles[role] += 1
        else:
            issue("UNKNOWN_PIPE_ROLE", "Неизвестная роль трубы; production mapping нельзя подобрать автоматически.", pipe.get("id") if _text(pipe.get("id")) else None)

    required = []
    for kind, candidates in (("junction", ["JOINT"]), ("source", ["SOURCE"]), ("boundary", ["SINK", "SEP"])):
        if kinds[kind]:
            required.append({"semantic_type": kind, "count": kinds[kind], "native_candidates": candidates,
                             "mapping_status": "unverified"})
    if counts["pipes"]:
        required.append({"semantic_type": "pipe", "count": counts["pipes"], "roles": dict(pipe_roles),
                         "native_candidates": ["PIPE"], "mapping_status": "unverified"})
    if counts["valves"]:
        required.append({"semantic_type": "valve", "count": counts["valves"], "native_candidates": ["VALVE"],
                         "mapping_status": "unverified"})

    engineering = {"status": "NOT_ASSESSED", "ready_for_transfer": None, "execution_status": None,
                   "issue_summary": {"error": 0, "warning": 0, "info": 0, "total": 0}}
    if validation is not None:
        if not isinstance(validation, dict):
            issue("INVALID_VALIDATION", "Переданный результат валидации должен быть объектом JSON.")
        else:
            ready = validation.get("ready_for_transfer")
            validation_issues = validation.get("issues")
            valid_validation = isinstance(ready, bool) and isinstance(validation_issues, list)
            if not valid_validation:
                issue("INVALID_VALIDATION", "Результат валидации должен содержать bool ready_for_transfer и список issues.")
            if validation.get("case_id") not in (None, case_id):
                valid_validation = False
                issue("VALIDATION_CASE_MISMATCH", "Валидация относится к другому case_id.")
            if isinstance(validation_issues, list):
                severity = Counter(row.get("severity") for row in validation_issues
                                   if isinstance(row, dict) and isinstance(row.get("severity"), str))
                engineering["issue_summary"] = {**{key: severity[key] for key in ("error", "warning", "info")},
                                                "total": len(validation_issues)}
                if any(not isinstance(row, dict) or row.get("severity") not in ("error", "warning", "info")
                       for row in validation_issues):
                    valid_validation = False
                    issue("INVALID_VALIDATION_ISSUES", "Сообщения валидации должны быть объектами JSON с severity=error|warning|info.")
            status = validation.get("execution_status")
            if status not in ("SUCCESS", "ERROR"):
                valid_validation = False
                issue("INVALID_VALIDATION_STATUS", "Неизвестен execution_status переданной валидации.")
            engineering["execution_status"] = status if status in ("SUCCESS", "ERROR") else None
            if valid_validation:
                engineering["ready_for_transfer"] = ready
                engineering["status"] = "READY" if ready and status == "SUCCESS" and not engineering["issue_summary"]["error"] else "NOT_READY"

    matrix = deepcopy(profile["capabilities"])
    needed = {
        "create_equipment": bool(counts["nodes"] or counts["valves"]),
        "joint_pipe_connections": bool(counts["pipes"]),
        "source_boundary_mapping": bool(kinds["source"] or kinds["boundary"]),
        "native_source_sink_valve_connections": bool(kinds["source"] or kinds["boundary"] or counts["valves"]),
        "pipe_geometry": bool(counts["pipes"]), "pipe_segment_indexing": bool(counts["pipes"]),
        "pipe_units": bool(counts["pipes"]), "valve_state": bool(counts["valves"]),
        "valve_dn_and_rating": bool(counts["valves"]), "identity_and_labels": bool(sum(counts.values())),
        "pipe_active_state": bool(counts["pipes"]), "independent_normalized_export": True,
        "production_build": True,
    }
    references = profile["official_references"]
    for row in matrix:
        row["required_by_manifest"] = needed.get(row["id"], False)
        row["official_pdf_refs"] = []
        for reference_id in row.get("reference_ids", []):
            reference = references.get(reference_id, {})
            document = references.get(reference.get("document"), {})
            if document.get("path"):
                row["official_pdf_refs"].append({"path": document["path"], "section": reference.get("section"),
                                                  "pdf_pages": reference.get("pdf_pages", [])})

    blockers = []

    def block(code, message, capability_id=None, affected_ids=None):
        blockers.append({"code": code, "severity": "error", "message": message,
                         "capability_id": capability_id, "affected_ids": affected_ids or []})

    if engineering["status"] == "NOT_ASSESSED":
        block("ENGINEERING_VALIDATION_NOT_ASSESSED", "Инженерная готовность не оценена: передайте актуальный результат validate_manifest с проверкой исходных SHA.")
    elif engineering["status"] != "READY":
        block("ENGINEERING_DATA_NOT_READY", "Инженерный манифест не готов. Исполнение плана не заменяет подтверждение данных и разрешение неоднозначностей.")
    for row in matrix:
        if row["required_by_manifest"] and row["status"] in ("unverified", "unsupported"):
            block("CAPABILITY_" + row["id"].upper(), row["note_ru"], row["id"])
    # Keep the backend refusal explicit even if a damaged profile could not load.
    if not any(row["capability_id"] == "production_build" for row in blockers):
        block("CAPABILITY_PRODUCTION_BUILD", "Текущий backend production build возвращает UNSUPPORTED_MAPPING; исполняемый производственный профиль не подтверждён.", "production_build")
    if groups["valves"]:
        block("VALVE_INSERTION_MAPPING_REQUIRED",
              "Согласуйте размещение и порядок клапанов на pipe_id, вспомогательные узлы/соединения и правило их обратного сворачивания. Эти решения нельзя вывести из одного pipe_id.",
              "native_source_sink_valve_connections",
              [row["id"] for row in groups["valves"] if _text(row.get("id"))])
    if any(isinstance(row.get("parameters"), dict)
           and any(row["parameters"].get(field) is not None for field in ("outer_diameter_mm", "wall_thickness_mm"))
           for row in groups["pipes"]):
        block("PIPE_OD_WALL_MODEL_MODE_REQUIRED", "OD и Thickness документированы только для Enthalpy Balance. Нужны утверждённый режим и фактическое чтение этих значений; запись в COMMENT не подтверждает native геометрию.", "pipe_geometry")
    if any(row["required_by_manifest"] and row["status"] == "documented_unverified" for row in matrix):
        block("DOCUMENTATION_IS_NOT_RUNTIME_VERIFICATION", "Официальная документация подтверждает описание API, но не его успешную работу в установленной версии. Нужна проверка на отдельной модели.")
    if not counts["nodes"] or not counts["pipes"]:
        block("EMPTY_PRODUCTION_FRAGMENT", "Для production-фрагмента требуются узлы и соединения; пустые коллекции не подтверждают готовность.")

    steps = [
        {"order": 1, "requires_live_gap": False, "action_ru": "Подтвердить инженерный манифест и закрыть неоднозначности; согласовать native типы источников/границ, представление клапанов и единицы.",
         "success_criteria_ru": "Актуальная валидация ready_for_transfer=true и отдельный reviewed mapping; никаких автоподтверждений."},
        {"order": 2, "requires_live_gap": False, "action_ru": "Подготовить строгий offline compiler структурированных операций и pure normalizer записанных ответов с контролем capability matrix.",
         "success_criteria_ru": "Неподдержанное отображение отклоняется до COM; нормализатор не берёт измеренные значения из манифеста. Тестовые ответы помечены synthetic."},
        {"order": 3, "requires_live_gap": True, "action_ru": "После получения действительной лицензии выполнить отдельный JOINT–JOINT smoke, сохранить, открыть и повторно считать модель.",
         "success_criteria_ru": "Фактические журналы, 2 JOINT + 1 auto-PIPE, корректные концы и отсутствие дубликатов; LASTCMDRET проверяется по типу функции."},
        {"order": 4, "requires_live_gap": True, "action_ru": "На отдельной модели проверить индекс/создание секции PIPE и UNITNAME каждого параметра; записать и независимо прочитать геометрию.",
         "success_criteria_ru": "Доказаны точные теги и единицы Length/ID/OD/Thickness, режим OD/Thickness и обработка FNA/FNAT; угадывания индексов и SI нет."},
        {"order": 5, "requires_live_gap": True, "action_ru": "Проверить SOURCE/SINK/VALVE соединения, реальное open/closed, DN/рейтинг и сворачивание разрешённых helper-объектов.",
         "success_criteria_ru": "Закрытый клапан сохраняется физически и блокирует путь; маскирование не подменяет физическое состояние без утверждённого правила."},
        {"order": 6, "requires_live_gap": True, "action_ru": "Собрать подтверждённый фрагмент, сохранить/открыть, получить независимый normalized_export и выполнить сравнение с утверждёнными допусками.",
         "success_criteria_ru": "Поле evidence=real ставится только фактическому readback; нет пропусков, лишних объектов или скрытого копирования входных параметров."},
    ]
    return {
        "schema_version": "1.0", "case_id": case_id,
        "execution_status": "ERROR" if issues else "SUCCESS",
        "assessment_status": "NOT_ASSESSED", "ready_for_production_build": False,
        "backend_production_status": "UNSUPPORTED_MAPPING", "runtime_verification": "NOT_ASSESSED",
        "profile_id": profile.get("profile_id"), "software": profile.get("software", {}),
        "engineering_validation": engineering, "counts": counts,
        "required_semantic_types": required, "capability_matrix": matrix,
        "official_references": deepcopy(references), "blockers": blockers,
        "next_verification_steps": steps, "issues": issues,
        "limitations": ["Отчёт только читает данные; COM и GAP не вызываются.",
                        "Наличие утверждённого манифеста не доказывает работоспособность production API.",
                        "Отчёт не создаёт модель, snapshot или normalized_export и не проверяет лицензионные файлы."],
    }
