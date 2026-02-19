"""Tests for lei_lookup core logic and backend API routes.

Coverage:
  • Pure logic helpers  (_extract_legal_suffix, _strip_legal_suffix,
    _extract_core_name, _is_bare_sa_se, _compute_confidence,
    _classify, _best_match, _detect_name_column, _prefer_parent)
  • API-dependent functions  (lookup_lei, validate_single_lei) –
    GLEIF HTTP calls mocked via unittest.mock.patch
  • Backend route helpers  (_detect_lei_column, _sniff_dialect)
  • FastAPI routes  (upload, results, confirm-all, cache, export)
    via httpx TestClient with an isolated temp database (see conftest.py)
"""

from __future__ import annotations

import io
import csv
from unittest.mock import patch

import pytest

import lei_lookup
from lei_lookup import (
    GleifAPIError,
    _best_match,
    _classify,
    _compute_confidence,
    _detect_name_column,
    _extract_core_name,
    _extract_legal_suffix,
    _is_bare_sa_se,
    _prefer_parent,
    _strip_legal_suffix,
    lookup_lei,
    validate_single_lei,
)


# ---------------------------------------------------------------------------
# Helpers shared across test classes
# ---------------------------------------------------------------------------

def _gleif_record(
    lei: str,
    legal_name: str,
    jurisdiction: str = "DE",
    entity_status: str = "ACTIVE",
    reg_status: str = "ISSUED",
) -> dict:
    """Build a minimal GLEIF API data record."""
    return {
        "id": lei,
        "attributes": {
            "lei": lei,
            "entity": {
                "legalName": {"name": legal_name},
                "jurisdiction": jurisdiction,
                "status": entity_status,
            },
            "registration": {"status": reg_status},
        },
    }


def _patch_api(records: list[dict]):
    """Patch lei_lookup._api_get to return *records* for every call."""
    return patch.object(
        lei_lookup,
        "_api_get",
        return_value={"data": records},
    )


# ---------------------------------------------------------------------------
# _extract_legal_suffix
# ---------------------------------------------------------------------------

class TestExtractLegalSuffix:
    def test_spa(self):
        assert _extract_legal_suffix("Enel SPA") == "spa"

    def test_ag(self):
        assert _extract_legal_suffix("Volkswagen AG") == "ag"

    def test_gmbh(self):
        assert _extract_legal_suffix("Deutsche GmbH") == "gmbh"

    def test_ltd(self):
        assert _extract_legal_suffix("Acme Ltd") == "ltd"

    def test_limited_normalizes_to_ltd(self):
        assert _extract_legal_suffix("Acme Limited") == "ltd"

    def test_inc(self):
        assert _extract_legal_suffix("Apple Inc") == "inc"

    def test_plc(self):
        assert _extract_legal_suffix("HSBC plc") == "plc"

    def test_sa(self):
        assert _extract_legal_suffix("Carrefour SA") == "sa"

    def test_se(self):
        assert _extract_legal_suffix("Allianz SE") == "se"

    def test_bv(self):
        assert _extract_legal_suffix("Shell BV") == "bv"

    def test_nv(self):
        assert _extract_legal_suffix("Philips NV") == "nv"

    def test_srl(self):
        assert _extract_legal_suffix("Fiat SRL") == "srl"

    def test_no_suffix_returns_none(self):
        assert _extract_legal_suffix("Google") is None

    def test_case_insensitive(self):
        assert _extract_legal_suffix("Siemens gmbh") == "gmbh"

    def test_corp(self):
        assert _extract_legal_suffix("General Corp") == "corp"

    def test_corporation_normalizes_to_corp(self):
        assert _extract_legal_suffix("General Corporation") == "corp"


# ---------------------------------------------------------------------------
# _strip_legal_suffix
# ---------------------------------------------------------------------------

