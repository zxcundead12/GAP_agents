"""Participant 2 input boundary; engineering validation belongs to participant 1."""
from datetime import datetime
import importlib
import json
import math
from pathlib import Path

PARAMETERS = {'length_m': 'm', 'outer_diameter_mm': 'mm', 'wall_thickness_mm': 'mm', 'inner_diameter_mm': 'mm'}


def finite_number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except (TypeError, ValueError, OverflowError):
        return False


def confirmed(value):
    if not isinstance(value, dict) or value.get('review_status') != 'confirmed':
        return False
    if not isinstance(value.get('reviewed_by'), str) or not value['reviewed_by'].strip():
        return False
    try:
        return datetime.fromisoformat(value.get('reviewed_at', '').replace('Z', '+00:00')).tzinfo is not None
    except (ValueError, TypeError, AttributeError):
        return False


def workspace_path(root, raw):
    if not isinstance(raw, str) or not raw.strip() or any(c in raw for c in ('\0', '\r', '\n')):
        raise ValueError('Expected a workspace path')
    path = Path(raw)
    if '..' in path.parts or (path.drive and not path.is_absolute()):
        raise ValueError('Parent traversal and drive-relative paths are forbidden')
    if any(':' in part for part in path.parts if part != path.anchor):
        raise ValueError('Alternate data streams are forbidden')
    root = Path(root).resolve()
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Path escapes workspace')
    return path


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'),
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON')))


def resolve_source(source, project_root):
    root = Path(project_root).resolve()
    raw = source['path']
    config = root / 'data/source_locations.local.json'
    if config.is_file():
        locations = read_json(config)
        if not isinstance(locations, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not v.strip()
                                                 for k, v in locations.items()):
            raise ValueError('Invalid local source locations')
        raw = locations.get(source['id'], raw)
    file = Path(raw)
    return (file if file.is_absolute() else root / file).resolve()


def validate_manifest(manifest, project_root=None, check_sources=True):
    """Fail closed when the peer engineering module is not installed.

    This package does not ship participant 1's graph/evidence validator. The
    technical smoke and MCP operations remain independently usable.
    """
    case_id = manifest.get('case_id') if isinstance(manifest, dict) else None
    code = 'ENGINEERING_VALIDATOR_UNAVAILABLE'
    message = 'Подключите модуль gap_manifest участника 1 для производственного переноса'
    try:
        module = importlib.import_module('gap_manifest.validation')
    except ImportError:
        module = None
    if module is not None:
        result = module.validate_manifest(manifest, project_root, check_sources=check_sources)
        if (isinstance(result, dict) and type(result.get('ready_for_transfer')) is bool
                and result.get('case_id') == case_id and result.get('execution_status') in ('SUCCESS', 'ERROR')
                and isinstance(result.get('issues'), list)
                and all(isinstance(i, dict) for i in result['issues'])):
            if result['execution_status'] != 'SUCCESS' or any(i.get('severity') == 'error' for i in result['issues']):
                return {**result, 'ready_for_transfer': False}
            return result
        code = 'ENGINEERING_VALIDATOR_CONTRACT_ERROR'
        message = 'Результат инженерного валидатора не соответствует контракту'
    return {'schema_version': '1.0', 'case_id': case_id, 'execution_status': 'ERROR',
            'ready_for_transfer': False, 'stats': {}, 'topology': {},
            'issues': [{'code': code, 'severity': 'error', 'object_id': None, 'message': message, 'source_refs': []}]}
