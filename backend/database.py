"""SQLite database layer for LEI Lookup — results and cache storage."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any

_DATA_DIR = os.environ.get(
    "DATA_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
_DB_PATH = os.path.join(_DATA_DIR, "lei_lookup.db")

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection with WAL mode."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return conn


def init_db() -> None:
    """Create tables and indexes if they don't exist."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL DEFAULT '',
            row_index INTEGER NOT NULL DEFAULT 0,
            entity_name TEXT DEFAULT '',
            lei TEXT DEFAULT '',
            legal_name TEXT DEFAULT '',
            jurisdiction TEXT DEFAULT '',
            status TEXT DEFAULT '',
            confidence TEXT DEFAULT '',
            match_status TEXT DEFAULT '',
            candidates TEXT DEFAULT '',
            original_json TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_results_match_status ON results(match_status);
        CREATE INDEX IF NOT EXISTS idx_results_session ON results(session_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_results_session_row ON results(session_id, row_index);

        CREATE TABLE IF NOT EXISTS validation_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL DEFAULT '',
            row_index INTEGER NOT NULL DEFAULT 0,
            entity_name TEXT DEFAULT '',
            provided_lei TEXT DEFAULT '',
            entity_status TEXT DEFAULT '',
            registration_status TEXT DEFAULT '',
            flag TEXT DEFAULT '',
            suggested_lei TEXT DEFAULT '',
            suggested_legal_name TEXT DEFAULT '',
            suggested_confidence TEXT DEFAULT '',
            original_json TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_vr_session ON validation_results(session_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_vr_session_row ON validation_results(session_id, row_index);

        CREATE TABLE IF NOT EXISTS cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_key TEXT NOT NULL UNIQUE,
            lei TEXT DEFAULT '',
            legal_name TEXT DEFAULT '',
            jurisdiction TEXT DEFAULT '',
            status TEXT DEFAULT '',
            confidence TEXT DEFAULT '',
            match_status TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_cache_company_key ON cache(company_key);
    """)
    conn.commit()

    # Migrate existing tables that lack the new columns
    try:
        conn.execute("SELECT session_id FROM results LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE results ADD COLUMN session_id TEXT NOT NULL DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_results_session ON results(session_id)")
        conn.commit()
    try:
        conn.execute("SELECT row_index FROM results LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE results ADD COLUMN row_index INTEGER NOT NULL DEFAULT 0")
        # Backfill row_index from id for existing rows
        conn.execute("UPDATE results SET row_index = id WHERE row_index = 0")
        conn.commit()


# ---------------------------------------------------------------------------
# Results CRUD
# ---------------------------------------------------------------------------

def clear_results(session_id: str) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM results WHERE session_id = ?", (session_id,))
    conn.commit()


def bulk_insert_results(rows: list[dict], session_id: str) -> None:
    """Insert rows from CSV upload (before lookup). Each dict has original CSV fields."""
    conn = _get_conn()
    # Use a session-scoped row index (0-based within the session)
    conn.executemany(
        """INSERT INTO results (session_id, row_index, entity_name, original_json)
           VALUES (?, ?, ?, ?)""",
        [(session_id, i, row.get("_entity_name", ""), json.dumps(row, default=str)) for i, row in enumerate(rows)],
    )
    conn.commit()


def _build_update(data: dict[str, Any]) -> tuple[list[str], list[Any]]:
    """Build SET clause fragments from a data dict."""
    fields = []
    values = []
    for key in ("entity_name", "lei", "legal_name", "jurisdiction", "status",
                "confidence", "match_status", "candidates", "original_json"):
        if key in data:
            fields.append(f"{key} = ?")
            values.append(data[key])
    return fields, values


def update_result(row_index: int, data: dict[str, Any], session_id: str) -> None:
    """Update a result row by (session_id, row_index) — used by the worker."""
    conn = _get_conn()
    fields, values = _build_update(data)
    if not fields:
        return
    values.extend([session_id, row_index])
    conn.execute(
        f"UPDATE results SET {', '.join(fields)} WHERE session_id = ? AND row_index = ?",
        values,
    )
    conn.commit()


def update_result_by_id(row_id: int, data: dict[str, Any]) -> None:
    """Update a result row by primary key — used by route handlers."""
    conn = _get_conn()
    fields, values = _build_update(data)
    if not fields:
        return
    values.append(row_id)
    conn.execute(f"UPDATE results SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()


def get_result_by_id(row_id: int) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM results WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def get_results_page(
    page: int = 1,
    page_size: int = 50,
    status_filter: str | None = None,
    session_id: str = "",
) -> dict:
    """Return a page of results with total count."""
    conn = _get_conn()
    conditions = ["session_id = ?"]
    params: list[Any] = [session_id]
    if status_filter:
        conditions.append("match_status = ?")
        params.append(status_filter)
    where = "WHERE " + " AND ".join(conditions)

    # Get total count
    count_row = conn.execute(f"SELECT COUNT(*) FROM results {where}", params).fetchone()
    total = count_row[0]

    # Status sort order via CASE expression, then confidence descending
    status_order = """
        CASE match_status
            WHEN 'AUTO-MATCHED' THEN 0
            WHEN 'CONFIRMED' THEN 1
            WHEN 'REVIEWED' THEN 2
            WHEN 'REVIEW NEEDED' THEN 3
            WHEN 'NO MATCH' THEN 4
            ELSE 5
        END
    """
    confidence_order = """
        CASE confidence
            WHEN 'high' THEN 0
            WHEN 'medium' THEN 1
            WHEN 'low' THEN 2
            ELSE 3
        END
    """
    offset = (page - 1) * page_size
    params.extend([page_size, offset])
    rows = conn.execute(
        f"SELECT * FROM results {where} ORDER BY {status_order}, {confidence_order}, id LIMIT ? OFFSET ?",
        params,
    ).fetchall()

    return {
        "rows": [_row_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_all_results_for_export(session_id: str = "") -> list[dict]:
    """Return all results in table view order (status, then confidence desc, then id)."""
    conn = _get_conn()
    status_order = """
        CASE match_status
            WHEN 'AUTO-MATCHED' THEN 0
            WHEN 'CONFIRMED' THEN 1
            WHEN 'REVIEWED' THEN 2
            WHEN 'REVIEW NEEDED' THEN 3
            WHEN 'NO MATCH' THEN 4
            ELSE 5
        END
    """
    confidence_order = """
        CASE confidence
            WHEN 'high' THEN 0
            WHEN 'medium' THEN 1
            WHEN 'low' THEN 2
            ELSE 3
        END
    """
    rows = conn.execute(
        f"SELECT * FROM results WHERE session_id = ? ORDER BY {status_order}, {confidence_order}, id",
        (session_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def compute_summary(session_id: str = "") -> dict:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT match_status, COUNT(*) as cnt FROM results WHERE session_id = ? GROUP BY match_status",
        (session_id,),
    ).fetchall()
    summary = {
        "auto_matched": 0,
        "confirmed": 0,
        "reviewed": 0,
        "review_needed": 0,
        "no_match": 0,
        "errors": 0,
        "total": 0,
    }
    for row in rows:
        ms = row["match_status"]
        cnt = row["cnt"]
        summary["total"] += cnt
        if ms == "AUTO-MATCHED":
            summary["auto_matched"] = cnt
        elif ms == "CONFIRMED":
            summary["confirmed"] = cnt
        elif ms == "REVIEWED":
            summary["reviewed"] = cnt
        elif ms == "REVIEW NEEDED":
            summary["review_needed"] = cnt
        elif ms == "NO MATCH":
            summary["no_match"] = cnt
        elif ms.startswith("ERROR"):
            summary["errors"] += cnt
    return summary


def confirm_reviewed_rows(session_id: str = "") -> int:
    """Mark all REVIEWED rows as CONFIRMED. Returns count affected."""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE results SET match_status = 'CONFIRMED' WHERE match_status = 'REVIEWED' AND session_id = ?",
        (session_id,),
    )
    conn.commit()
    return cur.rowcount


def results_count(session_id: str = "") -> int:
    conn = _get_conn()
    row = conn.execute("SELECT COUNT(*) FROM results WHERE session_id = ?", (session_id,)).fetchone()
    return row[0]


# ---------------------------------------------------------------------------
# Cache CRUD
# ---------------------------------------------------------------------------

def cache_lookup_exact(company_key: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM cache WHERE company_key = ?", (company_key,)
    ).fetchone()
    if row is None:
        return None
    return {
        "lei": row["lei"],
        "legal_name": row["legal_name"],
        "jurisdiction": row["jurisdiction"],
        "status": row["status"],
        "confidence": row["confidence"],
        "match_status": row["match_status"],
    }


def cache_get_all_keys() -> list[str]:
    """Return all cache keys for fuzzy matching."""
    conn = _get_conn()
    rows = conn.execute("SELECT company_key FROM cache").fetchall()
    return [r["company_key"] for r in rows]


def cache_get_entry(company_key: str) -> dict | None:
    return cache_lookup_exact(company_key)


def cache_insert_batch(entries: list[tuple[str, dict]]) -> None:
    """Insert or replace a batch of cache entries."""
    conn = _get_conn()
    conn.executemany(
        """INSERT OR REPLACE INTO cache (company_key, lei, legal_name, jurisdiction, status, confidence, match_status)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                key,
                d.get("lei", ""),
                d.get("legal_name", ""),
                d.get("jurisdiction", ""),
                d.get("status", ""),
                d.get("confidence", ""),
                d.get("match_status", ""),
            )
            for key, d in entries
        ],
    )
    conn.commit()


