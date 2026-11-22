"""Local operator CLI. Approval is intentionally not an MCP tool."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .service import GapService

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None):
    parser = argparse.ArgumentParser(description="Управление GAP через проверенный план и локальное подтверждение")
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, help="Сохранить JSON внутри workspace")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("environment", "probe", "prepare-smoke"):
        commands.add_parser(name)
    prepare = commands.add_parser("prepare-manifest")
    prepare.add_argument("--manifest", required=True)
    prepare.add_argument("--mapping")
    readiness = commands.add_parser("readiness", help="Проверить манифест и доступные возможности адаптера без COM/GAP")
    readiness.add_argument("--manifest", default="data/candidate_manifest.json")
    approve = commands.add_parser("approve")
    approve.add_argument("--plan-id", required=True)
    approve.add_argument("--approved-by", required=True)
    execute = commands.add_parser("execute")
    execute.add_argument("--plan-id", required=True)
    execute.add_argument("--approval-id", default="")
    acknowledge = commands.add_parser("acknowledge", help="Локально зафиксировать ручной разбор неопределённого исхода; старый план не повторяется")
    acknowledge.add_argument("--plan-id", required=True)
    acknowledge.add_argument("--reviewed-by", required=True)
    acknowledge.add_argument("--note", required=True)
    export = commands.add_parser("export")
    export.add_argument("--model", required=True)
    args = parser.parse_args(argv)
    service = GapService(args.workspace)
    if args.command == "environment":
        result = service.inspect_environment()
    elif args.command == "probe":
        result = service.probe()
    elif args.command == "prepare-smoke":
        result = service.prepare_smoke_plan()
    elif args.command == "prepare-manifest":
        result = service.prepare_manifest_plan(args.manifest, args.mapping)
    elif args.command == "readiness":
        from .contracts import validate_manifest
        from .capabilities import build_readiness
        try:
            manifest_path, manifest, digest = service._read(args.manifest)
            validation = validate_manifest(manifest, service.workspace)
            result = build_readiness(manifest, validation)
            result.update(manifest_path=str(manifest_path), manifest_sha256=digest)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            result = service._failure(exc, "READINESS_FAILED")
    elif args.command == "approve":
        result = service.approve_plan(args.plan_id, args.approved_by)
    elif args.command == "execute":
        result = service.execute_plan(args.plan_id, args.approval_id)
    elif args.command == "acknowledge":
        result = service.acknowledge_unknown(args.plan_id, args.reviewed_by, args.note)
    else:
        result = service.export_model(args.model)
    if args.output:
        destination = service._path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "readiness":
        return 0 if result.get("execution_status") == "SUCCESS" and result.get("ready_for_production_build") else 2
    return 0 if result["execution_status"] == "SUCCESS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
