"""Offline compiler for an explicitly reviewed IPM mapping.

The implemented profile represents valves by approved pipe masking and explicit
semantic metadata. It never calls that metadata native valve geometry. Enabling
this profile requires separate live verification of this exact representation.
"""
from copy import deepcopy
import hashlib
import json
import re

from .contracts import PARAMETERS, confirmed, validate_manifest, finite_number

PROFILE = 'ipm75-reviewed-pipe-mask-v1'
KINDS = {'junction': ('JOINT',), 'source': ('SOURCE', 'WELL'), 'boundary': ('SINK', 'SEP')}
NATIVE_FIELDS = {'length_m': 'Length', 'outer_diameter_mm': 'OD',
                 'wall_thickness_mm': 'Thickness', 'inner_diameter_mm': 'ID'}


class MappingError(ValueError):
    pass


def alias(identifier, prefix):
    return prefix + hashlib.sha256(identifier.encode('utf-8')).hexdigest()[:8]


def profile(mapping):
    if not isinstance(mapping, dict) or mapping.get('profile_id') != PROFILE or not confirmed(mapping):
        raise MappingError('Нужен явно подтверждённый профиль ' + PROFILE)
    if mapping.get('valve_representation') != 'reviewed_pipe_mask_with_metadata':
        raise MappingError('Представление клапанов не утверждено')
    if mapping.get('metadata_fields_acknowledged') is not True:
        raise MappingError('DN, рейтинг и исходные подписи хранятся как метаданные; требуется явное согласование')
    kinds = mapping.get('node_types')
    if not isinstance(kinds, dict) or set(kinds) != set(KINDS) or any(kinds[k] not in KINDS[k] for k in KINDS):
        raise MappingError('Недопустимое отображение узлов')
    section = mapping.get('section_index')
    if type(section) is not int or section not in (0, 1):
        raise MappingError('Нужен проверенный индекс секции 0 или 1')
    if type(mapping.get('array_field_index')) is not bool:
        raise MappingError('Нужно подтвердить синтаксис индексов полей Desc')
    if mapping.get('units') != PARAMETERS:
        raise MappingError('Профиль требует явных единиц m и mm')
    if mapping.get('pipe_model_mode') != 'enthalpy_balance_preconfigured_template':
        raise MappingError('OD/Thickness требуют согласованного шаблона Enthalpy Balance')
    pairs = mapping.get('implicit_pipe_pairs')
    if not isinstance(pairs, list) or any(not isinstance(p, list) or len(p) != 2 or
            any(t not in ('JOINT', 'SOURCE', 'WELL', 'SINK', 'SEP') for t in p) for p in pairs):
        raise MappingError('Некорректный список проверенных сочетаний LINKITEMS')
    return mapping


def suffixes(mapping):
    index = mapping['section_index']
    tail = f'[{index}]' if mapping['array_field_index'] else ''
    return {key: f'Desc[{index}].{name}{tail}' for key, name in NATIVE_FIELDS.items()}


def compile_manifest(manifest, mapping):
    mapping = profile(mapping)
    validation = validate_manifest(manifest, check_sources=False)
    if not validation['ready_for_transfer']:
        raise MappingError('Манифест не готов; компилятор не подтверждает данные автоматически')
    valves = {p['id']: [] for p in manifest['pipes']}
    for valve in manifest['valves']:
        valves[valve['pipe_id']].append(valve)
    if any(len(items) > 1 for items in valves.values()):
        raise MappingError('Этот профиль допускает один физический клапан на ветвь; сложную арматуру нужно моделировать отдельно')
    labels = {node['id']: alias(node['id'], 'N') for node in manifest['nodes']}
    labels.update({pipe['id']: alias(pipe['id'], 'P') for pipe in manifest['pipes']})
    if len(set(labels.values())) != len(labels):
        raise MappingError('Коллизия коротких идентификаторов GAP')
    nodes = []
    for node in manifest['nodes']:
        nodes.append({'label': labels[node['id']], 'type': mapping['node_types'][node['kind']],
                      'metadata': {'id': node['id'], 'kind': node['kind'], 'original_label': node['original_label']}})
    node_types = {node['label']: node['type'] for node in nodes}
    pipes = []
    for pipe in manifest['pipes']:
        left, right = labels[pipe['from_node']], labels[pipe['to_node']]
        pair = [node_types[left], node_types[right]]
        implicit = pair in mapping['implicit_pipe_pairs'] or pair[::-1] in mapping['implicit_pipe_pairs']
        own_valves = [{k: v[k] for k in ('id', 'original_label', 'pipe_id', 'physical_state', 'size_dn_mm', 'pressure_rating_raw')}
                      for v in valves[pipe['id']]]
        metadata = {'id': pipe['id'], 'kind': 'pipe', 'original_label': pipe['original_label'],
                    'active_state': pipe['active_state'], 'valves': own_valves}
        masked = pipe['active_state'] == 'inactive' or any(v['physical_state'] == 'closed' for v in own_valves)
        pipes.append({'label': labels[pipe['id']], 'from_label': left, 'to_label': right,
                      'from_type': pair[0], 'to_type': pair[1], 'implicit': implicit,
                      'parameters': deepcopy(pipe['parameters']), 'masked': masked, 'metadata': metadata})
    return {'case_id': manifest['case_id'], 'profile_id': PROFILE, 'nodes': nodes, 'pipes': pipes,
            'geometry_suffixes': suffixes(mapping), 'units': deepcopy(PARAMETERS),
            'limitations': ['Клапаны представлены согласованным отключением ветви, а не native VALVE',
                            'Подписи, физические состояния, DN и рейтинг — метаданные COMMENT; геометрия читается из native полей'],
            'runtime_verified': False}