def cache_insert_one(company_key: str, data: dict) -> None:
    cache_insert_batch([(company_key, data)])


def cache_clear() -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM cache")
    conn.commit()


def cache_count() -> int:
    conn = _get_conn()
    row = conn.execute("SELECT COUNT(*) FROM cache").fetchone()
    return row[0]


def cache_delete_keys(keys: list[str]) -> None:
    """Delete specific keys from cache (for validation removal)."""
    conn = _get_conn()
    conn.executemany("DELETE FROM cache WHERE company_key = ?", [(k,) for k in keys])
    conn.commit()


def migrate_json_cache(json_path: str) -> int:
    """Migrate lei_cache.json into SQLite cache table. Returns count migrated."""
    if not os.path.exists(json_path):
        return 0
    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return 0
    if not data:
        return 0
    entries = []
    for key, val in data.items():
        entries.append((key, val))
    cache_insert_batch(entries)
    # Rename old file to .bak
    bak = json_path + ".bak"
    try:
        os.rename(json_path, bak)
    except OSError:
        pass
    return len(entries)


# ---------------------------------------------------------------------------
# Validation Results CRUD
# ---------------------------------------------------------------------------

def clear_validation_results(session_id: str) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM validation_results WHERE session_id = ?", (session_id,))
    conn.commit()


