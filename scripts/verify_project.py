"""Fresh checks on this machine; optional real technical GAP check is explicit."""
import argparse
import asyncio
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))


def main():
    parser = argparse.ArgumentParser(description='Проверки проекта с разграничением реальных и искусственных данных')
    parser.add_argument('--gap-smoke', action='store_true', help='Выполнить явно порученный технический пример в отдельной модели')
    args = parser.parse_args()
    from gap_agent.diagnostics import doctor
    from gap_agent.storage import now
    from gap_integration.service import GapService
    from gap_manifest.validation import validate_manifest
    output = ROOT / 'outputs/verification'
    output.mkdir(parents=True, exist_ok=True)

    def save(name, value):
        path = output / name
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return str(path)

    log = io.StringIO()
    tests = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(tests)
    (output / 'tests.log').write_text(log.getvalue(), encoding='utf-8')
    diagnostics = doctor(ROOT)
    summary = {'checked_at': now(), 'tests': {'run': result.testsRun,
               'passed': result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
               'failures': len(result.failures), 'errors': len(result.errors),
               'skipped': [{'test': str(t), 'reason': reason} for t, reason in result.skipped]},
               'doctor': save('doctor.json', diagnostics),
               'runtime': {'executable': sys.executable, 'python_version': sys.version.split()[0]},
               'live_llm': 'NOT_VERIFIED: this command does not call the LLM; use scripts/evaluate_llm.py explicitly',
               'evidence_scope': {'unit_tests': 'synthetic fixtures',
                                  'mcp_stdio': 'real local protocol, no GAP calls'}}
    import tempfile
    try:
        from check_mcp_stdio import check_protocol
        with tempfile.TemporaryDirectory(prefix='wire-check-', dir=ROOT / 'work') as temporary:
            protocol = asyncio.run(asyncio.wait_for(check_protocol(ROOT, Path(temporary)), timeout=40))
        summary['mcp_stdio'] = protocol['execution_status']
        save('mcp_stdio.json', protocol)
    except Exception as exc:
        summary['mcp_stdio'] = 'ERROR'
        save('mcp_stdio.json', {'execution_status': 'ERROR', 'error_type': type(exc).__name__,
                                'com_called': False, 'message': 'MCP stdio check failed'})
    if args.gap_smoke and result.wasSuccessful() and summary['mcp_stdio'] == 'SUCCESS':
        service = GapService(ROOT)
        environment = service.inspect_environment()
        save('environment.json', environment)
        probe = service.probe()
        save('com_probe.json', probe)
        summary['com_probe'] = probe['execution_status']
        if probe['execution_status'] == 'SUCCESS' and not probe.get('gap_running'):
            plan = service.prepare_smoke_plan()
            save('smoke_plan.json', plan)
            print('Технический пример: AG_J1 + AG_J2 + AG_P1; целевая копия:', plan.get('target_model_path'), flush=True)
            receipt = service.approve_plan(plan['plan_id'], 'Codex: technical smoke explicitly requested by user')
            smoke = service.execute_plan(plan['plan_id'], receipt['approval_id'])
            summary['gap_smoke'] = smoke['execution_status']
            save('gap_smoke.json', smoke)
        else:
            summary['gap_smoke'] = 'NOT_READY: existing GAP session or COM unavailable'
    else:
        summary['gap_smoke'] = 'NOT_VERIFIED: not requested in this run'
    save('summary.json', summary)
    candidate = json.loads((ROOT / 'data/candidate_manifest.json').read_text(encoding='utf-8'))
    validation = validate_manifest(candidate, ROOT)
    (ROOT / 'outputs/project_status.json').write_text(json.dumps({
        'schema_version': '2.0', 'checked_at': summary['checked_at'],
        'engineering': {'ready_for_transfer': validation['ready_for_transfer'], 'stats': validation['stats']},
        'software_checks': {'mcp_stdio': summary['mcp_stdio'], 'gap_smoke': summary['gap_smoke'],
                            'live_llm': summary['live_llm'], 'doctor_status': diagnostics['execution_status'],
                            'doctor_issues': [item.get('code') for item in diagnostics['issues']]},
        'tests': summary['tests'], 'details': str(output / 'summary.json'),
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result.wasSuccessful() and summary['mcp_stdio'] == 'SUCCESS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
