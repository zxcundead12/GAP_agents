# Интерфейс второй части

Корень проекта задаётся текущей папкой/параметром workspace, без привязки к диску. Все планы, подтверждения, модели и логи внутри корня. Рантайм `.venv/Scripts/python.exe`; SDK MCP устанавливается в `.venv`. COM прогID `PX32.OpenServer.1`, out-of-process `CLSCTX_LOCAL_SERVER`. Дополнения агента — docs/agent_contracts.md, guarded production-профиль — docs/production_profile.md. Прежние журналы с F: являются историческими.

## Backend

`gap_integration.backend.run_job(job:dict, workspace:Path|str, timeout_seconds:float=60)->dict` запускает отдельный Python worker и возвращает структурированный ответ. Свободные команды и произвольные теги не принимаются. Обязательные поля ответа: `execution_status`, `trace_id`, `message`; дополнительные `snapshot`, `checks`, `model`, `journal_path`, `details`.

Доступные `job`:

- `{operation:"probe"}` — проверка COM и состояния GAP, без запуска GAP и без записи модели.
- `{operation:"smoke",model_path:abs}` — отдельная искусственная модель: JOINT AG_J1 + AG_J2 и автоматически создаваемый PIPE AG_P1; сохранение/открытие/чтение/повтор без дублей. При чужой сессии отказ SESSION_BUSY.
- `{operation:"export",model_path:abs}` — чтение уже сохранённой модели, без переключения чужой сессии. Если GAP закрыт, допустимо открыть **только модель внутри проекта**; owned session может быть закрыта после чтения.
- `{operation:"build",model_path:abs,manifest:dict,mapping:dict}` — перенос только утверждённого манифеста с явно утверждённым отображением GAP и единиц; реализация сообщает UNSUPPORTED_MAPPING, если mapping не подтверждён/не поддержан. Неполный production-кандидат сюда не поступает.

Рабочий API не принимает команды LLM как строки OpenServer. `NEWFILE`, `OPENFILE` не выполняются поверх чужой сессии. При таймауте worker не убивает GAP: состояние UNKNOWN/TIMEOUT и инструкция проверки, пока поддержанная отмена не подтверждена. Дубли одного задания должны быть исключены сервисом до запуска worker.

## Service

`GapService(workspace:Path|str, runner=None)`, runner по умолчанию backend.run_job.

Методы возвращают JSON dict и не печатают в stdout:

- `inspect_environment()` — доступность библиотек/путей/записанного inventory, без активации COM.
- `probe()` — runner probe.
- `prepare_smoke_plan()` — новый неизменяемый JSON план; результат содержит plan_id, plan_path, plan_sha256, summary, target_model_path. Нет записи в GAP.
- `prepare_manifest_plan(manifest_path:str,mapping_path:str|None=None)` — валидация первого участника и mapping. При pending возвращает NOT_READY с причинами, план записи не готовится.
- `approve_plan(plan_id:str,approved_by:str)` — только локальный CLI/человек, не экспортируется в MCP. Возвращает approval_id/receipt_path, привязанные к SHA-256 плана. Не подделывать инженерное подтверждение манифеста.
- `execute_plan(plan_id:str,approval_id:str)` — требует receipt конкретного плана; отклоняет подмену плана/манифеста/mapping, путь вне workspace и повтор чужого задания. Выполненный запрос возвращает прежний результат без повторной записи; in-progress/unknown не ретраится автоматически.
- `export_model(model_path:str)` — runner export в пределах workspace, сохранить реальный snapshot/normalized export в outputs/gap. Неподтверждённые свойства не выдумывать.

Статусы: SUCCESS, ERROR, NOT_READY, CONFIRMATION_REQUIRED, SESSION_BUSY, COM_UNAVAILABLE, API_ERROR, TIMEOUT, UNSUPPORTED_MAPPING, LICENSE_UNAVAILABLE; LICENSE_BUSY только если подтверждается фактическим ответом. LICENSE_UNAVAILABLE привязан к фактически наблюдённому native 22 и сообщению «No OpenServer license available» на GAP.START(), а не выводится из любого сбоя COM. Файл журнала JSONL с trace_id/временем/фактическими вызовами/ответами. Секреты не логировать.

Локальный операторский `acknowledge_unknown(plan_id,reviewed_by,note)` фиксирует ручной разбор UNKNOWN в отдельном артефакте, сохраняет первичную ошибку и переводит старый план в RESOLVED_NO_RETRY. Он не является MCP-инструментом, не повторяет запись и не подтверждает инженерные данные.

Для RUNNING после прерывания процесса нужен сохранённый execution claim: владелец
должен завершиться, а проверка процессов должна подтвердить отсутствие GAP и worker.
При живом владельце или непроверяемом состоянии разбор отклоняется. Старый RUNNING
без сведений о владельце возвращает OWNERSHIP_NOT_RECORDED; автоматического сброса нет.
Хеши proof, его модели/журнала и шаблона также закреплены в производственном плане.

## MCP

`gap_integration.mcp_server`: официальный установленный SDK MCP 2.x. Транспорт stdio, logging в stderr. Фабрика `create_server(workspace)`.

Только инструменты:

- `gap_environment()`
- `gap_probe()`
- `gap_prepare_smoke()`
- `gap_prepare_manifest(manifest_path,mapping_path=None)`
- `gap_execute(plan_id,approval_id)`
- `gap_export(model_path)`

approve_plan не является инструментом LLM. Без receipt execute возвращает CONFIRMATION_REQUIRED. Аргументы типизированы, результаты возвращаются структурированными; свободного execute_command нет. В factory допустим service injection для тестов. Не запускать COM в MCP-модульных тестах. Реальный MCP stdio клиент проверяет discovery+read-only calls, а smoke через MCP — только после локального receipt.
