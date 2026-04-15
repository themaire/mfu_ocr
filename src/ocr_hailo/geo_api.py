"""Client Python pour les API géographiques IGN / geo.api.gouv.fr.

Inspiré du module JavaScript geo_api.js — permet de valider les communes
et les parcelles cadastrales extraites par l'OCR.
"""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.request
import urllib.parse
from typing import Any

# Départements CENCA par défaut
DEFAULT_DEPARTEMENTS = ["08", "10", "51", "52"]

_TIMEOUT = 10  # secondes


def _get_json(url: str, timeout: int = _TIMEOUT) -> Any:
    """GET une URL et retourne le JSON parsé."""
    req = urllib.request.Request(url, headers={"User-Agent": "Python-OCR-GeoAPI/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Communes
# ---------------------------------------------------------------------------

def get_communes_by_departements(
    departements: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Récupère la liste des communes pour les départements donnés.

    Retourne une liste de dicts ``{"nom": ..., "code": ...}``
    triée par nom.
    """
    deps = departements or DEFAULT_DEPARTEMENTS
    all_communes: list[dict[str, Any]] = []
    for dep in deps:
        url = (
            f"https://geo.api.gouv.fr/communes"
            f"?codeDepartement={dep}&fields=nom,code&format=json"
        )
        communes = _get_json(url)
        if isinstance(communes, list):
            all_communes.extend(communes)
    all_communes.sort(key=lambda c: c.get("nom", ""))
    return all_communes


def get_commune_details(code_insee: str) -> dict[str, Any]:
    """Récupère les détails d'une commune par son code INSEE."""
    fields = "nom,code,codesPostaux,population,surface,codeDepartement,centre,contour"
    url = f"https://geo.api.gouv.fr/communes/{code_insee}?fields={fields}&format=json"
    return _get_json(url)


def _normalize_commune_name(name: str) -> str:
    """Normalise un nom de commune pour la comparaison fuzzy."""
    # Supprime les accents
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Majuscules, réduit les séparateurs
    upper = ascii_name.upper().strip()
    upper = re.sub(r"['\-]+", " ", upper)
    upper = re.sub(r"\s+", " ", upper)
    return upper


def match_commune(
    candidate: str,
    departements: list[str] | None = None,
    *,
    _cache: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any] | None:
    """Cherche la meilleure correspondance d'un nom de commune OCR
    parmi les communes officielles des départements donnés.

    Retourne ``{"nom": ..., "code": ...}`` ou ``None``.
    """
    if _cache is None:
        _cache = {}

    cache_key = ",".join(departements or DEFAULT_DEPARTEMENTS)
    if cache_key not in _cache:
        _cache[cache_key] = get_communes_by_departements(departements)
    communes = _cache[cache_key]

    norm_candidate = _normalize_commune_name(candidate)
    if not norm_candidate:
        return None

    # 1) Correspondance exacte normalisée
    for c in communes:
        if _normalize_commune_name(c["nom"]) == norm_candidate:
            return c

    # 2) Inclusion : le nom officiel est contenu dans le candidat ou inversement
    best: dict[str, Any] | None = None
    best_len = 0
    for c in communes:
        norm_official = _normalize_commune_name(c["nom"])
        if norm_official in norm_candidate or norm_candidate in norm_official:
            if len(norm_official) > best_len:
                best = c
                best_len = len(norm_official)

    return best


# ---------------------------------------------------------------------------
# Parcelles cadastrales
# ---------------------------------------------------------------------------

def verify_parcelle(
    code_insee: str,
    section: str,
    numero: str,
) -> dict[str, Any] | None:
    """Vérifie l'existence d'une parcelle via l'API Carto IGN.

    Retourne les propriétés de la parcelle ou ``None`` si introuvable.
    """
    # Normaliser section sur 2 caractères (ex: "B" → "0B")
    section_norm = section.upper().strip()
    if len(section_norm) == 1:
        section_norm = "0" + section_norm

    # Numéro sur 4 chiffres
    numero_norm = numero.strip().zfill(4)

    params = urllib.parse.urlencode({
        "code_insee": code_insee,
        "section": section_norm,
        "numero": numero_norm,
    })
    url = f"https://apicarto.ign.fr/api/cadastre/parcelle?{params}"

    try:
        data = _get_json(url, timeout=15)
    except Exception:
        return None

    if (
        isinstance(data, dict)
        and data.get("type") == "FeatureCollection"
        and data.get("features")
    ):
        feature = data["features"][0]
        props = feature.get("properties", {})
        contenance_m2 = props.get("contenance")
        contenance_ha = round(contenance_m2 / 10000, 4) if contenance_m2 is not None else None
        result = {
            "idu": props.get("idu") or props.get("code_parcelle"),
            "nom_com": props.get("nom_com") or props.get("nom_commune"),
            "contenance_ha": contenance_ha,
            "section": props.get("section"),
            "numero": props.get("numero"),
        }
        # Calcul de la bbox si géométrie présente
        geom = feature.get("geometry", {})
        coords = None
        if geom.get("type") == "Polygon":
            coords = geom["coordinates"][0]
        elif geom.get("type") == "MultiPolygon":
            coords = geom["coordinates"][0][0]
        if coords:
            xs = [c[0] for c in coords]
            ys = [c[1] for c in coords]
            result["bbox"] = f"{min(xs)},{min(ys)},{max(xs)},{max(ys)}"
        return result

    return None


def verify_parcelles_batch(
    code_insee: str,
    parcelles: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Vérifie un lot de parcelles et retourne celles confirmées par l'IGN.

    Chaque élément de ``parcelles`` doit avoir les clés ``section`` et ``number``.
    """
    verified: list[dict[str, Any]] = []
    for p in parcelles:
        result = verify_parcelle(code_insee, p["section"], p["number"])
        if result:
            verified.append({**p, "ign_verified": True, **result})
        else:
            verified.append({**p, "ign_verified": False})
    return verified
