#!/usr/bin/env bash
# Starts the hackathon copy of WhiteHats: its own backend, frontend, node library and data.
# Nothing here reads from or writes to the shared platform/ folder.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
export WH_LOCAL_STUBS=1 WH_SEED_DEMO=1 WH_LOAD_DOTENV=0
export WH_DATA_DIR="$here/backend/.data"
export WH_PORT="${WH_PORT:-8011}"
export WH_PUBLIC_URL="http://127.0.0.1:$WH_PORT"
if lsof -nP -iTCP:"$WH_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $WH_PORT is already in use. Set WH_PORT to a free port." >&2
  exit 1
fi
export WH_FACTORY_TENANT=acme
unset WH_DATABASE_URL DATABASE_URL WH_JWT_SECRET WH_SECRET_KEY
export WH_ENV=development
# The frontend build needs Node 20 or newer; prefer Homebrew's Node over an older system one.
if [ -x /opt/homebrew/bin/node ]; then export PATH="/opt/homebrew/bin:$PATH"; fi
if [ ! -d "$here/frontend/node_modules" ]; then (cd "$here/frontend" && npm ci); fi
(cd "$here/frontend" && npm run build)
cd "$here/backend"
echo "WhiteHats hackathon workspace is starting at $WH_PUBLIC_URL"
exec uv run uvicorn app.main:app --host 127.0.0.1 --port "$WH_PORT"