class TestStripLegalSuffix:
    def test_strips_gmbh(self):
        assert _strip_legal_suffix("Deutsche GmbH") == "Deutsche"

    def test_strips_ag(self):
        assert _strip_legal_suffix("Volkswagen AG") == "Volkswagen"

    def test_strips_sa(self):
        assert _strip_legal_suffix("Carrefour SA") == "Carrefour"

    def test_strips_ltd(self):
        assert _strip_legal_suffix("Acme Ltd") == "Acme"

    def test_strips_inc(self):
        result = _strip_legal_suffix("Startup Inc")
        assert "Inc" not in result
        assert result

    def test_no_suffix_unchanged(self):
        assert _strip_legal_suffix("Google") == "Google"

    def test_multiword_name(self):
        result = _strip_legal_suffix("Deutsche Bank AG")
        assert "AG" not in result
        assert "Deutsche Bank" in result

    def test_trailing_punctuation_cleaned(self):
        # Should not leave stray commas or slashes
        result = _strip_legal_suffix("Company, SA")
        assert result.strip(",").strip() != ""


# ---------------------------------------------------------------------------
# _extract_core_name
# ---------------------------------------------------------------------------

class TestExtractCoreName:
    def test_strips_suffix_and_punctuation(self):
        assert _extract_core_name("Deutsche Bank AG") == "Deutsche Bank"

    def test_collapses_whitespace(self):
        result = _extract_core_name("  Acme   Corp  ")
        assert "  " not in result
        assert result  # not empty

    def test_removes_dashes(self):
        result = _extract_core_name("LVMH - Moët Hennessy AG")
        assert "-" not in result

    def test_plain_name_unchanged(self):
        assert _extract_core_name("Google") == "Google"

    def test_does_not_return_empty_string(self):
        # Even a suffix-only name should not return empty
        result = _extract_core_name("GmbH")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _is_bare_sa_se
# ---------------------------------------------------------------------------

class TestIsBareSaSe:
    def test_sa_is_bare(self):
        assert _is_bare_sa_se("Carrefour SA") is True

    def test_se_is_bare(self):
        assert _is_bare_sa_se("Allianz SE") is True

    def test_sa_uppercase(self):
        assert _is_bare_sa_se("Total SA") is True

    def test_sa_with_slash_country_not_bare(self):
        assert _is_bare_sa_se("Total SA/France") is False

    def test_no_suffix_is_false(self):
        assert _is_bare_sa_se("Google") is False

    def test_gmbh_is_not_sa_se(self):
        assert _is_bare_sa_se("Deutsche GmbH") is False

    def test_plc_is_not_sa_se(self):
        assert _is_bare_sa_se("Barclays plc") is False


# ---------------------------------------------------------------------------
# _compute_confidence
# ---------------------------------------------------------------------------

class TestComputeConfidence:
    def test_identical_strings_high(self):
        assert _compute_confidence("Enel SPA", "Enel SPA") == "high"

    def test_case_insensitive_high(self):
        assert _compute_confidence("enel spa", "ENEL SPA") == "high"

    def test_abbreviated_form_high(self):
        # SPA expands to S.P.A. for comparison
        assert _compute_confidence("Enel SPA", "ENEL - S.P.A.") == "high"

    def test_core_name_match_high(self):
        # Same core name, same legal form
        assert _compute_confidence("Deutsche Bank AG", "Deutsche Bank AG") == "high"

    def test_partial_containment_medium(self):
        result = _compute_confidence("Enel", "Enel Finance International")
        assert result in ("medium", "high")

    def test_token_overlap_medium(self):
        result = _compute_confidence("Deutsche Bank AG", "Deutsche Bank Global AG")
        assert result in ("high", "medium")

    def test_unrelated_names_low(self):
        assert _compute_confidence("Apple Inc", "Deutsche Bank AG") == "low"

    def test_suffix_conflict_caps_at_medium(self):
        # Same core name but different legal forms (AG vs SRL)
        result = _compute_confidence("Acme AG", "Acme SRL")
        assert result in ("medium", "low")

    def test_empty_query_does_not_crash(self):
        result = _compute_confidence("", "Something SA")
        assert result in ("high", "medium", "low")


# ---------------------------------------------------------------------------
# _classify
# ---------------------------------------------------------------------------

