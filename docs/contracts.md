# Контракты первого участника, версия 1.0

Все координаты `bbox=[x0,y0,x1,y1]` задаются в пунктах PDF, начало сверху слева. Нумерация страниц с 1. ID не зависит от подписи; контекст позволяет различать одноимённые узлы. Исходные файлы доступны только для чтения.

## Манифест

JSON: `schema_version`, `case_id`, `sources`, `selection`, `nodes`, `pipes`, `valves`, `ambiguities`.

- `sources`: `{id, path, sha256, kind:pdf|text, pages:[{number,width_pt,height_pt}]}`. `path` относительно корня проекта. Для текстового README `pages=[]`.
- `selection`: `{id, review_status, reviewed_by, reviewed_at, regions:[{source_id,page,bbox}], required_minima:{nodes:10,pipes:12,sources:2,valves:4}, notes}`.
- Общие поля объектов: `id`, `original_label`, `review_status` (`pending|confirmed|rejected`), `reviewed_by`, `reviewed_at`, `source_refs`.
- Ссылка PDF: `{source_id,page,bbox,raw_text,method,fields}`; `method=text_layer|visual|derived`, `fields` — поля, подтверждаемые ссылкой. Ссылка на текст: `{source_id,line_start,line_end,raw_text,method:text_file,fields}`; номера строк с 1.
- `nodes`: общие поля + `kind=junction|source|boundary`, `context`, `position:[x,y]`, `boundary_reason` (для границ).
- `pipes`: общие поля + `from_node`, `to_node`, `parameters:{length_m,outer_diameter_mm,wall_thickness_mm,inner_diameter_mm}` (числа или null), `role=pipeline|internal_connector`, `line_class`, `active_state=active|inactive|unknown`. Все pipes — физические связи, направление потока не выводится из порядка концов.
- `valves`: общие поля + `pipe_id`, `physical_state=open|closed|unknown`, `size_dn_mm` (число или null), `pressure_rating_raw` (строка или null). Рейтинг из `150х40` не объявляется толщиной трубы или давлением в барах без подтверждения единицы.
- `ambiguities`: `{id,kind,description,status:open|resolved,affected_ids,source_refs,resolution,reviewed_by,reviewed_at}`. Ключевые виды: `crossing`, `detail_mapping`, `missing_parameter`, `connection`, `valve_state`, `line_status`.

`confirmed` требует имени проверившего и времени ISO 8601. Число объектов кандидата не означает выполнение приёмки. Незакрытые неоднозначности блокируют готовность к переносу.

## Нормализованный экспорт GAP

Экспорт — отдельный JSON, формируемый адаптером второго участника:

```
{
  "schema_version":"1.0", "case_id":"...",
  "model":{"path":"...","software_version":"...","exported_at":"...","evidence":"real|synthetic"},
  "nodes":[{"id":"...","kind":"junction","original_label":"..."}],
  "pipes":[{"id":"...","from_node":"...","to_node":"...","parameters":{"length_m":1,"outer_diameter_mm":159,"wall_thickness_mm":8,"inner_diameter_mm":143},"active_state":"active"}],
  "valves":[{"id":"...","pipe_id":"...","physical_state":"open","size_dn_mm":150,"pressure_rating_raw":"40"}]
}
```

Экспорт должен быть считан из сохранённой модели, а не скопирован из входного манифеста. Сравнение физических концов трубы не зависит от их порядка. Синтетический экспорт служит тестам и не подтверждает интеграцию с GAP.

## Числовые сравнения

Отдельная конфигурация `numeric_tolerances` содержит для каждого параметра `{unit,absolute,approved_by,approved_at,source}`. Пример параметров: `length_m` (m), `outer_diameter_mm`, `wall_thickness_mm`, `inner_diameter_mm` (mm). Допуски не назначаются автоматически. Без конфигурации числовая оценка `NOT_ASSESSED`. Идентификаторы, связи, физические состояния и число объектов сравниваются точно.

Файл для `--tolerances` содержит непосредственно словарь параметров, **без** обёртки `numeric_tolerances`. Заготовка `data/tolerances.template.json` не утверждена и не разрешает оценку. Пример структуры одной записи: `{"length_m":{"unit":"m","absolute":null,"approved_by":null,"approved_at":null,"source":null}}`. Нулевой допуск допустим только после явного утверждения. `size_dn_mm` клапана — номинальный размер, сравнивается точно без численного допуска; неизвестный размер не считается успешной числовой проверкой.

## Результаты

Валидация: `{schema_version,case_id,execution_status,ready_for_transfer,stats,issues,topology}`. Issue: `{code,severity:error|warning|info,object_id,message,source_refs}`. `topology` различает физическую связность и разрешённые пути при текущих клапанах; для unknown выдаёт отдельно гарантированные/возможные связи.

Сравнение: `{schema_version,case_id,execution_status,assessment_status,structural_status,numeric_status,issues,summary,model}`. `execution_status=SUCCESS|ERROR` означает выполнение программы; `assessment_status=OK|OUT_OF_TOLERANCE|NOT_ASSESSED` означает результат проверки. Черновик и synthetic не получают подтверждённый OK. `structural_status` может отражать вычисленное сравнение независимо от общей готовности.

HTML-отчёт принимает манифест, результат валидации и необязательный результат сравнения. Должен явно показывать черновой статус, нерешённые вопросы и отсутствие реального экспорта.
