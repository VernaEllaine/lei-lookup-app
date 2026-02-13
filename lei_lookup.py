"""Look up LEI codes by company name using the GLEIF public API."""

import csv
import re
import sys
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

GLEIF_BASE = "https://api.gleif.org/api/v1"

# Retry settings
MAX_RETRIES = 5
BACKOFF_BASE = 1.0       # first retry waits 1s, then 2s, 4s, 8s, …
BACKOFF_MAX = 60.0       # cap any single wait
RETRYABLE_STATUS = (429, 500, 502, 503, 504)


class GleifAPIError(Exception):
    """Raised when the GLEIF API returns an unrecoverable error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _build_session() -> requests.Session:
    """Build a requests Session with transport-level retries for connection
    errors and read timeouts.  Application-level retries (429 / 5xx with
    Retry-After) are handled in ``_api_get``."""
    session = requests.Session()
    adapter = HTTPAdapter(
        max_retries=Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[],       # we handle status retries ourselves
            allowed_methods=["GET"],
            raise_on_status=False,
        )
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_session = _build_session()


def _api_get(url: str, params: dict, *, timeout: int = 30) -> dict:
    """GET *url* with retries on rate-limits and transient server errors.

    Handles HTTP 429 (rate-limit) by reading the ``Retry-After`` header and
    sleeping accordingly.  For 5xx errors it uses exponential backoff.
    Non-retryable 4xx errors are raised immediately.

    Returns the parsed JSON body.
    """
    last_exc: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = _session.get(url, params=params, timeout=timeout)
        except requests.ConnectionError as exc:
            last_exc = exc
            wait = min(BACKOFF_BASE * 2 ** (attempt - 1), BACKOFF_MAX)
            print(f"           connection error (attempt {attempt}/{MAX_RETRIES}), "
                  f"retrying in {wait:.0f}s …", flush=True)
            time.sleep(wait)
            continue
        except requests.Timeout as exc:
            last_exc = exc
            wait = min(BACKOFF_BASE * 2 ** (attempt - 1), BACKOFF_MAX)
            print(f"           timeout (attempt {attempt}/{MAX_RETRIES}), "
                  f"retrying in {wait:.0f}s …", flush=True)
            time.sleep(wait)
            continue

        if resp.status_code == 429:
            wait = _retry_after(resp, attempt)
            print(f"           rate-limited (attempt {attempt}/{MAX_RETRIES}), "
                  f"retrying in {wait:.0f}s …", flush=True)
            time.sleep(wait)
            last_exc = GleifAPIError("rate-limited", status_code=429)
            continue

        if resp.status_code in RETRYABLE_STATUS:
            wait = _retry_after(resp, attempt)
            print(f"           server error {resp.status_code} "
                  f"(attempt {attempt}/{MAX_RETRIES}), "
                  f"retrying in {wait:.0f}s …", flush=True)
            time.sleep(wait)
            last_exc = GleifAPIError(
                f"HTTP {resp.status_code}", status_code=resp.status_code
            )
            continue

        if resp.status_code >= 400:
            raise GleifAPIError(
                f"HTTP {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
            )

        return resp.json()

    # All retries exhausted.
    raise GleifAPIError(
        f"request failed after {MAX_RETRIES} attempts: {last_exc}"
    ) from last_exc


def _retry_after(resp: requests.Response, attempt: int) -> float:
    """Compute how long to wait before the next retry.

    Uses the Retry-After header if present, otherwise falls back to
    exponential backoff.
    """
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return min(float(header), BACKOFF_MAX)
        except ValueError:
            pass
    return min(BACKOFF_BASE * 2 ** (attempt - 1), BACKOFF_MAX)


def lookup_lei(company_name: str, max_results: int = 10) -> dict:
    """Search GLEIF for LEI records matching a company name.

    Uses the entity.legalName filter for fuzzy/substring matching, sorted
    by relevance. Returns all matches flagged for manual review when there
    are multiple results.

    Args:
        company_name: The company name to search for.
        max_results: Maximum number of results to return (default 10).

    Returns:
        A dict with keys:
            - query: the original search term
            - result_count: number of matches found
            - match_type: "exact", "multiple", or "none"
            - needs_review: True if multiple matches require human review
            - results: list of dicts with lei, legal_name, jurisdiction,
              status, registration_status, and confidence fields

    Raises:
        GleifAPIError: On unrecoverable API errors after exhausting retries.
    """
    payload = _api_get(
        f"{GLEIF_BASE}/lei-records",
        params={
            "filter[entity.legalName]": company_name,
            "page[size]": str(max_results),
        },
    )

    records = payload.get("data", [])
    total = payload.get("meta", {}).get("pagination", {}).get("total", len(records))

    results = []
    for rec in records:
        attrs = rec.get("attributes", {})
        entity = attrs.get("entity", {})
        registration = attrs.get("registration", {})
        legal_name = entity.get("legalName", {}).get("name", "")

        # Confidence indicator: compare query against the legal name.
        confidence = _compute_confidence(company_name, legal_name)

        results.append({
            "lei": attrs.get("lei", ""),
            "legal_name": legal_name,
            "jurisdiction": entity.get("jurisdiction", ""),
            "status": entity.get("status", ""),
            "registration_status": registration.get("status", ""),
            "confidence": confidence,
        })

    # Sort by confidence so best matches appear first.
    confidence_order = {"high": 0, "medium": 1, "low": 2}
    results.sort(key=lambda r: confidence_order[r["confidence"]])

    if len(results) == 0:
        match_type = "none"
    elif len(results) == 1:
        match_type = "exact"
    else:
        match_type = "multiple"

    return {
        "query": company_name,
        "result_count": total,
        "match_type": match_type,
        "needs_review": len(results) > 1,
        "results": results,
    }


def _compute_confidence(query: str, legal_name: str) -> str:
    """Rate match confidence based on how closely the name matches the query.

    Returns "high", "medium", or "low".
    """
    q = re.sub(r"[^\w\s]", "", query.strip().lower())
    name = re.sub(r"[^\w\s]", "", legal_name.strip().lower())

    if q == name:
        return "high"
    # One contains the other fully
    if q in name or name in q:
        return "medium"
    # Check token overlap
    q_tokens = set(q.split())
    name_tokens = set(name.split())
    if not q_tokens:
        return "low"
    overlap = len(q_tokens & name_tokens) / len(q_tokens)
    if overlap >= 0.5:
        return "medium"
    return "low"


def print_results(result: dict) -> None:
    """Pretty-print lookup results to the console."""
    print(f"\nQuery: {result['query']}")
    print(f"Matches: {result['result_count']}")

    if result["needs_review"]:
        print("** Multiple matches — manual review recommended **")

    if not result["results"]:
        print("No results found.\n")
        return

    for i, r in enumerate(result["results"], 1):
        print(f"\n  [{i}] {r['legal_name']}")
        print(f"      LEI:           {r['lei']}")
        print(f"      Jurisdiction:  {r['jurisdiction']}")
        print(f"      Status:        {r['status']}")
        print(f"      Reg. Status:   {r['registration_status']}")
        print(f"      Confidence:    {r['confidence']}")
    print()


def _classify(result: dict) -> str:
    """Return 'AUTO-MATCHED', 'REVIEW NEEDED', or 'NO MATCH' for a lookup result."""
    if not result["results"]:
        return "NO MATCH"
    high = [r for r in result["results"] if r["confidence"] == "high"]
    if len(high) == 1 and len(result["results"]) == 1:
        return "AUTO-MATCHED"
    if len(high) == 1:
        # Single high-confidence match among several — still auto-match.
        return "AUTO-MATCHED"
    return "REVIEW NEEDED"


def _best_match(result: dict) -> dict | None:
    """Return the single high-confidence match, or None."""
    high = [r for r in result["results"] if r["confidence"] == "high"]
    if len(high) == 1:
        return high[0]
    return None


def _detect_name_column(headers: list[str]) -> str | None:
    """Guess which column holds the company name."""
    candidates = ["company_name", "company", "name", "entity_name", "entity",
                   "legal_name", "legalname", "organization", "org_name"]
    lower_headers = {h.lower().strip(): h for h in headers}
    for c in candidates:
        if c in lower_headers:
            return lower_headers[c]
    return None


def process_csv(input_path: str, output_path: str, name_column: str | None = None,
                delay: float = 0.5) -> dict:
    """Read company names from a CSV, look up LEIs, and write results.

    Args:
        input_path: Path to the input CSV file.
        output_path: Path for the output CSV file.
        name_column: Column header containing company names. Auto-detected
                     if not provided.
        delay: Seconds to wait between API calls (rate-limit courtesy).

    Returns:
        A summary dict with counts for auto_matched, review_needed,
        no_match, errors, and total.
    """
    with open(input_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        input_headers = reader.fieldnames or []

        if not name_column:
            name_column = _detect_name_column(input_headers)
        if not name_column or name_column not in input_headers:
            raise ValueError(
                f"Could not find company name column. Available columns: "
                f"{input_headers}. Pass --name-column explicitly."
            )

        rows = list(reader)

    output_headers = list(input_headers) + [
        "lei", "lei_legal_name", "lei_jurisdiction", "lei_status",
        "lei_confidence", "lei_match_status", "lei_candidates",
    ]

    summary = {"auto_matched": 0, "review_needed": 0, "no_match": 0,
               "errors": 0, "total": len(rows)}

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_headers, extrasaction="ignore")
        writer.writeheader()

        for i, row in enumerate(rows):
            company = row.get(name_column, "").strip()
            if not company:
                row.update({h: "" for h in output_headers if h not in row})
                row["lei_match_status"] = "NO MATCH"
                summary["no_match"] += 1
                writer.writerow(row)
                continue

            print(f"  [{i + 1}/{len(rows)}] Looking up: {company}", flush=True)

            try:
                result = lookup_lei(company)
            except (requests.RequestException, GleifAPIError) as exc:
                print(f"           ERROR: {exc}")
                row.update({h: "" for h in output_headers if h not in row})
                row["lei_match_status"] = f"ERROR: {exc}"
                summary["errors"] += 1
                writer.writerow(row)
                if delay:
                    time.sleep(delay)
                continue

            status = _classify(result)
            best = _best_match(result)

            if status == "AUTO-MATCHED" and best:
                row["lei"] = best["lei"]
                row["lei_legal_name"] = best["legal_name"]
                row["lei_jurisdiction"] = best["jurisdiction"]
                row["lei_status"] = best["status"]
                row["lei_confidence"] = best["confidence"]
                row["lei_candidates"] = ""
                summary["auto_matched"] += 1
            elif status == "REVIEW NEEDED":
                # Put the top candidate in the main fields for convenience.
                top = result["results"][0]
                row["lei"] = top["lei"]
                row["lei_legal_name"] = top["legal_name"]
                row["lei_jurisdiction"] = top["jurisdiction"]
                row["lei_status"] = top["status"]
                row["lei_confidence"] = top["confidence"]
                # Pack all candidates into a single field.
                candidates = []
                for r in result["results"]:
                    candidates.append(
                        f"{r['legal_name']} | {r['lei']} | "
                        f"{r['jurisdiction']} | {r['confidence']}"
                    )
                row["lei_candidates"] = "; ".join(candidates)
                summary["review_needed"] += 1
            else:
                row.update({h: "" for h in output_headers if h not in row})
                summary["no_match"] += 1

            row["lei_match_status"] = status
            writer.writerow(row)

            if delay and i < len(rows) - 1:
                time.sleep(delay)

    return summary


def _print_summary(summary: dict) -> None:
    """Print a batch-run summary to the console."""
    print(f"\n{'=' * 45}")
    print(f"  Total rows:      {summary['total']}")
    print(f"  AUTO-MATCHED:    {summary['auto_matched']}")
    print(f"  REVIEW NEEDED:   {summary['review_needed']}")
    print(f"  NO MATCH:        {summary['no_match']}")
    print(f"  ERRORS:          {summary['errors']}")
    print(f"{'=' * 45}\n")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Look up LEI codes from the GLEIF API."
    )
    sub = parser.add_subparsers(dest="command")

    # Single-name lookup
    single = sub.add_parser("lookup", help="Look up a single company name.")
    single.add_argument("name", nargs="+", help="Company name to search for.")

    # Batch CSV processing
    batch = sub.add_parser("batch", help="Process a CSV file of company names.")
    batch.add_argument("input_csv", help="Path to the input CSV file.")
    batch.add_argument("-o", "--output", default=None,
                       help="Output CSV path (default: <input>_lei_results.csv).")
    batch.add_argument("-c", "--name-column", default=None,
                       help="Column header containing company names.")
    batch.add_argument("-d", "--delay", type=float, default=0.5,
                       help="Delay in seconds between API calls (default: 0.5).")

    args = parser.parse_args()

    if args.command == "lookup":
        name = " ".join(args.name)
        result = lookup_lei(name)
        print_results(result)

    elif args.command == "batch":
        output = args.output
        if not output:
            stem = args.input_csv.rsplit(".", 1)[0]
            output = f"{stem}_lei_results.csv"

        print(f"Input:  {args.input_csv}")
        print(f"Output: {output}")
        print()

        summary = process_csv(
            args.input_csv, output,
            name_column=args.name_column,
            delay=args.delay,
        )
        _print_summary(summary)
        print(f"Results written to: {output}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