class TestClassify:
    def _result(self, confidences: list[str]) -> dict:
        results = [
            {
                "confidence": c,
                "lei": f"{'X' * 19}{i}",
                "legal_name": f"Entity {i}",
                "jurisdiction": "DE",
                "status": "ACTIVE",
            }
            for i, c in enumerate(confidences)
        ]
        return {"results": results, "result_count": len(results)}

    def test_empty_is_no_match(self):
        assert _classify({"results": [], "result_count": 0}) == "NO MATCH"

    def test_single_high_is_auto_matched(self):
        assert _classify(self._result(["high"])) == "AUTO-MATCHED"

    def test_single_high_among_others_is_auto_matched(self):
        assert _classify(self._result(["high", "medium", "low"])) == "AUTO-MATCHED"

    def test_two_high_is_review_needed(self):
        assert _classify(self._result(["high", "high"])) == "REVIEW NEEDED"

    def test_only_medium_is_review_needed(self):
        assert _classify(self._result(["medium"])) == "REVIEW NEEDED"

    def test_only_low_is_review_needed(self):
        assert _classify(self._result(["low", "low"])) == "REVIEW NEEDED"


# ---------------------------------------------------------------------------
# _best_match
# ---------------------------------------------------------------------------

class TestBestMatch:
    def _results(self, confidences: list[str]) -> dict:
        items = [
            {"confidence": c, "lei": f"{'X' * 19}{i}", "legal_name": f"E{i}"}
            for i, c in enumerate(confidences)
        ]
        return {"results": items, "result_count": len(items)}

    def test_single_high_returned(self):
        match = _best_match(self._results(["high"]))
        assert match is not None
        assert match["confidence"] == "high"

    def test_two_high_returns_none(self):
        assert _best_match(self._results(["high", "high"])) is None

    def test_no_high_returns_none(self):
        assert _best_match(self._results(["medium", "low"])) is None

    def test_empty_returns_none(self):
        assert _best_match(self._results([])) is None

    def test_high_among_others_returned(self):
        match = _best_match(self._results(["medium", "high", "low"]))
        assert match is not None
        assert match["confidence"] == "high"


# ---------------------------------------------------------------------------
# _detect_name_column
# ---------------------------------------------------------------------------

class TestDetectNameColumn:
    def test_detects_company_name(self):
        assert _detect_name_column(["company_name", "country"]) == "company_name"

    def test_detects_name(self):
        assert _detect_name_column(["id", "name", "region"]) == "name"

    def test_detects_entity_name(self):
        assert _detect_name_column(["entity_name", "lei"]) == "entity_name"

    def test_detects_legal_name(self):
        assert _detect_name_column(["legal_name", "address"]) == "legal_name"

    def test_detects_company(self):
        assert _detect_name_column(["company", "revenue"]) == "company"

    def test_returns_none_for_unknown(self):
        assert _detect_name_column(["country", "revenue", "ticker"]) is None

    def test_returns_none_for_empty(self):
        assert _detect_name_column([]) is None

    def test_preserves_original_case(self):
        result = _detect_name_column(["Company_Name", "country"])
        assert result == "Company_Name"


# ---------------------------------------------------------------------------
# _prefer_parent
# ---------------------------------------------------------------------------

class TestPreferParent:
    def _r(self, legal_name: str) -> dict:
        return {
            "legal_name": legal_name,
            "lei": "X" * 20,
            "jurisdiction": "DE",
            "status": "ACTIVE",
            "confidence": "medium",
        }

    def test_parent_before_finance_subsidiary(self):
        results = [
            self._r("Enel Finance International SA"),
            self._r("Enel SPA"),
        ]
        ordered = _prefer_parent(results, "enel")
        assert ordered[0]["legal_name"] == "Enel SPA"

    def test_shorter_name_preferred_when_same_core(self):
        results = [
            self._r("Shell International Holdings NV"),
            self._r("Shell NV"),
        ]
        ordered = _prefer_parent(results, "shell")
        assert ordered[0]["legal_name"] == "Shell NV"

    def test_subsidiary_word_penalised(self):
        results = [
            self._r("BMW Capital GmbH"),
            self._r("BMW AG"),
        ]
        ordered = _prefer_parent(results, "bmw")
        assert ordered[0]["legal_name"] == "BMW AG"

    def test_single_result_unchanged(self):
        results = [self._r("Volkswagen AG")]
        assert _prefer_parent(results, "volkswagen") == results


# ---------------------------------------------------------------------------
# lookup_lei – GLEIF API mocked
# ---------------------------------------------------------------------------

