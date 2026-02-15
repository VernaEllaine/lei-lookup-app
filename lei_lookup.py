"""Look up LEI codes by company name using the GLEIF public API."""

import csv
import random
import re
import sys
import time

import requests
from rapidfuzz import fuzz
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
            base = min(float(header), BACKOFF_MAX)
            return base + random.uniform(0, 1)
        except ValueError:
            pass
    base = min(BACKOFF_BASE * 2 ** (attempt - 1), BACKOFF_MAX)
    return base + random.uniform(0, 1)


# Common legal-form suffixes to strip when searching
_LEGAL_SUFFIXES = re.compile(
    r"\b("
    r"s\.?p\.?a\.?|spa|s\.?a\.?|sa|s\.?e\.?|se|"
    r"a\.?g\.?|ag|aktiengesellschaft|"
    r"gmbh|g\.?m\.?b\.?h\.?|gesellschaft mit beschr[aä]nkter haftung|"
    r"s\.?a\.?s\.?|sas|s\.?a\.?c\.?a\.?|saca|"
    r"b\.?v\.?|bv|n\.?v\.?|nv|plc|s\.?a\.?u\.?|sau|"
    r"s\.?r\.?l\.?|srl|ltd|limited|inc|incorporated|corp|corporation|"
    r"kgaa|co\.?\s*kgaa"
    r")\s*$",
    re.IGNORECASE,
)

# Abbreviation expansions for confidence scoring
_ABBREV_MAP = {
    "spa": "s.p.a.",
    "ag": "aktiengesellschaft",
    "gmbh": "gesellschaft mit beschränkter haftung",
    "sa": "s.a.",
    "se": "s.e.",
    "sas": "s.a.s.",
    "bv": "b.v.",
    "nv": "n.v.",
    "srl": "s.r.l.",
    "sau": "s.a.u.",
    "saca": "s.a.c.a.",
    "kgaa": "kommanditgesellschaft auf aktien",
}


def _extract_legal_suffix(name: str) -> str | None:
    """Return the normalized legal suffix if present, else None."""
    m = _LEGAL_SUFFIXES.search(name.strip())
    if not m:
        return None
    raw = re.sub(r"[.\s]", "", m.group(1)).lower()
    # Normalize common variants to canonical forms
    _CANONICAL = {
        "spa": "spa", "sa": "sa", "se": "se",
        "ag": "ag", "aktiengesellschaft": "ag",
        "gmbh": "gmbh", "gesellschaftmitbeschränkterhaftung": "gmbh",
        "gesellschaftmitbeschrankter haftung": "gmbh",
        "sas": "sas", "saca": "saca",
        "bv": "bv", "nv": "nv", "plc": "plc", "sau": "sau",
        "srl": "srl", "ltd": "ltd", "limited": "ltd",
        "inc": "inc", "incorporated": "inc",
        "corp": "corp", "corporation": "corp",
        "kgaa": "kgaa", "cokgaa": "kgaa",
    }
    return _CANONICAL.get(raw, raw)


def _strip_legal_suffix(name: str) -> str:
    """Remove trailing legal-form suffix from a company name."""
    cleaned = _LEGAL_SUFFIXES.sub("", name).strip().rstrip(",-/")
    return cleaned.strip() or name


# Regex to find 20-character LEI codes in text
_LEI_PATTERN = re.compile(r"\b([A-Z0-9]{20})\b")


