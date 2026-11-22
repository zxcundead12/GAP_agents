"""Reviewable file update with preflight hashes and backups; never touches source data."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import os

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def bound(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Resolved path escaped the selected project')
    return path


def candidates():
    files = set()
    for folder, pattern in (('gap_agent', '*.py'), ('gap_manifest', '*.py'), ('gap_integration', '*.py'),
                            ('tests', '*.py'), ('scripts', '*.py'), ('docs', '*.md'), ('schemas', '*.json')):
        files.update(p.relative_to(ROOT) for p in (ROOT / folder).rglob(pattern) if '__pycache__' not in p.parts)
    files.update(Path(name) for name in ('README.md', 'pyproject.toml', 'requirements-integration.lock.txt',
                 '.gitignore', '.env.example', 'setup.ps1', 'run-agent.ps1',
                 'data/agent_config.json', 'data/gap_mapping.template.json', 'data/ipm75_capabilities.json'))
    for folder in ('outputs/review_packet', 'outputs/verification'):
        files.update(p.relative_to(ROOT) for p in (ROOT / folder).rglob('*') if p.is_file())
    files.add(Path('outputs/live_llm_evaluation.json'))
    return sorted(files, key=str)


def main():
    parser = argparse.ArgumentParser(description='Подготовить/применить проверяемое обновление проекта')
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--repair-venv', action='store_true')
    args = parser.parse_args()
    target = args.target.resolve()
    if target == ROOT or not (target / 'pyproject.toml').is_file() or not (target / 'data/candidate_manifest.json').is_file():
        raise ValueError('Target must be the existing separate GAP project')
    manifest_path = ROOT / 'outputs/update_manifest.json'
    if not args.apply:
        entries = []
        for relative in candidates():
            source = bound(ROOT, relative)
            destination = bound(target, relative)
            new, old = sha(source), sha(destination)
            if new is not None and new != old:
                entries.append({'path': relative.as_posix(), 'old_sha256': old, 'new_sha256': new})
        manifest = {'source': str(ROOT), 'target': str(target), 'files': entries,
                    'candidate_manifest_sha256': sha(target / 'data/candidate_manifest.json'),
                    'preserved': ['candidate_manifest.json', 'pipeline_catalog.json', 'tolerances.template.json',
                                  'all existing GAP models, receipts and journals', 'license and API keys'],
                    'venv_repair': 'upgrade launcher/config in place using installed Python; keep site-packages'}
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'manifest': str(manifest_path), 'target': str(target), 'changed_files': len(entries)}, ensure_ascii=False))
        return 0
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest['source'] != str(ROOT) or manifest['target'] != str(target):
        raise ValueError('Update manifest target differs')
    if sha(target / 'data/candidate_manifest.json') != manifest['candidate_manifest_sha256']:
        raise ValueError('Engineering manifest changed since preflight; nothing applied')
    for item in manifest['files']:
        if sha(bound(target, item['path'])) != item['old_sha256'] or sha(bound(ROOT, item['path'])) != item['new_sha256']:
            raise ValueError('File changed since preflight: ' + item['path'])
    venv = bound(target, '.venv')
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = bound(target, 'backups/codex_update_' + timestamp)
    backup.mkdir(parents=True, exist_ok=False)
    for item in manifest['files']:
        relative = item['path']
        destination = bound(target, relative)
        if destination.is_file():
            saved = bound(backup, relative)
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, saved)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bound(ROOT, relative), destination)
    shutil.copy2(manifest_path, backup / 'update_manifest.json')
    if args.repair_venv:
        cfg = venv / 'pyvenv.cfg'
        if cfg.is_file():
            shutil.copy2(cfg, backup / 'pyvenv.cfg.before_update')
        base_python = getattr(sys, '_base_executable', sys.executable)
        subprocess.run([base_python, '-m', 'venv', '--upgrade', '--without-pip', '--system-site-packages', str(venv)],
                       check=True, timeout=60)
    interpreter = venv / 'Scripts/python.exe'
    environment = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    check = subprocess.run([str(interpreter), '-B', '-X', 'utf8', '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                           cwd=target, env=environment, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
    log_path = bound(target, 'outputs/verification/deployment_tests.log')
    log_path.write_text(check.stdout + check.stderr, encoding='utf-8')
    status = {'target': str(target), 'backup': str(backup), 'updated_files': len(manifest['files']),
              'tests_exit_code': check.returncode, 'tests_log': str(log_path), 'gap_called_during_deployment': False,
              'candidate_manifest_preserved': sha(target / 'data/candidate_manifest.json') == manifest['candidate_manifest_sha256']}
    bound(target, 'outputs/installation_status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(status, ensure_ascii=False, indent=2))
    print((check.stdout + check.stderr)[-600:])
    return check.returncode


if __name__ == '__main__':
    raise SystemExit(main())
