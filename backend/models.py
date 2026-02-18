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


# ---------------------------------------------------------------------------
# LEI Validation (bulk LEI checking) models
# ---------------------------------------------------------------------------

class ValidationRowResult(BaseModel):
    """A single row result from the LEI validation workflow."""
    index: int
    entity_name: str = ""
    provided_lei: str = ""
    entity_status: str = ""
    registration_status: str = ""
    flag: str = ""  # OK, LAPSED, INVALID, NOT_FOUND, ERROR
    suggested_lei: str = ""
    suggested_legal_name: str = ""
    suggested_confidence: str = ""


class ValidationProgress(BaseModel):
    """SSE event payload during validation."""
    index: int
    total: int
    entity_name: str = ""
    row: ValidationRowResult
    done: bool = False


class ValidationSummary(BaseModel):
    ok: int = 0
    lapsed: int = 0
    invalid: int = 0
    not_found: int = 0
    errors: int = 0
    total: int = 0