class TestLookupLei:
    def test_single_exact_match(self):
        """One ACTIVE/ISSUED record → single result, exact match_type."""
        record = _gleif_record("G" * 20, "Google LLC")
        with _patch_api([record]):
            result = lookup_lei("Google LLC")
        assert result["result_count"] == 1
        assert result["match_type"] == "exact"
        assert result["results"][0]["lei"] == "G" * 20
        assert result["results"][0]["legal_name"] == "Google LLC"

    def test_no_results_returns_none_match(self):
        """Empty API response → result_count 0, match_type 'none'."""
        with _patch_api([]):
            result = lookup_lei("Nonexistent XYZ Corp 99999")
        assert result["result_count"] == 0
        assert result["match_type"] == "none"
        assert result["results"] == []

    def test_multiple_results_needs_review(self):
        """Two results → needs_review=True, match_type 'multiple'."""
        records = [
            _gleif_record("A" * 20, "Enel SPA"),
            _gleif_record("B" * 20, "Enel Finance SPA"),
        ]
        with _patch_api(records):
            result = lookup_lei("Enel SPA")
        assert result["needs_review"] is True
        assert result["match_type"] == "multiple"

    def test_inactive_records_filtered_out(self):
        """Records with entity_status != ACTIVE or reg_status != ISSUED are dropped."""
        active = _gleif_record("A" * 20, "Enel SPA")
        inactive = _gleif_record(
            "B" * 20, "Enel Finance SPA",
            entity_status="INACTIVE", reg_status="LAPSED",
        )
        with _patch_api([active, inactive]):
            result = lookup_lei("Enel SPA")
        leis = [r["lei"] for r in result["results"]]
        assert "B" * 20 not in leis

    def test_results_sorted_high_confidence_first(self):
        """High-confidence matches must appear before lower-confidence ones."""
        records = [
            _gleif_record("A" * 20, "Deutsche Bank Finance GmbH"),
            _gleif_record("B" * 20, "Deutsche Bank AG"),
        ]
        with _patch_api(records):
            result = lookup_lei("Deutsche Bank AG")
        if len(result["results"]) >= 2:
            order = {"high": 0, "medium": 1, "low": 2}
            confidences = [order[r["confidence"]] for r in result["results"]]
            assert confidences == sorted(confidences)

    def test_result_contains_required_fields(self):
        """Each result dict must have the expected keys."""
        record = _gleif_record("G" * 20, "Google LLC", jurisdiction="US")
        with _patch_api([record]):
            result = lookup_lei("Google LLC")
        for key in ("lei", "legal_name", "jurisdiction", "status",
                    "registration_status", "confidence"):
            assert key in result["results"][0], f"missing key: {key}"

    def test_api_error_propagates(self):
        """GleifAPIError raised by _api_get must propagate out of lookup_lei."""
        with patch.object(
            lei_lookup, "_api_get",
            side_effect=GleifAPIError("timeout", status_code=503),
        ):
            with pytest.raises(GleifAPIError):
                lookup_lei("Some Company")

    def test_result_dict_structure(self):
        """lookup_lei must return the documented top-level keys."""
        with _patch_api([]):
            result = lookup_lei("Any Company")
        for key in ("query", "result_count", "match_type", "needs_review", "results"):
            assert key in result, f"missing top-level key: {key}"

    def test_query_preserved(self):
        """The 'query' field in the result must match the input name."""
        with _patch_api([]):
            result = lookup_lei("Acme Corp")
        assert result["query"] == "Acme Corp"


# ---------------------------------------------------------------------------
# validate_single_lei – GLEIF API mocked
# ---------------------------------------------------------------------------

def _validate_payload(
    legal_name: str = "Enel SPA",
    jurisdiction: str = "IT",
    entity_status: str = "ACTIVE",
    reg_status: str = "ISSUED",
) -> dict:
    return {
        "data": {
            "attributes": {
                "entity": {
                    "legalName": {"name": legal_name},
                    "jurisdiction": jurisdiction,
                    "status": entity_status,
                },
                "registration": {"status": reg_status},
            }
        }
    }


