#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. MOCK_AI=true works without an API key."
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.12+ is required."
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "Node.js 20+ is required."
  exit 1
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

. .venv/bin/activate
pip install -r requirements.txt

cd frontend
if [ ! -d node_modules ]; then
  if command -v pnpm >/dev/null 2>&1; then
    pnpm install
  else
    npm install
  fi
fi
cd "$ROOT"

.venv/bin/python -m uvicorn botc_ai.api.app:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!
trap 'kill "$BACKEND_PID"' EXIT

cd frontend
if command -v pnpm >/dev/null 2>&1; then
  pnpm run dev
else
  npm run dev
fi
