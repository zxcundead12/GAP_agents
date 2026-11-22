"""Reproducible, offline workflow for the first project participant."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .validation import validate_manifest

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "data" / "candidate_manifest.json"


def read_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Non-finite JSON number: {value}")))


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def stamp(result, manifest_path, export_path=None):
    result["trace_id"] = str(uuid.uuid4())
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    result["provenance"] = {"manifest_sha256": hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest()}
    if export_path:
        result["provenance"]["export_sha256"] = hashlib.sha256(Path(export_path).read_bytes()).hexdigest()
    return result


def parser():
    command = argparse.ArgumentParser(description="Манифест схемы, проверка топологии и сравнение с экспортом GAP")
    commands = command.add_subparsers(dest="command", required=True)
    extract = commands.add_parser("extract", help="Извлечь текст и координаты подписей PDF, без вывода соединений")
    extract.add_argument("pdf", type=Path)
    extract.add_argument("--output", type=Path, required=True)
    render = commands.add_parser("render", help="Создать PNG страницы PDF через Poppler")
    render.add_argument("pdf", type=Path)
    render.add_argument("--output", type=Path, required=True)
    render.add_argument("--page", type=int, default=1)
    render.add_argument("--scale-to", type=int, default=6000)
    render.add_argument("--poppler", type=Path)
    tables = commands.add_parser("tables", help="Сформировать CSV-таблицы для ручной проверки")
    tables.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    tables.add_argument("--output", type=Path, required=True, help="Каталог таблиц")
    for name in ("validate", "report", "compare"):
        sub = commands.add_parser(name)
        sub.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
        sub.add_argument("--root", type=Path, default=ROOT)
        sub.add_argument("--output", type=Path, required=True)
        if name in ("report", "compare"):
            sub.add_argument("--export", type=Path, help="Нормализованный экспорт, считанный из GAP")
            sub.add_argument("--tolerances", type=Path, help="Утвержденные численные допуски")
        if name == "report":
            sub.add_argument("--image", type=Path, default=ROOT / "work" / "scheme_overview.png")
    return command


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "render":
            from .render import render_page
            print(render_page(args.pdf, args.output, args.page, args.scale_to, args.poppler))
            return 0
        if args.command == "tables":
            from .tables import write_tables
            paths = write_tables(read_json(args.manifest), args.output)
            print(f"CSV-таблицы: {len(paths)}; исходный JSON не изменён. {args.output}")
            return 0
        if args.command == "extract":
            from .pdf_inventory import extract_inventory
            inventory = extract_inventory(args.pdf)
            save_json(args.output, inventory)
            print(f"Извлечено страниц: {len(inventory['pages'])}; соединения не определялись. {args.output}")
            return 0
        manifest = read_json(args.manifest)
        validation = stamp(validate_manifest(manifest, args.root), args.manifest)
        if args.command == "validate":
            save_json(args.output, validation)
            print(json.dumps({"ready_for_transfer": validation["ready_for_transfer"], "stats": validation["stats"]}, ensure_ascii=False))
            return 0 if validation["ready_for_transfer"] else 2
        comparison = None
        if args.export:
            from .compare import compare_export
            tolerances = read_json(args.tolerances) if args.tolerances else None
            comparison = compare_export(manifest, read_json(args.export), tolerances)
            if not validation["ready_for_transfer"]:
                comparison["assessment_status"] = "NOT_ASSESSED"
                comparison["issues"].append({"code": "MANIFEST_NOT_READY", "severity": "warning", "object_id": None,
                                            "message": "Валидация источников и манифеста не разрешает подтвержденную приемку", "source_refs": []})
                comparison["summary"]["issues"] = {severity: sum(i["severity"] == severity for i in comparison["issues"])
                                                      for severity in ("error", "warning", "info")}
                comparison["summary"]["issues"]["total"] = len(comparison["issues"])
            stamp(comparison, args.manifest, args.export)
        if args.command == "compare":
            if comparison is None:
                raise ValueError("Для compare требуется --export. Экспорт нельзя подменять входным манифестом.")
            save_json(args.output, comparison)
            print(f"Оценка: {comparison['assessment_status']}; {args.output}")
            return 0 if comparison["assessment_status"] == "OK" else 2
        from .report import write_html_report
        write_html_report(manifest, validation, args.output, comparison, args.image if args.image.exists() else None)
        print(f"Отчет: {args.output}; ready_for_transfer={validation['ready_for_transfer']}")
        return 0
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
