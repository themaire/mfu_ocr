# Feuille de route initiale

## Cible fonctionnelle
Mettre en place une chaîne capable de :
1. détecter si le PDF contient déjà du texte numérique ;
2. sinon rasteriser les pages et lancer une brique OCR ;
3. restituer un texte lisible avec séparation des paragraphes ;
4. préparer une future étape d'analyse métier sur les actes fonciers.

## Stratégie recommandée
- **Étape 1 — baseline CPU** : extraction du texte natif avec pypdf.
- **Étape 2 — OCR fallback** : OCR sur pages scannées.
- **Étape 3 — accélération Hailo** : étude d'un modèle de détection/lecture compatible ou exportable vers Hailo.
- **Étape 4 — post-traitement** : nettoyage, regroupement des paragraphes, validation métier.

## Points à valider sur la machine
- présence effective du runtime Hailo ;
- disponibilité de Tesseract ou d'un autre moteur OCR de référence ;
- disponibilité de Poppler pour convertir les PDF scannés en images.