def bulk_insert_validation_results(rows: list[dict], session_id: str) -> None:
    """Insert rows from CSV upload for validation. Each dict has original CSV fields."""
    conn = _get_conn()
    conn.executemany(
        """INSERT INTO validation_results
           (session_id, row_index, entity_name, provided_lei, original_json)
           VALUES (?, ?, ?, ?, ?)""",
        [
            (session_id, i, row.get("_entity_name", ""), row.get("_provided_lei", ""), json.dumps(row, default=str))
            for i, row in enumerate(rows)
        ],
    )
    conn.commit()


def update_validation_result(row_index: int, data: dict[str, Any], session_id: str) -> None:
    """Update a validation result row by (session_id, row_index)."""
    conn = _get_conn()
    fields = []
    values = []
    for key in ("entity_name", "provided_lei", "entity_status", "registration_status",
                "flag", "suggested_lei", "suggested_legal_name", "suggested_confidence"):
        if key in data:
            fields.append(f"{key} = ?")
            values.append(data[key])
    if not fields:
        return
    values.extend([session_id, row_index])
    conn.execute(
        f"UPDATE validation_results SET {', '.join(fields)} WHERE session_id = ? AND row_index = ?",
        values,
    )
    conn.commit()


_FLAG_ORDER = """
    CASE flag
        WHEN 'LAPSED' THEN 0
        WHEN 'INVALID' THEN 1
        WHEN 'NOT_FOUND' THEN 2
        WHEN 'ERROR' THEN 3
        WHEN 'OK' THEN 4
        ELSE 5
    END
"""


