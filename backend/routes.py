"""API route handlers for the LEI Lookup web app."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import sys

import requests
from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from rapidfuzz import fuzz

from backend import database
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

# In-memory state
_input_headers: list[str] = []
_rows: list[dict] = []
_name_column: str = ""
_running: bool = False

# Shared cache manager
cache = CacheManager()

# Import detect helper
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lei_lookup import _detect_name_column

LEI_FIELDS = [
    "lei", "lei_legal_name", "lei_jurisdiction", "lei_status",
    "lei_confidence", "lei_match_status", "lei_candidates",
]


def _build_summary() -> dict:
    s = database.compute_summary()
    s["cache_size"] = cache.size
    return s


@router.post("/upload", response_model=UploadResponse)
async def upload_csv(file: UploadFile = File(...)):
    global _input_headers, _rows, _name_column, _running

    if _running:
        return UploadResponse(headers=[], row_count=0, detected_column=None)

    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    _input_headers = list(reader.fieldnames or [])
    _rows = list(reader)

    detected = _detect_name_column(_input_headers)
    _name_column = detected or ""

    # Store rows in database
    database.clear_results()
    db_rows = []
    for row in _rows:
        r = dict(row)
        r["_entity_name"] = r.get(_name_column, "").strip() if _name_column else ""
        db_rows.append(r)
    database.bulk_insert_results(db_rows)

    return UploadResponse(
        headers=_input_headers,
        row_count=len(_rows),
        detected_column=detected,
    )


@router.get("/lookup")
async def start_lookup(column: str = Query(...)):
    global _running, _name_column

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
    _running = True

    queue: asyncio.Queue = asyncio.Queue()

    async def event_generator():
        global _running

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

                data = progress.model_dump_json()
                yield f"data: {data}\n\n"

                if progress.done:
                    break
        except asyncio.CancelledError:
            task.cancel()
            raise
        finally:
            _running = False
            summary = _build_summary()
            yield f"event: summary\ndata: {json.dumps(summary)}\n\n"

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
async def get_results(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    status: str | None = Query(None),
):
    result = database.get_results_page(page, page_size, status)
    return {
        "rows": result["rows"],
        "total": result["total"],
        "page": result["page"],
        "page_size": result["page_size"],
        "summary": _build_summary(),
    }


@router.put("/results/{row_id}")
async def update_cell(row_id: int, update: CellUpdate):
    row_data = database.get_result_by_id(row_id)
    if row_data is None:
        return {"error": "Invalid index"}

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
    old_value = row_data.get(attr, "")

    db_update = {attr: update.value}

    # If LEI changed, don't mark reviewed yet — frontend should call validate-lei
    if update.field != "lei" or update.value == old_value:
        db_update["match_status"] = "REVIEWED"
        # Cache the reviewed row
        entity_name = row_data.get("entity_name", "")
        if entity_name:
            cache.cache_row(entity_name, {
                "lei": update.value if attr == "lei" else row_data.get("lei", ""),
                "legal_name": update.value if attr == "legal_name" else row_data.get("legal_name", ""),
                "jurisdiction": update.value if attr == "jurisdiction" else row_data.get("jurisdiction", ""),
                "status": update.value if attr == "status" else row_data.get("status", ""),
                "confidence": update.value if attr == "confidence" else row_data.get("confidence", ""),
                "match_status": "REVIEWED",
            })
            cache.flush()

    database.update_result(row_id, db_update)
    updated_row = database.get_result_by_id(row_id)
    return {"row": updated_row, "summary": _build_summary()}


@router.put("/results/{row_id}/validate-lei")
async def validate_lei(row_id: int):
    row_data = database.get_result_by_id(row_id)
    if row_data is None:
        return ValidateLeiResponse(valid=False, message="Invalid index")

    lei_code = row_data.get("lei", "")
    entity_name = row_data.get("entity_name", "")

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

        score = (
            fuzz.token_sort_ratio(entity_name.lower(), legal_name.lower())
            if entity_name and legal_name
            else 0
        )
        needs_confirmation = score < 50

        database.update_result(row_id, {
            "legal_name": legal_name,
            "jurisdiction": jurisdiction,
            "status": status,
            "match_status": "REVIEWED",
        })

        if entity_name:
            cache.cache_row(entity_name, {
                "lei": lei_code,
                "legal_name": legal_name,
                "jurisdiction": jurisdiction,
                "status": status,
                "confidence": row_data.get("confidence", ""),
                "match_status": "REVIEWED",
            })
            cache.flush()

        return ValidateLeiResponse(
            valid=True,
            legal_name=legal_name,
            jurisdiction=jurisdiction,
            status=status,
            needs_confirmation=needs_confirmation,
            message="LEI validated successfully." if not needs_confirmation
            else f"GLEIF legal name '{legal_name}' does not closely match entity '{entity_name}'.",
        )
    except Exception as exc:
        return ValidateLeiResponse(valid=False, message=f"Validation error: {exc}")


@router.post("/confirm-all")
async def confirm_all():
    count = database.confirm_reviewed_rows()
    # Also update cache for confirmed rows
    return {"confirmed": count, "summary": _build_summary()}


@router.get("/export")
async def export_csv():
    all_rows = database.get_all_results_for_export()
    if not all_rows:
        return StreamingResponse(
            iter([""]),
            media_type="text/csv",
        )

    output = io.StringIO()
    output_headers = list(_input_headers) + LEI_FIELDS
    writer = csv.DictWriter(output, fieldnames=output_headers, extrasaction="ignore")
    writer.writeheader()

    for row_data in all_rows:
        original = row_data.get("original", {})
        out = dict(original)
        out["lei"] = row_data["lei"]
        out["lei_legal_name"] = row_data["legal_name"]
        out["lei_jurisdiction"] = row_data["jurisdiction"]
        out["lei_status"] = row_data["status"]
        out["lei_confidence"] = row_data["confidence"]
        out["lei_match_status"] = row_data["match_status"]
        out["lei_candidates"] = row_data["candidates"]
        writer.writerow(out)

    csv_content = output.getvalue()
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=lei_results.csv"},
    )


@router.get("/cache/summary")
async def cache_summary():
    return {"cache_size": cache.size, "summary": _build_summary()}


@router.delete("/cache")
async def clear_cache():
    cache.clear()
    return {"message": "Cache cleared.", "summary": _build_summary()}
