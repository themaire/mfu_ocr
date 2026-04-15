from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def _pil_to_cv2(image: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def _cv2_to_pil(image: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))


def detect_table_regions(image: Image.Image, min_area_ratio: float = 0.03) -> list[tuple[int, int, int, int]]:
    """Détecte les régions de tableau dans une image via les lignes de bordure.

    Retourne une liste de bounding boxes (x, y, w, h) triées de haut en bas.
    min_area_ratio : surface minimale du tableau par rapport à la page (3% par défaut).
    """
    cv_img = _pil_to_cv2(image)
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)

    h, w = binary.shape
    page_area = h * w

    # Détection des lignes horizontales
    horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 8, 100), 1))
    horiz_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horiz_kernel, iterations=1)

    # Détection des lignes verticales
    vert_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(h // 15, 50)))
    vert_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vert_kernel, iterations=1)

    # Combiner les lignes
    table_mask = cv2.add(horiz_lines, vert_lines)

    # Dilater pour relier les lignes proches
    dilate_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    table_mask = cv2.dilate(table_mask, dilate_kernel, iterations=3)

    # Trouver les contours
    contours, _ = cv2.findContours(table_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    regions: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, cw, ch = cv2.boundingRect(contour)
        area = cw * ch
        if area < page_area * min_area_ratio:
            continue
        # Un tableau est plus large que haut ou au moins de taille raisonnable
        if cw < w * 0.2:
            continue
        regions.append((x, y, cw, ch))

    # Trier de haut en bas
    regions.sort(key=lambda r: r[1])
    return regions


def extract_table_images(
    image: Image.Image,
    page_num: int,
    output_dir: Path,
    padding: int = 20,
) -> list[Path]:
    """Détecte et extrait les tableaux d'une page en images séparées.

    Sauvegarde dans output_dir/page{N}_table{M}.png.
    Les lignes de bordure du tableau sont effacées pour améliorer l'OCR.
    Retourne la liste des chemins créés.
    """
    regions = detect_table_regions(image)
    if not regions:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    w, h = image.size
    paths: list[Path] = []

    for idx, (x, y, cw, ch) in enumerate(regions, 1):
        # Appliquer un padding et clamper aux limites
        x0 = max(0, x - padding)
        y0 = max(0, y - padding)
        x1 = min(w, x + cw + padding)
        y1 = min(h, y + ch + padding)

        table_img = image.crop((x0, y0, x1, y1))
        cleaned = remove_table_lines(table_img)
        path = output_dir / f"page{page_num}_table{idx}.png"
        cleaned.save(path)
        paths.append(path)

    return paths


def remove_table_lines(image: Image.Image) -> Image.Image:
    """Efface les lignes horizontales et verticales (bordures de tableau) d'une image."""
    cv_img = _pil_to_cv2(image)
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)

    h, w = binary.shape

    # Lignes horizontales
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 4, 80), 1))
    horiz = cv2.morphologyEx(binary, cv2.MORPH_OPEN, hk)

    # Lignes verticales
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(h // 8, 40)))
    vert = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vk)

    # Combiner et dilater pour couvrir les bords
    lines = cv2.add(horiz, vert)
    lines = cv2.dilate(lines, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=2)

    # Peindre les lignes en blanc
    cleaned = gray.copy()
    cleaned[lines > 0] = 255

    return Image.fromarray(cleaned)
