"""Standalone, read-only HTML review of an engineering manifest (stdlib only)."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import math
import mimetypes
from pathlib import Path
from typing import Any


_LABELS = {
    "pending": "Ожидает проверки", "confirmed": "Подтверждено", "rejected": "Отклонено",
    "open": "Открыт", "closed": "Закрыт", "unknown": "Неизвестно",
    "resolved": "Решено", "active": "Действует", "inactive": "Не действует",
    "junction": "Соединительный узел", "source": "Источник", "boundary": "Граница",
    "pipeline": "Трубопровод", "internal_connector": "Внутреннее соединение",
    "error": "Ошибка", "warning": "Предупреждение", "info": "Информация",
}
_PARAMETERS = {
    "length_m": "Длина, м", "outer_diameter_mm": "Наружный диаметр, мм",
    "wall_thickness_mm": "Толщина стенки, мм", "inner_diameter_mm": "Внутренний диаметр, мм",
}


def _value(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _e(value: Any) -> str:
    return html.escape(_value(value), quote=True)


def _records(value: Any) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _finite(value: Any) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _anchor(source_id: Any) -> str:
    return "source-" + hashlib.sha256(str(source_id).encode("utf-8")).hexdigest()[:16]


def _status(value: Any) -> str:
    return f'<span class="status">{_e(_LABELS.get(str(value), value))} <code>{_e(value)}</code></span>'


def _refs(refs: Any) -> str:
    rows = []
    for ref in _records(refs):
        location = []
        if ref.get("page") is not None:
            location.append("стр. " + _value(ref["page"]))
        if ref.get("bbox") is not None:
            location.append("bbox " + _value(ref["bbox"]))
        if ref.get("line_start") is not None:
            end = ref.get("line_end", ref["line_start"])
            location.append(f'строки {ref["line_start"]}–{end}')
        if ref.get("text_file"):
            location.append(_value(ref["text_file"]))
        source_id = ref.get("source_id")
        source_link = (
            f'<a href="#{_anchor(source_id)}">{_e(source_id)}</a>'
            if source_id is not None else "—"
        )
        rows.append(
            '<li>' + source_link + ": " + _e("; ".join(location) or "координаты не заданы")
            + f'<br><span class="muted">Метод: {_e(ref.get("method"))}; поля: {_e(ref.get("fields"))}</span>'
            + (f'<blockquote>{_e(ref["raw_text"])}</blockquote>' if ref.get("raw_text") else "")
            + '</li>'
        )
    return '<ul class="refs">' + "".join(rows) + '</ul>' if rows else '<span class="muted">Ссылка не задана</span>'


def _review(record: dict) -> str:
    return _status(record.get("review_status")) + (
        f'<br><span class="muted">Проверил: {_e(record.get("reviewed_by"))}'
        f'<br>Время: {_e(record.get("reviewed_at"))}</span>'
    )


def _table(headers: list[str], rows: list[list[str]], empty: str = "Нет записей") -> str:
    if not rows:
        return f'<p class="muted">{_e(empty)}</p>'
    return (
        '<div class="table-scroll"><table><thead><tr>'
        + "".join(f'<th scope="col">{_e(header)}</th>' for header in headers)
        + '</tr></thead><tbody>'
        + "".join('<tr>' + "".join(f'<td>{cell}</td>' for cell in row) + '</tr>' for row in rows)
        + '</tbody></table></div>'
    )


def _issue_table(issues: Any) -> str:
    rows = [
        [_status(item.get("severity")), _e(item.get("code")), _e(item.get("object_id")),
         _e(item.get("message")), _refs(item.get("source_refs"))]
        for item in _records(issues)
    ]
    return _table(["Уровень", "Код", "Объект", "Описание", "Источник"], rows)


def _component_count(topology: dict, names: tuple[str, ...]) -> Any:
    """Accept either explicit counters or lists of graph components."""
    for name in names:
        if name not in topology:
            continue
        value = topology[name]
        if isinstance(value, dict):
            for counter in ("component_count", "connected_component_count", "count"):
                if counter in value:
                    return value[counter]
            for listing in ("components", "connected_components"):
                if isinstance(value.get(listing), list):
                    return len(value[listing])
        elif isinstance(value, list):
            return len(value)
        elif isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _page_overlay(manifest: dict, validation: dict, comparison: dict | None, image_path: Path) -> str:
    sources = _records(manifest.get("sources"))
    selection = _mapping(manifest.get("selection"))
    regions = _records(selection.get("regions"))
    first = next((ref for ref in regions if ref.get("source_id") is not None and ref.get("page") is not None), None)
    if first is None:
        return '<p class="notice">Изображение страницы не показано: у кандидата нет ссылки на страницу и её размер.</p>'
    source = next((src for src in sources if src.get("id") == first["source_id"]), {})
    page = next((p for p in _records(source.get("pages")) if p.get("number") == first["page"]), {})
    width, height = page.get("width_pt"), page.get("height_pt")
    if not _finite(width) or not _finite(height) or width <= 0 or height <= 0:
        return '<p class="notice">Изображение страницы не показано: отсутствуют корректные размеры страницы PDF.</p>'
    media_type = mimetypes.guess_type(image_path.name)[0]
    if media_type not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
        return f'<p class="notice">Неподдерживаемый формат изображения: {_e(image_path.name)}. Используйте PNG, JPEG, GIF или WebP.</p>'
    try:
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    except OSError as exc:
        return f'<p class="notice">Не удалось прочитать изображение страницы: {_e(str(exc))}</p>'
    overlays = [(ref, "region", f'Кандидат {index}') for index, ref in enumerate(regions, 1)]
    for item in _records(manifest.get("ambiguities")):
        if item.get("status") != "resolved":
            overlays.extend((ref, "ambiguity", _value(item.get("id"))) for ref in _records(item.get("source_refs")))
    for issue in _records(validation.get("issues")) + _records((comparison or {}).get("issues")):
        overlays.extend((ref, "issue", _value(issue.get("code"))) for ref in _records(issue.get("source_refs")))
    # Many validation findings refer to the same source area. Draw a single
    # rectangle per visual category and area; preserve a compact list of IDs.
    # Separate categories keep a candidate outline distinct from a warning.
    grouped = {}
    for ref, kind, label in overlays:
        bbox = ref.get("bbox")
        if ref.get("source_id") != first["source_id"] or ref.get("page") != first["page"]:
            continue
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        if not all(_finite(number) for number in bbox):
            continue
        x0, y0, x1, y1 = bbox
        if x1 <= x0 or y1 <= y0 or not (0 <= x0 <= x1 <= width and 0 <= y0 <= y1 <= height):
            continue
        key = (kind, tuple(bbox))
        labels = grouped.setdefault(key, [])
        if label not in labels:
            labels.append(label)
    rectangles = []
    for (kind, bbox), labels in grouped.items():
        x0, y0, x1, y1 = bbox
        visible_label = labels[0] + (f" (+{len(labels)-1})" if len(labels) > 1 else "")
        title = "; ".join(labels[:8])
        if len(labels) > 8:
            title += f"; ещё {len(labels)-8} (полный список — в таблицах)"
        # Values used in SVG attributes are verified numeric, never free text.
        rectangles.append(
            f'<g class="{kind}"><title>{_e(title)}</title>'
            f'<rect x="{x0:g}" y="{y0:g}" width="{x1-x0:g}" height="{y1-y0:g}"/>'
            f'<text x="{x0:g}" y="{max(12, y0-3):g}">{_e(visible_label)}</text></g>'
        )
    return (
        f'<p>Источник: <a href="#{_anchor(first["source_id"])}">{_e(first["source_id"])}</a>, '
        f'страница {_e(first["page"])}. Координаты — пункты PDF от верхнего левого угла. '
        'Изображение должно быть рендером всей этой страницы без обрезки.</p>'
        '<p class="legend"><span class="swatch region-color">Синий — выбранный кандидат</span> '
        '<span class="swatch ambiguity-color">Оранжевый — открытый вопрос</span> '
        '<span class="swatch issue-color">Красный — замечание проверки</span></p>'
        '<p class="muted">Указатель мыши на рамке показывает её ID. Браузерное увеличение помогает рассмотреть подписи. '
        'Ссылки без координат остаются в таблицах ниже. Для других страниц приведены текстовые ссылки.</p>'
        '<figure class="scheme"><svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width:g} {height:g}" role="img" aria-label="Страница схемы с рамками кандидата и замечаний">'
        f'<image href="data:{media_type};base64,{encoded}" width="{width:g}" height="{height:g}"/>'
        + "".join(rectangles) + '</svg>'
        f'<figcaption>Показано рамок: {len(rectangles)}. Совпадающие области одной категории объединены. {_e(image_path.name)}</figcaption></figure>'
    )


_CSS = """
:root{color-scheme:light;--ink:#182b37;--muted:#536574;--line:#d4dde4;--paper:#fff;--bg:#edf2f5;--accent:#0d4c75}
*{box-sizing:border-box}body{margin:0;background:var(--bg);font:15px/1.5 system-ui,'Segoe UI',sans-serif;color:var(--ink)}
main{max-width:1500px;margin:0 auto;padding:28px}h1{font-size:29px;line-height:1.2;margin:0 0 12px}h2{font-size:22px;margin:0 0 14px}h3{font-size:17px}a{color:var(--accent)}p{margin:10px 0}
section,.intro{background:var(--paper);padding:22px;margin:18px 0;border:1px solid var(--line);border-radius:10px}nav{display:flex;flex-wrap:wrap;gap:8px 20px;margin:16px 0}
.notice{padding:12px 16px;background:#fff3d3;border-left:5px solid #b87800}.safe{background:#e4f1eb;border-color:#3a7156}.muted{color:var(--muted)}.small{font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:16px 0}.card{padding:14px;border:1px solid var(--line);border-radius:7px;background:#f8fafb}.card strong{display:block;font-size:23px}.status{white-space:normal;font-weight:600}code{font:13px/1.5 ui-monospace,Consolas,monospace;overflow-wrap:anywhere;background:#eef3f6;padding:1px 3px;border-radius:3px}
.table-scroll{overflow:auto;max-width:100%;border:1px solid var(--line)}table{border-collapse:collapse;width:100%;font-size:14px;text-align:left}th,td{border-bottom:1px solid var(--line);padding:10px;vertical-align:top;overflow-wrap:anywhere;min-width:90px}th{background:#eaf1f6;position:sticky;top:0}tbody tr:nth-child(even){background:#f9fbfc}td:first-child{min-width:110px}.refs{margin:0;padding-left:18px;min-width:210px}.refs li+li{margin-top:10px}blockquote{margin:5px 0;padding-left:8px;border-left:2px solid var(--line);white-space:pre-wrap}
dl{display:grid;grid-template-columns:minmax(130px,220px) 1fr;gap:6px 14px}dt{font-weight:600}dd{margin:0;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6f8;padding:14px;border-radius:5px;font-size:12px}details{margin:14px 0}summary{cursor:pointer;font-weight:600}
.scheme{margin:15px 0}.scheme svg{display:block;width:100%;height:auto;background:white;border:1px solid var(--line)}figcaption{font-size:13px;color:var(--muted)}.scheme rect{fill-opacity:.03;stroke-width:2;vector-effect:non-scaling-stroke}.scheme text{font:bold 12px sans-serif;paint-order:stroke;stroke:white;stroke-width:3px;stroke-linejoin:round}.region rect{stroke:#0c62be;fill:#0c62be}.region text{fill:#0c62be}.ambiguity rect{stroke:#cf7100;fill:#cf7100;stroke-dasharray:5 3}.ambiguity text{fill:#a25200}.issue rect{stroke:#cc2631;fill:#cc2631}.issue text{fill:#b0202b}.legend{display:flex;flex-wrap:wrap;gap:10px}.swatch{border-left:5px solid;padding-left:7px}.region-color{border-color:#0c62be}.ambiguity-color{border-color:#cf7100}.issue-color{border-color:#cc2631}
footer{padding:15px;color:var(--muted);font-size:13px}@media(max-width:650px){main{padding:10px}section,.intro{padding:14px}h1{font-size:24px}dl{grid-template-columns:1fr}dd{margin-bottom:8px}}@media print{body{background:white}main{padding:0}section,.intro{break-inside:avoid;border-radius:0}nav{display:none}.table-scroll{overflow:visible}th{position:static}table{font-size:10px}pre{font-size:9px}}
"""


def write_html_report(
    manifest: dict,
    validation: dict,
    output_path: Path | str,
    comparison: dict | None = None,
    page_image: Path | str | None = None,
) -> Path:
    """Write a portable report without changing evidence or approving any record.

    ``page_image`` must be the complete, uncropped page referenced by the first
    selection region. The source page size supplies the SVG coordinate space.
    This report does not invoke GAP or claim that a synthetic export is real.
    """
    output = Path(output_path)
    selection = _mapping(manifest.get("selection"))
    nodes, pipes, valves = (_records(manifest.get(key)) for key in ("nodes", "pipes", "valves"))
    ambiguities = _records(manifest.get("ambiguities"))
    open_questions = [item for item in ambiguities if item.get("status") != "resolved"]
    ready = validation.get("ready_for_transfer") is True and selection.get("review_status") == "confirmed"
    evidence = _mapping((comparison or {}).get("model"))
    real_export = bool(comparison) and evidence.get("evidence") == "real"
    title = "Проверка кандидата схемы для GAP"
    parts = [
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
        f'<title>{title} — {_e(manifest.get("case_id"))}</title><style>{_CSS}</style></head><body><main>',
        f'<header class="intro"><h1>{title}</h1><p>Кейс: <code>{_e(manifest.get("case_id"))}</code>; '
        f'версия схемы данных: {_e(manifest.get("schema_version"))}.</p>',
        '<p class="notice safe">Валидация заявляет готовность к переносу; подтверждение источника и фрагмента зафиксировано в данных.</p>' if ready
        else '<p class="notice"><strong>Черновик: к переносу не готов.</strong> Это кандидат для проверки инженером и преподавателем. '
             'Количество записей само по себе не подтверждает выполнение задания.</p>',
    ]
    if not comparison:
        parts.append('<p class="notice"><strong>Реальный экспорт GAP не предоставлен.</strong> Создание объектов в GAP и их соответствие исходным данным не проверены.</p>')
    elif not real_export:
        parts.append('<p class="notice"><strong>Реальный экспорт GAP не подтверждён.</strong> Синтетический или неидентифицированный экспорт пригоден только для проверки программы. Интеграция с GAP и приёмка этим отчётом не подтверждаются.</p>')
    elif not ready:
        parts.append('<p class="notice">Передан результат сравнения с реальным экспортом GAP, но исходный кандидат ещё не готов к переносу. Вычисленные совпадения не означают инженерную приёмку.</p>')
    else:
        parts.append('<p>Передан результат сравнения с реальным экспортом GAP. Его статусы и ограничения приведены ниже.</p>')
    parts.append('<nav aria-label="Разделы отчёта">' + "".join(
        f'<a href="#{anchor}">{label}</a>' for anchor, label in [
            ("selection", "Фрагмент"), ("scheme", "Схема"), ("questions", "Открытые вопросы"),
            ("objects", "Объекты"), ("validation", "Валидация"), ("comparison", "Сравнение с GAP"), ("sources", "Источники")
        ]) + '</nav></header>')
    required = _mapping(selection.get("required_minima"))
    counts = [("Узлы", len(nodes), required.get("nodes")), ("Трубы / ветви", len(pipes), required.get("pipes")),
              ("Источники", sum(node.get("kind") == "source" for node in nodes), required.get("sources")),
              ("Клапаны", len(valves), required.get("valves")), ("Открытые вопросы", len(open_questions), None)]
    parts.append('<section id="selection"><h2>Выбранный фрагмент</h2><div class="cards">' + "".join(
        f'<div class="card">{label}<strong>{count}</strong><span class="muted">' + (f'Минимум по заданию: {_e(minimum)}' if minimum is not None else 'Требуют решения') + '</span></div>'
        for label, count, minimum in counts
    ) + '</div>')
    parts.append(f'<dl><dt>ID фрагмента</dt><dd>{_e(selection.get("id"))}</dd><dt>Проверка</dt><dd>{_review(selection)}</dd>'
                 f'<dt>Примечание</dt><dd>{_e(selection.get("notes"))}</dd></dl>')
    parts.append('<p class="muted">Числа отражают записи кандидата. Узлы внутренних соединений считаются узлами только после согласования. '
                 'Рамки PDF, состояния клапанов и соответствие деталировок требуют самостоятельной инженерной проверки.</p>'
                 + _refs(selection.get("regions")) + '</section>')
    parts.append('<section id="scheme"><h2>Кандидат и проблемные участки на схеме</h2>')
    parts.append(_page_overlay(manifest, validation, comparison, Path(page_image)) if page_image
                 else '<p class="muted">Рендер страницы не передан. Местоположение объектов и вопросов указано в ссылках на источники.</p>')
    parts.append('</section><section id="questions"><h2>Неоднозначности и инженерные решения</h2>')
    parts.append(_table(["ID / вид", "Статус", "Описание / затронутые объекты", "Решение и проверка", "Источник"], [
        [f'<code>{_e(item.get("id"))}</code><br>{_e(item.get("kind"))}',
         _status(item.get("status")), _e(item.get("description")) + '<br><code>' + _e(item.get("affected_ids")) + '</code>',
         _e(item.get("resolution")) + f'<br><span class="muted">{_e(item.get("reviewed_by"))}<br>{_e(item.get("reviewed_at"))}</span>',
         _refs(item.get("source_refs"))] for item in ambiguities
    ]))
    parts.append('</section><section id="objects"><h2>Объекты кандидата</h2><h3>Узлы, источники и границы</h3>')
    parts.append(_table(["ID / исходная подпись", "Тип / контекст", "Позиция / основание границы", "Проверка", "Источник"], [
        [f'<code>{_e(item.get("id"))}</code><br>{_e(item.get("original_label"))}',
         _status(item.get("kind")) + '<br>' + _e(item.get("context")),
         _e(item.get("position")) + '<br>' + _e(item.get("boundary_reason")), _review(item), _refs(item.get("source_refs"))]
        for item in nodes
    ]))
    parts.append('<h3>Трубы и ветви</h3><p class="muted">Порядок концов трубы не задаёт направление потока. '
                 'Физическое соединение сохраняется и при закрытом клапане. Пропуск параметра обозначен «—», без подстановки предположения.</p>')
    parts.append(_table(["ID / исходная подпись", "Физические концы", "Роль / класс / действие", "Параметры", "Проверка", "Источник"], [
        [f'<code>{_e(item.get("id"))}</code><br>{_e(item.get("original_label"))}',
         f'<code>{_e(item.get("from_node"))}</code> ↔ <code>{_e(item.get("to_node"))}</code>',
         _status(item.get("role")) + '<br>' + _e(item.get("line_class")) + '<br>' + _status(item.get("active_state")),
         '<dl>' + "".join(f'<dt>{label}</dt><dd>{_e(_mapping(item.get("parameters")).get(key))}</dd>' for key, label in _PARAMETERS.items()) + '</dl>',
         _review(item), _refs(item.get("source_refs"))] for item in pipes
    ]))
    parts.append('<h3>Клапаны</h3><p class="muted">DN и исходное обозначение рейтинга хранятся отдельно. '
                 'Обозначение вида «150×40» не является доказательством толщины стенки или давления в барах.</p>')
    parts.append(_table(["ID / исходная подпись", "Труба", "Физическое состояние", "Размер / исходный рейтинг", "Проверка", "Источник"], [
        [f'<code>{_e(item.get("id"))}</code><br>{_e(item.get("original_label"))}', _e(item.get("pipe_id")),
         _status(item.get("physical_state")), f'DN, мм: {_e(item.get("size_dn_mm"))}<br>Рейтинг: {_e(item.get("pressure_rating_raw"))}',
         _review(item), _refs(item.get("source_refs"))] for item in valves
    ]))
    parts.append('</section><section id="validation"><h2>Проверка исходного манифеста</h2>')
    parts.append(f'<p>Выполнение программы: <code>{_e(validation.get("execution_status"))}</code>. '
                 f'Поле ready_for_transfer: <code>{_e(validation.get("ready_for_transfer"))}</code>. '
                 'Успешное выполнение программы не означает готовность инженерных данных.</p>')
    topology = validation.get("topology") or {}
    if not isinstance(topology, dict):
        topology = {}
    components = [
        ("Физическая связность", _component_count(topology, ("physical", "physical_components", "physical_component_count"))),
        ("Гарантированные пути протока", _component_count(topology, ("guaranteed_flow", "guaranteed", "permitted", "guaranteed_components", "guaranteed_flow_components"))),
        ("Возможные пути протока", _component_count(topology, ("possible_flow", "possible", "possible_components", "possible_flow_components"))),
    ]
    parts.append('<h3>Связность графа</h3>' + _table(["Граф", "Число компонент связности"], [[label, _e(count)] for label, count in components]))
    parts.append('<p class="muted">Физический граф описывает соединение труб. Гарантированный проток учитывает известные закрытые клапаны '
                 'и исключает неопределённые состояния; возможный допускает неизвестные состояния как открытые. '
                 'Количество компонент само по себе не подтверждает гидравлическую работоспособность.</p>')
    parts.append(_issue_table(validation.get("issues")))
    parts.append('<details><summary>Полные счётчики и топология</summary><pre>' + _e(json.dumps(
        {"stats": validation.get("stats"), "topology": validation.get("topology")}, ensure_ascii=False, indent=2
    )) + '</pre></details></section>')
    parts.append('<section id="comparison"><h2>Сравнение с экспортом GAP</h2>')
    if comparison:
        parts.append(_table(["Проверка", "Статус"], [
            ["Выполнение программы", _e(comparison.get("execution_status"))],
            ["Общая оценка", _e(comparison.get("assessment_status"))],
            ["Структура и дискретные состояния", _e(comparison.get("structural_status"))],
            ["Числовые параметры", _e(comparison.get("numeric_status"))],
            ["Происхождение экспорта", _e(evidence.get("evidence"))],
        ]))
        parts.append('<p class="muted">Структурное совпадение и числовая оценка показаны отдельно. Без согласованных числовых допусков '
                     'числовая оценка должна быть NOT_ASSESSED. Даже вычисленное OK при черновом манифесте или синтетическом экспорте не является приёмкой.</p>')
        parts.append(_issue_table(comparison.get("issues")))
        parts.append('<details><summary>Модель и сводка сравнения</summary><pre>' + _e(json.dumps(
            {"model": comparison.get("model"), "summary": comparison.get("summary")}, ensure_ascii=False, indent=2
        )) + '</pre></details>')
    else:
        parts.append('<p>Структура: <code>NOT_ASSESSED</code>. Числовые параметры: <code>NOT_ASSESSED</code>. '
                     'Необходим отдельный нормализованный экспорт, считанный адаптером из сохранённой модели GAP.</p>')
    parts.append('</section><section id="sources"><h2>Источники и контрольные суммы</h2>')
    parts.append(_table(["ID", "Файл", "SHA-256", "Страницы / размеры в пунктах PDF"], [
        [f'<span id="{_anchor(src.get("id"))}"><code>{_e(src.get("id"))}</code></span>', _e(src.get("path")),
         '<code>' + _e(src.get("sha256")) + '</code>', _e(src.get("pages"))] for src in _records(manifest.get("sources"))
    ]))
    parts.append('<p class="muted">Контрольные суммы приведены из манифеста. HTML не перечитывает исходные документы и не подтверждает '
                 'их подлинность самостоятельно. Проверка хеша выполняется отдельной командой валидации источников.</p></section>')
    parts.append('<footer>Отчёт сформирован для ручной проверки. Он не меняет манифест, не подтверждает объекты и не выполняет команды GAP. '
                 'Все изображения встроены; соединение с интернетом и JavaScript для просмотра не нужны.</footer></main></body></html>')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(parts), encoding="utf-8")
    return output
