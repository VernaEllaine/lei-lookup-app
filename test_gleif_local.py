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
]

LEI_ROWS = [
    ["815600AD83B2B6317788", "ENEL - S.P.A.", "", "IT", "IT", "ACTIVE", "ISSUED"],
    ["YEH5ZCD6E441RHVHD759", "Bayerische Motoren Werke Aktiengesellschaft", "BMW AG",
     "DE", "DE", "ACTIVE", "ISSUED"],
    ["5299009N55YRQC69CN08", "BMW Finance N.V.", "", "NL", "NL", "ACTIVE", "ISSUED"],
    ["LAPSED00000000000001", "Société Générale Lapsed SA", "", "FR", "FR", "ACTIVE", "LAPSED"],
    ["O2RNE8IBXP4R0TD8PU41", "SOCIÉTÉ GÉNÉRALE", "", "FR", "FR", "ACTIVE", "ISSUED"],
]

ISIN_ROWS = [["YEH5ZCD6E441RHVHD759", "DE0005190003"]]


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
    gl.build(lei_zip, isin_zip, publish_date="2026-10-09", log=lambda _m: None)
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


def _fake_latest(monkeypatch, lei_name, isin_name):
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
        raise AssertionError(f"unexpected download of {url}")

    monkeypatch.setattr(requests, "get", fake_get)


def test_download_latest_skips_when_already_loaded(local_copy, monkeypatch):
    _fake_latest(monkeypatch, "lei2.csv.zip", "isin.zip")
    assert gl.download_latest(log=lambda _m: None) is None


def test_download_latest_fetches_new_publish(local_copy, monkeypatch):
    _fake_latest(monkeypatch, "lei2-new.csv.zip", "isin.zip")
    fetched = []
    monkeypatch.setattr(gl, "_download", lambda url, dest, log: fetched.append(url) or dest)
    lei_zip, isin_zip, publish_date = gl.download_latest(log=lambda _m: None)
    assert lei_zip.endswith("lei2-new.csv.zip")
    assert publish_date == "2026-10-10 00:00:00"
    assert len(fetched) == 2


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
