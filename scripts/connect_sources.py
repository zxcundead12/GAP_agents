"""Bind the two original files to local paths, only when their hashes match."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gap_agent.storage import read_json


def main(argv=None):
    parser = argparse.ArgumentParser(description='Подключить исходники без изменения манифеста')
    parser.add_argument('--scheme', type=Path, required=True)
    parser.add_argument('--legend', type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = read_json(ROOT / 'data/candidate_manifest.json')
    wanted = {'scheme_pdf': args.scheme, 'scheme_readme': args.legend}
    locations = {}
    try:
        for source in manifest['sources']:
            file = wanted[source['id']].resolve()
            if hashlib.sha256(file.read_bytes()).hexdigest() != source['sha256']:
                raise ValueError('SHA-256 не совпадает: ' + source['id'])
            locations[source['id']] = str(file)
    except (OSError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    target = ROOT / 'data/source_locations.local.json'
    target.write_text(json.dumps(locations, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Подключены файлы с исходными SHA-256. Инженерное подтверждение не изменено.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

