# Производственный профиль и границы подтверждения

Компилятор строит структурированные спецификации. production.py формирует
предусмотренные кодом команды и читает независимый результат. Профиль реального
кандидата пока не подтверждён.

Реализованы короткие стабильные LABEL с контролем коллизий, SOURCE/WELL, JOINT,
SINK/SEP, реальные PIPE, implicit/explicit соединения и проверка концов сразу после
LINKITEMS. Геометрия записывается SetValue с GetLastError, проверяются UNITNAME.
Сохраняются/открываются копия шаблона и модель; повтор сверяет native UniqueID.

Один клапан на ветвь представляется только отдельно принятым masking. DN, рейтинг,
подписи и физическое состояние — семантические метаданные COMMENT, проверяемые
против native ISMASKED; это не native VALVE. Другие представления требуют другого
профиля. OD/Thickness требуют испытанного шаблона Enthalpy Balance.

## Включение

1. Получить OpenServer и успешно выполнить технический пример.
2. На отдельной модели проверить типы, связи, секции, OD/Thickness, единицы,
   COMMENT и MASK/UNMASK. Согласовать смысл клапанов и границ.
3. Заполнить копию data/gap_mapping.template.json: review, индексы, типы,
   template_path/template_sha256, verification_path. Шаблон находится в workspace.
4. Приложить реальный протокол: evidence=real, execution_status=SUCCESS,
   profile_id, software_version, mapping_sha256, verified_features,
   model:{path,sha256}, journal:{path,sha256}.
5. verified_features: node_types, connections, pipe_geometry, units,
   valve_masking, metadata_readback. Версия: IPM 7.5 Build 600 / GAP 8.5.

mapping_sha256 — SHA-256 UTF-8 результата json.dumps(mapping без verification_path,
sort_keys=True, ensure_ascii=False). Модель проверки и журнал перечитываются по
хешам. Редактирование JSON не доказывает правильность наблюдений: ответственный
инженер проверяет реальные вызовы и сопоставимость условий. Unit-фикстуры не
используются как рабочий протокол.

Затем нужны готовый манифест и разрешение на конкретный план. Незакрытая
предпосылка даёт NOT_READY/UNSUPPORTED_MAPPING до COM. При сбое возможна частично
созданная целевая копия, журнал сохраняется, исходник не меняется. Гидравлика не выполняется.
