# Projet OCR avec Hailo sur Raspberry Pi 5

## Objectif
Mettre en place une chaîne d'extraction de texte pour des actes fonciers, capable de traiter :
- des PDF contenant du texte numérique ;
- des PDF scannés ;
- des documents mixtes avec annexes scannées.

La cible matérielle est un Raspberry Pi 5 équipé d'un Hailo AI HAT+ 26 TOPS.

## Ce qui a été préparé
Une base de projet Python a été initialisée pour démarrer la réflexion technique et les premiers essais :
- structure de code dans `src/ocr_hailo/` ;
- script de diagnostic de l'environnement ;
- commande CLI de base ;
- feuille de route dans `docs/roadmap.md` ;
- export annexe au format JSON des données métier détectées.

## Démarrage rapide
### 1) Créer l'environnement virtuel et installer les dépendances
```bash
make install
```

### 2) Vérifier l'état de la machine
```bash
make check-env
```

### 3) Voir les commandes disponibles
```bash
make help
```

### 4) Lancer un premier traitement sur un PDF
```bash
PYTHONPATH=src .venv/bin/python -m ocr_hailo.cli process-pdf mon_document.pdf -o output/mon_document.txt
```

### 5) Exemple réel de test métier
Convention de nommage d'un document :
- `52003` : code site ;
- `BE` : type d'acte, ici bail emphytéotique ;
- `19900817` : date de l'acte au format AAAAMMJJ.

Pour un fichier nommé `52003_BE_19900817.pdf` :
```bash
PYTHONPATH=src .venv/bin/python -m ocr_hailo.cli process-pdf input/52003_BE_19900817.pdf -o output/52003_BE.txt
```

Cette commande génère :
- `output/52003_BE.txt` : texte OCRisé ;
- `output/52003_BE.json` : synthèse structurée.

Commandes utiles de vérification :
```bash
cat output/52003_BE.json
grep -ni "cadastr\|section" output/52003_BE.txt
```

## Dépendances système utiles
Pour aller plus loin sur les PDF scannés, il faudra disposer au minimum de :
- `tesseract-ocr`
- `tesseract-ocr-fra`
- `poppler-utils`

Exemple d'installation sur Debian / Raspberry Pi OS :
```bash
sudo apt update
sudo apt install -y tesseract-ocr tesseract-ocr-fra poppler-utils
```

## Structure actuelle
```text
.
├── README.md
├── Makefile
├── pyproject.toml
├── docs/
│   └── roadmap.md
├── scripts/
│   └── check_env.py
└── src/
    └── ocr_hailo/
        ├── __init__.py
        ├── cli.py
        ├── diagnostics.py
        └── extraction.py
```

## Priorités métier à retenir
Le but immédiat est d'obtenir un OCR le plus propre possible sur des actes parfois anciens et difficiles.

En complément, quelques repères métier simples sont extraits pour faciliter la relecture :
1. le code site à partir du nom de fichier ;
2. le nom de la commune mentionnée dans l'acte ;
3. les références des parcelles cadastrales, point le plus important ;
4. le type de document lorsqu'une mention explicite apparaît dans le texte, par exemple BAIL EMPHYTEOTIQUE.

Exemple observé sur un test réel : `Cadastrée Section V N° 12` dans le paragraphe `DESIGNATION DES BIENS LOUES`.

Le JSON annexe vise justement à structurer ces informations sous forme exploitable :
- code site ;
- type d'acte ;
- libellé du type trouvé dans le texte ;
- date de l'acte ;
- commune ;
- parcelles cadastrales avec section et numéro.

Une étape de toilettage par modèle local via LM Studio ou Ollama pourra venir plus tard, mais elle reste volontairement séparée du cœur OCR.

## Prochaines étapes proposées
1. fiabiliser encore l'extraction des références cadastrales ;
2. tester sur plusieurs actes anciens de qualité variable ;
3. ajouter des règles de post-traitement métier ;
4. étudier ensuite la compatibilité d'un modèle OCR avec Hailo.
