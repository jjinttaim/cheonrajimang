#!/bin/sh
set -eu
SEARCHPROOF_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$SEARCHPROOF_ROOT"
SEARCHPROOF_URL="http://127.0.0.1:8000"
browse() {
  if command -v open >/dev/null 2>&1; then open "$1" || true
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1 || true
  else printf 'Open %s in a browser.\n' "$1"; fi
}
SEARCHPROOF_HEALTH="$(curl -fsS --max-time 2 "$SEARCHPROOF_URL/api/health" 2>/dev/null || true)"
case "$SEARCHPROOF_HEALTH" in
  *'"engine":"searchproof-'*) printf '천라지망 is already running: %s\n' "$SEARCHPROOF_URL"; browse "$SEARCHPROOF_URL"; exit 0 ;;
esac
if ! command -v npm >/dev/null 2>&1; then
  printf 'Node.js 22 and npm are required. See README.md.\n'; exit 1
fi
if [ ! -x .venv/bin/python ]; then
  if ! command -v python3.12 >/dev/null 2>&1; then
    printf 'Python 3.12 (python3.12) is required. See README.md.\n'; exit 1
  fi
  python3.12 -m venv .venv
fi
if ! .venv/bin/python -c 'import fastapi, uvicorn, numpy, rasterio, scipy, PIL, defusedxml, multipart' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r backend/requirements.lock
fi
if [ ! -f data/hallim/meta.json ]; then
  .venv/bin/python pipeline/prepare.py
fi
(
  cd frontend
  if [ ! -d node_modules ]; then npm ci; fi
  npm run build
)
printf '\n천라지망: %s\nTraining prototype only. Stop with Control+C.\n' "$SEARCHPROOF_URL"
(sleep 2; browse "$SEARCHPROOF_URL") &
exec .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