def _web_search_lei(company_name: str) -> list[dict]:
    """Search the web for an LEI code and validate any found against GLEIF.

    Returns a list of result dicts (same format as _search in lookup_lei),
    or an empty list if nothing is found.
    """
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return []

    query = f"{company_name} LEI legal entity identifier"
    try:
        with DDGS() as ddgs:
            web_results = list(ddgs.text(query, max_results=5))
    except Exception:
        return []

    # Extract candidate LEI codes from search result titles and bodies
    candidates: list[str] = []
    seen: set[str] = set()
    for r in web_results:
        text = f"{r.get('title', '')} {r.get('body', '')}"
        for match in _LEI_PATTERN.findall(text):
            if match not in seen:
                seen.add(match)
                candidates.append(match)

    # Validate candidates against GLEIF in a single batch request
    if not candidates:
        return []

    lei_filter = ",".join(candidates[:20])
    try:
        resp = requests.get(
            f"{GLEIF_BASE}/lei-records",
            params={"filter[lei]": lei_filter, "page[size]": "20"},
            timeout=15,
        )
        if resp.status_code != 200:
            return []
    except Exception:
        return []

    results = []
    for rec in resp.json().get("data", []):
        attrs = rec.get("attributes", {})
        entity = attrs.get("entity", {})
        registration = attrs.get("registration", {})
        legal_name = entity.get("legalName", {}).get("name", "")
        status = entity.get("status", "")
        reg_status = registration.get("status", "")

        if status != "ACTIVE" and reg_status != "ISSUED":
            continue

        confidence = _compute_confidence(company_name, legal_name)

        # Filter out unrelated entities by checking name similarity
        query_core = _extract_core_name(company_name).lower()
        legal_core = _extract_core_name(legal_name).lower()
        score = fuzz.token_sort_ratio(query_core, legal_core)
        if score < _FUZZY_THRESHOLD:
            continue

        results.append({
            "lei": attrs.get("lei", ""),
            "legal_name": legal_name,
            "jurisdiction": entity.get("jurisdiction", ""),
            "status": status,
            "registration_status": reg_status,
            "confidence": confidence,
        })

    return results


# Words that indicate a subsidiary or sub-entity rather than the parent
_SUBSIDIARY_WORDS = re.compile(
    r"\b(finance|finanzas|financiacion|financi|funding|capital|"
    r"treasury|emissions|emisiones|productos|produits|"
    r"holdings?|filiales?|subsidiary|services?|solutions?|"
    r"international|global|europe|americas?|asia|pacific)\b",
    re.IGNORECASE,
)

_BARE_SA_SE = re.compile(r"\b(sa|se)\s*$", re.IGNORECASE)


def _is_bare_sa_se(name: str) -> bool:
    """Return True if *name* ends with SA or SE without a country qualifier."""
    stripped = name.strip()
    if not _BARE_SA_SE.search(stripped):
        return False
    # Check there's no country-like word (e.g. "/France", "/Spain")
    if re.search(r"/\w+$", stripped):
        return False
    return True


def _prefer_parent(results: list[dict], core_name: str) -> list[dict]:
    """Re-order *results* so the most likely parent entity comes first.

    Prefers results whose core legal name matches the query core name,
    penalises names containing subsidiary-type words, and among ties
    prefers shorter names (parent entities have simpler names).
    """
    def sort_key(r: dict) -> tuple:
        legal = r["legal_name"]
        legal_core = re.sub(r"[^\w\s]", "", _strip_legal_suffix(legal).strip().lower())

        # Exact core match is best
        exact_core = 0 if legal_core == core_name else 1

        # Penalise subsidiary-like words in the legal name
        has_sub_word = 1 if _SUBSIDIARY_WORDS.search(legal) else 0

        # Prefer shorter names (parent entities are simpler)
        name_len = len(legal)

        return (exact_core, has_sub_word, name_len)

    return sorted(results, key=sort_key)


# Minimum rapidfuzz score to consider a fuzzy spelling match
_FUZZY_THRESHOLD = 65


def _extract_core_name(name: str) -> str:
    """Extract the main company name: strip legal suffixes and punctuation."""
    core = _strip_legal_suffix(name)
    core = re.sub(r"[^\w\s]", " ", core)
    # Collapse whitespace
    return " ".join(core.split()).strip()


