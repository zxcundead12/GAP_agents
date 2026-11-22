# Интерфейс участника 2

MCP: stdio, stdout принадлежит протоколу, диагностические сообщения — stderr.

| Инструмент | Аргументы | Результат |
|---|---|---|
| gap_environment | — | Библиотеки/версия/пути; лицензия не проверяется |
| gap_probe | — | COM и текущая сессия без создания модели |
| gap_prepare_smoke | — | plan_id, plan_sha256, summary, target_model_path |
| gap_prepare_manifest | manifest_path, mapping_path | План только после инженерной проверки и согласования mapping |
| gap_execute | plan_id, approval_id | Результат конкретного плана; без receipt — CONFIRMATION_REQUIRED |
| gap_export | model_path | Raw snapshot; для проверенного профиля также normalized_export |

Общий ответ содержит execution_status, message и trace_id. В журнале worker
по trace_id сохранены фактические вызовы API, аргументы, ответы и время.
SUCCESS означает выполнение, не инженерную точность или результат гидравлики.

Локальный CLI: environment, probe, prepare-smoke, prepare-manifest, readiness,
approve, execute, acknowledge, export. Подтверждение и ручной разбор не опубликованы
как инструменты для языковой модели. Receipt связан с точным хешем плана.

Отказы: NOT_READY, CONFIRMATION_REQUIRED, SESSION_BUSY, COM_UNAVAILABLE, API_ERROR,
TIMEOUT, UNSUPPORTED_MAPPING, LICENSE_UNAVAILABLE. LICENSE_BUSY используется только
при соответствующем фактическом ответе ПО, не вместо отсутствующей лицензии.
Текущий известный отказ START: native 22, No OpenServer license available.

## Инженерный вход

Манифест передаётся участником 1, исходные файлы только читаются.
Поля: schema_version=1.0, case_id, sources, selection, nodes, pipes, valves, ambiguities.
У объектов id, original_label, review_status, reviewed_by, reviewed_at, source_refs.
nodes.kind: junction/source/boundary; pipes: from_node, to_node, active_state,
parameters.{length_m,outer_diameter_mm,wall_thickness_mm,inner_diameter_mm};
valves: pipe_id, physical_state, size_dn_mm, pressure_rating_raw.

Инженерный валидатор поставляется отдельно и проверяет фрагмент, связи, легенду,
единицы и источники. Входы и разрешённые операции дополнительно проверяет сервис;
пути записи ограничены workspace, источники повторно связаны с SHA-256.

## Нормализованный экспорт

schema_version=1.0, case_id, model:{path,software_version,exported_at,evidence},
nodes:[{id,kind,original_label}], pipes:[{id,from_node,to_node,parameters,active_state}],
valves:[{id,pipe_id,physical_state,size_dn_mm,pressure_rating_raw}].
evidence=real допустимо только при фактическом чтении сохранённой модели;
искусственные unit-снимки явно маркируются synthetic. Нормализатор получает
только прочитанный snapshot и метаданные модели, исходного манифеста у него нет.
Недостающие значения не подставляются. Сравнение экспорта выполняет участник 1.
