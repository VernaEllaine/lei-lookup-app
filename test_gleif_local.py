"""Tests for the local GLEIF copy (backend.gleif_local) and its use in lookups."""

import csv
import io
import zipfile

import pytest

import backend.gleif_local as gl
import lei_lookup

LEI_HEADER = [
    "LEI", "Entity.LegalName",
    "Entity.OtherEntityNames.OtherEntityName.1",
    "Entity.LegalAddress.Country", "Entity.LegalJurisdiction",
    "Entity.EntityStatus", "Registration.RegistrationStatus",
    "Entity.LegalName.xmllang",
    "Entity.OtherEntityNames.OtherEntityName.1.type",
    "Entity.OtherEntityNames.OtherEntityName.1.xmllang",
    "Entity.EntityCategory", "Entity.EntitySubCategory",
]

LEI_ROWS = [
    ["815600AD83B2B6317788", "ENEL - S.P.A.", "", "IT", "IT", "ACTIVE", "ISSUED", "it", "", "",
     "GENERAL", ""],
    ["YEH5ZCD6E441RHVHD759", "Bayerische Motoren Werke Aktiengesellschaft", "BMW AG",
     "DE", "DE", "ACTIVE", "ISSUED", "de", "TRADING_OR_OPERATING_NAME", "de", "GENERAL", ""],
    ["5299009N55YRQC69CN08", "BMW Finance N.V.", "", "NL", "NL", "ACTIVE", "ISSUED", "en", "", "",
     "GENERAL", ""],
    ["LAPSED00000000000001", "Société Générale Lapsed SA", "", "FR", "FR", "ACTIVE", "LAPSED",
     "fr", "", "", "GENERAL", ""],
    ["O2RNE8IBXP4R0TD8PU41", "SOCIÉTÉ GÉNÉRALE", "Societe Generale SA", "FR", "FR", "ACTIVE",
     "ISSUED", "fr", "ALTERNATIVE_LANGUAGE_LEGAL_NAME", "en-GB", "GENERAL", ""],
]

ISIN_ROWS = [["YEH5ZCD6E441RHVHD759", "DE0005190003"]]
BIC_ROWS = [["O2RNE8IBXP4R0TD8PU41", "SOGEFRPPXXX"]]


