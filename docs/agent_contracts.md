# Общие форматы, версия 1.0

Это реализованные контракты для всех трёх частей; их принятие участниками и
преподавателем не подменяется генерацией файла.

## Манифест и экспорт

Совместимы с docs/contracts.md. Валидатор проверяет источники, геометрию, единицы
через канонические поля, связи, подтверждения и проток. sources в манифесте —
документы; кусты находятся в nodes[kind=source], границы — nodes[kind=boundary].
Локальная конфигурация местоположения не меняет SHA и инженерное подтверждение.

normalize_snapshot(snapshot, model, case_id) не получает исходный манифест.
Отсутствующие native значения не заменяются ожидаемыми параметрами.

## AgentTask

schemas/agent_task.schema.json, dataclass gap_agent.contracts.AgentTask.
Поля: operation, case_id, manifest_path, mapping_path, export_path, model_path,
tolerances_path, clarifications. Все обязательны; отсутствующий путь — null.

Операции: inspect, validate, report, compare, build, smoke, export, unsupported.
Команды API, параметры труб, reviewer и approval_id не входят в ответ LLM.
Пути ограничены workspace, case_id сверяется, обязательные поля проверяются.
Неоднозначность возвращается вопросом до MCP.

## План изменений

schemas/change_plan.schema.json: run_id, время, задача, хеши входов, ID/хеш плана
интеграции, целевая копия, действия и происхождение разбора LLM.
SHA самого плана вычисляется без включения хеша внутрь JSON; в ответе — plan.sha256.

Локальный receipt связывает хеш, имя/время и receipt интеграции. approve — только
локальная команда человека. Изменение входов/плана блокирует выполнение, SQLite
исключает одновременный повтор. Подтверждение исполнения не подтверждает инженерию.
Входы производственного плана также фиксируют хеши протокола испытаний,
его модели и журнала, а также шаблона GAP. Эти файлы перечитываются перед
подтверждением и исполнением; замена согласованной зависимости требует нового плана.

## AgentResult и журнал

schemas/agent_result.schema.json, validate_result. Поля: schema_version, run_id,
trace_id, execution_status, assessment_status, message, task, plan, artifacts,
issues, llm. Оценки: OK, OUT_OF_TOLERANCE, NOT_ASSESSED. Выполнение и оценка различны.
Smoke не подтверждает производственный фрагмент. llm различает real/synthetic,
факт обращения, модель и метаданные запроса/ответа без Authorization.

trace_id равен run_id. JSONL хранит llm_request/response, mcp_request/response,
plan_prepared, human_approval, agent_result. tool_trace_id связывает записи с
фактическими native вызовами. Настроенные секреты удаляются из общего журнала.

LLM получает пользовательский запрос и минимальный контекст ID/путей. Тексты
документов и notes не добавляются к инструкциям. Содержащаяся в документе команда
не выполняется; ответ LLM не может создать receipt или произвольную команду API.
