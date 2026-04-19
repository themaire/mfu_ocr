"""Serveur Flask exposant le pipeline OCR via une API HTTP."""

from __future__ import annotations

import json
import time
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory

from .extraction import extract_digital_text, _is_digital_text_usable, process_pdf, write_text_output
from .metadata import extract_document_metadata, write_metadata_json

app = Flask(__name__)

INPUT_DIR = Path("input")
OUTPUT_DIR = Path("output")


def _list_files_urls(directory: Path, url_prefix: str) -> list[str]:
    """Liste les fichiers d'un répertoire et retourne leurs URLs relatives."""
    if not directory.is_dir():
        return []
    return sorted(
        f"{url_prefix}/{f.name}"
        for f in directory.iterdir()
        if f.is_file()
    )


@app.route("/process", methods=["GET"])
def process_pdf_route():
    """Traite un PDF situé dans ./input/.

    Paramètres GET :
        pdf : nom du fichier PDF (ex. ``mon_document.pdf``)
        lang : langue Tesseract (défaut ``fra``)
        hailo : ``true`` / ``false`` / absent (auto)
        debug : ``true`` pour sauvegarder les crops (défaut ``false``)

    Retourne un JSON contenant :
        - status, is_native, duration
        - liens vers le .txt et le .json de sortie
        - liens vers les crops et tables générés
    """
    pdf_name = request.args.get("pdf")
    if not pdf_name:
        return jsonify({"error": "Paramètre 'pdf' manquant"}), 400

    # Sécurité : interdire la traversée de répertoire
    if "/" in pdf_name or "\\" in pdf_name or ".." in pdf_name:
        return jsonify({"error": "Nom de fichier invalide"}), 400

    pdf_path = INPUT_DIR / pdf_name
    if not pdf_path.is_file():
        return jsonify({"error": f"Fichier introuvable : {pdf_path}"}), 404

    language = request.args.get("lang", "fra")

    hailo_param = request.args.get("hailo")
    use_hailo: bool | None = None
    if hailo_param is not None:
        use_hailo = hailo_param.lower() in ("true", "1", "yes")

    debug = request.args.get("debug", "false").lower() in ("true", "1", "yes")

    # Nom de sortie dérivé du PDF (sans extension)
    stem = pdf_path.stem

    # Déterminer si le PDF est natif (texte numérique uniquement)
    digital_text = extract_digital_text(pdf_path)
    is_native = bool(digital_text) and _is_digital_text_usable(pdf_path)

    output_txt = OUTPUT_DIR / f"{stem}.txt"
    output_json = OUTPUT_DIR / f"{stem}.json"

    t0 = time.monotonic()
    try:
        text, analysis_text = process_pdf(
            pdf_path,
            language=language,
            return_analysis=True,
            use_hailo=use_hailo,
            debug=debug,
        )
    except Exception as exc:
        return jsonify({"error": f"Erreur lors du traitement : {exc}"}), 500

    if not text:
        return jsonify({"error": "Aucun texte n'a pu être extrait du document."}), 422

    target = write_text_output(text, output_txt)
    metadata = extract_document_metadata(analysis_text, pdf_path.name)
    json_target = write_metadata_json(metadata, output_json)

    # Insérer le JSON au début du fichier texte
    json_block = json.dumps(metadata, indent=2, ensure_ascii=False)
    separator = "\n" + "=" * 72 + "\n\n"
    original_text = target.read_text(encoding="utf-8")
    target.write_text(json_block + separator + original_text, encoding="utf-8")

    elapsed = time.monotonic() - t0
    minutes, seconds = divmod(elapsed, 60)

    # Lister les fichiers crops et tables générés
    crops_dir = INPUT_DIR / "crops" / stem
    tables_dir = INPUT_DIR / "tables" / stem

    return jsonify({
        "status": "ok",
        "pdf": pdf_name,
        "is_native": is_native,
        "duration": f"{int(minutes)}m{seconds:05.2f}s",
        "output_txt": f"/files/output/{stem}.txt",
        "output_json": f"/files/output/{stem}.json",
        "metadata": metadata,
        "crops": _list_files_urls(crops_dir, f"/files/crops/{stem}"),
        "tables": _list_files_urls(tables_dir, f"/files/tables/{stem}"),
    })