def get_validation_results_page(
    page: int = 1,
    page_size: int = 50,
    flag_filter: str | None = None,
    session_id: str = "",
) -> dict:
    conn = _get_conn()
    conditions = ["session_id = ?"]
    params: list[Any] = [session_id]
    if flag_filter:
        conditions.append("flag = ?")
        params.append(flag_filter)
    where = "WHERE " + " AND ".join(conditions)

    count_row = conn.execute(f"SELECT COUNT(*) FROM validation_results {where}", params).fetchone()
    total = count_row[0]

    offset = (page - 1) * page_size
    params.extend([page_size, offset])
    rows = conn.execute(
        f"SELECT * FROM validation_results {where} ORDER BY {_FLAG_ORDER}, id LIMIT ? OFFSET ?",
        params,
    ).fetchall()

    return {
        "rows": [_vr_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_all_validation_results_for_export(session_id: str = "") -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        f"SELECT * FROM validation_results WHERE session_id = ? ORDER BY {_FLAG_ORDER}, id",
        (session_id,),
    ).fetchall()
    return [_vr_to_dict(r) for r in rows]


def compute_validation_summary(session_id: str = "") -> dict:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT flag, COUNT(*) as cnt FROM validation_results WHERE session_id = ? GROUP BY flag",
        (session_id,),
    ).fetchall()
    summary = {"ok": 0, "lapsed": 0, "invalid": 0, "not_found": 0, "errors": 0, "total": 0}
    for row in rows:
        flag = row["flag"]
        cnt = row["cnt"]
        summary["total"] += cnt
        if flag == "OK":
            summary["ok"] = cnt
        elif flag == "LAPSED":
            summary["lapsed"] = cnt
        elif flag == "INVALID":
            summary["invalid"] = cnt
        elif flag == "NOT_FOUND":
            summary["not_found"] = cnt
        elif flag == "ERROR":
            summary["errors"] += cnt
    return summary


def _vr_to_dict(row: sqlite3.Row) -> dict:
    """Convert a validation_results Row to a dict."""
    return {
        "index": row["row_index"],
        "entity_name": row["entity_name"],
        "provided_lei": row["provided_lei"],
        "entity_status": row["entity_status"],
        "registration_status": row["registration_status"],
        "flag": row["flag"],
        "suggested_lei": row["suggested_lei"],
        "suggested_legal_name": row["suggested_legal_name"],
        "suggested_confidence": row["suggested_confidence"],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a results Row to a RowResult-compatible dict."""
    original = {}
    try:
        original = json.loads(row["original_json"])
    except (json.JSONDecodeError, TypeError):
        pass
    return {
        "index": row["id"],
        "entity_name": row["entity_name"],
        "lei": row["lei"],
        "legal_name": row["legal_name"],
        "jurisdiction": row["jurisdiction"],
        "status": row["status"],
        "confidence": row["confidence"],
        "match_status": row["match_status"],
        "candidates": row["candidates"],
        "original": original,
    }
