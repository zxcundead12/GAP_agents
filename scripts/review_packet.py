"""Produce a current review packet without pretending to approve engineering data."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gap_agent.storage import read_json
from gap_manifest.report import write_html_report
from gap_manifest.tables import write_tables
from gap_manifest.validation import validate_manifest


def main():
    manifest = read_json(ROOT / 'data/candidate_manifest.json')
    validation = validate_manifest(manifest, ROOT)
    folder = ROOT / 'outputs/review_packet'
    folder.mkdir(parents=True, exist_ok=True)
    write_tables(manifest, folder / 'tables')
    image = ROOT / 'work/scheme_overview.png'
    write_html_report(manifest, validation, folder / 'report.html', None, image if image.is_file() else None)
    (folder / 'validation.json').write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# Вопросы для согласования фрагмента', '',
             'Этот пакет не является подтверждением. Решения фиксирует проверивший инженер.', '']
    for item in manifest['ambiguities']:
        lines.extend([f"## {item['id']} — {item['status']}", '', item['description'], '',
                      'Решение: ____________________', 'Проверивший и дата: ____________________', ''])
    (folder / 'engineering_questions.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'folder': str(folder), 'stats': validation['stats'],
                      'ready_for_transfer': validation['ready_for_transfer']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
