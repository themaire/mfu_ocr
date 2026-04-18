from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import unicodedata


def parse_filename_metadata(filename: str) -> dict[str, str | None]:
    """Extrait les métadonnées métier à partir du nom de fichier."""
    stem = Path(filename).stem
    match = re.search(r"(?P<site>\d{5})_(?P<doc>[A-Za-z]{2,})_(?P<date>\d{8})", stem)

    metadata: dict[str, str | None] = {
        "site_code": None,
        "document_type": None,
        "document_date": None,
        "source_filename": Path(filename).name,
    }

    if not match:
        return metadata

    raw_date = match.group("date")
    metadata.update(
        {
            "site_code": match.group("site"),
            "document_type": match.group("doc").upper(),
            "document_date": f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}",
        }
    )
    return metadata


def _normalize_label(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value.replace("\n", " ")).strip(" .,:;()[]{}\t\r\n")
    cleaned = re.sub(r"\s*-\s*", "-", cleaned)
    return cleaned.upper()


def _normalize_for_matching(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return _normalize_label(without_accents)


def detect_document_type(text: str) -> dict[str, str | None]:
    normalized = _normalize_for_matching(text)

    patterns = [
        (r"\bBAIL\s+EMPHYTEOTIQUE\b", "BE", "BAIL EMPHYTEOTIQUE"),
    ]

    for pattern, code, label in patterns:
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            return {"document_type": code, "document_type_label": label}

    return {"document_type": None, "document_type_label": None}


def _clean_commune_candidate(candidate: str) -> str:
    # Tronquer au premier séparateur " - " AVANT normalisation
    candidate = re.split(r"\s+-\s+|\s+—\s+", candidate)[0].strip()
    cleaned = _normalize_label(candidate)
    cleaned = re.split(r"\(|,|;|\.", cleaned)[0].strip(" -")
    # Supprimer les caractères parasites en début (artefacts OCR)
    cleaned = re.sub(r"^[^A-ZÀ-Ÿa-zà-ÿ]+", "", cleaned)
    # Supprimer les mots courts isolés en préfixe (1-2 lettres + bruit)
    cleaned = re.sub(r"^[A-ZÀ-Ÿa-zà-ÿ]{1,2}\s+[^A-ZÀ-Ÿa-zà-ÿ\s].*?\s+", "", cleaned)

    stop_markers = [
        " AU CONSERVATOIRE",
        " DU PATRIMOINE",
        " ASSOCIATION",
        " NOTAIRE",
        " BAIL EMPHYTEOTIQUE",
    ]
    for marker in stop_markers:
        if marker in cleaned:
            cleaned = cleaned.split(marker, maxsplit=1)[0].strip(" -")

    return cleaned


def _score_commune_candidate(candidate: str) -> tuple[int, int, int]:
    penalty = 0
    if len(candidate.split()) > 5:
        penalty -= 5
    if any(word in candidate for word in ["CONSERVATOIRE", "PATRIMOINE", "NOTAIRE", "ASSOCIATION"]):
        penalty -= 10

    bonus = 0
    if "-" in candidate:
        bonus += 5
    if 4 <= len(candidate) <= 40:
        bonus += 3

    return (bonus + penalty, 1 if "-" in candidate else 0, -len(candidate))


def extract_commune(text: str) -> str | None:
    candidates: list[str] = []
    lines = [line.strip() for line in text.splitlines()]

    for index, line in enumerate(lines):
        if "COMMUNE" not in line.upper():
            continue

        same_line = re.search(r"COMMUNE\s+D(?:E|')?\s+(.*)$", line, flags=re.IGNORECASE)
        if same_line and same_line.group(1).strip():
            candidate = _clean_commune_candidate(same_line.group(1))
            if len(candidate) >= 4:
                candidates.append(candidate)

        if re.search(r"COMMUNE\s+D(?:E|')?\s*$", line, flags=re.IGNORECASE):
            for next_line in lines[index + 1 : index + 3]:
                if next_line.strip():
                    candidate = _clean_commune_candidate(next_line)
                    if len(candidate) >= 4:
                        candidates.append(candidate)
                    break

    if not candidates:
        return None

    counts = Counter(candidates)
    unique_candidates = list(counts)
    unique_candidates.sort(
        key=lambda item: (*_score_commune_candidate(item), counts[item]),
        reverse=True,
    )
    return unique_candidates[0]


def extract_cadastral_parcels(text: str) -> list[dict[str, str]]:
    patterns = [
        r"CADASTR[ÉE]E?\s+SECTION\s+([A-ZIVX]+)\s*[NNO°º\.\-\s]*([0-9]{1,4}[A-Z]?)",
        r"SECTION\s+([A-ZIVX]+)\s*[NNO°º\.\-\s]*([0-9]{1,4}[A-Z]?)",
    ]

    parcels: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    # Sections invalides pour le parsing général (mots courants, unités de surface)
    _INVALID_SECTIONS = {
        "A", "HA", "CA", "DE", "DU", "LE", "LA", "LES", "ET", "N", "NO",
        "PAGE", "SUR", "OHA", "HAO", "TOTAL", "ART", "ANNEXE",
        "OS", "SE", "SI", "BAS", "JZS", "PETE", "DES", "PAR", "EST",
    }
    # En contexte fiable (après "parcelle cadastrée", début de ligne tabulaire),
    # seules les unités de surface restent interdites.
    _INVALID_SECTIONS_TRUSTED = {"HA", "CA"}

    def add_parcel(section_value: str, number_value: str, *, trusted: bool = False) -> None:
        section = _normalize_label(section_value)
        number = _normalize_label(number_value)
        invalid = _INVALID_SECTIONS_TRUSTED if trusted else _INVALID_SECTIONS
        if section in invalid:
            return
        if not re.fullmatch(r"[0-9]+", number) or number == "0":
            return
        key = (section, number)
        if key in seen:
            return
        seen.add(key)
        parcels.append({"section": section, "number": number})

    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            add_parcel(match.group(1), match.group(2))

    # "parcelle cadastrée A 38" — format notarié sans le mot "SECTION"
    for match in re.finditer(
        r"PARCELL\w*\s+CADASTR\w*\s+([A-Z]{1,3})\s+(\d{1,4})\b",
        text, flags=re.IGNORECASE,
    ):
        add_parcel(match.group(1), match.group(2), trusted=True)

    normalized_lines = [_normalize_for_matching(line) for line in text.splitlines()]

    # Pré-scanner les blocs [TABLEAU DETECTE]...[FIN TABLEAU] pour identifier
    # ceux qui contiennent du vrai texte (pas juste du bruit OCR de cartes/photos).
    # Critère : la moyenne de caractères alphanumériques par ligne non-vide
    # doit être >= 8 (les vrais tableaux ont des lignes riches, le bruit a
    # des lignes de 1-3 caractères parsemées de symboles).
    _valid_table_starts: set[int] = set()
    for idx, nline in enumerate(normalized_lines):
        if nline == "TABLEAU DETECTE":
            alnum_counts: list[int] = []
            for j in range(idx + 1, min(idx + 200, len(normalized_lines))):
                if normalized_lines[j] == "FIN TABLEAU":
                    break
                stripped = normalized_lines[j].strip()
                if not stripped:
                    continue
                alnum_counts.append(sum(c.isalnum() for c in stripped))
            if alnum_counts:
                avg_alnum = sum(alnum_counts) / len(alnum_counts)
                if avg_alnum >= 8:
                    _valid_table_starts.add(idx)

    table_context = False
    in_table_block = False
    blank_streak = 0

    for line_idx, line in enumerate(normalized_lines):
        # Bloc [TABLEAU DETECTE]...[FIN TABLEAU] = table_context si contenu valide
        if line == "TABLEAU DETECTE":
            in_table_block = True
            table_context = line_idx in _valid_table_starts
            blank_streak = 0
            continue

        if line == "FIN TABLEAU":
            in_table_block = False
            table_context = False
            blank_streak = 0
            continue

        # Triggers textuels hors bloc tableau
        if not in_table_block:
            if "REFERENCE CADASTRALE" in line or "REFERENCES CADASTRALES" in line:
                table_context = True
                blank_streak = 0
                continue

            if "SECTION" in line and ("PARCELLE" in line or "NUMERO" in line):
                table_context = True
                blank_streak = 0
                continue

            if "N°" in line and "PARCELLE" in line:
                table_context = True
                blank_streak = 0
                continue

            if "FIGURANT AU CADASTRE" in line:
                table_context = True
                blank_streak = 0
                continue

        if "TOTAL DE LA SURFACE" in line or "CONTENANCE TOTALE" in line or "SURFACE TOTALE" in line:
            table_context = False
            blank_streak = 0
            continue

        if table_context and not in_table_block and not line.strip():
            blank_streak += 1
            if blank_streak >= 2:
                table_context = False
            continue

        blank_streak = 0

        if table_context and not in_table_block and line.startswith("ARTICLE "):
            table_context = False
            continue

        if table_context:
            # Lignes tabulaires : section + numéro en début de ligne (ex: "A 38 LES TRIOTS...")
            # Contexte fiable → autorise les sections courtes comme "A"
            start_match = re.match(r"\s*([A-Z]{1,3})\s+(\d{1,4})\s+[A-Z]", line)
            if start_match:
                add_parcel(start_match.group(1), start_match.group(2), trusted=True)

            # Section avec préfixe commune (ex: "078 ZA 265" → section ZA, numéro 265)
            # Utilise re.search pour trouver le pattern au milieu d'une ligne (layout ligne par ligne)
            # Lookahead négatif pour exclure les surfaces décimales (ex: "ZA 15,6790")
            # Sections de 2+ lettres : les sections 1 lettre ne sont jamais préfixées du code commune
            prefix_match = re.search(
                r"\b\d{2,3}\s+([A-Z]{2,3})\s+(\d{1,4})(?![,\.]\d)", line
            )
            if prefix_match:
                add_parcel(prefix_match.group(1), prefix_match.group(2), trusted=True)

            # Pattern général section + numéro (ex: "ZW 22", "AV 365")
            # Lookahead négatif pour exclure les surfaces décimales
            # Dans un bloc tableau, exiger section de 2+ lettres pour éviter le bruit OCR
            min_sect = 2 if in_table_block else 1
            for match in re.finditer(
                rf"\b([A-Z]{{{min_sect},3}})\s+([0-9]{{1,4}}[A-Z]?)\b(?![,\.]\d)", line
            ):
                add_parcel(match.group(1), match.group(2))

    # --- Listes compactes de références (ex: "B132 / B133 / ZS10 et ZS19") ---
    flat_text = re.sub(r"\s+", " ", text)
    list_pattern = (
        r"[A-Z]{1,3}\d{1,4}"
        r"(?:\s*/\s*[A-Z]{1,3}\d{1,4})+"
        r"(?:\s+et\s+[A-Z]{1,3}\d{1,4})?"
    )
    for list_match in re.finditer(list_pattern, flat_text, re.IGNORECASE):
        for ref in re.finditer(r"([A-Z]{1,3})(\d{1,4})", list_match.group(0), re.IGNORECASE):
            add_parcel(ref.group(1).upper(), ref.group(2))

    # --- Listes compactes séparées par virgules (ex: "parcelles ZA1, ZA3 et BS55") ---
    comma_list_pattern = (
        r"[A-Z]{1,3}\d{1,4}"
        r"(?:\s*,\s*[A-Z]{1,3}\d{1,4})+"
        r"(?:\s+et\s+[A-Z]{1,3}\d{1,4})?"
    )
    for list_match in re.finditer(comma_list_pattern, flat_text, re.IGNORECASE):
        for ref in re.finditer(r"([A-Z]{1,3})(\d{1,4})", list_match.group(0), re.IGNORECASE):
            add_parcel(ref.group(1).upper(), ref.group(2), trusted=True)

    return parcels


def extract_document_metadata(text: str, source_filename: str) -> dict[str, object]:
    metadata: dict[str, object] = parse_filename_metadata(source_filename)
    detected_type = detect_document_type(text)

    if not metadata.get("document_type") and detected_type.get("document_type"):
        metadata["document_type"] = detected_type["document_type"]

    metadata["document_type_label"] = detected_type.get("document_type_label")

    # Extraction brute depuis l'OCR
    raw_commune = extract_commune(text)
    raw_parcels = extract_cadastral_parcels(text)

    # Validation via l'API IGN
    commune_info = _validate_commune(raw_commune)
    if commune_info:
        metadata["commune"] = commune_info["nom"]
        metadata["code_insee"] = commune_info["code"]
    else:
        metadata["commune"] = raw_commune
        metadata["code_insee"] = None

    # Validation des parcelles si on a un code INSEE
    code_insee = metadata.get("code_insee")
    if code_insee and raw_parcels:
        validated = _validate_parcels(code_insee, raw_parcels)
        # Ne garder que les parcelles confirmées par l'IGN
        confirmed = [p for p in validated if p.get("ign_verified") is True]
        metadata["cadastral_parcels"] = confirmed if confirmed else validated
    else:
        metadata["cadastral_parcels"] = [
            {**p, "ign_verified": None} for p in raw_parcels
        ]

    return metadata


def _validate_commune(raw_commune: str | None) -> dict[str, str] | None:
    """Valide un nom de commune OCR via l'API geo.api.gouv.fr."""
    if not raw_commune:
        return None
    try:
        from ocr_hailo.geo_api import match_commune
        result = match_commune(raw_commune)
        return result
    except Exception as exc:
        import sys
        print(f"[WARN] Validation commune échouée: {exc}", file=sys.stderr)
        return None


def _validate_parcels(
    code_insee: str, parcels: list[dict[str, str]],
) -> list[dict[str, object]]:
    """Vérifie les parcelles via l'API Carto IGN."""
    try:
        from ocr_hailo.geo_api import verify_parcelles_batch
        return verify_parcelles_batch(code_insee, parcels)
    except Exception as exc:
        import sys
        print(f"[WARN] Validation parcelles échouée: {exc}", file=sys.stderr)
        return [{**p, "ign_verified": None} for p in parcels]


def write_metadata_json(metadata: dict[str, object], output_path: str | Path) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return target