# --- Routes pour servir les fichiers générés ---

@app.route("/files/output/<path:filename>")
def serve_output(filename: str):
    """Sert un fichier depuis ./output/."""
    return send_from_directory(OUTPUT_DIR.resolve(), filename)


@app.route("/files/crops/<stem>/<path:filename>")
def serve_crop(stem: str, filename: str):
    """Sert un fichier crop depuis ./input/crops/{stem}/."""
    directory = (INPUT_DIR / "crops" / stem).resolve()
    return send_from_directory(directory, filename)


@app.route("/files/tables/<stem>/<path:filename>")
def serve_table(stem: str, filename: str):
    """Sert un fichier table depuis ./input/tables/{stem}/."""
    directory = (INPUT_DIR / "tables" / stem).resolve()
    return send_from_directory(directory, filename)


@app.route("/health", methods=["GET"])
def health():
    """Endpoint de vérification que le serveur est opérationnel."""
    return jsonify({"status": "ok"})


def _file_entry(filepath: Path, url: str, file_type: str) -> dict:
    """Construit un descripteur de fichier."""
    return {
        "filename": filepath.name,
        "url": url,
        "type": file_type,
        "size": filepath.stat().st_size,
    }


@app.route("/results", methods=["GET"])
def list_results():
    """Liste tous les PDF déjà traités avec leurs fichiers de sortie.

    Retourne un JSON indexé par nom de PDF, contenant pour chacun
    la liste de tous les fichiers générés (txt, json, crops, tables).
    """
    results: dict[str, list[dict]] = {}

    # Parcourir les fichiers de sortie (.txt et .json) pour identifier les PDF traités
    if OUTPUT_DIR.is_dir():
        for f in sorted(OUTPUT_DIR.iterdir()):
            if not f.is_file():
                continue
            stem = f.stem
            ext = f.suffix.lower()
            if ext == ".txt":
                ftype = "text"
            elif ext == ".json":
                ftype = "json"
            else:
                continue
            results.setdefault(stem, []).append(
                _file_entry(f, f"/files/output/{f.name}", ftype)
            )

    # Parcourir les crops
    crops_root = INPUT_DIR / "crops"
    if crops_root.is_dir():
        for stem_dir in sorted(crops_root.iterdir()):
            if not stem_dir.is_dir():
                continue
            stem = stem_dir.name
            for f in sorted(stem_dir.iterdir()):
                if f.is_file():
                    results.setdefault(stem, []).append(
                        _file_entry(f, f"/files/crops/{stem}/{f.name}", "crop")
                    )

    # Parcourir les tables
    tables_root = INPUT_DIR / "tables"
    if tables_root.is_dir():
        for stem_dir in sorted(tables_root.iterdir()):
            if not stem_dir.is_dir():
                continue
            stem = stem_dir.name
            for f in sorted(stem_dir.iterdir()):
                if f.is_file():
                    results.setdefault(stem, []).append(
                        _file_entry(f, f"/files/tables/{stem}/{f.name}", "table")
                    )

    return jsonify(results)


