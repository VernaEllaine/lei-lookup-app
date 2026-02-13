"""Test retry logic for _api_get against simulated HTTP errors."""

import time
from unittest.mock import MagicMock, patch

import lei_lookup


def _make_response(status_code, json_data=None, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.text = f"error {status_code}"
    resp.json.return_value = json_data or {}
    return resp


def test_success_on_first_try():
    ok = _make_response(200, json_data={"data": []})
    with patch.object(lei_lookup._session, "get", return_value=ok) as mock_get:
        result = lei_lookup._api_get("http://test", {})
    assert result == {"data": []}
    assert mock_get.call_count == 1
    print("  PASS: success on first try")


def test_retry_on_429_then_succeed():
    rate_limited = _make_response(429, headers={"Retry-After": "0"})
    ok = _make_response(200, json_data={"data": [{"id": "1"}]})

    with patch.object(lei_lookup._session, "get",
                      side_effect=[rate_limited, rate_limited, ok]) as mock_get:
        result = lei_lookup._api_get("http://test", {})
    assert result == {"data": [{"id": "1"}]}
    assert mock_get.call_count == 3
    print("  PASS: retry on 429 then succeed")


def test_retry_on_503_then_succeed():
    error = _make_response(503, headers={"Retry-After": "0"})
    ok = _make_response(200, json_data={"data": []})

    with patch.object(lei_lookup._session, "get",
                      side_effect=[error, ok]) as mock_get:
        result = lei_lookup._api_get("http://test", {})
    assert result == {"data": []}
    assert mock_get.call_count == 2
    print("  PASS: retry on 503 then succeed")


def test_exhausted_retries_raises():
    rate_limited = _make_response(429, headers={"Retry-After": "0"})

    with patch.object(lei_lookup._session, "get",
                      return_value=rate_limited):
        try:
            lei_lookup._api_get("http://test", {})
            assert False, "should have raised"
        except lei_lookup.GleifAPIError as exc:
            assert "failed after" in str(exc)
    print("  PASS: exhausted retries raises GleifAPIError")


def test_non_retryable_4xx_raises_immediately():
    bad_request = _make_response(400)

    with patch.object(lei_lookup._session, "get",
                      return_value=bad_request) as mock_get:
        try:
            lei_lookup._api_get("http://test", {})
            assert False, "should have raised"
        except lei_lookup.GleifAPIError as exc:
            assert exc.status_code == 400
    assert mock_get.call_count == 1
    print("  PASS: 400 raises immediately without retrying")


def test_retry_after_header_honoured():
    rate_limited = _make_response(429, headers={"Retry-After": "0"})
    ok = _make_response(200, json_data={"data": []})

    with patch.object(lei_lookup._session, "get",
                      side_effect=[rate_limited, ok]):
        start = time.monotonic()
        lei_lookup._api_get("http://test", {})
        elapsed = time.monotonic() - start
    # Retry-After: 0 means it should barely wait at all (< 1s).
    assert elapsed < 1.0
    print("  PASS: Retry-After header honoured")


def test_connection_error_retries():
    import requests as req

    with patch.object(lei_lookup._session, "get",
                      side_effect=req.ConnectionError("refused")) as mock_get:
        # Override backoff to 0 for speed.
        orig = lei_lookup.BACKOFF_BASE
        lei_lookup.BACKOFF_BASE = 0.0
        try:
            lei_lookup._api_get("http://test", {})
            assert False, "should have raised"
        except lei_lookup.GleifAPIError:
            pass
        finally:
            lei_lookup.BACKOFF_BASE = orig
    assert mock_get.call_count == lei_lookup.MAX_RETRIES
    print("  PASS: connection errors retry up to MAX_RETRIES")


if __name__ == "__main__":
    print("Running retry tests …\n")
    test_success_on_first_try()
    test_retry_on_429_then_succeed()
    test_retry_on_503_then_succeed()
    test_exhausted_retries_raises()
    test_non_retryable_4xx_raises_immediately()
    test_retry_after_header_honoured()
    test_connection_error_retries()
    print("\nAll tests passed.")