def _zip_csv(path, header, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(path.stem + ".csv", buf.getvalue())
    return str(path)


@pytest.fixture
def local_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(gl, "DATA_DIR", str(tmp_path / "gleif"))
    monkeypatch.setattr(gl, "_local", __import__("threading").local())
    lei_zip = _zip_csv(tmp_path / "lei2.csv.zip", LEI_HEADER, LEI_ROWS)
    isin_zip = _zip_csv(tmp_path / "isin.zip", ["LEI", "ISIN"], ISIN_ROWS)
    bic_zip = _zip_csv(tmp_path / "bic.zip", ["LEI", "BIC"], BIC_ROWS)
    gl.build(lei_zip, isin_zip, publish_date="2026-10-09", log=lambda _m: None, bic_zip=bic_zip)
    yield
    conn = getattr(gl._local, "conn", None)
    if conn is not None:
        conn.close()


def test_not_available_without_load(tmp_path, monkeypatch):
    monkeypatch.setattr(gl, "DATA_DIR", str(tmp_path))
    assert not gl.is_available()
    assert gl.search_names("Enel") == []
    assert gl.get_record("815600AD83B2B6317788") is None


def test_status_reports_counts(local_copy):
    s = gl.status()
    assert s["available"] is True
    assert s["lei_count"] == "5"
    assert s["isin_count"] == "1"
    assert s["publish_date"] == "2026-10-09"


def test_search_matches_other_names_and_ranks_closest_first(local_copy):
    hits = gl.search_names("BMW")
    assert [h["lei"] for h in hits] == ["YEH5ZCD6E441RHVHD759", "5299009N55YRQC69CN08"]


def test_search_excludes_lapsed_registrations(local_copy):
    assert gl.search_names("Generale Lapsed") == []


def test_search_ignores_case_and_diacritics(local_copy):
    hits = gl.search_names("societe generale")
    assert [h["lei"] for h in hits] == ["O2RNE8IBXP4R0TD8PU41"]


def test_get_record_returns_any_status(local_copy):
    rec = gl.get_record("lapsed00000000000001")
    assert rec["registration_status"] == "LAPSED"


def test_isin_mapping(local_copy):
    assert gl.leis_for_isin("de0005190003") == ["YEH5ZCD6E441RHVHD759"]


def test_lookup_lei_uses_local_copy(local_copy, monkeypatch):
    def no_api(*_a, **_k):
        raise AssertionError("API should not be called")

    monkeypatch.setattr(lei_lookup, "_api_get", no_api)
    result = lei_lookup.lookup_lei("Enel SPA")
    assert lei_lookup._classify(result) == "AUTO-MATCHED"
    assert lei_lookup._best_match(result)["lei"] == "815600AD83B2B6317788"


def test_validate_uses_local_copy(local_copy, monkeypatch):
    monkeypatch.setattr(lei_lookup, "_api_get", lambda *_a, **_k: pytest.fail("API called"))
    assert lei_lookup.validate_single_lei("LAPSED00000000000001")["flag"] == "LAPSED"
    assert lei_lookup.validate_single_lei_local("NOTINLOCALCOPY000000") is None


def _fake_latest(monkeypatch, lei_name, isin_name, bic_name="bic.zip"):
    """Make the GLEIF 'latest' endpoints advertise the given file names."""
    import requests

    class Resp:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    def fake_get(url, **_kw):
        if url == gl.GOLDEN_COPY_LATEST:
            return Resp({"data": {"publish_date": "2026-10-10 00:00:00", "lei2": {"full_file": {
                "csv": {"url": f"https://example.test/{lei_name}"}}}}})
        if url == gl.ISIN_LATEST:
            return Resp({"data": {"attributes": {
                "fileName": isin_name, "downloadLink": f"https://example.test/{isin_name}"}}})
        if url == gl.BIC_LATEST:
            return Resp({"data": {"attributes": {
                "fileName": bic_name, "downloadLink": f"https://example.test/{bic_name}"}}})
        raise AssertionError(f"unexpected download of {url}")

    monkeypatch.setattr(requests, "get", fake_get)


def test_download_latest_skips_when_already_loaded(local_copy, monkeypatch):
    _fake_latest(monkeypatch, "lei2.csv.zip", "isin.zip")
    assert gl.download_latest(log=lambda _m: None) is None


def test_download_latest_fetches_new_publish(local_copy, monkeypatch):
    _fake_latest(monkeypatch, "lei2-new.csv.zip", "isin.zip")
    fetched = []
    monkeypatch.setattr(gl, "_download", lambda url, dest, log: fetched.append(url) or dest)
    lei_zip, isin_zip, publish_date, bic_zip = gl.download_latest(log=lambda _m: None)
    assert lei_zip.endswith("lei2-new.csv.zip")
    assert bic_zip.endswith("bic.zip")
    assert publish_date == "2026-10-10 00:00:00"
    assert len(fetched) == 3


def test_download_latest_refetches_when_bic_file_changes(local_copy, monkeypatch):
    _fake_latest(monkeypatch, "lei2.csv.zip", "isin.zip", "bic-new.zip")
    monkeypatch.setattr(gl, "_download", lambda url, dest, log: dest)
    assert gl.download_latest(log=lambda _m: None) is not None


def test_download_latest_force_ignores_loaded(local_copy, monkeypatch):
    _fake_latest(monkeypatch, "lei2.csv.zip", "isin.zip")
    monkeypatch.setattr(gl, "_download", lambda url, dest, log: dest)
    assert gl.download_latest(log=lambda _m: None, force=True) is not None


def test_rebuild_switches_current_db(local_copy, tmp_path):
    first = gl._current_db_path()
    assert gl.get_record("815600AD83B2B6317788") is not None
    lei_zip = _zip_csv(tmp_path / "lei2b.csv.zip", LEI_HEADER, LEI_ROWS[:1])
    gl.build(lei_zip, None, log=lambda _m: None)
    assert gl._current_db_path() != first
    assert gl.get_record("YEH5ZCD6E441RHVHD759") is None


# ---------------------------------------------------------------------------
# Issuer details endpoint (/api/lei-details) and region mapping
# ---------------------------------------------------------------------------

def test_region_for():
    from backend.regions import region_for

    assert region_for("de") == "Europe"
    assert region_for("GB") == "Europe"
    assert region_for("CH") == "Europe"
    assert region_for("US") == "Global"
    assert region_for("RU") == "Global"
    assert region_for("TR") == "Global"
    assert region_for("") == ""


def _post_lei_details(leis):
    from fastapi.testclient import TestClient
    from backend.main import app

    # No `with`: the endpoint doesn't need the startup hooks (rate limiter,
    # cache), and running them again here interferes with other test modules.
    client = TestClient(app)
    return client.post("/api/lei-details", json={"leis": leis}).json()["results"]


def test_lei_details_keeps_order_and_duplicates(local_copy, monkeypatch):
    import backend.routes as routes

    monkeypatch.setattr(routes, "_api_lei_details", lambda leis: pytest.fail("API called"))
    results = _post_lei_details([
        "yeh5zcd6e441rhvhd759", "815600AD83B2B6317788", "YEH5ZCD6E441RHVHD759",
    ])
    assert [(r["lei"], r["legal_name"], r["country"], r["region"]) for r in results] == [
        ("YEH5ZCD6E441RHVHD759", "Bayerische Motoren Werke Aktiengesellschaft", "DE", "Europe"),
        ("815600AD83B2B6317788", "ENEL - S.P.A.", "IT", "Europe"),
        ("YEH5ZCD6E441RHVHD759", "Bayerische Motoren Werke Aktiengesellschaft", "DE", "Europe"),
    ]
    assert all(r["error"] == "" for r in results)


def test_lei_details_falls_back_to_api_and_flags_bad_input(local_copy, monkeypatch):
    import backend.routes as routes

    asked = []

    def fake_api(leis):
        asked.extend(leis)
        return {"HWUPKR0MPOU8FGXBT394": {
            "legal_name": "Apple Inc.", "country": "US",
            "status": "ACTIVE", "registration_status": "ISSUED",
        }}

    monkeypatch.setattr(routes, "_api_lei_details", fake_api)
    monkeypatch.setattr(lei_lookup, "_api_get", lambda *_a, **_k: {"data": []})
    results = _post_lei_details([
        "HWUPKR0MPOU8FGXBT394", "NOTANLEI", "ZZZZZZZZZZZZZZZZZZ00", "815600AD83B2B6317788",
    ])
    assert asked == ["HWUPKR0MPOU8FGXBT394", "ZZZZZZZZZZZZZZZZZZ00"]
    assert (results[0]["legal_name"], results[0]["region"]) == ("Apple Inc.", "Global")
    assert results[1]["error"] == "Not a valid LEI format"
    assert results[2]["error"] == "LEI not found in GLEIF"
    assert results[3]["region"] == "Europe"


# ---------------------------------------------------------------------------
# Issuer English names
# ---------------------------------------------------------------------------

def test_english_name_rules():
    alt = [("Foo Ltd", "ALTERNATIVE_LANGUAGE_LEGAL_NAME", "en")]
    assert gl.english_name("Foo GmbH", "de", alt) == "Foo Ltd"
    assert gl.english_name("Foo Ltd", "EN", []) == "Foo Ltd"
    # Trading names and non-English alternatives don't count
    assert gl.english_name("Foo GmbH", "de", [("Foo", "TRADING_OR_OPERATING_NAME", "en")]) == ""
    assert gl.english_name("Foo GmbH", "de", [("Foo", "ALTERNATIVE_LANGUAGE_LEGAL_NAME", "fr")]) == ""


def test_local_records_carry_english_name(local_copy, monkeypatch):
    monkeypatch.setattr(lei_lookup, "_api_get", lambda *_a, **_k: pytest.fail("API called"))
    assert gl.has_english_names()
    assert lei_lookup.english_names([
        "o2rne8ibxp4r0td8pu41", "5299009N55YRQC69CN08", "YEH5ZCD6E441RHVHD759",
    ]) == {
        "O2RNE8IBXP4R0TD8PU41": "Societe Generale SA",
        "5299009N55YRQC69CN08": "BMW Finance N.V.",
        "YEH5ZCD6E441RHVHD759": "",
    }
    assert gl.search_names("societe generale")[0]["english_name"] == "Societe Generale SA"
    assert lei_lookup.validate_single_lei("O2RNE8IBXP4R0TD8PU41")["english_name"] == "Societe Generale SA"


def test_english_names_api_fallback(local_copy, monkeypatch):
    calls = []

    def fake_api(url, params, **_k):
        calls.append(params["filter[lei]"])
        return {"data": [{"attributes": {"lei": "HWUPKR0MPOU8FGXBT394", "entity": {
            "legalName": {"name": "Apple Inc.", "language": "en"}, "otherNames": [],
        }}}]}

    monkeypatch.setattr(lei_lookup, "_api_get", fake_api)
    names = lei_lookup.english_names(["HWUPKR0MPOU8FGXBT394", "ZZZZZZZZZZZZZZZZZZ00", "bad"])
    assert names == {"HWUPKR0MPOU8FGXBT394": "Apple Inc.", "ZZZZZZZZZZZZZZZZZZ00": "", "BAD": ""}
    assert calls == ["HWUPKR0MPOU8FGXBT394,ZZZZZZZZZZZZZZZZZZ00"] or calls == [
        "ZZZZZZZZZZZZZZZZZZ00,HWUPKR0MPOU8FGXBT394"]
    lei_lookup.english_names(["HWUPKR0MPOU8FGXBT394"])
    assert len(calls) == 1  # memoised


def test_download_latest_rebuilds_old_schema(local_copy, monkeypatch):
    conn = __import__("sqlite3").connect(gl._current_db_path())
    conn.execute("DELETE FROM meta WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    _fake_latest(monkeypatch, "lei2.csv.zip", "isin.zip")
    monkeypatch.setattr(gl, "_download", lambda url, dest, log: dest)
    assert gl.download_latest(log=lambda _m: None) is not None


def test_isin_lookup_and_lei_details_include_english_name(local_copy, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import app

    monkeypatch.setattr(lei_lookup, "_api_get", lambda *_a, **_k: pytest.fail("API called"))
    client = TestClient(app)
    isin = client.get("/api/isin-lookup", params={"isins": "DE0005190003"}).json()["results"]
    assert (isin[0]["lei"], isin[0]["english_name"], isin[0]["region"]) == (
        "YEH5ZCD6E441RHVHD759", "", "Europe")
    assert _post_lei_details(["O2RNE8IBXP4R0TD8PU41"])[0]["english_name"] == "Societe Generale SA"


# ---------------------------------------------------------------------------
# Sector classification
# ---------------------------------------------------------------------------

def test_sector_rules():
    from backend.sector import classify

    assert classify("INTERNATIONAL_ORGANIZATION", "", ["European Investment Bank"]) == (
        "SPS", "Category: INTERNATIONAL_ORGANIZATION")
    assert classify("RESIDENT_GOVERNMENT_ENTITY", "LOCAL_GOVERNMENT", ["Stadt Wien"])[0] == "SPS"
    # Every government entity counts, whatever its subcategory
    assert classify("RESIDENT_GOVERNMENT_ENTITY", "SOCIAL_SECURITY", ["Caisse"]) == (
        "SPS", "Category: RESIDENT_GOVERNMENT_ENTITY / SOCIAL_SECURITY")
    assert classify("RESIDENT_GOVERNMENT_ENTITY", "", ["Agency"]) == (
        "SPS", "Category: RESIDENT_GOVERNMENT_ENTITY")
    assert classify("GENERAL", "", ["Landesbank Baden-Württemberg"]) == (
        "Bank", 'Name contains "bank"')
    assert classify("GENERAL", "", ["BANCO SANTANDER"])[0] == "Bank"
    assert classify("GENERAL", "", ["Navy Federal Credit Union"])[0] == "Bank"
    assert classify("GENERAL", "", ["BANCA DI CREDITO COOPERATIVO"])[0] == "Bank"
    assert classify("GENERAL", "", ["Cajamar Caja Rural"])[0] == "Bank"
    assert classify("GENERAL", "", ["CAIXA GERAL DE DEPÓSITOS"])[0] == "Bank"
    assert classify("GENERAL", "", ["Kreissparkasse Köln"])[0] == "Bank"
    # "Caja" only as a whole word; foundations aren't banks
    assert classify("GENERAL", "", ["Hacaja Holding B.V."])[0] == "Other"
    assert classify("GENERAL", "", ["Stiftung der Sparkasse Finnentrop"])[0] == "Other"
    assert classify("GENERAL", "", ["Sparkassenstiftung Frankenthal"])[0] == "Other"
    assert classify("GENERAL", "", ["FUNDACIO ESPECIAL ANTIGA CAIXA SABADELL 1859"])[0] == "Other"
    assert classify("GENERAL", "", ["Fundación Bancaria Ibercaja"])[0] == "Other"
    # Bank wins over the other keyword lists
    assert classify("GENERAL", "", ["Infrastructure Bank Ltd"])[0] == "Bank"
    assert classify("GENERAL", "", ["Hornsea Wind Farm Ltd"])[0] == "Project Finance"
    assert classify("GENERAL", "", ["Solar Power Holdings"])[0] == "Project Finance"
    assert classify("GENERAL", "", ["National Grid plc"])[0] == "Utilities"
    # Keywords outside the bank list match whole words only
    assert classify("GENERAL", "", ["Windsor Gaskets Ltd"])[0] == "Other"
    # Name rules only apply to GENERAL entities
    assert classify("FUND", "", ["Bank Loan Fund"]) == ("Other", "Category: FUND")
    assert classify("GENERAL", "", ["Dassault Aviation"], has_bic=True) == (
        "Other", "Category: GENERAL; BIC registered")
    assert classify("", "", ["Unknown"]) == ("Other", "Category: unknown")


def test_lei_details_include_sector(local_copy, monkeypatch):
    import backend.routes as routes

    monkeypatch.setattr(routes, "_api_lei_details", lambda leis: pytest.fail("API called"))
    monkeypatch.setattr(lei_lookup, "_api_get", lambda *_a, **_k: pytest.fail("API called"))
    assert gl.bics_for_lei("o2rne8ibxp4r0td8pu41") == ["SOGEFRPPXXX"]
    assert gl.status()["bic_count"] == "1"
    results = _post_lei_details(["O2RNE8IBXP4R0TD8PU41", "815600AD83B2B6317788", "NOTANLEI"])
    assert [(r["sector"], r["sector_basis"]) for r in results] == [
        ("Other", "Category: GENERAL; BIC registered"),
        ("Other", "Category: GENERAL"),
        ("", ""),
    ]


def test_lei_details_sector_from_api(local_copy, monkeypatch):
    import backend.routes as routes

    monkeypatch.setattr(routes, "_api_lei_details", lambda leis: {"5493000IBP32UQZ0KL24": {
        "legal_name": "Republic of Austria", "country": "AT", "status": "ACTIVE",
        "registration_status": "ISSUED", "category": "RESIDENT_GOVERNMENT_ENTITY",
        "subcategory": "CENTRAL_GOVERNMENT", "bics": [],
    }})
    monkeypatch.setattr(lei_lookup, "_api_get", lambda *_a, **_k: {"data": []})
    [row] = _post_lei_details(["5493000IBP32UQZ0KL24"])
    assert row["sector"] == "SPS"
