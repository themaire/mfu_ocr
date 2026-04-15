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
    cleaned = _normalize_label(candidate)
    cleaned = re.split(r"\(|,|;|\.", cleaned)[0].strip(" -")

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

    return (bonus + penalty, candidate.count("-"), -len(candidate))


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

    def add_parcel(section_value: str, number_value: str) -> None:
        section = _normalize_label(section_value)
        number = _normalize_label(number_value)
        invalid_sections = {
            "A", "HA", "CA", "DE", "DU", "LE", "LA", "LES", "ET", "N", "NO",
            "PAGE", "SUR", "OHA", "HAO", "TOTAL", "ART", "ANNEXE",
            "OS", "SE", "SI", "BAS", "JZS", "PETE", "DES", "PAR", "EST",
        }
        if section in invalid_sections:
            return
        if not re.fullmatch(r"[0-9]+", number):
            return
        key = (section, number)
        if key in seen:
            return
        seen.add(key)
        parcels.append({"section": section, "number": number})

    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            add_parcel(match.group(1), match.group(2))

    normalized_lines = [_normalize_for_matching(line) for line in text.splitlines()]
    table_context = False
    blank_streak = 0

    for line in normalized_lines:
        if "REFERENCE CADASTRALE" in line or "REFERENCES CADASTRALES" in line:
            table_context = True
            blank_streak = 0
            continue

        if "SECTION" in line and "PARCELLE" in line:
            table_context = True
            blank_streak = 0
            continue

        if "TOTAL DE LA SURFACE" in line:
            table_context = False
            blank_streak = 0
            continue

        if table_context and not line.strip():
            blank_streak += 1
            if blank_streak >= 2:
                table_context = False
            continue

        blank_streak = 0

        if table_context and (line.startswith("ARTICLE ") or line.startswith("ANNEXE ")):
            table_context = False
            continue

        if table_context:
            for match in re.finditer(r"\b([A-Z]{1,4})\s+([0-9]{1,4}[A-Z]?)\b", line):
                add_parcel(match.group(1), match.group(2))

            compact_tokens = re.findall(r"\b[A-Z]{1,4}\b|\b[0-9]{1,4}[A-Z]?\b", line)
            for left, right in zip(compact_tokens, compact_tokens[1:]):
                if re.fullmatch(r"[A-Z]{1,4}", left) and re.fullmatch(r"[0-9]{1,4}[A-Z]?", right):
                    add_parcel(left, right)

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

    return parcels


def extract_document_metadata(text: str, source_filename: str) -> dict[str, object]:
    metadata: dict[str, object] = parse_filename_metadata(source_filename)
    detected_type = detect_document_type(text)

    if not metadata.get("document_type") and detected_type.get("document_type"):
        metadata["document_type"] = detected_type["document_type"]

    metadata["document_type_label"] = detected_type.get("document_type_label")
    metadata["commune"] = extract_commune(text)
    metadata["cadastral_parcels"] = extract_cadastral_parcels(text)
    return metadata


def write_metadata_json(metadata: dict[str, object], output_path: str | Path) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return target
