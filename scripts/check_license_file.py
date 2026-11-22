"""Export safe, read-only metadata for two explicitly selected license files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gap_integration.license_diagnostics import diagnose_license_files


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description="Read-only license-file hashes and safe metadata; no activation or API validation")
    parser.add_argument("--license-file", type=Path, required=True, help="Явно выбранный исходный файл лицензии")
    parser.add_argument("--installed-file", type=Path, required=True, help="Явно выбранный установленный файл для побайтового сравнения")
    parser.add_argument("--output", type=Path, required=True, help="JSON отчёт внутри папки проекта")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if not output.is_relative_to(PROJECT_ROOT):
        parser.error("--output должен находиться внутри папки проекта")
    # Never overwrite either input, including aliases that resolve to it.
    inputs = (args.license_file, args.installed_file)
    if output in {file.resolve() for file in inputs}:
        parser.error("--output не должен совпадать с исходным или установленным файлом")
    if output.exists():
        try:
            if any(file.exists() and output.samefile(file) for file in inputs):
                parser.error("--output не должен быть ссылкой на исходный или установленный файл")
        except (OSError, ValueError):
            parser.error("Не удалось безопасно проверить файл отчёта")
    result = diagnose_license_files(args.license_file, args.installed_file)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError):
        print("Не удалось записать JSON отчёт; содержимое лицензии не выводится.", file=sys.stderr)
        return 2
    print(str(output))
    return 0 if result["execution_status"] == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
