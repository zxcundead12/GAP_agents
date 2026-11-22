"""Read-only COM availability probe; does not launch GAP or replace models."""

from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from gap_integration.backend import run_job

if __name__ == "__main__":
    result = run_job({"operation": "probe"}, ROOT)
    destination = ROOT / "outputs" / "gap" / "com_probe.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