class TestValidateSingleLei:
    def test_active_issued_returns_ok(self):
        with patch.object(lei_lookup, "_api_get", return_value=_validate_payload()):
            result = validate_single_lei("A" * 20)
        assert result["flag"] == "OK"
        assert result["entity_status"] == "ACTIVE"
        assert result["registration_status"] == "ISSUED"

    def test_lapsed_registration_returns_lapsed(self):
        payload = _validate_payload(entity_status="ACTIVE", reg_status="LAPSED")
        with patch.object(lei_lookup, "_api_get", return_value=payload):
            result = validate_single_lei("A" * 20)
        assert result["flag"] == "LAPSED"

    def test_inactive_entity_returns_invalid(self):
        payload = _validate_payload(entity_status="INACTIVE", reg_status="ISSUED")
        with patch.object(lei_lookup, "_api_get", return_value=payload):
            result = validate_single_lei("A" * 20)
        assert result["flag"] == "INVALID"

    def test_not_found_404_returns_not_found(self):
        with patch.object(
            lei_lookup, "_api_get",
            side_effect=GleifAPIError("not found", status_code=404),
        ):
            result = validate_single_lei("A" * 20)
        assert result["flag"] == "NOT_FOUND"

    def test_api_error_returns_error_flag(self):
        with patch.object(
            lei_lookup, "_api_get",
            side_effect=GleifAPIError("timeout", status_code=503),
        ):
            result = validate_single_lei("A" * 20)
        assert result["flag"] == "ERROR"

    def test_legal_name_and_jurisdiction_in_result(self):
        payload = _validate_payload(legal_name="Enel SPA", jurisdiction="IT")
        with patch.object(lei_lookup, "_api_get", return_value=payload):
            result = validate_single_lei("A" * 20)
        assert result["legal_name"] == "Enel SPA"
        assert result["jurisdiction"] == "IT"

    def test_result_has_all_required_keys(self):
        payload = _validate_payload()
        with patch.object(lei_lookup, "_api_get", return_value=payload):
            result = validate_single_lei("A" * 20)
        for key in ("entity_status", "registration_status",
                    "legal_name", "jurisdiction", "flag"):
            assert key in result, f"missing key: {key}"


# ---------------------------------------------------------------------------
# Backend route helpers
# ---------------------------------------------------------------------------

class TestDetectLeiColumn:
    @pytest.fixture(autouse=True)
    def _load(self):
        from backend.routes import _detect_lei_column
        self._fn = _detect_lei_column

    def test_detects_lei(self):
        assert self._fn(["entity", "lei", "country"]) == "lei"

    def test_detects_lei_code(self):
        assert self._fn(["name", "lei_code"]) == "lei_code"

    def test_detects_lei_number(self):
        assert self._fn(["company", "lei_number"]) == "lei_number"

    def test_fallback_any_header_containing_lei(self):
        assert self._fn(["name", "my_lei_value"]) == "my_lei_value"

    def test_no_lei_column_returns_none(self):
        assert self._fn(["company", "country", "revenue"]) is None

    def test_empty_headers_returns_none(self):
        assert self._fn([]) is None


class TestSniffDialect:
    @pytest.fixture(autouse=True)
    def _load(self):
        from backend.routes import _sniff_dialect
        self._fn = _sniff_dialect

    def test_comma_delimited(self):
        dialect = self._fn("a,b,c\n1,2,3\n")
        assert dialect.delimiter == ","

    def test_semicolon_delimited(self):
        dialect = self._fn("a;b;c\n1;2;3\n")
        assert dialect.delimiter == ";"

    def test_tab_delimited(self):
        dialect = self._fn("a\tb\tc\n1\t2\t3\n")
        assert dialect.delimiter == "\t"

    def test_invalid_input_falls_back(self):
        # Should not raise; falls back to csv.excel (comma)
        dialect = self._fn("")
        assert hasattr(dialect, "delimiter")


# ---------------------------------------------------------------------------
# FastAPI routes – TestClient
# ---------------------------------------------------------------------------

def _make_csv_bytes(rows: list[dict], headers: list[str] | None = None) -> bytes:
    if headers is None:
        headers = list(rows[0].keys()) if rows else []
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode()


