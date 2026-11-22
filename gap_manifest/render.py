"""Optional PDF previews using Poppler; source PDFs remain unchanged."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def render_page(pdf_path, output_path, page=1, scale_to=6000, poppler=None):
    binary = str(poppler) if poppler else shutil.which("pdftoppm")
    if not binary:
        raise RuntimeError("Не найден pdftoppm. Укажите --poppler с полным путем к программе Poppler.")
    destination = Path(output_path)
    if destination.suffix.lower() != ".png":
        raise ValueError("Выходной файл должен иметь расширение .png")
    if page < 1 or scale_to < 500:
        raise ValueError("Номер страницы должен быть >=1, scale-to >=500")
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [binary, "-f", str(page), "-l", str(page), "-scale-to", str(scale_to), "-singlefile", "-png",
               str(Path(pdf_path).resolve()), str(destination.with_suffix("").resolve())]
    result = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"Poppler завершился с кодом {result.returncode}: {result.stderr.strip()}")
    if not destination.is_file():
        raise RuntimeError("Poppler не создал PNG")
    return destination
