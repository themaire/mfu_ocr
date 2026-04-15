# Projet OCR avec Hailo sur Raspberry Pi 5

## Objectif
Mettre en place une chaîne d'extraction de texte pour des actes fonciers, capable de traiter :
- des PDF contenant du texte numérique ;
- des PDF scannés ;
- des documents mixtes avec annexes scannées.

La cible matérielle est un Raspberry Pi 5 équipé d'un Hailo AI HAT+ 26 TOPS.

## Fonctionnalités

### Pipeline OCR
- OCR multi-passes avec Tesseract (2 variantes d'image × 3 configs PSM + reconstruction spatiale)
- Détection et extraction de tableaux via OpenCV (morphologie) avec suppression des bordures
- Nettoyage automatique du bruit OCR (artefacts de sidebar, logos, rafales de lignes courtes)
- Exclusion configurable de pages inutiles (annexes, plans, cartes) via `config/skip_pages.txt`
- Chronométrage intégré du temps de traitement

### Extraction de métadonnées
- Code site, type d'acte et date depuis le nom de fichier (ex : `52003_BE_19900817.pdf`)
- Détection du type de document dans le texte (bail emphytéotique, convention de gestion…)
- Extraction de la commune avec nettoyage des artefacts OCR
- Extraction des parcelles cadastrales (tableaux, listes compactes type `B132 / B133 / ZS10`)

### Validation IGN
- Validation du nom de commune via l'API geo.api.gouv.fr (fuzzy matching + code INSEE)
- Vérification de chaque parcelle via l'API Carto IGN (existence, contenance en hectares, bbox)
- Filtrage automatique des faux positifs OCR grâce à la validation IGN

### Sortie
- Fichier `.txt` : JSON des métadonnées en en-tête + texte OCR complet
- Fichier `.json` : métadonnées structurées avec parcelles enrichies (idu, contenance, bbox)

## Démarrage rapide

### 1) Créer l'environnement virtuel et installer les dépendances
```bash
make install
```

### 2) Vérifier l'état de la machine
```bash
make check-env
```

### 3) Lancer un traitement sur un PDF
```bash
PYTHONPATH=src .venv/bin/python -m ocr_hailo.cli process-pdf input/52003_BE_19900817.pdf -o output/52003_BE.txt
```

Cette commande génère :
- `output/52003_BE.txt` : métadonnées JSON + texte OCRisé
- `output/52003_BE.json` : synthèse structurée avec validation IGN

### 4) Vérifier le résultat
```bash
cat output/52003_BE.json
grep -ni "cadastr\|section" output/52003_BE.txt
```

## Dépendances système
```bash
sudo apt update
sudo apt install -y tesseract-ocr tesseract-ocr-fra poppler-utils
```

Dépendances Python supplémentaires (installées automatiquement) :
- `opencv-python-headless` — détection de tableaux
- `numpy` — traitement d'image

## Convention de nommage des fichiers
```
{code_site}_{type_acte}_{date}.pdf
```
- Code site : 5 chiffres (ex : `52003`, `10088`)
- Type d'acte : 2+ lettres (ex : `BE` = bail emphytéotique, `CG` = convention de gestion)
- Date : format AAAAMMJJ (ex : `19900817`)

## Structure du projet
```text
.
├── config/
│   └── skip_pages.txt          # Mots-clés pour ignorer des pages (annexes, plans…)
├── docs/
│   └── roadmap.md
├── input/                      # PDF à traiter
├── javascript/
│   └── geo_api.js              # Référence JS des appels API IGN
├── output/                     # Résultats (.txt + .json)
├── scripts/
│   └── check_env.py
├── src/
│   └── ocr_hailo/
│       ├── cli.py              # Interface CLI (typer)
│       ├── diagnostics.py      # Diagnostic environnement
│       ├── extraction.py       # Pipeline OCR (Tesseract + OpenCV)
│       ├── geo_api.py          # Client API IGN / geo.api.gouv.fr
│       ├── metadata.py         # Extraction métadonnées métier
│       └── table_detection.py  # Détection de tableaux (OpenCV morphologie)
├── tests/
│   ├── test_diagnostics.py
│   ├── test_extraction.py
│   └── test_metadata.py       # 7 tests (parcelles, commune, type de doc)
├── Makefile
├── pyproject.toml
└── README.md
```

## Exemple de sortie JSON
```json
{
  "site_code": "10088",
  "document_type": "CG",
  "document_date": "2026-01-23",
  "source_filename": "10088_CG_20260123.pdf",
  "document_type_label": null,
  "commune": "Neuville-sur-Vanne",
  "code_insee": "10263",
  "cadastral_parcels": [
    {
      "section": "0B",
      "number": "132",
      "ign_verified": true,
      "idu": "102630000B0132",
      "nom_com": "Neuville-sur-Vanne",
      "contenance_ha": 0.3205,
      "numero": "0132",
      "bbox": "3.783663,48.26034575,3.78475852,48.26096594"
    }
  ]
}
```

## Configuration du filtre de pages
Le fichier `config/skip_pages.txt` permet d'ignorer les pages inutiles (annexes, cartes, plans).
Une expression par ligne, insensible à la casse :
```
carte de localisation du site
plan de localisation cadastral
annexe 1
annexe 2
```
Seules les pages pauvres en texte sont filtrées — une page de contenu mentionnant "Annexe 1" dans une phrase ne sera pas ignorée.

## Tests
```bash
PYTHONPATH=src .venv/bin/pytest tests/ -v
```

## Prochaines étapes
1. Exploiter le Hailo AI HAT+ pour la détection de layout documentaire (nécessite un modèle compilé sur x86)
2. Étape de toilettage par modèle local via LM Studio / Ollama (séparée du cœur OCR)
3. Traitement batch de dossiers complets
