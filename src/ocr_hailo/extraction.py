from __future__ import annotations

from pathlib import Path
import re

from pdf2image import convert_from_path
from PIL import Image, ImageFilter, ImageOps
from pypdf import PdfReader
import pytesseract

from ocr_hailo.table_detection import detect_table_regions, remove_table_lines

# Import optionnel du module Hailo (peut ne pas être disponible sur toutes les machines)
try:
    from ocr_hailo.hailo_ocr import detect_text_regions, extract_text_zone_images, draw_detections
    _HAILO_AVAILABLE = True
except (ImportError, OSError):
    _HAILO_AVAILABLE = False


def _is_digital_text_usable(pdf_path: str | Path) -> bool:
    """Vérifie si le texte numérique d'un PDF est fiable.

    Un scan PDF avec couche OCR embarquée (mauvaise qualité) contient
    une image pleine page + du texte dégradé. On le détecte en vérifiant
    si la majorité des pages contiennent des images : si oui, c'est un
    scan déguisé et le texte natif n'est pas fiable.
    """
    reader = PdfReader(str(pdf_path))
    if not reader.pages:
        return False
    pages_with_images = sum(
        1 for p in reader.pages if hasattr(p, "images") and len(p.images) > 0
    )
    # Si plus de la moitié des pages contiennent des images → scan déguisé
    return pages_with_images <= len(reader.pages) // 2


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


