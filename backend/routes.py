"""API route handlers for the LEI Lookup web app."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import threading

import requests
from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from rapidfuzz import fuzz

from backend.cache import CacheManager
from backend.models import (
    CellUpdate,
    RowResult,
    Summary,
    UploadResponse,
    ValidateLeiResponse,
)
from backend.worker import run_lookup

router = APIRouter(prefix="/api")

# In-memory state (single-user, no DB needed)
_input_headers: list[str] = []
_rows: list[dict] = []
_result_rows: list[RowResult] = []
_name_column: str = ""
_running: bool = False

# Shared cache manager
cache = CacheManager()

# Import detect helper
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lei_lookup import _detect_name_column

_STATUS_SORT_ORDER = {
    "AUTO-MATCHED": 0,
    "CONFIRMED": 1,
    "REVIEWED": 2,
    "REVIEW NEEDED": 3,
    "NO MATCH": 4,
}

LEI_FIELDS = [
    "lei", "lei_legal_name", "lei_jurisdiction", "lei_status",
    "lei_confidence", "lei_match_status", "lei_candidates",
]


def _compute_summary() -> Summary:
    auto = sum(1 for r in _result_rows if r.match_status == "AUTO-MATCHED")
    confirmed = sum(1 for r in _result_rows if r.match_status == "CONFIRMED")
    reviewed = sum(1 for r in _result_rows if r.match_status == "REVIEWED")
    review = sum(1 for r in _result_rows if r.match_status == "REVIEW NEEDED")
    none_ = sum(1 for r in _result_rows if r.match_status == "NO MATCH")
    errs = sum(1 for r in _result_rows if r.match_status.startswith("ERROR"))
    return Summary(
        auto_matched=auto,
        confirmed=confirmed,
        reviewed=reviewed,
        review_needed=review,
        no_match=none_,
        errors=errs,
        total=len(_result_rows),
        cache_size=cache.size,
    )


@router.post("/upload", response_model=UploadResponse)
async def upload_csv(file: UploadFile = File(...)):
    global _input_headers, _rows, _result_rows, _name_column, _running

    if _running:
        return UploadResponse(headers=[], row_count=0, detected_column=None)

    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    _input_headers = list(reader.fieldnames or [])
    _rows = list(reader)
    _result_rows = []

    detected = _detect_name_column(_input_headers)
    _name_column = detected or ""

    return UploadResponse(
        headers=_input_headers,
        row_count=len(_rows),
        detected_column=detected,
    )


@router.get("/lookup")
async def start_lookup(column: str = Query(...)):
    global _result_rows, _running, _name_column

    if _running:
        return StreamingResponse(
            iter(["data: {\"error\": \"Lookup already running\"}\n\n"]),
            media_type="text/event-stream",
        )

    if not _rows:
        return StreamingResponse(
            iter(["data: {\"error\": \"No CSV uploaded\"}\n\n"]),
            media_type="text/event-stream",
        )

    _name_column = column
    _result_rows = []
    _running = True

    queue: asyncio.Queue = asyncio.Queue()

    async def event_generator():
        global _result_rows, _running

        task = asyncio.create_task(run_lookup(_rows, column, cache, queue))

        try:
            while True:
                try:
                    progress = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if task.done():
                        break
                    yield ":\n\n"  # keepalive
                    continue

                _result_rows.append(progress.row)
                data = progress.model_dump_json()
                yield f"data: {data}\n\n"

                if progress.done:
                    break
        except asyncio.CancelledError:
            task.cancel()
            raise
        finally:
            _running = False
            # Send summary as final event
            summary = _compute_summary()
            yield f"event: summary\ndata: {summary.model_dump_json()}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/results")
async def get_results():
    sorted_rows = sorted(
        _result_rows,
        key=lambda r: _STATUS_SORT_ORDER.get(r.match_status, 5),
    )
    return {
        "rows": [r.model_dump() for r in sorted_rows],
        "summary": _compute_summary().model_dump(),
    }


@router.put("/results/{index}")
async def update_cell(index: int, update: CellUpdate):
    if index < 0 or index >= len(_result_rows):
        return {"error": "Invalid index"}

    row = _result_rows[index]
    field_map = {
        "lei": "lei",
        "legal_name": "legal_name",
        "jurisdiction": "jurisdiction",
        "status": "status",
        "confidence": "confidence",
    }

    if update.field not in field_map:
        return {"error": f"Invalid field: {update.field}"}

    attr = field_map[update.field]
    old_value = getattr(row, attr)
    setattr(row, attr, update.value)

    # If LEI changed, don't mark reviewed yet — frontend should call validate-lei
    if update.field != "lei" or update.value == old_value:
        row.match_status = "REVIEWED"
        # Cache the reviewed row
        if row.entity_name:
            cache.cache_row(row.entity_name, {
                "lei": row.lei,
                "lei_legal_name": row.legal_name,
                "lei_jurisdiction": row.jurisdiction,
                "lei_status": row.status,
                "lei_confidence": row.confidence,
                "lei_match_status": row.match_status,
            })

    return {"row": row.model_dump(), "summary": _compute_summary().model_dump()}


@router.put("/results/{index}/validate-lei")
async def validate_lei(index: int):
    if index < 0 or index >= len(_result_rows):
        return ValidateLeiResponse(valid=False, message="Invalid index")

    row = _result_rows[index]
    lei_code = row.lei

    if not lei_code:
        return ValidateLeiResponse(valid=False, message="No LEI to validate")

    try:
        resp = requests.get(
            f"https://api.gleif.org/api/v1/lei-records/{lei_code}",
            timeout=15,
        )
        if resp.status_code != 200:
            return ValidateLeiResponse(
                valid=False,
                message=f"LEI '{lei_code}' not found (HTTP {resp.status_code}).",
            )

        data = resp.json().get("data", {})
        attr = data.get("attributes", {}).get("entity", {})
        legal_name = attr.get("legalName", {}).get("name", "")
        jurisdiction = attr.get("jurisdiction", "")
        reg = data.get("attributes", {}).get("registration", {})
        status = reg.get("status", "")

        # Check if the GLEIF legal name matches the entity
        score = (
            fuzz.token_sort_ratio(row.entity_name.lower(), legal_name.lower())
            if row.entity_name and legal_name
            else 0
        )
        needs_confirmation = score < 50

        # Apply the validated data
        row.legal_name = legal_name
        row.jurisdiction = jurisdiction
        row.status = status
        row.match_status = "REVIEWED"

        # Cache the reviewed row
        if row.entity_name:
            cache.cache_row(row.entity_name, {
                "lei": row.lei,
                "lei_legal_name": row.legal_name,
                "lei_jurisdiction": row.jurisdiction,
                "lei_status": row.status,
                "lei_confidence": row.confidence,
                "lei_match_status": row.match_status,
            })

        return ValidateLeiResponse(
            valid=True,
            legal_name=legal_name,
            jurisdiction=jurisdiction,
            status=status,
            needs_confirmation=needs_confirmation,
            message="LEI validated successfully." if not needs_confirmation
            else f"GLEIF legal name '{legal_name}' does not closely match entity '{row.entity_name}'.",
        )
    except Exception as exc:
        return ValidateLeiResponse(valid=False, message=f"Validation error: {exc}")


@router.post("/confirm-all")
async def confirm_all():
    count = 0
    for row in _result_rows:
        if row.match_status == "REVIEWED":
            row.match_status = "CONFIRMED"
            count += 1
            if row.entity_name:
                cache.cache_row(row.entity_name, {
                    "lei": row.lei,
                    "lei_legal_name": row.legal_name,
                    "lei_jurisdiction": row.jurisdiction,
                    "lei_status": row.status,
                    "lei_confidence": row.confidence,
                    "lei_match_status": row.match_status,
                })
    return {"confirmed": count, "summary": _compute_summary().model_dump()}


@router.get("/export")
async def export_csv():
    if not _result_rows:
        return StreamingResponse(
            iter([""]),
            media_type="text/csv",
        )

    output = io.StringIO()
    output_headers = list(_input_headers) + LEI_FIELDS
    writer = csv.DictWriter(output, fieldnames=output_headers, extrasaction="ignore")
    writer.writeheader()

    sorted_rows = sorted(
        _result_rows,
        key=lambda r: _STATUS_SORT_ORDER.get(r.match_status, 5),
    )

    for row in sorted_rows:
        out = dict(row.original)
        out["lei"] = row.lei
        out["lei_legal_name"] = row.legal_name
        out["lei_jurisdiction"] = row.jurisdiction
        out["lei_status"] = row.status
        out["lei_confidence"] = row.confidence
        out["lei_match_status"] = row.match_status
        out["lei_candidates"] = row.candidates
        writer.writerow(out)

    csv_content = output.getvalue()
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=lei_results.csv"},
    )


@router.get("/cache/summary")
async def cache_summary():
    return {"cache_size": cache.size, "summary": _compute_summary().model_dump()}


@router.delete("/cache")
async def clear_cache():
    cache.clear()
    return {"message": "Cache cleared.", "summary": _compute_summary().model_dump()}
