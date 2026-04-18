# Projet OCR avec Hailo sur Raspberry Pi 5

## Objectif
Mettre en place une chaîne d'extraction de texte pour des actes fonciers, capable de traiter :
- des PDF contenant du texte numérique ;
- des PDF scannés ;
- des documents mixtes avec annexes scannées.

La cible matérielle est un Raspberry Pi 5 équipé d'un Hailo AI HAT+ 26 TOPS.

## Fonctionnalités

### Pipeline OCR hybride (Hailo NPU + Tesseract)

Le pipeline fonctionne en deux passes complémentaires sur chaque page :

#### 1. Détection de zones de texte — Hailo NPU
Le modèle PaddleOCR v5 Mobile Detection tourne sur le NPU Hailo-8 (26 TOPS) pour détecter
les zones de texte (paragraphes, titres, mentions isolées) dans chaque page.
Chaque zone est découpée (**crop**) et envoyée individuellement à Tesseract pour la
reconnaissance de caractères.

- Filtrage des zones trop petites (bruit : points, traits, caractères isolés)
- Exclusion automatique des zones qui chevauchent un tableau détecté (pas de doublon)
- Tri dans l'ordre de lecture (haut → bas, gauche → droite)
- Mode debug (`--debug`) : sauvegarde des crops dans `input/crops/{pdf_stem}/`

#### 2. Détection et extraction de tableaux — OpenCV
Les tableaux sont détectés par analyse morphologique (OpenCV), indépendamment du Hailo :

- Détection des régions tabulaires via contours morphologiques
- Découpe (**crop**) de chaque tableau avec marge de sécurité
- Suppression des bordures (lignes horizontales et verticales) pour isoler le contenu
- Sauvegarde des crops nettoyés dans `input/tables/{pdf_stem}/`
- OCR spécialisé avec **reconstruction spatiale ligne par ligne** : les mots sont regroupés
  par coordonnée Y (centre du mot), ce qui fusionne correctement les colonnes d'un tableau
  en lignes cohérentes — au lieu de lire colonne par colonne
- Fallback sur OCR multi-PSM (3, 4, 6) si la reconstruction spatiale échoue

#### Assemblage
Le texte de chaque page combine le texte des zones Hailo (paragraphes) et le texte des
tableaux, balisé par des marqueurs :

```
--- Page 2 ---
Texte courant extrait par Hailo + Tesseract…

[TABLEAU DETECTE]
Latrecey-Ormoy-sur-    Vaudry    ZW    22    4ha09a20c
TOTAL DE LA SURFACE SOUS CONVENTION    4 ha 09 a 20 ca
[FIN TABLEAU]
```

#### Autres traitements
- OCR multi-passes avec Tesseract (2 variantes d'image × 3 configs PSM) en mode sans Hailo
- Nettoyage automatique du bruit OCR (artefacts de sidebar, logos, rafales de lignes courtes)
- Exclusion configurable de pages inutiles (annexes, plans, cartes) via `config/skip_pages.txt`
- Chronométrage intégré du temps de traitement

### Extraction de métadonnées (première passe — regex)
- Code site, type d'acte et date depuis le nom de fichier (ex : `52003_BE_19900817.pdf`)
- Détection du type de document dans le texte (bail emphytéotique, convention de gestion…)
- Extraction de la commune avec nettoyage des artefacts OCR
- Extraction des parcelles cadastrales :
  - Contexte tabulaire : les blocs `[TABLEAU DETECTE]` activent l'extraction ligne par ligne
  - Pré-validation qualité : les blocs de bruit OCR (cartes, photos) sont automatiquement écartés
  - Formats reconnus : tableaux (section + numéro), préfixe commune (`078 ZA 265`),
    listes compactes (`B132 / B133 / ZS10`), listes virgule/et (`ZA1, ZA3 et BS55`),
    prose notariale (`parcelle cadastrée A 38`)

### Deuxième passe — validation par LLM (prochaine étape)
L'extraction regex est fragile face à la variété des actes fonciers (formats changeants selon
les notaires, les années, les départements). Le pipeline est conçu pour alimenter un modèle
de langage local (Ollama) en deuxième passe :

