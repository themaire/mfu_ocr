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


def test_extract_tabular_parcels_section_a() -> None:
    """Format notarié ORE : tableau avec section A en début de ligne."""
    text = """
    ARTICLE UN
    Sur la commune de RIMOGNE (08150), LES TRIOTS DE L ETANG,
    DIVERSES PARCELLES EN NATURE DE PRE, BOIS.

    Figurant au cadastre sous les références suivantes :

    Section Numéro Lieudit ha a ca
    A 38 LES TRIOTS DE L ETANG 0 09 20
    A 39 LES TRIOTS DE L ETANG 0 19 30
    A 71 ETANG DE ROSAINRU 1 04 10
    A 352 LES TRIOTS DE L ETANG 0 15 85
    A 794 LES TRIOTS DE L ETANG 1 09 47
     Contenance Totale : 15ha 29a 71ca

    ARTICLE DEUX
    """

    meta = extract_document_metadata(text, "08113_ORE_20250527.pdf")

    parcels = [{"section": p["section"], "number": p["number"]} for p in meta["cadastral_parcels"]]
    expected = [38, 39, 71, 352, 794]
    for num in expected:
        assert {"section": "A", "number": str(num)} in parcels, f"Missing A {num}"
    assert len(parcels) == 5


def test_extract_comma_separated_compact_parcels() -> None:
    """Parcelles compactes séparées par virgules et 'et' (ex: ZA1, ZA3 et BS55)."""
    text = """
    La présente convention s'applique aux parcelles ZA1, ZA3 et BS55, dont une
    partie est concernée par cette convention.
    """

    meta = extract_document_metadata(text, "52132_CG_20250805.pdf")

    parcels = [{"section": p["section"], "number": p["number"]} for p in meta["cadastral_parcels"]]
    assert {"section": "ZA", "number": "1"} in parcels
    assert {"section": "ZA", "number": "3"} in parcels
    assert {"section": "BS", "number": "55"} in parcels
    assert len(parcels) == 3


def test_extract_prefixed_section_in_table() -> None:
    """Format tableau avec préfixe commune : '078 ZA 1' → section ZA, numéro 1."""
    text = """
    Article 2 - Références cadastrales

    Commune    Lieu-dit    Section    N° parcelle    Surface
    BROTTES    La Combe    078 ZA 1    15,6790 ha    5,9470 ha
    BROTTES    Côte        078 ZA 3    2,0820 ha     1,2170 ha
    CHAUMONT   Côte        AV 365      0,3500 ha     0,3500 ha
    BROTTES    Grillée     078 BS 55   3,5020 ha     3,5020 ha
    BROTTES    La Combe    078 ZA 265  0,8750 ha     0,8750 ha
    BROTTES    La Combe    078 ZA 266  0,4100 ha     0,4100 ha
    """

    meta = extract_document_metadata(text, "52132_CG_20250805.pdf")

    parcels = [{"section": p["section"], "number": p["number"]} for p in meta["cadastral_parcels"]]
    assert {"section": "ZA", "number": "1"} in parcels
    assert {"section": "ZA", "number": "3"} in parcels
    assert {"section": "AV", "number": "365"} in parcels
    assert {"section": "BS", "number": "55"} in parcels
    assert {"section": "ZA", "number": "265"} in parcels
    assert {"section": "ZA", "number": "266"} in parcels


def test_extract_parcelle_cadastree_notarial_prose() -> None:
    """Format notarié : 'la parcelle cadastrée A 38' dans le texte courant."""
    text = """
    EFFET RELATIF
    La parcelle cadastrée A 61
    - Acquisition aux termes d'un acte reçu par Maître X.
    La parcelle cadastrée A 38, la parcelle cadastrée A 39, la parcelle cadastrée A 71
    et la parcelle cadastrée A 352.
    """

    meta = extract_document_metadata(text, "08113_ORE_20250527.pdf")

    parcels = [{"section": p["section"], "number": p["number"]} for p in meta["cadastral_parcels"]]
    for num in [61, 38, 39, 71, 352]:
        assert {"section": "A", "number": str(num)} in parcels, f"Missing A {num}"
