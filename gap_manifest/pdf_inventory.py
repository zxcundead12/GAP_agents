"""Extract located PDF labels. Text extraction does not infer connections."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path


def extract_inventory(pdf_path: str | Path) -> dict:
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("Для extract установите optional зависимости pdf из pyproject.toml") from exc
    source = Path(pdf_path)
    pages = []
    with pdfplumber.open(source) as pdf:
        for number, page in enumerate(pdf.pages, 1):
            words = [{"text": word["text"], "bbox": [round(word[k], 3) for k in ("x0", "top", "x1", "bottom")]}
                     for word in page.extract_words(x_tolerance=2, y_tolerance=2)]
            pages.append({"number": number, "width_pt": float(page.width), "height_pt": float(page.height),
                          "text": page.extract_text() or "", "words": words})
    return {"schema_version": "1.0", "source_name": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "generated_at": datetime.now(timezone.utc).isoformat(), "coordinate_system": "PDF points, top-left, pages from 1",
            "warning": "Подписи и координаты не определяют соединения. Геометрия и связи требуют визуальной проверки и подтверждения.",
            "pages": pages}
