from ocr_hailo.metadata import extract_document_metadata, parse_filename_metadata


def test_parse_filename_metadata() -> None:
    meta = parse_filename_metadata("52003_BE_19900817.pdf")

    assert meta["site_code"] == "52003"
    assert meta["document_type"] == "BE"
    assert meta["document_date"] == "1990-08-17"


def test_extract_document_metadata_from_text() -> None:
    text = """
    DESIGNATION DES BIENS LOUES
    UNE PARCELLE de TERRAIN sise à LATRECEY-ORMOY-SUR-AUBE
    Cadastrée Section V N° 12.

    LA COMMUNE DE LATRECEY-ORMOY-SUR-AUBE
    """

    meta = extract_document_metadata(text, "52003_BE_19900817.pdf")

    assert meta["site_code"] == "52003"
    assert meta["document_type"] == "BE"
    assert meta["document_date"] == "1990-08-17"
    assert meta["commune"].upper() == "LATRECEY-ORMOY-SUR-AUBE"
    parcels = [{"section": p["section"], "number": p["number"]} for p in meta["cadastral_parcels"]]
    assert parcels == [{"section": "V", "number": "12"}]


def test_extract_commune_ignores_trailing_noise() -> None:
    text = """
    BAIL EMPHYTEOTIQUE
    par la commune de
    LATRECEY au CONSERVATOIRE
    du PATRIMOINE NATUREL DE
    CHAMPAGNE ARDENNE.

    LA COMMUNE DE LATRECEY-ORMOY-SUR-AUBE (Haute-Marne)
    """

    meta = extract_document_metadata(text, "52003_BE_19900817.pdf")

    assert meta["commune"].upper() == "LATRECEY-ORMOY-SUR-AUBE"


def test_extract_document_type_from_text() -> None:
    text = """
    BAIL EMPHYTEOTIQUE
    Par les présentes, le bailleur donne à bail emphytéotique.
    """

    meta = extract_document_metadata(text, "52003_SCAN_SANS_TYPE_19900817.pdf")

    assert meta["document_type"] == "BE"
    assert meta["document_type_label"] == "BAIL EMPHYTEOTIQUE"


def test_extract_cadastral_parcel_from_table_ocr() -> None:
    text = """
    Article 2 - Références cadastrales :
    La présente convention s'applique à la parcelle référencée comme suit :

    Commune Lieu-dit Section N° parcelle Surface de la parcelle
    Latrecey-Ormoy-sur-Aube Vaudry ZW 22 4 ha 09 a 20 ca
    """

    meta = extract_document_metadata(text, "52003_CG_20151228.pdf")

    parcels = [{"section": p["section"], "number": p["number"]} for p in meta["cadastral_parcels"]]
    assert {"section": "ZW", "number": "22"} in parcels


def test_extract_multiple_cadastral_parcels_from_multipage_table() -> None:
    text = """
    Article 2 - Références cadastrales :
    La présente convention s'applique aux parcelles référencées comme suit :

    le Moulin              B           793       1 ha 54 a 74 ca
    Bas delaveson          ZS          10        17 ha 34 a 18 ca
    les Bas                PAS         18        4 ha 50 a 51 ca
    TOTAL DE LA SURFACE SOUS CONVENTION
    """

    meta = extract_document_metadata(text, "10088_CG_20260123.pdf")

    parcels = [{"section": p["section"], "number": p["number"]} for p in meta["cadastral_parcels"]]
    assert {"section": "B", "number": "793"} in parcels
    assert {"section": "ZS", "number": "10"} in parcels
    assert {"section": "PAS", "number": "18"} in parcels


def test_extract_compact_parcel_references() -> None:
    text = """
    Délibération n°2025-09-43
    Objet de la délibération : Convention de préservation

    la liste des parcelles communales qui pourraient être
    intégrées à ce projet (B132 / B133 / B335 / B336 / B338 / B339 / B340 / B344 / B345 / B346 / B348/
    B350 / B351 / B352 /B354 / B357 / B359 / B360 / B362 / B389 / B398 / B399 / B472 / B792 / B793
    /ZS10 / ZS18 et ZS19)
    """

    meta = extract_document_metadata(text, "10088_CG_20260123.pdf")

    parcels = [{"section": p["section"], "number": p["number"]} for p in meta["cadastral_parcels"]]
    expected_b = [132, 133, 335, 336, 338, 339, 340, 344, 345, 346, 348,
                  350, 351, 352, 354, 357, 359, 360, 362, 389, 398, 399, 472, 792, 793]
    for num in expected_b:
        assert {"section": "B", "number": str(num)} in parcels, f"Missing B {num}"
    assert {"section": "ZS", "number": "10"} in parcels
    assert {"section": "ZS", "number": "18"} in parcels
    assert {"section": "ZS", "number": "19"} in parcels
    assert len(parcels) == 28
