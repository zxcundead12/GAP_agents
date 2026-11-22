"""Human-editable review tables derived from the authoritative JSON manifest."""

from __future__ import annotations

import csv
import json
from pathlib import Path


def write_tables(manifest, directory):
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    paths = []
    collections = {key: manifest.get(key, []) for key in ("nodes", "pipes", "valves", "ambiguities")}
    collections['sources'] = [node for node in manifest.get('nodes', []) if node.get('kind') == 'source']
    collections['boundaries'] = [node for node in manifest.get('nodes', []) if node.get('kind') == 'boundary']
    for category, records in collections.items():
        rows = []
        for record in records:
            flat = {key: value for key, value in record.items() if key not in ("parameters", "source_refs")}
            flat.update(record.get("parameters", {}))
            flat["source_refs"] = record.get("source_refs", [])
            rows.append({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                         for key, value in flat.items()})
        fields = list(dict.fromkeys(key for row in rows for key in row))
        path = destination / f"{category}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        paths.append(path)
    return paths