```
Pi 5 + Hailo                          Serveur Ollama (CPU Xeon)
┌──────────────┐                      ┌──────────────────┐
│ PDF          │                      │ Ollama           │
│  → Hailo NPU │  texte OCR + crops   │  gemma3:12b      │
│  → Tesseract │ ────────────────────→ │  (vision)        │
│  → OpenCV    │                      │                  │
│  → crops     │  ← JSON structuré    │  raisonnement    │
└──────────────┘                      └──────────────────┘
```

- Le texte OCR brut + les **crops d'images** (paragraphes Hailo + tableaux OpenCV) sont
  envoyés à un LLM vision (ex : `gemma3:12b`) via l'API compatible OpenAI d'Ollama
- Le LLM raisonne sur le contenu et retourne un JSON structuré (commune, parcelles, surfaces…)
- La validation IGN reste en troisième passe pour confirmer l'existence des parcelles

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
│       ├── extraction.py       # Pipeline OCR (Hailo NPU + Tesseract + OpenCV)
│       ├── geo_api.py          # Client API IGN / geo.api.gouv.fr
│       ├── hailo_ocr.py        # Détection de zones de texte via Hailo NPU (PaddleOCR v5)
│       ├── metadata.py         # Extraction métadonnées métier (regex)
│       └── table_detection.py  # Détection de tableaux (OpenCV morphologie)
├── tests/
│   ├── test_diagnostics.py
│   ├── test_extraction.py
│   └── test_metadata.py       # 11 tests (parcelles, commune, type de doc)
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
1. ~~Exploiter le Hailo AI HAT+ pour la détection de layout documentaire~~ ✅ Fait — crops de paragraphes via Hailo NPU
2. Deuxième passe LLM via Ollama (serveur dédié, modèle vision `gemma3:12b`) pour l'extraction structurée des métadonnées
3. Traitement batch de dossiers complets
4. Évaluation de PaddleOCR v5 Mobile Recognition sur Hailo (remplacement de Tesseract pour la reconnaissance de caractères)

## Notes techniques — Hailo AI HAT+

### Matériel détecté
- **Board** : Hailo-8 (architecture HAILO8, 26 TOPS)
- **Firmware** : 4.20.0
- **HailoRT** : 4.20.0-1 (paquets Debian `hailort` + `python3-hailort`)
- **TAPPAS Core** : hailo-tappas-core 3.31.0 (éléments GStreamer : `hailonet`, `hailofilter`, `hailocropper`…)
- **Interface** : PCIe (HAT+ sur Raspberry Pi 5)