def _boxes_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int], threshold: float = 0.5) -> bool:
    """Vérifie si la box 'a' chevauche la box 'b' au-delà du seuil (ratio de l'aire de 'a')."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix0 = max(ax, bx)
    iy0 = max(ay, by)
    ix1 = min(ax + aw, bx + bw)
    iy1 = min(ay + ah, by + bh)
    if ix1 <= ix0 or iy1 <= iy0:
        return False
    inter = (ix1 - ix0) * (iy1 - iy0)
    return inter / (aw * ah) >= threshold


def _ocr_zones_hailo(
    image: Image.Image,
    language: str = "fra",
    debug_dir: Path | None = None,
    page_index: int = 0,
    exclude_regions: list[tuple[int, int, int, int]] | None = None,
) -> str:
    """OCR guidé par la détection de zones de texte Hailo NPU.

    Détecte les zones de texte via le Hailo, découpe chaque zone,
    puis lance Tesseract sur chaque zone individuellement.
    Les zones qui chevauchent les régions exclues (tableaux) sont ignorées.
    Les résultats sont assemblés dans l'ordre de lecture (haut→bas, gauche→droite).
    """
    boxes, _ = detect_text_regions(image, threshold=0.3, min_area=500)
    if not boxes:
        return ""

    # Filtrer les zones trop petites (bruit : points, traits, caractères isolés)
    MIN_W, MIN_H = 250, 60
    boxes = [(x, y, w, h) for x, y, w, h in boxes if w >= MIN_W and h >= MIN_H]

    # Exclure les zones qui chevauchent les tableaux détectés
    if exclude_regions:
        boxes = [
            box for box in boxes
            if not any(_boxes_overlap(box, tr) for tr in exclude_regions)
        ]

    if not boxes:
        return ""

    # Mode debug : sauvegarder l'image annotée et les crops
    if debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        annotated = draw_detections(image, boxes)
        annotated.save(debug_dir / f"page{page_index}_detections.png")

    crops = extract_text_zone_images(image, boxes, padding=10)
    parts: list[str] = []
    for i, crop in enumerate(crops, start=1):
        if debug_dir is not None:
            crop.save(debug_dir / f"page{page_index}_zone{i:02d}.png")

        # Préparer l'image de la zone pour Tesseract
        gray = ImageOps.grayscale(crop)
        gray = ImageOps.autocontrast(gray)
        if gray.width < 600:
            gray = gray.resize(
                (gray.width * 2, gray.height * 2), Image.Resampling.LANCZOS
            )
        sharp = gray.filter(ImageFilter.SHARPEN)
        text = pytesseract.image_to_string(
            sharp, lang=language,
            config="--oem 1 --psm 6 -c preserve_interword_spaces=1",
        ).strip()
        if text:
            parts.append(text)

    return "\n\n".join(parts)


def _ocr_table_layout(image: Image.Image, language: str = "fra") -> str:
    """Reconstruction spatiale par coordonnées Y, optimisée pour les tableaux.

    Contrairement à _ocr_page_layout qui suit le groupage block/par/line de Tesseract,
    cette fonction regroupe les mots purement par position verticale (centre du mot).
    Cela fusionne les colonnes d'un tableau en lignes cohérentes.
    """
    data = pytesseract.image_to_data(
        image, lang=language, config="--oem 1 --psm 6",
        output_type=pytesseract.Output.DICT,
    )

    n = len(data["text"])
    words: list[tuple[int, int, int, int, str]] = []  # (top, h, left, right, text)
    for i in range(n):
        txt = data["text"][i].strip()
        if not txt or int(data["conf"][i]) < 20:
            continue
        top = data["top"][i]
        h = data["height"][i]
        left = data["left"][i]
        right = left + data["width"][i]
        words.append((top, h, left, right, txt))

    if not words:
        return ""

    # Tolérance adaptative : demi-hauteur médiane des mots
    heights = sorted(w[1] for w in words)
    median_h = heights[len(heights) // 2]
    row_tol = max(median_h // 2, 10)

    # Tri par centre Y puis X
    words.sort(key=lambda w: (w[0] + w[1] // 2, w[2]))

    # Regroupement en lignes par centre Y
    rows: list[list[tuple[int, int, int, int, str]]] = []
    row: list[tuple[int, int, int, int, str]] = [words[0]]
    row_center = words[0][0] + words[0][1] // 2

    for w in words[1:]:
        w_center = w[0] + w[1] // 2
        if abs(w_center - row_center) <= row_tol:
            row.append(w)
        else:
            rows.append(row)
            row = [w]
            row_center = w_center
    rows.append(row)

    # Construction du texte ligne par ligne
    lines: list[str] = []
    for row_words in rows:
        row_words.sort(key=lambda w: w[2])
        parts: list[str] = []
        prev_right = 0
        for _top, _h, left, right, txt in row_words:
            if prev_right > 0:
                gap = left - prev_right
                parts.append("    " if gap > 40 else " ")
            parts.append(txt)
            prev_right = right
        lines.append("".join(parts))

    return "\n".join(lines)


def _ocr_table_image(image: Image.Image, language: str = "fra") -> str:
    """OCR optimisé pour une image de tableau dont les bordures ont été effacées."""
    gray = ImageOps.grayscale(image) if image.mode != "L" else image
    gray = ImageOps.autocontrast(gray)
    sharp = gray.filter(ImageFilter.SHARPEN)

    # Reconstruction spatiale par coordonnées Y (prioritaire pour les tableaux)
    layout_text = _ocr_table_layout(sharp, language)
    if layout_text and len(layout_text.strip()) > 20:
        return layout_text

    # Fallback : PSM classiques si la reconstruction spatiale échoue
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


def ocr_scanned_pdf(
    pdf_path: str | Path,
    language: str = "fra",
    return_analysis: bool = False,
    use_hailo: bool | None = None,
    debug: bool = False,
) -> str | tuple[str, str]:
    """OCR fallback pour un PDF scanné via Poppler + Tesseract.

    Args:
        use_hailo: Active la détection de zones de texte via le Hailo NPU.
            None = auto-détection (True si le Hailo est disponible).
            Le mode Hailo détecte d'abord les zones de texte (rapide),
            puis lance Tesseract sur chaque zone individuellement.
        debug: Sauvegarde les crops et l'image annotée dans input/crops/{pdf_stem}/.
    """
    hailo_enabled = _HAILO_AVAILABLE if use_hailo is None else (use_hailo and _HAILO_AVAILABLE)
    images = convert_from_path(str(pdf_path), dpi=400)
    blocks: list[str] = []
    analysis_blocks: list[str] = []

    # Répertoire temporaire pour les tables extraites
    pdf_stem = Path(pdf_path).stem
    table_dir = Path(pdf_path).parent / "tables" / pdf_stem
    debug_dir = Path(pdf_path).parent / "crops" / pdf_stem if debug else None

    for index, image in enumerate(images, start=1):
        # Pré-détection rapide → ignorer les pages inutiles (annexes, plans, cartes…)
        skip_reason = _is_skip_page(image, language)
        if skip_reason:
            blocks.append(f"--- Page {index} --- [PAGE IGNORÉE : « {skip_reason} » détecté]")
            continue

        # Détecter les tableaux dans la page (toujours, même en mode Hailo)
        table_regions = detect_table_regions(image)
        table_texts: list[str] = []

        if table_regions:
            table_dir.mkdir(parents=True, exist_ok=True)

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

        # OCR du texte hors-tableau : Hailo si disponible, sinon pipeline classique
        if hailo_enabled:
            hailo_text = _ocr_zones_hailo(
                image, language, debug_dir=debug_dir, page_index=index,
                exclude_regions=table_regions or [],
            )
            if hailo_text:
                page_text = _clean_ocr_text(hailo_text)
            else:
                page_text = ""
            raw_text = hailo_text
        else:
            text, candidates = _ocr_page(image, language=language)
            page_text = _clean_ocr_text(text) if text else ""
            raw_text = text

        # Assembler : texte de page + texte de table en section dédiée
        page_parts = []
        if page_text:
            page_parts.append(page_text)
        for t_text in table_texts:
            page_parts.append(f"[TABLEAU DETECTE]\n{t_text}\n[FIN TABLEAU]")

        if page_parts:
            blocks.append(f"--- Page {index} ---\n" + "\n\n".join(page_parts))

        # Analyse
        analysis_parts = []
        if raw_text:
            analysis_parts.append(raw_text)
        if not hailo_enabled and not raw_text and candidates:
            analysis_parts.extend(candidates[:2])
        analysis_parts.extend(table_texts)
        if analysis_parts:
            analysis_blocks.append(f"--- Page {index} ---\n" + "\n\n".join(analysis_parts))

    result = "\n\n".join(blocks).strip()
    analysis_text = "\n\n".join(analysis_blocks).strip() or result

    if return_analysis:
        return result, analysis_text
    return result


def process_pdf(
    pdf_path: str | Path,
    language: str = "fra",
    return_analysis: bool = False,
    use_hailo: bool | None = None,
    debug: bool = False,
) -> str | tuple[str, str]:
    """Privilégie le texte natif puis bascule sur l'OCR si nécessaire."""
    digital_text = extract_digital_text(pdf_path)
    if digital_text and _is_digital_text_usable(pdf_path):
        if return_analysis:
            return digital_text, digital_text
        return digital_text

    return ocr_scanned_pdf(pdf_path, language=language, return_analysis=return_analysis, use_hailo=use_hailo, debug=debug)


def write_text_output(text: str, output_path: str | Path) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target
