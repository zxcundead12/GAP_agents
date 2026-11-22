# GAP agents — участник 2

В этом репозитории только MCP-сервер и управление GAP через OpenServer.
LLM-агент, инженерный манифест/разметка, исходные схемы и код проверки участника 1
сюда не включены. Лицензии, модели, ключи, окружения и локальные журналы не публикуются.

## Что реализовано

- 6 типизированных MCP-инструментов, транспорт stdio.
- OpenServer COM-worker в отдельном процессе, проверка native ошибок и таймаута.
- Технический пример: 2 JOINT, соединение с implicit PIPE, сохранение, открытие,
  обратное чтение и повтор без дубликатов.
- Неизменяемые планы, отдельное локальное подтверждение, SHA-256 и SQLite ledger.
- Защита от повторного/параллельного исполнения и записи поверх чужой сессии GAP.
- Offline-компилятор подтверждённого графа, guarded production-профиль,
  копирование шаблона, SetValue/GetValue и независимая нормализация снимка.
- Диагностика метаданных лицензии без вывода ключей и без активации.

## Установка и проверка

Windows, Python 3.11+, установленный IPM 7.5 Build 600 / GAP 8.5.
Из корня репозитория:

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-integration.lock.txt
.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -v
.venv\Scripts\python.exe -B -X utf8 scripts/check_mcp_stdio.py --workspace .
```

Unit-тесты используют искусственный runner/снимки. Проверка stdio действительно
запускает MCP-сервер, но не COM и не GAP. Успешные тесты не доказывают наличие лицензии.

```powershell
.venv\Scripts\python.exe -B -X utf8 -m gap_integration environment
.venv\Scripts\python.exe -B -X utf8 -m gap_integration probe
.venv\Scripts\python.exe -B -X utf8 -m gap_integration.mcp_server --workspace .
```

## Технический пример в реальном GAP

```powershell
.venv\Scripts\python.exe -B -X utf8 -m gap_integration prepare-smoke
```

Команда показывает план и целевую новую модель, но не запускает создание объектов.
После просмотра конкретного плана оператор отдельно подтверждает и исполняет его:

```powershell
.venv\Scripts\python.exe -B -X utf8 -m gap_integration approve --plan-id PLAN_ID --approved-by 'ИМЯ'
.venv\Scripts\python.exe -B -X utf8 -m gap_integration execute --plan-id PLAN_ID --approval-id APPROVAL_ID
```

Подставляются идентификаторы фактических ответов. approve не является MCP-инструментом.
При открытой чужой сессии тест отказывается переключать модель. Таймаут не считается
отменой GAP; неопределённый результат требует ручного разбора через acknowledge.

На машине разработки COM был доступен, но GAP.START() вернул native 22:
`No OpenServer license available`. Сохранённая модель и успешный native round-trip
не заявляются. Для их проверки нужен действующий доступ к OpenServer.

## Совместная работа с участниками 1 и 3

Для производственного манифеста нужен внешний модуль участника 1:
`gap_manifest.validation.validate_manifest(manifest, project_root, check_sources=True)`.
Он возвращает schema_version, case_id, execution_status, ready_for_transfer,
issues и статистику. Реализация инженерной проверки намеренно не скопирована сюда.
Без неё prepare-manifest/readiness возвращают NOT_READY с кодом
ENGINEERING_VALIDATOR_UNAVAILABLE. MCP, probe и smoke от неё не зависят.

Участник 3 вызывает инструменты, описанные в docs/interfaces.md. Команды OpenServer
формирует код адаптера; инструмента произвольной команды или самоподтверждения нет.

Производственный профиль: data/gap_mapping.template.json. Он пока pending.
Поддерживаемое представление — один физический клапан на ветвь через отдельно
согласованное отключение ветви с метаданными; это не native VALVE.
Геометрия считывается из native полей; нужны проверенные индексы, единицы и шаблон
Enthalpy Balance для OD/стенки. См. docs/production_profile.md.
Неподтверждённый профиль отклоняется до COM. Полноценный перенос фрагмента требует
инженерного согласования, лицензии и реальных испытаний профиля.
