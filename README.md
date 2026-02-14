# LEI Lookup

Web app for resolving company names to Legal Entity Identifiers (LEIs) via the GLEIF API. Upload a CSV of entity names, review matches, edit/confirm results, and export.

## Tech Stack

- **Backend:** FastAPI, Pydantic, rapidfuzz, SSE streaming
- **Frontend:** React, TypeScript, Vite
- **APIs:** GLEIF public API (primary), DuckDuckGo search (fallback)
- **Storage:** In-memory state + JSON file cache (no database)

## Quick Start

```bash
pip install -r requirements.txt
cd frontend && npm install && cd ..

# Terminal 1
uvicorn backend.main:app --reload

# Terminal 2
cd frontend && npm run dev
```

Open `http://localhost:5173`

## Open Points

- [ ] Dockerize (backend + frontend in single container)
- [ ] Production build: `cd frontend && npm run build` then serve via FastAPI static mount
