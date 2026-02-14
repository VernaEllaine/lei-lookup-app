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

## Architecture

### Backend

| Module | Purpose |
|--------|---------|
| `backend/database.py` | SQLite layer — results table, cache table, pagination queries |
| `backend/cache.py` | Cache manager with buffered writes (flushes every 100 entries) |
| `backend/worker.py` | Concurrent GLEIF lookups via `asyncio.Semaphore(20)` + `gather` |
| `backend/routes.py` | FastAPI endpoints — upload, lookup (SSE), paginated results, export |
| `backend/main.py` | App entry point, DB init, cache migration on startup |
| `lei_lookup.py` | Core GLEIF API client with retry/backoff logic |

### Frontend

| Module | Purpose |
|--------|---------|
| `App.tsx` | Main app — pagination state, status filter, delta cell updates |
| `ResultsTable.tsx` | Virtual-scrolled table via `@tanstack/react-virtual` |
| `EditableCell.tsx` | Inline-editable cell component |
| `api.ts` | API client with `getResultsPage()` for paginated fetches |

### Key Design Decisions

- **SQLite with WAL mode** allows concurrent reads during writes. Thread-local connections since SQLite can't share across threads.
- **Concurrent lookups** (20 parallel) instead of sequential 0.5s/row. A 100-row file completes in seconds instead of minutes.
- **Server-side pagination** with status filtering — only 50 rows sent per page, not the full dataset.
- **Delta updates** — cell edits return the single updated row + summary; no full re-fetch needed.
- **Virtual scrolling** — only ~40-60 DOM rows rendered regardless of page size.
- **Cache migration** — existing `lei_cache.json` is automatically migrated to SQLite on first startup (original renamed to `.bak`).

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/upload` | Upload CSV file |
| GET | `/api/lookup?column=` | Start lookup (SSE stream) |
| GET | `/api/results?page=&page_size=&status=` | Paginated results |
| PUT | `/api/results/{id}` | Update a cell (delta) |
| PUT | `/api/results/{id}/validate-lei` | Validate LEI against GLEIF |
| POST | `/api/confirm-all` | Confirm all reviewed rows |
| GET | `/api/export` | Export results as CSV |
| GET | `/api/cache/summary` | Cache stats |
| DELETE | `/api/cache` | Clear cache |

## Docker

```bash
# Build
docker build -t lei-lookup .

# Run (persistent DB via named volume)
docker run -p 8000:8000 -v lei-data:/app/data lei-lookup
```

Open `http://localhost:8000` — the React frontend is served by FastAPI.

The SQLite database is stored in the `/app/data` volume so it persists across container restarts.
