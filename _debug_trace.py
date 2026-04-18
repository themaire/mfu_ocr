"""Debug: trace table_context on actual output text."""
import re
from ocr_hailo.metadata import _normalize_for_matching

with open("output/52003_CG.txt") as f:
    text = f.read()

normalized_lines = [_normalize_for_matching(line) for line in text.splitlines()]
table_context = False
blank_streak = 0
document_has_cadastral_ref = False

for i, line in enumerate(normalized_lines):
    if "REFERENCE CADASTRALE" in line or "REFERENCES CADASTRALES" in line:
        document_has_cadastral_ref = True
        table_context = True
        blank_streak = 0
        print(f"[{i}] TRIGGER CADASTRAL REF: {line[:60]}")
        continue

    if "SECTION" in line and ("PARCELLE" in line or "NUMERO" in line):
        table_context = True
        blank_streak = 0
        print(f"[{i}] TRIGGER SECTION: {line[:60]}")
        continue

    if "N°" in line and "PARCELLE" in line:
        table_context = True
        blank_streak = 0
        print(f"[{i}] TRIGGER N°PARCELLE: {line[:60]}")
        continue

    if "FIGURANT AU CADASTRE" in line:
        table_context = True
        blank_streak = 0
        continue

    if line == "TABLEAU DETECTE" and document_has_cadastral_ref:
        table_context = True
        blank_streak = 0
        print(f"[{i}] TRIGGER TABLEAU (has_ref={document_has_cadastral_ref}): {line}")
        continue

    if line == "FIN TABLEAU":
        print(f"[{i}] FIN TABLEAU (table_context was {table_context})")
        table_context = False
        blank_streak = 0
        continue

    if "TOTAL DE LA SURFACE" in line or "CONTENANCE TOTALE" in line or "SURFACE TOTALE" in line:
        table_context = False
        blank_streak = 0
        print(f"[{i}] TRIGGER OFF: {line[:60]}")
        continue

    if table_context and not line.strip():
        blank_streak += 1
        if blank_streak >= 2:
            table_context = False
            print(f"[{i}] BLANK STREAK OFF")
        continue

    blank_streak = 0

    if table_context and line.startswith("ARTICLE "):
        table_context = False
        print(f"[{i}] ARTICLE OFF: {line[:60]}")
        continue

    if table_context:
        print(f"[{i}] TC=True: {line[:80]}")
