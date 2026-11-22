"""Guarded native execution of the reviewed pipe-mask profile."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

from .contracts import workspace_path, read_json
from .compiler import MappingError, compile_manifest, metadata_text, normalize_snapshot, profile, suffixes

REQUIRED_FEATURES = {'node_types', 'connections', 'pipe_geometry', 'units', 'valve_masking', 'metadata_readback'}


def verify_runtime_profile(mapping, root):
    profile(mapping)
    proof = read_json(workspace_path(root, mapping.get('verification_path')))
    frozen = {k: v for k, v in mapping.items() if k != 'verification_path'}
    expected_hash = hashlib.sha256(json.dumps(frozen, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    if (proof.get('evidence') != 'real' or proof.get('execution_status') != 'SUCCESS'
            or proof.get('mapping_sha256') != expected_hash
            or proof.get('profile_id') != mapping['profile_id'] or proof.get('software_version') != 'IPM 7.5 Build 600 / GAP 8.5'
            or set(proof.get('verified_features', [])) != REQUIRED_FEATURES):
        raise MappingError('Профиль не проверен на реальном GAP для всех необходимых возможностей')
    # The referenced native model and journal must still exist, not just a Boolean in a mapping.
    for key in ('model', 'journal'):
        binding = proof[key]
        file = workspace_path(root, binding['path'])
        if hashlib.sha256(file.read_bytes()).hexdigest() != binding['sha256']:
            raise MappingError('Изменилось доказательство проверки профиля')
    template = workspace_path(root, mapping['template_path'])
    if template.suffix.lower() != '.gap' or hashlib.sha256(template.read_bytes()).hexdigest() != mapping['template_sha256']:
        raise MappingError('Шаблон не соответствует подтверждённому профилю')
    return template


def ref(kind, label):
    return f'MOD[0].{kind}[{{{label}}}]'


def ensure(client, compiled, snapshot_fn):
    state = snapshot_fn(client)
    existing = {item['label']: item for item in state['equipment']}
    if len(existing) != len(state['equipment']):
        raise MappingError('Повтор label в модели')
    expected = {row['label']: row['type'] for row in compiled['nodes']}
    expected.update({row['label']: 'PIPE' for row in compiled['pipes']})
    if any(label not in expected or expected[label] != row['type'] for label, row in existing.items()):
        raise MappingError('В шаблоне есть постороннее оборудование или коллизия типа')
    for node in compiled['nodes']:
        if node['label'] not in existing:
            client.cmd(f'GAP.NEWITEM("{node["type"]}","{node["label"]}")')
        client.set('GAP.' + ref(node['type'], node['label']) + '.COMMENT', metadata_text(compiled['case_id'], node['metadata']))
    for pipe in compiled['pipes']:
        label = pipe['label']
        current = {item['label']: item for item in snapshot_fn(client)['equipment']}
        if label not in current:
            left, right = ref(pipe['from_type'], pipe['from_label']), ref(pipe['to_type'], pipe['to_label'])
            if pipe['implicit']:
                client.cmd(f'GAP.LINKITEMS({left},{right},"{label}")')
            else:
                client.cmd(f'GAP.NEWITEM("PIPE","{label}")')
                client.cmd(f'GAP.LINKITEMS({left},{ref("PIPE", label)})')
                client.cmd(f'GAP.LINKITEMS({ref("PIPE", label)},{right})')
        current = {item['label']: item for item in snapshot_fn(client)['equipment']}
        item = current.get(label, {})
        if item.get('type') != 'PIPE' or {item.get('from_node'), item.get('to_node')} != {pipe['from_label'], pipe['to_label']}:
            raise MappingError('LINKITEMS не дал ожидаемую трубу и концы; дальнейшая запись остановлена')
        prefix = 'GAP.' + ref('PIPE', label)
        for key, suffix in compiled['geometry_suffixes'].items():
            tag = prefix + '.' + suffix
            unit = client.get(tag + '.UNITNAME')
            if unit != compiled['units'][key]:
                raise MappingError('Фактические единицы отличаются от утверждённых; автоматическая подмена запрещена')
            client.set(tag, str(pipe['parameters'][key]))
        client.set(prefix + '.COMMENT', metadata_text(compiled['case_id'], pipe['metadata']))
        client.cmd(prefix + ('.MASK()' if pipe['masked'] else '.UNMASK()'))
    after = snapshot_fn(client)
    if {item['label']: item['type'] for item in after['equipment']} != expected or len(after['equipment']) != len(expected):
        raise MappingError('После соединений есть лишнее или отсутствующее оборудование')


def enriched_snapshot(client, mapping, snapshot_fn):
    state = snapshot_fn(client)
    for item in state['equipment']:
        prefix = 'GAP.' + ref(item['type'], item['label'])
        item['comment'] = client.get(prefix + '.COMMENT')
        if item['type'] == 'PIPE':
            item['is_masked'] = client.get(prefix + '.ISMASKED')
            item['native_parameters'], item['native_units'] = {}, {}
            for key, suffix in suffixes(mapping).items():
                tag = prefix + '.' + suffix
                raw = client.get(tag)
                threshold = float(client.get('GAP.FNAT'))
                if not float(raw) < threshold:
                    raise MappingError('GAP вернул неопределённое значение native параметра')
                item['native_parameters'][key] = float(raw)
                item['native_units'][key] = client.get(tag + '.UNITNAME')
    return state


def execute_build(client, job, root, target, snapshot_fn, software_version):
    manifest, mapping = job['manifest'], job['mapping']
    compiled = compile_manifest(manifest, mapping)
    template = verify_runtime_profile(mapping, root)
    shutil.copyfile(template, target)
    client.cmd(f'GAP.OPENFILE("{target.as_posix()}")')
    ensure(client, compiled, snapshot_fn)
    client.cmd(f'GAP.SAVEFILE("{target.as_posix()}")')
    client.cmd(f'GAP.OPENFILE("{target.as_posix()}")')
    first = enriched_snapshot(client, mapping, snapshot_fn)
    # Repeat the real ensure operation and require native identities to remain stable.
    ensure(client, compiled, snapshot_fn)
    second = enriched_snapshot(client, mapping, snapshot_fn)
    identities = lambda state: sorted((row['label'], row['unique_id']) for row in state['equipment'])
    if identities(first) != identities(second):
        raise MappingError('Повтор создал дубликаты или изменил native идентификаторы')
    client.cmd(f'GAP.SAVEFILE("{target.as_posix()}")')
    client.cmd(f'GAP.OPENFILE("{target.as_posix()}")')
    saved = enriched_snapshot(client, mapping, snapshot_fn)
    model = {'path': str(target), 'software_version': software_version, 'evidence': 'real',
             'exported_at': datetime.now(timezone.utc).isoformat(),
             'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    normalized = normalize_snapshot(saved, model, manifest['case_id'])
    # A sidecar describes how to read native fields, never carries expected parameter values.
    sidecar = {'case_id': manifest['case_id'], 'mapping': mapping}
    target.with_suffix('.mapping.json').write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'execution_status': 'SUCCESS', 'message': 'Модель сохранена и прочитана обратно; повтор не создал дубликатов',
            'snapshot': saved, 'normalized_export': normalized, 'model': model,
            'checks': {'repeat_no_duplicates': True}, 'limitations': compiled['limitations']}