def metadata_text(case_id, metadata):
    return json.dumps({'gap_agent_schema': '1.0', 'case_id': case_id, **metadata},
                      ensure_ascii=False, separators=(',', ':'))


def normalize_snapshot(snapshot, model, case_id):
    """Only actual snapshot values are inputs. No manifest parameter fallback."""
    if model.get('evidence') not in ('real', 'synthetic'):
        raise MappingError('Нужно указать происхождение снимка')
    equipment = snapshot.get('equipment')
    if not isinstance(equipment, list) or snapshot.get('count') != len(equipment):
        raise MappingError('Некорректный снимок оборудования')
    nodes, pipes, valves, labels, ids = [], [], [], {}, set()
    for item in equipment:
        if not isinstance(item, dict) or not isinstance(item.get('label'), str) or item['label'] in labels:
            raise MappingError('Повтор или отсутствие native label')
        try:
            meta = json.loads(item['comment'])
        except (KeyError, TypeError, ValueError):
            raise MappingError('У оборудования отсутствуют читаемые метаданные; экспорт не дополнен манифестом') from None
        if meta.get('gap_agent_schema') != '1.0' or meta.get('case_id') != case_id:
            raise MappingError('Чужое оборудование или неверный case_id')
        identifier = meta.get('id')
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            raise MappingError('Коллизия ID в считанных метаданных')
        ids.add(identifier)
        labels[item['label']] = (item, meta)
    for item, meta in labels.values():
        kind = meta.get('kind')
        if kind in KINDS:
            if item.get('type') not in KINDS[kind]:
                raise MappingError('Native тип не соответствует прочитанной семантике')
            nodes.append({'id': meta['id'], 'kind': kind, 'original_label': meta['original_label']})
            continue
        if kind != 'pipe' or item.get('type') != 'PIPE':
            raise MappingError('Неожиданный тип оборудования')
        try:
            ends = [labels[item[key]][1] for key in ('from_node', 'to_node')]
            if any(end['kind'] not in KINDS for end in ends):
                raise MappingError('Конец трубы не является узлом')
            parameters = item['native_parameters']
            if set(parameters) != set(PARAMETERS) or item['native_units'] != PARAMETERS:
                raise MappingError('Не все native параметры/единицы прочитаны')
            for value in parameters.values():
                if not finite_number(value) or value <= 0:
                    raise MappingError('Неопределённый или некорректный native параметр')
            own_valves = meta['valves']
            if not isinstance(own_valves, list) or len(own_valves) > 1:
                raise MappingError('Неподдержанная конфигурация арматуры')
            if meta['active_state'] not in ('active', 'inactive') or any(v['physical_state'] not in ('open', 'closed') for v in own_valves):
                raise MappingError('Неизвестное физическое состояние')
            masked = meta['active_state'] == 'inactive' or any(v['physical_state'] == 'closed' for v in own_valves)
            if str(item['is_masked']) != str(int(masked)):
                raise MappingError('Native masking не соответствует прочитанному физическому состоянию')
            pipes.append({'id': meta['id'], 'from_node': ends[0]['id'], 'to_node': ends[1]['id'],
                          'parameters': deepcopy(parameters), 'active_state': meta['active_state']})
            for valve in own_valves:
                if valve['pipe_id'] != meta['id'] or valve['id'] in ids:
                    raise MappingError('Коллизия или неверная привязка клапана')
                ids.add(valve['id'])
                valves.append(deepcopy(valve))
        except (KeyError, TypeError):
            raise MappingError('Снимок неполон; недостающие значения не подставлялись') from None
    return {'schema_version': '1.0', 'case_id': case_id, 'model': deepcopy(model),
            'nodes': nodes, 'pipes': pipes, 'valves': valves,
            'field_storage': {'pipe_parameters': 'native GAP fields',
                              'valves': 'reviewed COMMENT metadata, checked against native ISMASKED'},
            'hydraulics_performed': False}
