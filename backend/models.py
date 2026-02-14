"""Pydantic models for the LEI Lookup API."""

from __future__ import annotations

from pydantic import BaseModel


class LeiResult(BaseModel):
    lei: str = ""
    legal_name: str = ""
    jurisdiction: str = ""
    status: str = ""
    confidence: str = ""
    match_status: str = ""
    candidates: str = ""


class RowResult(BaseModel):
    """A single result row: original CSV data + LEI fields."""
    index: int
    entity_name: str = ""
    lei: str = ""
    legal_name: str = ""
    jurisdiction: str = ""
    status: str = ""
    confidence: str = ""
    match_status: str = ""
    candidates: str = ""
    original: dict = {}


class LookupProgress(BaseModel):
    """SSE event payload during lookup."""
    index: int
    total: int
    company: str
    row: RowResult
    done: bool = False


class Summary(BaseModel):
    auto_matched: int = 0
    confirmed: int = 0
    reviewed: int = 0
    review_needed: int = 0
    no_match: int = 0
    errors: int = 0
    total: int = 0
    cache_size: int = 0


class UploadResponse(BaseModel):
    headers: list[str]
    row_count: int
    detected_column: str | None = None


class CellUpdate(BaseModel):
    field: str
    value: str


class ValidateLeiResponse(BaseModel):
    valid: bool
    legal_name: str = ""
    jurisdiction: str = ""
    status: str = ""
    needs_confirmation: bool = False
    message: str = ""
