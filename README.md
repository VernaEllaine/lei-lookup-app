# LEI Lookup

Web app for resolving company names to Legal Entity Identifiers (LEIs) via the GLEIF API. Upload a CSV of entity names, review matches, edit/confirm results, and export.

## Tech Stack

- **Backend:** FastAPI, Pydantic, rapidfuzz, SSE streaming
- **Frontend:** React, TypeScript, Vite, @tanstack/react-virtual
- **APIs:** GLEIF public API (primary), DuckDuckGo search (fallback)
- **Storage:** SQLite (WAL mode) for results and cache

## Quick Start

```bash
pip install -r requirements.txt
cd frontend && npm install && cd ..

# Run both servers
./dev.sh

# Or run separately:
# Terminal 1
uvicorn backend.main:app --reload
# Terminal 2
cd frontend && npm run dev
```

Open `http://localhost:5173`

## Local GLEIF Copy (optional, recommended)

Load the full GLEIF golden copy (~3.5M LEI records) and the ISIN-LEI mapping into a local SQLite database so lookups don't wait on the rate-limited GLEIF API:

```bash
python -m backend.gleif_local load     # downloads ~510 MB, builds the DB
python -m backend.gleif_local status   # shows publish date and counts
```

When a local copy is loaded:

- **Name lookups** use SQLite FTS5 full-text search over legal, other and transliterated names of ACTIVE/ISSUED entities, then run through the same confidence scoring as before. They skip the global rate limiter.
- **LEI validation** and **ISIN lookups** read the local tables and only call the API for LEIs/ISINs that aren't in the local copy (e.g. issued after it was published).
- `GET /api/gleif-local/status` reports what is loaded.

GLEIF republishes the golden copy three times a day; rerun `load` to refresh. It can run while the server is up — each load writes a new `gleif-<timestamp>.db` and switches the `current` pointer, and older files are removed on the next load.

A load takes a few minutes and the DB is ~1 GB. It is stored in `%LOCALAPPDATA%\lei-lookup\gleif` on Windows, `~/.local/share/lei-lookup/gleif` elsewhere, or `$DATA_DIR/gleif` when `DATA_DIR` is set (Docker). Override with `GLEIF_DATA_DIR`. Keep it out of synced folders such as OneDrive.

## Architecture

### Backend

| Module | Purpose |
|--------|---------|
| `backend/main.py` | App entry point, DB init, cache migration, rate limiter lifecycle |
| `backend/routes.py` | FastAPI endpoints — upload, lookup (SSE), paginated results, export; per-session state |
| `backend/worker.py` | GLEIF lookups via global rate limiter queue |
| `backend/rate_limiter.py` | Token-bucket rate limiter (~60 req/min) shared across all sessions |
| `backend/database.py` | SQLite layer — results table (session-scoped), cache table, pagination queries |
| `backend/cache.py` | Cache manager with buffered writes (flushes every 100 entries) |
| `lei_lookup.py` | Core GLEIF API client with retry/backoff, fuzzy matching, confidence scoring |

### Frontend

| Module | Purpose |
|--------|---------|
| `App.tsx` | Main app — session state, pagination, status filter, delta cell updates |
| `ResultsTable.tsx` | Virtual-scrolled table via `@tanstack/react-virtual` |
| `EditableCell.tsx` | Inline-editable cell component |
| `api.ts` | API client — all calls scoped by `session_id` |

### Key Design Decisions

- **Per-session isolation** — each CSV upload creates a UUID session. All data (rows, results, running state) is scoped by session ID, so multiple users can work concurrently without interference.
- **Global rate limiter** — a token-bucket queue (1 token/sec, burst of 3) serializes all GLEIF API calls across all sessions, preventing 429 errors. The limiter runs as a background asyncio task started at app startup.
- **SQLite with WAL mode** allows concurrent reads during writes. Thread-local connections since SQLite can't share across threads. Results table includes `session_id` and `row_index` columns for multi-session support.
- **Server-side pagination** with status filtering — only 50-100 rows sent per page, not the full dataset.
- **Delta updates** — cell edits return the single updated row + summary; no full re-fetch needed.
- **Virtual scrolling** — only ~40-60 DOM rows rendered regardless of page size.
- **Confidence scoring** — case-insensitive matching with legal suffix normalization (e.g. "S.M.E." matches "SME"), abbreviation expansion, and token overlap analysis. Fuzzy fallback matches use computed confidence instead of hardcoded "low".
- **Cache migration** — existing `lei_cache.json` is automatically migrated to SQLite on first startup (original renamed to `.bak`).

## API Endpoints

All endpoints that return session-scoped data accept a `session_id` query parameter.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/upload` | Upload CSV file; returns `session_id` |
| GET | `/api/lookup?column=&session_id=` | Start lookup (SSE stream) |
| GET | `/api/results?page=&page_size=&status=&session_id=` | Paginated results |
| PUT | `/api/results/{id}` | Update a cell (delta) |
| PUT | `/api/results/{id}/validate-lei` | Validate LEI against GLEIF |
| POST | `/api/confirm-all?session_id=` | Confirm all reviewed rows |
| GET | `/api/export?session_id=` | Export results as CSV |
| GET | `/api/cache/summary?session_id=` | Cache stats |
| DELETE | `/api/cache?session_id=` | Clear cache |

### Data Flow

```
User A: POST /upload -> session_id=abc -> GET /lookup?session_id=abc&column=...
User B: POST /upload -> session_id=xyz -> GET /lookup?session_id=xyz&column=...

Both users' lookup_lei() calls -> global rate limiter queue (FIFO, ~1/sec)
Each user gets own SSE stream with progress for their session only
```

## Docker

```bash
# Build
docker build -t lei-lookup .

# Run (persistent DB via named volume)
docker run -p 8000:8000 -v lei-data:/app/data lei-lookup
```

Open `http://localhost:8000` — the React frontend is served by FastAPI.

The SQLite database is stored in the `/app/data` volume so it persists across container restarts.
