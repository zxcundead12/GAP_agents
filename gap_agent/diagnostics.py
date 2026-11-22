"""Current local readiness, never recycled integration evidence."""
import importlib.util
import os
from pathlib import Path
import sys
import uuid

from gap_manifest.validation import validate_manifest
from .contracts import validate_result
from .storage import read_json


def _gap_executable(root):
    """Check current files from a recorded path/COM registration; never activate COM."""
    candidates = []
    inventory_path = root / 'work/gap_inventory/installed_gap_inventory.json'
    if inventory_path.is_file():
        value = read_json(inventory_path)
        if not isinstance(value, dict):
            raise ValueError('Invalid inventory')
        for item in value.get('binaries', []):
            if isinstance(item, dict) and isinstance(item.get('path'), str):
                path = Path(item['path'])
                if path.is_absolute() and path.name.lower() == 'gap.exe':
                    candidates.append(path)
    if os.name == 'nt':
        import winreg
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r'PX32.OpenServer.1\CLSID', 0, winreg.KEY_READ | view) as key:
                    clsid = winreg.QueryValueEx(key, None)[0]
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, 'CLSID\\' + clsid + r'\LocalServer32', 0, winreg.KEY_READ | view) as key:
                    server = Path(winreg.QueryValueEx(key, None)[0].strip().strip('"'))
                if server.is_absolute() and server.name.lower() == 'pxserver.exe':
                    candidates.append(server.parent / 'gap.exe')
            except OSError:
                continue
    return next((p for p in candidates if p.is_file()), None), bool(candidates)


def doctor(root):
    root = Path(root).resolve()
    run_id = 'run_' + uuid.uuid4().hex
    issues = []
    config_path = root / 'data/agent_config.json'
    try:
        settings = read_json(config_path) if config_path.is_file() else {}
        if not isinstance(settings, dict) or not isinstance(settings.get('llm', {}), dict):
            raise ValueError('Invalid configuration')
        config = settings.get('llm', {})
    except (OSError, ValueError, TypeError):
        config = {}
        issues.append({'code': 'CONFIG_INVALID', 'message': 'Конфигурация агента не прошла проверку'})
    if not os.environ.get('GAP_LLM_MODEL', config.get('model')):
        issues.append({'code': 'LLM_MODEL_MISSING', 'message': 'Не выбрана модель LLM'})
    key_name = config.get('api_key_env', 'GROQ_API_KEY')
    if not isinstance(key_name, str) or not key_name or not os.environ.get(key_name):
        issues.append({'code': 'LLM_KEY_MISSING', 'message': 'Ключ API не задан локально'})
    for module in ('mcp', 'win32com'):
        try:
            available = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            available = False
        if not available:
            issues.append({'code': 'DEPENDENCY_MISSING', 'module': module})
    manifest_path = root / 'data/candidate_manifest.json'
    if manifest_path.is_file():
        try:
            validation = validate_manifest(read_json(manifest_path), root)
            if not validation['ready_for_transfer']:
                issues.append({'code': 'MANIFEST_NOT_READY', 'stats': validation['stats']})
            issues.extend(i for i in validation['issues'] if i['severity'] == 'error')
        except (OSError, ValueError, TypeError, KeyError):
            issues.append({'code': 'MANIFEST_INVALID', 'message': 'Манифест не прошёл проверку'})
    else:
        issues.append({'code': 'MANIFEST_MISSING'})
    cfg_path = root / '.venv/pyvenv.cfg'
    if cfg_path.is_file():
        entries = dict(line.split(' = ', 1) for line in cfg_path.read_text(encoding='utf-8').splitlines() if ' = ' in line)
        if not entries.get('home') or not Path(entries['home']).is_dir():
            issues.append({'code': 'VENV_RELOCATION_REQUIRED', 'message': 'Окружение ссылается на отсутствующий Python'})
    artifacts = {'runtime': sys.executable}
    try:
        executable, recorded_path = _gap_executable(root)
        if executable is not None:
            artifacts['gap_executable'] = str(executable)
        else:
            issues.append({'code': 'GAP_EXECUTABLE_MISSING' if recorded_path else 'GAP_INSTALLATION_NOT_VERIFIED',
                           'message': 'Текущий исполняемый файл GAP не найден либо его путь не подтверждён'})
    except (OSError, ValueError, TypeError, KeyError):
        issues.append({'code': 'GAP_INSTALLATION_NOT_VERIFIED', 'message': 'Текущая установка GAP не подтверждена'})
    return validate_result({
        'schema_version': '1.0', 'run_id': run_id, 'trace_id': run_id,
        'execution_status': 'NOT_READY' if issues else 'SUCCESS', 'assessment_status': 'NOT_ASSESSED',
        'message': 'Диагностика текущей машины; LLM, COM и лицензия не вызывались',
        'task': None, 'plan': None, 'artifacts': artifacts, 'issues': issues,
        'llm': {'called': False, 'evidence': 'not_verified'},
    })

