"""Validate engineering evidence and distinguish topology from permitted flow."""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from .sources import resolve_source

PARAMETERS = {"length_m": "m", "outer_diameter_mm": "mm", "wall_thickness_mm": "mm", "inner_diameter_mm": "mm"}
MINIMA = {"nodes": 10, "pipes": 12, "sources": 2, "valves": 4}


def finite_number(value):
    try:
        return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)
    except (ValueError, TypeError, OverflowError):
        return False


def confirmed(item):
    if not isinstance(item, dict) or item.get("review_status") != "confirmed":
        return False
    if not isinstance(item.get("reviewed_by"), str) or not item["reviewed_by"].strip():
        return False
    try:
        return datetime.fromisoformat(item.get("reviewed_at", "").replace("Z", "+00:00")).tzinfo is not None
    except (ValueError, TypeError, AttributeError):
        return False


def components(node_ids, edges):
    adjacency = {identifier: set() for identifier in node_ids}
    for left, right in edges:
        if left in adjacency and right in adjacency:
            adjacency[left].add(right)
            adjacency[right].add(left)
    result = []
    unseen = set(adjacency)
    while unseen:
        pending = [min(unseen)]
        group = set()
        while pending:
            current = pending.pop()
            if current in group:
                continue
            group.add(current)
            unseen.discard(current)
            pending.extend(adjacency[current] - group)
        result.append(sorted(group))
    return sorted(result, key=lambda group: group[0])