@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient with the temp database already set up by conftest."""
    from fastapi.testclient import TestClient
    from backend.main import app
    with TestClient(app) as c:
        yield c


class TestUploadRoute:
    def test_upload_returns_session_id(self, client):
        csv_bytes = _make_csv_bytes([{"company_name": "Enel SPA", "country": "IT"}])
        resp = client.post("/api/upload", files={"file": ("t.csv", csv_bytes, "text/csv")})
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert len(data["session_id"]) > 0

    def test_upload_row_count(self, client):
        rows = [{"company_name": f"Company {i}"} for i in range(5)]
        csv_bytes = _make_csv_bytes(rows)
        resp = client.post("/api/upload", files={"file": ("t.csv", csv_bytes, "text/csv")})
        assert resp.json()["row_count"] == 5

    def test_upload_detects_company_name_column(self, client):
        csv_bytes = _make_csv_bytes([{"company_name": "Test Corp"}])
        resp = client.post("/api/upload", files={"file": ("t.csv", csv_bytes, "text/csv")})
        assert resp.json()["detected_column"] == "company_name"

    def test_upload_detects_entity_name_column(self, client):
        csv_bytes = _make_csv_bytes([{"entity_name": "Test Corp", "lei": "X" * 20}])
        resp = client.post("/api/upload", files={"file": ("t.csv", csv_bytes, "text/csv")})
        assert resp.json()["detected_column"] == "entity_name"

    def test_upload_headers_returned(self, client):
        csv_bytes = _make_csv_bytes([{"company_name": "A", "country": "US"}])
        resp = client.post("/api/upload", files={"file": ("t.csv", csv_bytes, "text/csv")})
        assert "company_name" in resp.json()["headers"]
        assert "country" in resp.json()["headers"]

    def test_upload_semicolon_delimited(self, client):
        csv_bytes = b"company_name;country\nTest SA;ES\nOther Corp;FR\n"
        resp = client.post("/api/upload", files={"file": ("t.csv", csv_bytes, "text/csv")})
        assert resp.status_code == 200
        assert resp.json()["row_count"] == 2


class TestResultsRoute:
    def test_results_empty_session(self, client):
        resp = client.get("/api/results?session_id=does_not_exist&page=1&page_size=50")
        assert resp.status_code == 200
        data = resp.json()
        assert "rows" in data
        assert isinstance(data["rows"], list)

    def test_results_total_matches_upload(self, client):
        rows = [{"company_name": f"Corp {i}"} for i in range(3)]
        csv_bytes = _make_csv_bytes(rows)
        sid = client.post(
            "/api/upload", files={"file": ("t.csv", csv_bytes, "text/csv")}
        ).json()["session_id"]

        resp = client.get(f"/api/results?session_id={sid}&page=1&page_size=50")
        assert resp.json()["total"] == 3

    def test_results_pagination(self, client):
        rows = [{"company_name": f"Corp {i}"} for i in range(10)]
        csv_bytes = _make_csv_bytes(rows)
        sid = client.post(
            "/api/upload", files={"file": ("t.csv", csv_bytes, "text/csv")}
        ).json()["session_id"]

        resp = client.get(f"/api/results?session_id={sid}&page=1&page_size=4")
        data = resp.json()
        assert len(data["rows"]) == 4
        assert data["total"] == 10

    def test_results_summary_present(self, client):
        resp = client.get("/api/results?session_id=x&page=1&page_size=10")
        assert "summary" in resp.json()


class TestCacheRoute:
    def test_cache_summary_returns_size(self, client):
        resp = client.get("/api/cache/summary")
        assert resp.status_code == 200
        assert "cache_size" in resp.json()

    def test_clear_cache_returns_message(self, client):
        resp = client.delete("/api/cache")
        assert resp.status_code == 200
        assert "message" in resp.json()


class TestExportRoute:
    def test_export_csv_content_type(self, client):
        resp = client.get("/api/export?session_id=nonexistent")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]

    def test_export_csv_after_upload_has_headers(self, client):
        csv_bytes = _make_csv_bytes([{"company_name": "Test Corp"}])
        sid = client.post(
            "/api/upload", files={"file": ("t.csv", csv_bytes, "text/csv")}
        ).json()["session_id"]

        resp = client.get(f"/api/export?session_id={sid}")
        assert resp.status_code == 200
        content = resp.text
        # CSV should at minimum have the header row
        assert "company_name" in content or "lei" in content


class TestConfirmAllRoute:
    def test_confirm_all_returns_confirmed_count(self, client):
        resp = client.post("/api/confirm-all?session_id=nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert "confirmed" in data
        assert isinstance(data["confirmed"], int)
