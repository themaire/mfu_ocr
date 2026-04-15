from __future__ import annotations

from ocr_hailo.diagnostics import run_checks


for item in run_checks():
    status = "OK" if item.ok else "MANQUANT"
    print(f"[{status}] {item.name}: {item.details}")
