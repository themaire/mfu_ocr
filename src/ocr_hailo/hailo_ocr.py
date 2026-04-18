"""Détection de zones de texte via Hailo NPU + PaddleOCR v5 Mobile Detection."""

import cv2
import numpy as np
from pathlib import Path
from PIL import Image
from typing import List, Tuple

from hailo_platform import (
    HEF,
    VDevice,
    HailoStreamInterface,
    ConfigureParams,
    InputVStreamParams,
    OutputVStreamParams,
    InputVStreams,
    OutputVStreams,
    FormatType,
)

# Chemin par défaut du modèle HEF
_DEFAULT_HEF = Path(__file__).resolve().parents[2] / "models" / "paddle_ocr_v5_mobile_detection.hef"

# Seuils par défaut
_DEFAULT_THRESHOLD = 0.3
_DEFAULT_MIN_AREA = 100  # pixels² minimum pour garder un contour

# Singleton pour garder le VDevice en vie (évite un segfault/abort au GC de HailoRT 4.20.0)
_vdevice = None


def _pil_to_cv2(image: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def _cv2_to_pil(image: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))


def _preprocess(image: Image.Image, target_h: int, target_w: int) -> np.ndarray:
    """Redimensionne une image PIL vers la taille d'entrée du modèle, retourne uint8 RGB."""
    resized = image.convert("RGB").resize((target_w, target_h), Image.BILINEAR)
    return np.array(resized, dtype=np.uint8)


def detect_text_regions(
    image: Image.Image,
    hef_path: str | Path | None = None,
    threshold: float = _DEFAULT_THRESHOLD,
    min_area: int = _DEFAULT_MIN_AREA,
) -> Tuple[List[Tuple[int, int, int, int]], np.ndarray]:
    """Détecte les zones de texte dans une image via le Hailo NPU.

    Args:
        image: Image PIL (RGB, taille quelconque).
        hef_path: Chemin vers le fichier HEF. Par défaut : models/paddle_ocr_v5_mobile_detection.hef.
        threshold: Seuil de probabilité pour binariser la probability map (0-1).
        min_area: Surface minimum (en pixels, à l'échelle du modèle) pour garder un contour.

    Returns:
        Tuple de :
        - Liste de bounding boxes (x, y, w, h) en coordonnées de l'image originale.
        - Probability map brute (H_model × W_model), float32, valeurs 0-1.
    """
    hef_path = Path(hef_path) if hef_path else _DEFAULT_HEF
    hef = HEF(str(hef_path))

    input_info = hef.get_input_vstream_infos()[0]
    model_h, model_w, model_c = input_info.shape

    orig_w, orig_h = image.size

    # Préparer l'image
    img_np = _preprocess(image, model_h, model_w)
    img_batch = img_np.reshape(1, model_h, model_w, model_c)

    # Inférence Hailo (singleton VDevice pour éviter crash au GC)
    global _vdevice
    if _vdevice is None:
        _vdevice = VDevice()
    configure_params = ConfigureParams.create_from_hef(hef=hef, interface=HailoStreamInterface.PCIe)
    ng = _vdevice.configure(hef, configure_params)[0]

    with ng.activate(ng.create_params()):
        ivsp = InputVStreamParams.make(ng, format_type=FormatType.UINT8)
        ovsp = OutputVStreamParams.make(ng, format_type=FormatType.FLOAT32)
        with InputVStreams(ng, ivsp) as ivs, OutputVStreams(ng, ovsp) as ovs:
            for vs in ivs:
                vs.send(img_batch)
            for vs in ovs:
                prob_map = vs.recv()

    # prob_map shape: (model_h, model_w, 1) → squeeze
    prob_map = prob_map.squeeze()  # (model_h, model_w)

    # Post-traitement : binariser + trouver les contours
    binary = (prob_map > threshold).astype(np.uint8) * 255

    # Dilatation pour fusionner les caractères proches en blocs de texte
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 3))
    dilated = cv2.dilate(binary, kernel, iterations=2)

    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Calculer les facteurs d'échelle vers l'image d'origine
    scale_x = orig_w / model_w
    scale_y = orig_h / model_h

    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w * h < min_area:
            continue
        # Convertir en coordonnées de l'image originale
        ox = int(x * scale_x)
        oy = int(y * scale_y)
        ow = int(w * scale_x)
        oh = int(h * scale_y)
        boxes.append((ox, oy, ow, oh))

    # Trier par position verticale puis horizontale (lecture naturelle)
    boxes.sort(key=lambda b: (b[1], b[0]))

    return boxes, prob_map


def extract_text_zone_images(
    image: Image.Image,
    boxes: List[Tuple[int, int, int, int]],
    padding: int = 5,
) -> List[Image.Image]:
    """Découpe les zones de texte depuis l'image originale.

    Args:
        image: Image PIL source (taille originale).
        boxes: Bounding boxes (x, y, w, h) en coordonnées de l'image.
        padding: Marge en pixels ajoutée autour de chaque zone.

    Returns:
        Liste d'images PIL, une par zone de texte.
    """
    orig_w, orig_h = image.size
    crops = []
    for x, y, w, h in boxes:
        x1 = max(0, x - padding)
        y1 = max(0, y - padding)
        x2 = min(orig_w, x + w + padding)
        y2 = min(orig_h, y + h + padding)
        crop = image.crop((x1, y1, x2, y2))
        crops.append(crop)
    return crops


def save_text_zones(
    image: Image.Image,
    boxes: List[Tuple[int, int, int, int]],
    output_dir: str | Path,
    prefix: str = "zone",
    padding: int = 5,
) -> List[Path]:
    """Découpe et sauvegarde les zones de texte dans des fichiers séparés.

    Args:
        image: Image PIL source.
        boxes: Bounding boxes (x, y, w, h).
        output_dir: Dossier de sortie.
        prefix: Préfixe pour les noms de fichiers.
        padding: Marge en pixels.

    Returns:
        Liste des chemins des fichiers créés.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    crops = extract_text_zone_images(image, boxes, padding)
    paths = []
    for i, crop in enumerate(crops):
        path = output_dir / f"{prefix}_{i:03d}.png"
        crop.save(path)
        paths.append(path)
    return paths


def draw_detections(
    image: Image.Image,
    boxes: List[Tuple[int, int, int, int]],
) -> Image.Image:
    """Dessine les bounding boxes sur une copie de l'image.

    Args:
        image: Image PIL source.
        boxes: Bounding boxes (x, y, w, h).

    Returns:
        Image PIL avec les rectangles dessinés en vert.
    """
    cv_img = _pil_to_cv2(image)
    for i, (x, y, w, h) in enumerate(boxes):
        cv2.rectangle(cv_img, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(cv_img, str(i), (x, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    return _cv2_to_pil(cv_img)
