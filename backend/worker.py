"""Background lookup worker with rate-limited GLEIF lookups and DB persistence."""

from __future__ import annotations

import asyncio
import os
import sys
import threading

# Add project root to path so lei_lookup can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from lei_lookup import GleifAPIError, _best_match, _classify, lookup_lei
from backend import database, gleif_local
from backend.cache import CacheManager
from backend.models import RowResult, LookupProgress
from backend.rate_limiter import get_rate_limiter

LEI_FIELDS = [
    "lei", "lei_legal_name", "lei_jurisdiction", "lei_status",
    "lei_confidence", "lei_match_status", "lei_candidates",
]


async def run_lookup(
    rows: list[dict],
    column: str,
    cache: CacheManager,
    queue: asyncio.Queue,
    session_id: str,
) -> None:
    """Run LEI lookups for all rows, pushing progress to *queue*.

    GLEIF API calls are funnelled through the global rate limiter so that
    all concurrent sessions share a single token bucket (~60 req/min).
    Results are written to the SQLite database scoped by *session_id*.
    """
    limiter = get_rate_limiter()
    completed_count = 0
    count_lock = threading.Lock()
    total = len(rows)

    async def process_row(i: int, row: dict) -> None:
        nonlocal completed_count

        company = row.get(column, "").strip()
        out = dict(row)

        if not company:
            out.update({h: "" for h in LEI_FIELDS})
            out["lei_match_status"] = "NO MATCH"
            match_status = "NO MATCH"
        else:
            cached = cache.fuzzy_lookup(company)
            if cached is not None:
                out["lei"] = cached["lei"]
                out["lei_legal_name"] = cached["legal_name"]
                out["lei_jurisdiction"] = cached["jurisdiction"]
                out["lei_status"] = cached["status"]
                out["lei_confidence"] = cached["confidence"]
                out["lei_match_status"] = cached["match_status"]
                out["lei_candidates"] = ""
                match_status = cached["match_status"]
            else:
                try:
                    if gleif_local.is_available():
                        # Local search makes no GLEIF calls, so skip the limiter
                        result = await asyncio.to_thread(lookup_lei, company)
                    else:
                        result = await limiter.execute(lookup_lei, company)
                except (requests.RequestException, GleifAPIError) as exc:
                    out.update({h: "" for h in LEI_FIELDS})
                    out["lei_match_status"] = f"ERROR: {exc}"
                    match_status = "ERROR"
                else:
                    match_status = _classify(result)
                    best = _best_match(result)

                    if match_status == "AUTO-MATCHED" and best:
                        out["lei"] = best["lei"]
                        out["lei_legal_name"] = best["legal_name"]
                        out["lei_jurisdiction"] = best["jurisdiction"]
                        out["lei_status"] = best["status"]
                        out["lei_confidence"] = best["confidence"]
                        out["lei_candidates"] = ""
                    elif match_status == "REVIEW NEEDED":
                        top = result["results"][0]
                        out["lei"] = top["lei"]
                        out["lei_legal_name"] = top["legal_name"]
                        out["lei_jurisdiction"] = top["jurisdiction"]
                        out["lei_status"] = top["status"]
                        out["lei_confidence"] = top["confidence"]
                        candidates = []
                        for r in result["results"]:
                            candidates.append(
                                f"{r['legal_name']} | {r['lei']} | "
                                f"{r['jurisdiction']} | {r['confidence']}"
                            )
                        out["lei_candidates"] = "; ".join(candidates)
                    else:
                        out.update({h: "" for h in LEI_FIELDS})
                        out["lei_match_status"] = "NO MATCH"

                    out["lei_match_status"] = match_status

        # Cache AUTO-MATCHED rows
        if match_status == "AUTO-MATCHED" and company:
            cache.cache_row(company, out)

        # Write result to database
        database.update_result(i, {
            "entity_name": company,
            "lei": out.get("lei", ""),
            "legal_name": out.get("lei_legal_name", ""),
            "jurisdiction": out.get("lei_jurisdiction", ""),
            "status": out.get("lei_status", ""),
            "confidence": out.get("lei_confidence", ""),
            "match_status": out.get("lei_match_status", ""),
            "candidates": out.get("lei_candidates", ""),
        }, session_id=session_id)

        row_result = RowResult(
            index=i,
            entity_name=company,
            lei=out.get("lei", ""),
            legal_name=out.get("lei_legal_name", ""),
            jurisdiction=out.get("lei_jurisdiction", ""),
            status=out.get("lei_status", ""),
            confidence=out.get("lei_confidence", ""),
            match_status=out.get("lei_match_status", ""),
            candidates=out.get("lei_candidates", ""),
            original={k: v for k, v in row.items()},
        )

        with count_lock:
            completed_count += 1
            is_done = completed_count == total

        progress = LookupProgress(
            index=i,
            total=total,
            company=company,
            row=row_result,
            done=is_done,
        )
        await queue.put(progress)

    tasks = [process_row(i, row) for i, row in enumerate(rows)]
    await asyncio.gather(*tasks)

    # Flush any remaining buffered cache writes
    cache.flush()
