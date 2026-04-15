from __future__ import annotations

from pathlib import Path
import re

from pdf2image import convert_from_path
from PIL import Image, ImageFilter, ImageOps
from pypdf import PdfReader
import pytesseract

from ocr_hailo.table_detection import detect_table_regions, remove_table_lines


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


def _ocr_table_image(image: Image.Image, language: str = "fra") -> str:
    """OCR optimisé pour une image de tableau dont les bordures ont été effacées."""
    gray = ImageOps.grayscale(image) if image.mode != "L" else image
    gray = ImageOps.autocontrast(gray)
    sharp = gray.filter(ImageFilter.SHARPEN)

    best_text = ""
    best_score = (-1,)
    for psm in [3, 4, 6]:
        config = f"--oem 1 --psm {psm} -c preserve_interword_spaces=1"
        text = pytesseract.image_to_string(sharp, lang=language, config=config).strip()
        if text:
            score = _score_ocr_text(text)
            if score > best_score:
                best_score = score
                best_text = text
    return best_text


_SKIP_PATTERNS: list[re.Pattern[str]] | None = None
_SKIP_CONFIG = Path(__file__).resolve().parent.parent.parent / "config" / "skip_pages.txt"


def _load_skip_patterns() -> list[re.Pattern[str]]:
    global _SKIP_PATTERNS
    if _SKIP_PATTERNS is not None:
        return _SKIP_PATTERNS
    patterns: list[re.Pattern[str]] = []
    if _SKIP_CONFIG.exists():
        for line in _SKIP_CONFIG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.append(re.compile(re.escape(line), re.IGNORECASE))
    _SKIP_PATTERNS = patterns
    return _SKIP_PATTERNS


def _is_skip_page(image: Image.Image, language: str = "fra") -> str | None:
    """OCR rapide basse résolution pour détecter les pages à ignorer.

    Retourne le mot-clé trouvé ou None. Ignore les pages riches en texte
    (le mot-clé y apparaît probablement dans une phrase, pas comme titre).
    """
    patterns = _load_skip_patterns()
    if not patterns:
        return None
    small = image.resize((image.width // 4, image.height // 4), Image.Resampling.LANCZOS)
    gray = ImageOps.grayscale(small)
    gray = ImageOps.autocontrast(gray)
    text = pytesseract.image_to_string(gray, lang=language, config="--oem 1 --psm 3").strip()

    # Pages avec beaucoup de texte = contenu utile, ne pas ignorer
    alnum = sum(1 for c in text if c.isalnum())
    if alnum > 300:
        return None

    for pat in patterns:
        if pat.search(text):
            return pat.pattern.replace("\\", "")
    return None


def ocr_scanned_pdf(pdf_path: str | Path, language: str = "fra", return_analysis: bool = False) -> str | tuple[str, str]:
    """OCR fallback pour un PDF scanné via Poppler + Tesseract."""
    images = convert_from_path(str(pdf_path), dpi=400)
    blocks: list[str] = []
    analysis_blocks: list[str] = []

    # Répertoire temporaire pour les tables extraites
    pdf_stem = Path(pdf_path).stem
    table_dir = Path(pdf_path).parent / "tables" / pdf_stem

    for index, image in enumerate(images, start=1):
        # Pré-détection rapide → ignorer les pages inutiles (annexes, plans, cartes…)
        skip_reason = _is_skip_page(image, language)
        if skip_reason:
            blocks.append(f"--- Page {index} --- [PAGE IGNORÉE : « {skip_reason} » détecté]")
            continue

        # Détecter les tableaux dans la page
        table_regions = detect_table_regions(image)

        if table_regions:
            # Page avec tableau : OCR séparé pour les zones table vs hors-table
            table_dir.mkdir(parents=True, exist_ok=True)
            table_texts: list[str] = []

            for t_idx, (x, y, cw, ch) in enumerate(table_regions, 1):
                padding = 20
                pw, ph = image.size
                x0, y0 = max(0, x - padding), max(0, y - padding)
                x1, y1 = min(pw, x + cw + padding), min(ph, y + ch + padding)

                table_crop = image.crop((x0, y0, x1, y1))
                cleaned = remove_table_lines(table_crop)
                table_path = table_dir / f"page{index}_table{t_idx}.png"
                cleaned.save(table_path)

                table_text = _ocr_table_image(cleaned, language)
                if table_text:
                    table_texts.append(table_text)

            # OCR du reste de la page (sans la zone tableau)
            text, candidates = _ocr_page(image, language=language)
            cleaned_text = _clean_ocr_text(text) if text else ""

            # Assembler : texte de page + texte de table en section dédiée
            page_parts = []
            if cleaned_text:
                page_parts.append(cleaned_text)
            for t_text in table_texts:
                page_parts.append(f"[TABLEAU DETECTE]\n{t_text}\n[FIN TABLEAU]")

            if page_parts:
                blocks.append(f"--- Page {index} ---\n" + "\n\n".join(page_parts))

            # Pour l'analyse, combiner tous les candidats
            all_analysis = []
            if candidates:
                all_analysis.extend(candidates[:2])
            all_analysis.extend(table_texts)
            if all_analysis:
                analysis_blocks.append(f"--- Page {index} ---\n" + "\n\n".join(all_analysis))
        else:
            # Page sans tableau : pipeline standard
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
