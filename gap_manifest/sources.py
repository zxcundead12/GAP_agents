"""Local source relocation preserves original manifest references and SHA-256."""
import json
from pathlib import Path


def resolve_source(source, project_root):
    root = Path(project_root).resolve() if project_root is not None else None
    raw = source['path']
    if root is not None:
        config = root / 'data/source_locations.local.json'
        if config.is_file():
            overrides = json.loads(config.read_text(encoding='utf-8-sig'))
            if not isinstance(overrides, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not v.strip()
                                                       for k, v in overrides.items()):
                raise ValueError('Invalid source location configuration')
            raw = overrides.get(source['id'], raw)
    file = Path(raw)
    if not file.is_absolute():
        if root is None:
            raise ValueError('Relative source requires project root')
        file = root / file
    return file.resolve()

