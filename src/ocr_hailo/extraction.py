from __future__ import annotations

from pathlib import Path
import re

from pdf2image import convert_from_path
from PIL import Image, ImageFilter, ImageOps
from pypdf import PdfReader
import pytesseract


def extract_digital_text(pdf_path: str | Path) -> str:
    """Extrait le texte présent nativement dans un PDF."""
    reader = PdfReader(str(pdf_path))
    blocks: list[str] = []

    for index, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            blocks.append(f"--- Page {index} ---\n{text}")

    return "\n\n".join(blocks).strip()


def _prepare_images_for_ocr(image: Image.Image) -> list[Image.Image]:
    base = ImageOps.exif_transpose(image)
    gray = ImageOps.grayscale(base)
    gray = ImageOps.autocontrast(gray)

    working = gray
    if working.width < 2200:
        working = working.resize((working.width * 2, working.height * 2), Image.Resampling.LANCZOS)

    sharp = working.filter(ImageFilter.SHARPEN)
    binary = sharp.point(lambda px: 255 if px > 170 else 0)
    return [sharp, binary]


def _score_ocr_text(text: str) -> tuple[int, int, int, int, int, int]:
    normalized = text.upper()
    keyword_bonus = sum(
        normalized.count(keyword)
        for keyword in ["SECTION", "PARCELLE", "COMMUNE", "ARTICLE", "PROPRIETAIRE", "REFERENCES CADASTRALES"]
    )
    parcel_pairs = len(re.findall(r"\b[A-Z]{1,4}\s+[0-9]{1,4}[A-Z]?\b", normalized))
    surface_hits = len(re.findall(r"\b[0-9O]+\s*HA\b", normalized))
    table_row_hits = sum(
        1
        for line in normalized.splitlines()
        if re.search(r"\b[0-9]{2,4}[A-Z]?\b", line) and re.search(r"\bHA\b", line)
    )
    alnum_count = len(re.findall(r"\w", text, flags=re.UNICODE))
    line_count = len([line for line in text.splitlines() if line.strip()])
    score = table_row_hits * 30 + parcel_pairs * 12 + keyword_bonus * 3 + surface_hits * 2
    return (score, table_row_hits, parcel_pairs, alnum_count, line_count, len(text))


def _ocr_page_layout(image: Image.Image, language: str = "fra") -> str:
    """OCR avec reconstruction spatiale pour améliorer la lisibilité des tableaux."""
    config = "--oem 1 --psm 6"
    data = pytesseract.image_to_data(
        image, lang=language, config=config,
        output_type=pytesseract.Output.DICT,
    )

    n = len(data["text"])
    entries: list[tuple[int, int, int, int, int, str]] = []
    for i in range(n):
        text = data["text"][i].strip()
        conf = int(data["conf"][i])
        if not text or conf < 25:
            continue
        entries.append((
            data["block_num"][i], data["par_num"][i], data["line_num"][i],
            data["left"][i], data["left"][i] + data["width"][i], text,
        ))

    if not entries:
        return ""

    line_groups: dict[tuple[int, int, int], list[tuple[int, int, str]]] = {}
    for block, par, line_num, x_start, x_end, text in entries:
        key = (block, par, line_num)
        line_groups.setdefault(key, []).append((x_start, x_end, text))

    output_lines: list[str] = []
    for key in sorted(line_groups):
        words = sorted(line_groups[key], key=lambda w: w[0])
        parts: list[str] = []
        prev_end = 0
        for x_start, x_end, text in words:
            if prev_end > 0:
                gap = x_start - prev_end
                parts.append("    " if gap > 50 else " ")
            parts.append(text)
            prev_end = x_end
        output_lines.append("".join(parts))

    return "\n".join(output_lines)


def _clean_ocr_text(text: str) -> str:
    """Post-traitement pour éliminer le bruit OCR (artefacts de sidebar, bordures)."""
    lines = text.split("\n")

    # Détecter les rafales de bruit : 3+ lignes consécutives très courtes
    is_short = [0 < len(line.strip()) <= 3 for line in lines]
    to_remove: set[int] = set()
    i = 0
    while i < len(lines):
        if is_short[i]:
            j = i
            while j < len(lines) and is_short[j]:
                j += 1
            if j - i >= 3:
                to_remove.update(range(i, j))
            i = j
        else:
            i += 1

    cleaned = [line for idx, line in enumerate(lines) if idx not in to_remove]

    # Supprimer les lignes purement symboliques
    filtered: list[str] = []
    for line in cleaned:
        stripped = line.strip()
        if stripped and not any(c.isalnum() for c in stripped):
            continue
        filtered.append(line)

    # Réduire les lignes vides multiples
    result: list[str] = []
    prev_blank = False
    for line in filtered:
        if not line.strip():
            if not prev_blank:
                result.append("")
            prev_blank = True
        else:
            prev_blank = False
            result.append(line)

    return "\n".join(result)


def _ocr_page(image: Image.Image, language: str = "fra") -> tuple[str, list[str]]:
    candidates: list[str] = []
    configs = [
        "--oem 1 --psm 3 -c preserve_interword_spaces=1",
        "--oem 1 --psm 4 -c preserve_interword_spaces=1",
        "--oem 1 --psm 6 -c preserve_interword_spaces=1",
    ]

    images = _prepare_images_for_ocr(image)
    for prepared in images:
        for config in configs:
            text = pytesseract.image_to_string(prepared, lang=language, config=config).strip()
            if text:
                candidates.append(text)

    # Reconstruction spatiale (meilleure pour les tableaux)
    layout_text = _ocr_page_layout(images[-1], language)
    if layout_text:
        candidates.append(layout_text)

    if not candidates:
        return "", []

    unique_candidates = list(dict.fromkeys(candidates))
    unique_candidates.sort(key=_score_ocr_text, reverse=True)
    return unique_candidates[0], unique_candidates


def ocr_scanned_pdf(pdf_path: str | Path, language: str = "fra", return_analysis: bool = False) -> str | tuple[str, str]:
    """OCR fallback pour un PDF scanné via Poppler + Tesseract."""
    images = convert_from_path(str(pdf_path), dpi=400)
    blocks: list[str] = []
    analysis_blocks: list[str] = []

    for index, image in enumerate(images, start=1):
        text, candidates = _ocr_page(image, language=language)
        if text:
            blocks.append(f"--- Page {index} ---\n{_clean_ocr_text(text)}")

        if candidates:
            top_candidates = candidates[:3]
            merged_candidates = "\n\n".join(top_candidates)
            analysis_blocks.append(f"--- Page {index} ---\n{merged_candidates}")

    result = "\n\n".join(blocks).strip()
    analysis_text = "\n\n".join(analysis_blocks).strip() or result

    if return_analysis:
        return result, analysis_text
    return result


def process_pdf(pdf_path: str | Path, language: str = "fra", return_analysis: bool = False) -> str | tuple[str, str]:
    """Privilégie le texte natif puis bascule sur l'OCR si nécessaire."""
    digital_text = extract_digital_text(pdf_path)
    if digital_text:
        if return_analysis:
            return digital_text, digital_text
        return digital_text

    return ocr_scanned_pdf(pdf_path, language=language, return_analysis=return_analysis)


def write_text_output(text: str, output_path: str | Path) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target