@app.route("/", methods=["GET"])
def api_doc():
    """Documentation de l'API destinée à être consommée par une IA générative."""
    return jsonify({
        "service": "OCR Hailo API",
        "description": (
            "API REST pour traiter des actes fonciers PDF (scannés ou numériques) "
            "via un pipeline OCR hybride : détection de zones de texte par NPU Hailo-8, "
            "extraction de tableaux par OpenCV, reconnaissance de caractères par Tesseract. "
            "Le traitement produit un fichier texte (.txt), un JSON de métadonnées (.json), "
            "des images crops des zones de texte détectées et des images de tableaux extraits."
        ),
        "base_url": "http://<host>:5000",
        "routes": [
            {
                "method": "GET",
                "path": "/process",
                "description": (
                    "Lance le traitement OCR sur un PDF situé dans le dossier ./input/. "
                    "Retourne un JSON avec le statut, les métadonnées extraites, "
                    "et les URLs vers tous les fichiers générés (texte, JSON, crops, tables)."
                ),
                "parameters": [
                    {"name": "pdf", "required": True, "type": "string",
                     "description": "Nom du fichier PDF dans ./input/ (ex: 52003_CG_20151228.pdf)"},
                    {"name": "lang", "required": False, "type": "string", "default": "fra",
                     "description": "Code langue Tesseract (fra, eng, deu…)"},
                    {"name": "hailo", "required": False, "type": "boolean", "default": "auto",
                     "description": "Forcer l'utilisation du NPU Hailo (true/false). Auto-détecté si absent."},
                    {"name": "debug", "required": False, "type": "boolean", "default": "false",
                     "description": "Sauvegarder les crops de détection Hailo dans input/crops/"},
                ],
                "response_fields": {
                    "status": "ok si le traitement a réussi",
                    "pdf": "Nom du fichier PDF traité",
                    "is_native": "true si le PDF contenait du texte numérique exploitable (pas de scan)",
                    "duration": "Durée du traitement (ex: 1m23.45s)",
                    "output_txt": "URL pour télécharger le fichier texte OCR",
                    "output_json": "URL pour télécharger le JSON de métadonnées",
                    "metadata": "Objet JSON des métadonnées (commune, parcelles, type d'acte…)",
                    "crops": "Liste d'URLs des images crops (zones de texte détectées par Hailo)",
                    "tables": "Liste d'URLs des images de tableaux (détectés par OpenCV)",
                },
                "example": "/process?pdf=52003_CG_20151228.pdf&debug=true",
            },
            {
                "method": "GET",
                "path": "/results",
                "description": (
                    "Liste tous les PDF déjà traités. Retourne un JSON indexé par nom de PDF, "
                    "contenant pour chacun la liste de tous les fichiers générés. "
                    "Chaque fichier a un nom, une URL de téléchargement, un type "
                    "(text, json, crop, table) et une taille en octets."
                ),
                "parameters": [],
                "example": "/results",
            },
            {
                "method": "GET",
                "path": "/files/output/<filename>",
                "description": "Télécharge un fichier de sortie (.txt ou .json) depuis ./output/.",
                "example": "/files/output/52003_CG_20151228.txt",
            },
            {
                "method": "GET",
                "path": "/files/crops/<stem>/<filename>",
                "description": (
                    "Télécharge une image crop d'une zone de texte détectée par le Hailo NPU. "
                    "<stem> est le nom du PDF sans extension."
                ),
                "example": "/files/crops/52003_CG_20151228/page1_zone01.png",
            },
            {
                "method": "GET",
                "path": "/files/tables/<stem>/<filename>",
                "description": (
                    "Télécharge une image de tableau détecté par OpenCV, "
                    "dont les bordures ont été supprimées. <stem> est le nom du PDF sans extension."
                ),
                "example": "/files/tables/52003_CG_20151228/page1_table1.png",
            },
            {
                "method": "GET",
                "path": "/health",
                "description": "Vérifie que le serveur est opérationnel. Retourne {\"status\": \"ok\"}.",
            },
        ],
        "workflow_tips": [
            "1. Appeler /process?pdf=<fichier> pour lancer le traitement OCR d'un PDF.",
            "2. Lire le champ 'is_native' pour savoir si le document était scanné ou numérique.",
            "3. Télécharger le .txt via output_txt pour obtenir le texte OCR complet.",
            "4. Télécharger le .json via output_json pour les métadonnées structurées (commune, parcelles…).",
            "5. Si les métadonnées regex sont insuffisantes, télécharger les images crops et tables "
               "pour les analyser visuellement avec un modèle vision (ex: gemma3).",
            "6. Appeler /results pour lister tous les traitements passés et leurs fichiers.",
        ],
        "file_naming_convention": (
            "Les PDF d'entrée suivent le format {code_site}_{type_acte}_{date}.pdf. "
            "Exemple : 52003_CG_20151228.pdf → code site 52003, convention de gestion, du 28/12/2015."
        ),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