def validate_manifest(manifest: dict, project_root: Path | str | None = None, check_sources: bool = True) -> dict:
    issues = []

    def issue(code, severity, object_id, message, obj=None):
        refs = obj.get("source_refs", []) if isinstance(obj, dict) else []
        issues.append(dict(code=code, severity=severity, object_id=object_id if isinstance(object_id, str) else None,
                           message=message, source_refs=refs if isinstance(refs, list) else []))

    if not isinstance(manifest, dict):
        return {"schema_version": "1.0", "case_id": None, "execution_status": "ERROR", "ready_for_transfer": False,
                "stats": {}, "topology": {}, "issues": [{"code": "SCHEMA", "severity": "error", "object_id": None,
                "message": "Манифест должен быть объектом JSON", "source_refs": []}]}
    if manifest.get("schema_version") != "1.0":
        issue("SCHEMA_VERSION", "error", None, "Ожидается schema_version=1.0")
    if not isinstance(manifest.get("case_id"), str) or not manifest["case_id"].strip():
        issue("CASE_ID", "error", None, "Не указан case_id")
    groups = {}
    for category in ("sources", "nodes", "pipes", "valves", "ambiguities"):
        value = manifest.get(category)
        if not isinstance(value, list):
            issue("SCHEMA", "error", category, "Ожидается список")
            value = []
        groups[category] = [row for row in value if isinstance(row, dict)]
        if len(groups[category]) != len(value):
            issue("SCHEMA", "error", category, "Каждая запись должна быть объектом")
    sources = {}
    for source in groups["sources"]:
        identifier = source.get("id")
        if not isinstance(identifier, str) or not identifier:
            issue("SOURCE_ID", "error", None, "Не указан ID источника")
            continue
        if identifier in sources:
            issue("DUPLICATE_SOURCE_ID", "error", identifier, "Повтор ID источника")
        sources[identifier] = source
        kind = source.get("kind", "pdf")
        if kind not in ("pdf", "text"):
            issue("SOURCE_KIND", "error", identifier, "Неизвестный тип источника")
        path = source.get("path")
        valid_path = isinstance(path, str) and bool(path.strip()) and "\0" not in path
        if not valid_path:
            issue("SOURCE_PATH", "error", identifier, "Некорректный путь источника")
        page_list = source.get("pages")
        if not isinstance(page_list, list):
            issue("SOURCE_PAGES", "error", identifier, "Ожидается список страниц источника")
            page_list = []
        page_numbers = set()
        for page in page_list:
            if not isinstance(page, dict):
                issue("SOURCE_PAGE", "error", identifier, "Некорректное описание страницы")
                continue
            number = page.get("number")
            if not isinstance(number, int) or isinstance(number, bool) or number < 1 or number in page_numbers:
                issue("SOURCE_PAGE_NUMBER", "error", identifier, "Номер страницы должен быть уникальным целым числом с 1")
            else:
                page_numbers.add(number)
            if not all(finite_number(page.get(key)) and page[key] > 0 for key in ("width_pt", "height_pt")):
                issue("SOURCE_PAGE_SIZE", "error", identifier, "Размеры страницы должны быть положительными числами")
        digest = source.get("sha256", "")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            issue("SOURCE_HASH", "error", identifier, "Требуется SHA-256 в нижнем регистре")
        if check_sources:
            if not valid_path:
                continue
            try:
                if not Path(path).is_absolute() and project_root is None:
                    issue("SOURCE_ROOT", "error", identifier, "Для относительных путей нужен корень проекта")
                    continue
                file = resolve_source(source, project_root)
                actual = hashlib.sha256(file.read_bytes()).hexdigest()
                if actual != digest:
                    issue("SOURCE_CHANGED", "error", identifier, "Контрольная сумма исходного файла изменилась")
            except (OSError, ValueError) as exc:
                issue("SOURCE_UNAVAILABLE", "error", identifier, f"Источник недоступен: {exc}")

    def check_refs(obj, identifier):
        refs = obj.get("source_refs")
        if not isinstance(refs, list) or not refs:
            issue("MISSING_EVIDENCE", "error", identifier, "Нет ссылок на исходные данные", obj)
            return
        for ref in refs:
            if not isinstance(ref, dict):
                issue("BAD_EVIDENCE", "error", identifier, "Некорректная ссылка на источник", obj)
                continue
            source_id = ref.get("source_id")
            source = sources.get(source_id) if isinstance(source_id, str) else None
            if source is None:
                issue("UNKNOWN_SOURCE", "error", identifier, "Источник ссылки не найден", obj)
                continue
            fields = ref.get("fields")
            if not isinstance(fields, list) or not fields or not all(isinstance(field, str) and field for field in fields):
                issue("EVIDENCE_FIELDS", "error", identifier, "Нужен список полей, подтверждаемых ссылкой", obj)
            if not isinstance(ref.get("raw_text"), str) or not ref["raw_text"].strip():
                issue("EVIDENCE_TEXT", "error", identifier, "Нужен исходный текст или описание наблюдения", obj)
            methods = ("text_file",) if source.get("kind") == "text" else ("text_layer", "visual", "derived")
            if ref.get("method") not in methods:
                issue("EVIDENCE_METHOD", "error", identifier, "Неизвестный метод получения данных", obj)
            if source.get("kind", "pdf") == "text":
                start, end = ref.get("line_start"), ref.get("line_end")
                if not isinstance(start, int) or isinstance(start, bool) or not isinstance(end, int) or isinstance(end, bool) or not 1 <= start <= end:
                    issue("BAD_TEXT_LOCATION", "error", identifier, "Для текста нужны line_start/line_end с 1", obj)
                continue
            page_number, bbox = ref.get("page"), ref.get("bbox")
            pages = source.get("pages", [])
            page = next((p for p in pages if isinstance(p, dict) and p.get("number") == page_number), None) if isinstance(pages, list) else None
            if page is None or not isinstance(page_number, int) or isinstance(page_number, bool):
                issue("BAD_PAGE", "error", identifier, "Страница ссылки отсутствует в источнике", obj)
                continue
            if not isinstance(bbox, list) or len(bbox) != 4 or not all(finite_number(x) for x in bbox):
                issue("BAD_BBOX", "error", identifier, "bbox должен содержать 4 конечных числа", obj)
                continue
            width, height = page.get("width_pt"), page.get("height_pt")
            if not finite_number(width) or not finite_number(height) or not (0 <= bbox[0] < bbox[2] <= width and 0 <= bbox[1] < bbox[3] <= height):
                issue("BBOX_OUTSIDE_PAGE", "error", identifier, "Область выходит за границы страницы", obj)

    selection = manifest.get("selection")
    if not isinstance(selection, dict):
        selection = {}
        issue("SELECTION", "error", None, "Не задан выбранный фрагмент")
    if not isinstance(selection.get("id"), str) or not selection["id"].strip():
        issue("SELECTION_ID", "error", None, "Нужен идентификатор фрагмента")
    if not confirmed(selection):
        issue("SELECTION_PENDING", "warning", selection.get("id"), "Фрагмент требует подтверждения преподавателя")
    regions = selection.get("regions")
    if not isinstance(regions, list) or not regions:
        issue("SELECTION_REGIONS", "error", selection.get("id"), "Нужны области выбранного фрагмента")
    else:
        check_refs({"source_refs": regions}, selection.get("id"))
    all_ids = set()
    maps = {}
    entities_confirmed = True
    for category in ("nodes", "pipes", "valves"):
        mapping = {}
        for obj in groups[category]:
            identifier = obj.get("id")
            if not isinstance(identifier, str) or not identifier.strip():
                issue("OBJECT_ID", "error", category, "Пустой или некорректный ID")
                continue
            if identifier in all_ids:
                issue("DUPLICATE_ID", "error", identifier, "ID должен быть уникален среди всех объектов", obj)
            all_ids.add(identifier)
            mapping[identifier] = obj
            if not isinstance(obj.get("original_label"), str) or not obj["original_label"].strip():
                issue("ORIGINAL_LABEL", "error", identifier, "Не сохранена исходная подпись", obj)
            if obj.get("review_status") not in ("pending", "confirmed", "rejected"):
                issue("REVIEW_STATUS", "error", identifier, "Некорректный статус подтверждения", obj)
            if not confirmed(obj):
                entities_confirmed = False
                issue("OBJECT_PENDING", "warning", identifier, "Объект или его данные не подтверждены инженером", obj)
            check_refs(obj, identifier)
        maps[category] = mapping
    nodes, pipes, valves = maps["nodes"], maps["pipes"], maps["valves"]
    for identifier, node in nodes.items():
        if node.get("kind") not in ("junction", "source", "boundary"):
            issue("NODE_KIND", "error", identifier, "Неизвестный тип узла", node)
        if not isinstance(node.get("context"), str) or not node["context"].strip():
            issue("NODE_CONTEXT", "error", identifier, "Нужен текстовый контекст подписи", node)
        if node.get("kind") == "boundary" and (not isinstance(node.get("boundary_reason"), str) or not node["boundary_reason"].strip()):
            issue("BOUNDARY_REASON", "error", identifier, "Не объяснена граница фрагмента", node)
        point = node.get("position")
        if not isinstance(point, list) or len(point) != 2 or not all(finite_number(v) and v >= 0 for v in point):
            issue("NODE_POSITION", "error", identifier, "Нужна позиция узла в пунктах PDF", node)
    labels = Counter((n["original_label"], n["context"]) for n in nodes.values()
                     if isinstance(n.get("original_label"), str) and isinstance(n.get("context"), str))
    for (label, context), count in labels.items():
        if count > 1:
            issue("AMBIGUOUS_NODE_LABEL", "error", label, "Повтор подписи в одном контексте требует разрешения")

    known_geometry = True
    for identifier, pipe in pipes.items():
        left, right = pipe.get("from_node"), pipe.get("to_node")
        if not isinstance(left, str) or not isinstance(right, str) or left not in nodes or right not in nodes:
            issue("PIPE_ENDPOINT", "error", identifier, "Концы трубы должны ссылаться на существующие узлы", pipe)
        if left == right:
            issue("SELF_LOOP", "error", identifier, "Петля на одном узле требует отдельного обоснования", pipe)
        if pipe.get("role") not in ("pipeline", "internal_connector"):
            issue("PIPE_ROLE", "error", identifier, "Не указан тип физической связи", pipe)
        if pipe.get("active_state") not in ("active", "inactive", "unknown"):
            issue("LINE_STATUS", "error", identifier, "Некорректное состояние участка", pipe)
        if pipe.get("active_state") == "unknown":
            issue("LINE_STATUS_UNKNOWN", "warning", identifier, "Рабочее состояние участка неизвестно", pipe)
        params = pipe.get("parameters")
        if not isinstance(params, dict):
            issue("PIPE_PARAMETERS", "error", identifier, "Не задан объект параметров", pipe)
            continue
        for name in PARAMETERS:
            value = params.get(name)
            if value is None:
                known_geometry = False
                issue("MISSING_PARAMETER", "warning", identifier, f"Неизвестен {name}; значение не подставлялось", pipe)
            elif not finite_number(value) or value <= 0:
                issue("PARAMETER_RANGE", "error", identifier, f"{name} должен быть конечным положительным числом", pipe)
            else:
                refs = pipe.get("source_refs")
                refs = refs if isinstance(refs, list) else []
                if not any(isinstance(ref, dict) and isinstance(ref.get("fields"), list) and name in ref["fields"] for ref in refs):
                    issue("PARAMETER_EVIDENCE", "error", identifier, f"Не указан источник поля {name}", pipe)
        od, wall, inner = (params.get(p) for p in ("outer_diameter_mm", "wall_thickness_mm", "inner_diameter_mm"))
        if all(finite_number(value) for value in (od, wall)):
            expected = Decimal(str(od)) - 2 * Decimal(str(wall))
            if expected <= 0:
                issue("IMPOSSIBLE_DIAMETER", "error", identifier, "OD должен быть больше удвоенной толщины стенки", pipe)
            elif finite_number(inner) and Decimal(str(inner)) != expected:
                issue("INNER_DIAMETER", "error", identifier, f"ID должен равняться OD - 2*стенка = {expected} мм", pipe)
    by_pipe = {identifier: [] for identifier in pipes}
    for identifier, valve in valves.items():
        pipe_id = valve.get("pipe_id")
        if not isinstance(pipe_id, str) or pipe_id not in pipes:
            issue("VALVE_PIPE", "error", identifier, "Клапан должен быть привязан к существующей трубе", valve)
        else:
            by_pipe[pipe_id].append(valve)
        if valve.get("physical_state") not in ("open", "closed", "unknown"):
            issue("VALVE_STATE", "error", identifier, "Некорректное физическое состояние клапана", valve)
        elif valve["physical_state"] == "unknown":
            issue("VALVE_STATE_UNKNOWN", "warning", identifier, "Состояние клапана неизвестно", valve)
        dn = valve.get("size_dn_mm")
        if dn is not None and (not finite_number(dn) or dn <= 0):
            issue("VALVE_SIZE", "error", identifier, "Некорректный условный диаметр", valve)
        if valve.get("pressure_rating_raw") is not None and not isinstance(valve["pressure_rating_raw"], str):
            issue("VALVE_PRESSURE_RAW", "error", identifier, "Неподтверждённый рейтинг должен храниться строкой", valve)

    physical, certain, possible = [], [], []
    for identifier, pipe in pipes.items():
        ends = (pipe.get("from_node"), pipe.get("to_node"))
        if not all(isinstance(end, str) and end in nodes for end in ends):
            continue
        physical.append(ends)
        states = [v.get("physical_state") for v in by_pipe[identifier]]
        active = pipe.get("active_state")
        if active != "inactive" and "closed" not in states:
            possible.append(ends)
            if active == "active" and all(state == "open" for state in states):
                certain.append(ends)
    topology = {"physical_components": components(nodes, physical), "guaranteed_flow_components": components(nodes, certain),
                "possible_flow_components": components(nodes, possible), "physical_edge_count": len(physical),
                "guaranteed_flow_edge_count": len(certain), "possible_flow_edge_count": len(possible),
                "note": "Неориентированная достижимость по арматуре; гидравлический расчёт и направление потока не выполняются."}
    if len(topology["physical_components"]) != 1:
        issue("DISCONNECTED_FRAGMENT", "error", selection.get("id"), "Кандидат должен быть физически связным")
    ambiguity_ids = set()
    open_ambiguities = []
    for ambiguity in groups["ambiguities"]:
        identifier = ambiguity.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in ambiguity_ids:
            issue("AMBIGUITY_ID", "error", identifier, "Неоднозначность должна иметь уникальный ID")
        ambiguity_ids.add(identifier) if isinstance(identifier, str) else None
        check_refs(ambiguity, identifier)
        if not isinstance(ambiguity.get("kind"), str) or not ambiguity["kind"].strip():
            issue("AMBIGUITY_KIND", "error", identifier, "Нужен текстовый тип неоднозначности", ambiguity)
        if ambiguity.get("status") not in ("open", "resolved"):
            issue("AMBIGUITY_STATUS", "error", identifier, "Неизвестный статус неоднозначности", ambiguity)
        elif ambiguity["status"] == "open":
            open_ambiguities.append(ambiguity)
            issue("UNRESOLVED_AMBIGUITY", "warning", identifier, ambiguity.get("description", "Неоднозначность не разрешена"), ambiguity)
        elif not ambiguity.get("resolution") or not confirmed({**ambiguity, "review_status": "confirmed"}):
            issue("AMBIGUITY_REVIEW", "error", identifier, "Решение требует обоснования, автора и времени", ambiguity)
        affected = ambiguity.get("affected_ids")
        if not isinstance(affected, list):
            issue("AMBIGUITY_TARGET", "error", identifier, "affected_ids должен быть списком", ambiguity)
            affected = []
        for target in affected:
            if not isinstance(target, str) or (target not in all_ids and target != selection.get("id")):
                issue("AMBIGUITY_TARGET", "error", identifier, f"Неизвестный затронутый объект: {target}", ambiguity)
    stats = {"nodes": len(nodes), "pipes": len(pipes), "sources": sum(n.get("kind") == "source" for n in nodes.values()),
             "valves": len(valves), "open_valves": sum(v.get("physical_state") == "open" for v in valves.values()),
             "closed_valves": sum(v.get("physical_state") == "closed" for v in valves.values()),
             "unknown_valves": sum(v.get("physical_state") == "unknown" for v in valves.values()),
             "confirmed_objects": sum(confirmed(o) for category in (nodes, pipes, valves) for o in category.values()),
             "open_ambiguities": len(open_ambiguities), "physical_components": len(topology["physical_components"]),
             "guaranteed_flow_components": len(topology["guaranteed_flow_components"]),
             "possible_flow_components": len(topology["possible_flow_components"])}
    configured_minima = selection.get("required_minima", {})
    for name, minimum in MINIMA.items():
        configured = configured_minima.get(name, minimum) if isinstance(configured_minima, dict) else minimum
        if not isinstance(configured, int) or isinstance(configured, bool) or configured < minimum:
            issue("MINIMUM_CONFIG", "error", name, f"Учебный минимум {name} нельзя уменьшать ниже {minimum}")
            configured = minimum
        if stats[name] < configured:
            issue("MINIMUM_NOT_MET", "error", name, f"Есть {stats[name]}, требуется не менее {configured}")
    for state in ("open", "closed"):
        if not any(v.get("physical_state") == state for v in valves.values()):
            issue("REQUIRED_VALVE_STATE", "error", state, f"Не представлен клапан {state}")
    feature_kinds = {a["kind"] for a in groups["ambiguities"] if isinstance(a.get("kind"), str)}
    for kind in ("crossing", "detail_mapping"):
        if kind not in feature_kinds:
            issue("REQUIRED_FEATURE", "error", kind, f"Не зафиксирован учебный случай {kind}")
    errors = sum(i["severity"] == "error" for i in issues)
    stats.update(errors=errors, warnings=sum(i["severity"] == "warning" for i in issues))
    known_states = all(p.get("active_state") in ("active", "inactive") for p in pipes.values()) and all(v.get("physical_state") in ("open", "closed") for v in valves.values())
    ready = not errors and confirmed(selection) and entities_confirmed and known_geometry and known_states and not open_ambiguities
    return {"schema_version": "1.0", "case_id": manifest.get("case_id"), "execution_status": "SUCCESS",
            "ready_for_transfer": ready, "stats": stats, "topology": topology, "issues": issues}
