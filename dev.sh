#!/usr/bin/env bash
# Start backend (FastAPI) and frontend (Vite) dev servers together.
# Usage: ./dev.sh

cd "$(dirname "$0")"

npx concurrently \
  --names "BE,FE" \
  --prefix-colors "cyan,magenta" \
  --kill-others \
  "uvicorn backend.main:app --reload" \
  "cd frontend && npm run dev"
