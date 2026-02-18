"""Background validation worker — checks LEIs against GLEIF and suggests replacements."""

from __future__ import annotations

import asyncio
import os
import re
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lei_lookup import lookup_lei, validate_single_lei, _best_match, _classify
from backend import database
from backend.models import ValidationRowResult, ValidationProgress
from backend.rate_limiter import get_rate_limiter

# Basic LEI format: 20 alphanumeric characters
_LEI_RE = re.compile(r"^[A-Z0-9]{20}$")


async def run_validation(
    rows: list[dict],
    entity_col: str,
    lei_col: str,
    queue: asyncio.Queue,
    session_id: str,
) -> None:
    """Validate LEIs for all rows, pushing progress to *queue*.

    For each row:
    1. Validate the provided LEI against GLEIF
    2. If not OK, search for a replacement by entity name
    Results are written to the validation_results table.
    """
    limiter = get_rate_limiter()
    completed_count = 0
    count_lock = threading.Lock()
    total = len(rows)

    async def process_row(i: int, row: dict) -> None:
        nonlocal completed_count

        entity_name = row.get(entity_col, "").strip()
        lei_code = row.get(lei_col, "").strip()

        entity_status = ""
        registration_status = ""
        flag = ""
        suggested_lei = ""
        suggested_legal_name = ""
        suggested_confidence = ""

        if not lei_code or not _LEI_RE.match(lei_code):
            # Empty or malformed LEI — skip API, mark as NOT_FOUND
            flag = "NOT_FOUND" if not lei_code else "INVALID"
        else:
            try:
                result = await limiter.execute(validate_single_lei, lei_code)
                entity_status = result["entity_status"]
                registration_status = result["registration_status"]
                flag = result["flag"]
            except Exception:
                flag = "ERROR"

        # If not OK and we have an entity name, try to find a replacement
        if flag != "OK" and entity_name:
            try:
                lookup_result = await limiter.execute(lookup_lei, entity_name)
                best = _best_match(lookup_result)
                if best:
                    suggested_lei = best["lei"]
                    suggested_legal_name = best["legal_name"]
                    suggested_confidence = best["confidence"]
                elif lookup_result["results"]:
                    top = lookup_result["results"][0]
                    suggested_lei = top["lei"]
                    suggested_legal_name = top["legal_name"]
                    suggested_confidence = top["confidence"]
            except Exception:
                pass  # No suggestion available

        # Write to DB
        database.update_validation_result(i, {
            "entity_name": entity_name,
            "provided_lei": lei_code,
            "entity_status": entity_status,
            "registration_status": registration_status,
            "flag": flag,
            "suggested_lei": suggested_lei,
            "suggested_legal_name": suggested_legal_name,
            "suggested_confidence": suggested_confidence,
        }, session_id=session_id)

        row_result = ValidationRowResult(
            index=i,
            entity_name=entity_name,
            provided_lei=lei_code,
            entity_status=entity_status,
            registration_status=registration_status,
            flag=flag,
            suggested_lei=suggested_lei,
            suggested_legal_name=suggested_legal_name,
            suggested_confidence=suggested_confidence,
        )

        with count_lock:
            completed_count += 1
            is_done = completed_count == total

        progress = ValidationProgress(
            index=i,
            total=total,
            entity_name=entity_name,
            row=row_result,
            done=is_done,
        )
        await queue.put(progress)

    tasks = [process_row(i, row) for i, row in enumerate(rows)]
    await asyncio.gather(*tasks)