def _fuzzy_name_search(
    company_name: str,
    search_fn,
) -> list[dict]:
    """Last-resort fuzzy spelling search for NO MATCH rows.

    Extracts the core company name (e.g. "Bayerische Motoren" from
    "Bayerische Motoren Werke AG"), searches GLEIF with progressively
    shorter prefixes, and returns any results whose legal names are
    similar enough (rapidfuzz score >= threshold) — marked as "low"
    confidence.
    """
    core = _extract_core_name(company_name)
    words = core.split()
    if not words:
        return []

    core_lower = core.lower()
    seen_leis: set[str] = set()
    hits: list[dict] = []

    # Try the full core name first, then drop trailing words
    # e.g. ["Bayerische Motoren Werke", "Bayerische Motoren", "Bayerische"]
    # Stop at single-word queries only if the name was originally one word
    min_words = 1 if len(words) <= 2 else 2
    for n in range(len(words), min_words - 1, -1):
        query = " ".join(words[:n])
        if len(query) < 3:
            continue
        try:
            candidates = search_fn(query)
        except Exception:
            continue

        for r in candidates:
            if r["lei"] in seen_leis:
                continue
            legal_core = _extract_core_name(r["legal_name"]).lower()
            score = fuzz.token_sort_ratio(core_lower, legal_core)
            if score >= _FUZZY_THRESHOLD:
                seen_leis.add(r["lei"])
                r["confidence"] = "low"
                hits.append(r)

        # Stop searching once we have hits
        if hits:
            break

    return hits


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
    def _search(query: str) -> list[dict]:
        payload = _api_get(
            f"{GLEIF_BASE}/lei-records",
            params={
                "filter[entity.legalName]": query,
                "page[size]": str(max_results),
            },
        )
        records = payload.get("data", [])
        hits = []
        for rec in records:
            attrs = rec.get("attributes", {})
            entity = attrs.get("entity", {})
            registration = attrs.get("registration", {})
            legal_name = entity.get("legalName", {}).get("name", "")
            confidence = _compute_confidence(company_name, legal_name)
            hits.append({
                "lei": attrs.get("lei", ""),
                "legal_name": legal_name,
                "jurisdiction": entity.get("jurisdiction", ""),
                "status": entity.get("status", ""),
                "registration_status": registration.get("status", ""),
                "confidence": confidence,
            })
        return [
            r for r in hits
            if r["status"] == "ACTIVE" or r["registration_status"] == "ISSUED"
        ]

    results = _search(company_name)

    # Also search with suffix stripped and merge results.  For SA/SE queries
    # like "Carrefour SA", the parent may be registered as just "CARREFOUR".
    stripped = _strip_legal_suffix(company_name)
    if stripped.lower() != company_name.strip().lower():
        stripped_results = _search(stripped)
        # Merge, avoiding duplicate LEIs
        seen_leis = {r["lei"] for r in results}
        for r in stripped_results:
            if r["lei"] not in seen_leis:
                results.append(r)
                seen_leis.add(r["lei"])

    # Last resort: web search for LEI codes
    if not results:
        results = _web_search_lei(company_name)

    # Final fallback: fuzzy spelling search on core name
    if not results:
        results = _fuzzy_name_search(company_name, _search)

    total = len(results)

    # For queries ending in SA/SE without a country qualifier, prefer the
    # parent/main entity — typically the one with the shortest legal name
    # that still matches the core search term.
    if results and _is_bare_sa_se(company_name):
        core = _strip_legal_suffix(company_name).strip().lower()
        results = _prefer_parent(results, core)

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


def _expand_abbrevs(text: str) -> str:
    """Expand known legal-form abbreviations in *text* for comparison."""
    words = text.split()
    expanded = []
    for w in words:
        expanded.append(_ABBREV_MAP.get(w, w))
    return " ".join(expanded)


def _compute_confidence(query: str, legal_name: str) -> str:
    """Rate match confidence based on how closely the name matches the query.

    Expands common abbreviations (SPA→S.P.A., AG→Aktiengesellschaft, etc.)
    before comparing so that "Enel SPA" scores high against "ENEL - S.P.A.".

    Returns "high", "medium", or "low".
    """
    q = re.sub(r"[^\w\s]", "", query.strip().lower())
    name = re.sub(r"[^\w\s]", "", legal_name.strip().lower())

    # Also compare with abbreviations expanded
    q_exp = re.sub(r"[^\w\s]", "", _expand_abbrevs(q))
    name_exp = re.sub(r"[^\w\s]", "", _expand_abbrevs(name))

    # Compare both original and expanded forms
    q_core = _strip_legal_suffix(q).strip()
    name_core = _strip_legal_suffix(name).strip()

    # Check if legal suffixes conflict (e.g. AG vs SRL = different entity)
    q_suffix = _extract_legal_suffix(query)
    name_suffix = _extract_legal_suffix(legal_name)
    suffix_conflict = q_suffix and name_suffix and q_suffix != name_suffix

    if q == name or q_exp == name_exp:
        return "medium" if suffix_conflict else "high"
    # Core names match (ignoring legal suffix)
    if q_core and name_core and q_core == name_core:
        return "medium" if suffix_conflict else "high"
    # One contains the other fully
    if q in name or name in q or q_core in name_core or name_core in q_core:
        return "medium"
    # Check token overlap (using expanded forms)
    q_tokens = set(q_exp.split())
    name_tokens = set(name_exp.split())
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
