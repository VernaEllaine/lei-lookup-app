"""Cache management for LEI lookups — backed by SQLite via database.py."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from rapidfuzz import process

from backend import database

GLEIF_BASE = "https://api.gleif.org/api/v1"

_CACHE_JSON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lei_cache.json",
)

_BUFFER_FLUSH_SIZE = 100


class CacheManager:
    def __init__(self) -> None:
        self._write_buffer: list[tuple[str, dict]] = []
        self._buffer_lock = threading.Lock()
        self._validating = False
        self._validation_progress: str = ""
        self._keys_cache: list[str] | None = None
        self._keys_lock = threading.Lock()

    @property
    def size(self) -> int:
        return database.cache_count()

    def load(self) -> None:
        database.init_db()
        # One-time migration from JSON cache
        if os.path.exists(_CACHE_JSON_PATH):
            count = database.migrate_json_cache(_CACHE_JSON_PATH)
            if count > 0:
                self._invalidate_keys_cache()

    def cache_row(self, company: str, row_data: dict) -> None:
        key = company.strip().lower()
        if not key:
            return
        data = {
            "lei": row_data.get("lei", row_data.get("lei", "")),
            "legal_name": row_data.get("lei_legal_name", row_data.get("legal_name", "")),
            "jurisdiction": row_data.get("lei_jurisdiction", row_data.get("jurisdiction", "")),
            "status": row_data.get("lei_status", row_data.get("status", "")),
            "confidence": row_data.get("lei_confidence", row_data.get("confidence", "")),
            "match_status": row_data.get("lei_match_status", row_data.get("match_status", "")),
        }
        with self._buffer_lock:
            self._write_buffer.append((key, data))
            if len(self._write_buffer) >= _BUFFER_FLUSH_SIZE:
                self._flush_locked()

    def flush(self) -> None:
        with self._buffer_lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        """Flush the write buffer to DB. Must be called with _buffer_lock held."""
        if not self._write_buffer:
            return
        database.cache_insert_batch(self._write_buffer)
        self._write_buffer.clear()
        self._invalidate_keys_cache()

    def fuzzy_lookup(self, company: str) -> dict | None:
        key = company.strip().lower()
        if not key:
            return None
        # Exact match first
        exact = database.cache_lookup_exact(key)
        if exact is not None:
            return exact
        # Also check the write buffer for exact match
        with self._buffer_lock:
            for buf_key, buf_data in self._write_buffer:
                if buf_key == key:
                    return buf_data.copy()
        # Fuzzy match against all keys
        all_keys = self._get_keys()
        if not all_keys:
            return None
        result = process.extractOne(key, all_keys, score_cutoff=95)
        if result is not None:
            matched_key, _score, _ = result
            entry = database.cache_get_entry(matched_key)
            if entry is not None:
                return entry
        return None

    def clear(self) -> None:
        with self._buffer_lock:
            self._write_buffer.clear()
        database.cache_clear()
        self._invalidate_keys_cache()

    def _get_keys(self) -> list[str]:
        with self._keys_lock:
            if self._keys_cache is None:
                self._keys_cache = database.cache_get_all_keys()
            return self._keys_cache

    def _invalidate_keys_cache(self) -> None:
        with self._keys_lock:
            self._keys_cache = None

    def validate(self, on_progress=None) -> str:
        """Validate all cached LEIs against GLEIF. Returns summary message."""
        self._validating = True
        self.flush()

        all_keys = database.cache_get_all_keys()
        entries = []
        for key in all_keys:
            entry = database.cache_get_entry(key)
            if entry and entry.get("lei"):
                entries.append((key, entry))

        total = len(entries)
        if total == 0:
            self._validating = False
            return "Cache empty, nothing to validate."

        BATCH_SIZE = 20
        batches: list[list[tuple[str, dict]]] = []
        for i in range(0, total, BATCH_SIZE):
            batches.append(entries[i : i + BATCH_SIZE])

        removed = 0
        validated = 0

        def _fetch_batch(batch: list[tuple[str, dict]]) -> set[str]:
            lei_codes = [entry["lei"] for _, entry in batch]
            lei_filter = ",".join(lei_codes)
            resp = requests.get(
                f"{GLEIF_BASE}/lei-records",
                params={
                    "filter[lei]": lei_filter,
                    "page[size]": str(BATCH_SIZE),
                },
                timeout=30,
            )
            active: set[str] = set()
            if resp.status_code == 200:
                for record in resp.json().get("data", []):
                    attrs = record.get("attributes", {})
                    entity_status = attrs.get("entity", {}).get("status", "")
                    reg_status = attrs.get("registration", {}).get("status", "")
                    if entity_status == "ACTIVE" or reg_status == "ISSUED":
                        active.add(record.get("id", ""))
            return active

        with ThreadPoolExecutor(max_workers=5) as pool:
            future_to_batch = {
                pool.submit(_fetch_batch, batch): batch
                for batch in batches
            }
            keys_to_remove: list[str] = []
            for future in as_completed(future_to_batch):
                if not self._validating:
                    break
                batch = future_to_batch[future]
                try:
                    active_leis = future.result()
                    for key, entry in batch:
                        if entry["lei"] not in active_leis:
                            keys_to_remove.append(key)
                            removed += 1
                except Exception:
                    for key, _ in batch:
                        keys_to_remove.append(key)
                        removed += 1
                validated += len(batch)
                self._validation_progress = f"Validating cache: {validated} / {total}..."
                if on_progress:
                    on_progress(self._validation_progress)

        if keys_to_remove:
            database.cache_delete_keys(keys_to_remove)
            self._invalidate_keys_cache()

        self._validating = False

        if removed > 0:
            return f"Cache validated: {removed} inactive entr{'y' if removed == 1 else 'ies'} removed."
        return "Cache validated: all entries active."
