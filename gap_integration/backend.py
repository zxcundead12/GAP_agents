"""Isolated, bounded COM worker. No free-form API command is exposed."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


def run_job(job: dict, workspace: Path | str, timeout_seconds: float = 60) -> dict:
    root = Path(workspace).resolve()
    trace_id = str(uuid.uuid4())
    if not isinstance(job, dict) or job.get("operation") not in ("probe", "smoke", "export", "build"):
        return {"execution_status": "ERROR", "trace_id": trace_id, "message": "Неподдерживаемая операция"}
    directory = root / "outputs" / "gap" / "jobs"
    directory.mkdir(parents=True, exist_ok=True)
    request_path = directory / f"{trace_id}.request.json"
    journal = directory / f"{trace_id}.jsonl"
    request = {"job": job, "workspace": str(root), "trace_id": trace_id, "journal_path": str(journal)}
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    interpreter = root / ".venv" / "Scripts" / "python.exe"
    if not interpreter.is_file():
        interpreter = Path(sys.executable)
    temporary = root / "work" / "gap_tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONUTF8": "1", "TEMP": str(temporary), "TMP": str(temporary)}
    command = [str(interpreter), "-X", "utf8", "-m", "gap_integration.worker", str(request_path)]
    try:
        process = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return {"execution_status": "TIMEOUT", "trace_id": trace_id, "journal_path": str(journal),
                "message": "Истёк срок ожидания worker. GAP не остановлен и расчёт не объявлен отменённым; проверьте сессию вручную. Автоповтор запрещён."}
    try:
        result = json.loads(process.stdout.strip())
        if not isinstance(result, dict) or "execution_status" not in result:
            raise ValueError("Invalid worker response")
    except (ValueError, json.JSONDecodeError):
        result = {"execution_status": "ERROR", "trace_id": trace_id, "journal_path": str(journal),
                  "message": "Worker не вернул корректный JSON", "details": process.stderr[-2000:]}
    result.setdefault("trace_id", trace_id)
    result.setdefault("journal_path", str(journal))
    (directory / f"{trace_id}.result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
