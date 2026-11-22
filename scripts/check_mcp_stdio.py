"""Exercise the actual MCP stdio protocol; never call COM or execute a GAP plan.

Run with the project venv, for example:
  .venv/Scripts/python.exe scripts/check_mcp_stdio.py --workspace .
Only discovery, gap_environment, and gap_prepare_smoke are called. The prepared
JSON plan lives in an isolated temporary workspace below workspace/work; no
approval or execution is performed. JSON evidence is saved under outputs.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


EXPECTED_TOOLS = {
    "gap_environment", "gap_probe", "gap_prepare_smoke", "gap_prepare_manifest", "gap_execute", "gap_export",
}


async def check_protocol(project: Path, temporary_workspace: Path) -> dict:
    from mcp import Client, StdioServerParameters

    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "gap_integration.mcp_server", "--workspace", str(temporary_workspace)],
        cwd=project,
        env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    async with Client(parameters, mode='legacy', read_timeout_seconds=15) as client:
        discovery = await client.list_tools()
        names = {tool.name for tool in discovery.tools}
        if names != EXPECTED_TOOLS:
            raise RuntimeError(f"Unexpected MCP tools: {sorted(names)}")
        environment = await client.call_tool("gap_environment", {})
        preparation = await client.call_tool("gap_prepare_smoke", {})
        for name, result in (("gap_environment", environment), ("gap_prepare_smoke", preparation)):
            if result.is_error or not isinstance(result.structured_content, dict):
                raise RuntimeError(f"{name} returned a protocol error or no structured JSON")
        if preparation.structured_content.get("execution_status") not in {"SUCCESS", "CONFIRMATION_REQUIRED"}:
            raise RuntimeError("Smoke plan preparation failed (no execution was requested)")
        return {
            "execution_status": "SUCCESS", "transport": "stdio", "sdk_version": importlib.metadata.version("mcp"),
            "tool_names": sorted(names),
            "tools": [tool.model_dump(mode="json", by_alias=True, exclude_none=True) for tool in discovery.tools],
            "calls": [
                {"tool": "gap_environment", "result": environment.structured_content},
                {"tool": "gap_prepare_smoke", "result": preparation.structured_content},
            ],
            "com_called": False, "gap_plan_executed": False, "approval_created": False,
            "temporary_workspace_removed_after_check": True,
            "scope": "Real MCP discovery and read-only environment call; JSON plan preparation only. No COM/GAP execution.",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check actual MCP 2.x stdio without COM or GAP writes")
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path("outputs/mcp_protocol_check.json"))
    args = parser.parse_args(argv)
    project = Path(__file__).resolve().parents[1]
    workspace = args.workspace.resolve()
    output = args.output.resolve() if args.output.is_absolute() else (workspace / args.output).resolve()
    if not output.is_relative_to(workspace):
        parser.error("--output должен находиться внутри --workspace")
    work = workspace / "work"
    work.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="mcp-protocol-", dir=work) as directory:
            temporary_workspace = Path(directory).resolve()
            if not temporary_workspace.is_relative_to(workspace):
                raise RuntimeError("Temporary workspace escaped the project")
            result = asyncio.run(asyncio.wait_for(check_protocol(project, temporary_workspace), timeout=40))
    except Exception as exc:
        result = {
            "execution_status": "ERROR", "transport": "stdio", "com_called": False,
            "gap_plan_executed": False, "approval_created": False,
            "error_type": type(exc).__name__, "message": str(exc),
        }
        print(f"MCP stdio check failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(str(output))
    return 0 if result["execution_status"] == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