### Modèle utilisé
- **PaddleOCR v5 Mobile Detection** (`paddle_ocr_v5_mobile_detection.hef`, 10.2 Mo)
- Entrée : `(544, 960, 3)` — image RGB uint8
- Sortie : `(544, 960, 1)` — probability map float32 (chaque pixel = probabilité d'être du texte)
- Source : [Hailo Model Zoo](https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/v2.14.0/hailo8/paddle_ocr_v5_mobile_detection.hef)

### Problèmes rencontrés et solutions

#### 1. numpy 2.x incompatible avec HailoRT 4.20.0
Les bindings pybind11 de HailoRT 4.20.0 ne supportent pas numpy ≥ 2.0.
Symptôme : toutes les API (`InferModel`, `InferVStreams`, `InputVStreams.send()`) échouent avec :
```
[HailoRT] [error] CHECK failed - Memory size [...] does not match the frame count! (Expected 1566720, got 0)
```
**Solution** : forcer numpy < 2 dans le venv :
```bash
pip install "numpy<2"
```
> Note : `opencv-python-headless ≥ 4.13` demande numpy ≥ 2 mais fonctionne en pratique avec numpy 1.26.x.

#### 2. API Python à utiliser (HailoRT 4.20.0)
L'API haut-niveau `InferModel` / `create_bindings()` / `run()` (documentée dans les tutoriels 5.x) ne fonctionne pas en 4.20.0 (bug `set_buffer` / size 0).

L'API qui fonctionne est **`InputVStreams` / `OutputVStreams`** avec `send()` / `recv()` :
```python
from hailo_platform import (HEF, VDevice, HailoStreamInterface,
    ConfigureParams, InputVStreamParams, OutputVStreamParams,
    InputVStreams, OutputVStreams, FormatType)

hef = HEF("models/paddle_ocr_v5_mobile_detection.hef")
target = VDevice()
configure_params = ConfigureParams.create_from_hef(hef=hef, interface=HailoStreamInterface.PCIe)
ng = target.configure(hef, configure_params)[0]

with ng.activate(ng.create_params()):
    ivsp = InputVStreamParams.make(ng, format_type=FormatType.UINT8)
    ovsp = OutputVStreamParams.make(ng, format_type=FormatType.FLOAT32)
    with InputVStreams(ng, ivsp) as ivs, OutputVStreams(ng, ovsp) as ovs:
        for vs in ivs:
            vs.send(image_batch)   # shape (1, 544, 960, 3), dtype=uint8
        for vs in ovs:
            prob_map = vs.recv()   # shape (544, 960, 1), dtype=float32
```

#### 3. Dimension batch obligatoire
`send()` attend un tableau de shape `(1, H, W, C)` (avec dimension batch), pas `(H, W, C)`.
Sans la dimension batch, le C++ reçoit `write size 2880` (= une seule ligne) au lieu de `1566720`.

#### 4. Segfault au cleanup
`target.release()` provoque un segfault à la sortie du processus. C'est un bug connu de HailoRT 4.20.0 qui n'affecte pas les résultats. Le contournement consiste à ne pas appeler `release()` explicitement (le processus se termine de toute façon).

#### 5. Accès à `hailo_platform` depuis le venv
Le paquet `python3-hailort` est installé au niveau système (`/usr/lib/python3/dist-packages/`).
Pour y accéder depuis le venv, activer les site-packages système dans `.venv/pyvenv.cfg` :
```
include-system-site-packages = true
```

---

### Annexes

## Panorama des modèles Hailo Model Zoo pour le Hailo-8

Modèles HEF pré-compilés disponibles sur le [Hailo Model Zoo](https://github.com/hailo-ai/hailo_model_zoo) (architecture `hailo8`).

### Classification d'images
| Modèle | Taille d'entrée | Précision (top-1) |
|--------|-----------------|-------------------|
| ResNet-18 | 224×224 | 69.0% |
| ResNet-50 | 224×224 | 75.5% |
| MobileNet v2 1.0 | 224×224 | 71.8% |
| MobileNet v3 Large | 224×224 | 73.2% |
| EfficientNet-B0 | 224×224 | 74.9% |
| EfficientNet-Lite4 | 300×300 | 80.4% |
| RegNetX-800MF | 224×224 | 74.7% |
| SqueezeNet 1.0 | 224×224 | 58.0% |
| ShuffleNet v2 x1.0 | 224×224 | 69.4% |
| Inception v1 | 224×224 | 69.5% |

### Détection d'objets
| Modèle | Taille d'entrée | mAP (COCO) |
|--------|-----------------|------------|
| YOLOv5m | 640×640 | 43.6% |
| YOLOv5s | 640×640 | 35.0% |
| YOLOv5n | 640×640 | 26.0% |
| YOLOv6n | 640×640 | 34.3% |
| YOLOv7 | 640×640 | 49.5% |
| YOLOv7-tiny | 640×640 | 34.1% |
| YOLOv8m | 640×640 | 45.8% |
| YOLOv8s | 640×640 | 40.5% |
| YOLOv8n | 640×640 | 33.4% |
| SSD MobileNet v1 | 300×300 | 23.0% |
| SSD MobileNet v2 | 300×300 | 24.2% |
| CenterNet (ResNet-18) | 512×512 | 27.5% |
| NanoDet | 416×416 | 24.8% |
| DETR (ResNet-50) | 800×800 | 41.0% |

### Segmentation sémantique
| Modèle | Taille d'entrée | mIoU |
|--------|-----------------|------|
| FCN-8s | 512×512 | 62.7% |
| DeepLab v3 (MobileNet v2) | 513×513 | 71.7% |
| Stacked Fast-SCNN | 1024×2048 | 72.7% |

### Segmentation d'instances
| Modèle | Taille d'entrée | mAP (mask) |
|--------|-----------------|------------|
| YOLOv5m-seg | 640×640 | 36.4% |
| YOLOv5n-seg | 640×640 | 22.3% |

### Estimation de pose
| Modèle | Taille d'entrée | AP (COCO) |
|--------|-----------------|-----------|
| YOLOv8m-pose | 640×640 | 62.7% |
| YOLOv8s-pose | 640×640 | 58.5% |
| CenterPose (ResNet-50) | 512×512 | 51.0% |
| MSPN | 256×192 | 71.0% |

### Détection de visages
| Modèle | Taille d'entrée | mAP |
|--------|-----------------|-----|
| SCRFD-10g | 640×640 | 82.1% |
| SCRFD-2.5g | 640×640 | 76.6% |
| RetinaFace (MobileNet) | 736×1280 | 72.7% |
| LightFace-Slim | 240×320 | 73.2% |

### Reconnaissance faciale
| Modèle | Taille d'entrée | LFW accuracy |
|--------|-----------------|-------------|
| ArcFace (MobileNet) | 112×112 | 99.4% |
| ArcFace (ResNet-50) | 112×112 | 99.5% |

### Re-identification de personnes
| Modèle | Taille d'entrée | Rank-1 |
|--------|-----------------|--------|
| RepVGG-A0 (ReID) | 256×128 | 89.6% |
| OSNet x1.0 | 256×128 | 94.4% |

### Estimation de profondeur
| Modèle | Taille d'entrée | δ<1.25 |
|--------|-----------------|--------|
| SCDepth v3 | 320×240 | 86.3% |
| Fast-Depth | 224×224 | 78.0% |

### Super-résolution
| Modèle | Facteur | Taille d'entrée |
|--------|---------|-----------------|
| ESPCN x2 | ×2 | variable |
| ESPCN x3 | ×3 | variable |
| ESPCN x4 | ×4 | variable |

### OCR / Détection et reconnaissance de texte
| Modèle | Taille d'entrée | Type | FPS (Hailo-8) |
|--------|-----------------|------|---------------|
| PaddleOCR v5 Mobile Detection | 544×960 | Détection de zones de texte (probability map) | ~20.5 |
| PaddleOCR v5 Mobile Recognition | 48×320 | Reconnaissance de caractères (CRNN) | ~91.5 |

### Détection de plaques d'immatriculation (LPR)
| Modèle | Taille d'entrée |
|--------|-----------------|
| LPRNet | 24×94 |
| Tiny-YOLOv4 (licence plate) | 416×416 |

### Autres
| Modèle | Taille d'entrée | Usage |
|--------|-----------------|-------|
| Hand Landmark (MediaPipe) | 224×224 | Détection de la main |
| Palm Detection (MediaPipe) | 192×192 | Localisation de la paume |
| Person Attributes | 224×224 | Attributs (genre, vêtements…) |
| Face Attributes | 120×120 | Âge, genre |
| Facial Landmarks | 120×120 | 5 points du visage |

> **Note** : les HEF pour Hailo-8 sont téléchargeables depuis `https://hailo-model-zoo.s3.eu-west-2.amazonaws.com/ModelZoo/Compiled/v2.14.0/hailo8/<model_name>.hef`. L'application officielle [hailo-apps](https://github.com/hailo-ai/hailo-apps) fournit un pipeline PaddleOCR complet (détection + tracking + cropping + reconnaissance) basé sur GStreamer.
