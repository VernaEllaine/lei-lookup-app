"""Cache management for LEI lookups — mirrors lei_app.py cache logic."""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from rapidfuzz import process


_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lei_cache.json",
)

GLEIF_BASE = "https://api.gleif.org/api/v1"


class CacheManager:
    def __init__(self) -> None:
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._validating = False
        self._validation_progress: str = ""

    @property
    def cache(self) -> dict[str, dict]:
        return self._cache

    @property
    def size(self) -> int:
        return len(self._cache)

    def load(self) -> None:
        try:
            with open(_CACHE_PATH, encoding="utf-8") as f:
                self._cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self._cache = {}

    def save(self) -> None:
        with self._lock:
            try:
                with open(_CACHE_PATH, "w", encoding="utf-8") as f:
                    json.dump(self._cache, f, indent=2)
            except OSError:
                pass

    def cache_row(self, company: str, row_data: dict) -> None:
        key = company.strip().lower()
        if not key:
            return
        with self._lock:
            self._cache[key] = {
                "lei": row_data.get("lei", ""),
                "legal_name": row_data.get("lei_legal_name", row_data.get("legal_name", "")),
                "jurisdiction": row_data.get("lei_jurisdiction", row_data.get("jurisdiction", "")),
                "status": row_data.get("lei_status", row_data.get("status", "")),
                "confidence": row_data.get("lei_confidence", row_data.get("confidence", "")),
                "match_status": row_data.get("lei_match_status", row_data.get("match_status", "")),
            }
        self.save()

    def fuzzy_lookup(self, company: str) -> dict | None:
        key = company.strip().lower()
        if not key:
            return None
        with self._lock:
            if key in self._cache:
                return self._cache[key].copy()
            if not self._cache:
                return None
            result = process.extractOne(key, self._cache.keys(), score_cutoff=95)
            if result is not None:
                matched_key, _score, _ = result
                return self._cache[matched_key].copy()
        return None

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
        try:
            os.remove(_CACHE_PATH)
        except FileNotFoundError:
            pass

    def validate(self, on_progress=None) -> str:
        """Validate all cached LEIs against GLEIF. Returns summary message."""
        self._validating = True

        entries = [
            (key, entry)
            for key, entry in self._cache.items()
            if entry.get("lei")
        ]
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
            for future in as_completed(future_to_batch):
                if not self._validating:
                    break
                batch = future_to_batch[future]
                try:
                    active_leis = future.result()
                    with self._lock:
                        for key, entry in batch:
                            if entry["lei"] not in active_leis:
                                self._cache.pop(key, None)
                                removed += 1
                except Exception:
                    with self._lock:
                        for key, _ in batch:
                            self._cache.pop(key, None)
                            removed += 1
                validated += len(batch)
                self._validation_progress = f"Validating cache: {validated} / {total}..."
                if on_progress:
                    on_progress(self._validation_progress)

        self.save()
        self._validating = False

        if removed > 0:
            return f"Cache validated: {removed} inactive entr{'y' if removed == 1 else 'ies'} removed."
        return "Cache validated: all entries active."
