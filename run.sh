#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

API_PORT=2010
API_DIR="$ROOT/limit-api"

wait_for() {
  local url=$1 label=$2
  echo "Waiting for $label..."
  for i in $(seq 1 20); do
    if curl -sf "$url" > /dev/null 2>&1; then
      echo "$label is up."
      return 0
    fi
    sleep 1
  done
  echo "ERROR: $label did not become ready in time." >&2
  exit 1
}

# Start limit-api if not already running
if ! curl -sf "http://127.0.0.1:$API_PORT/accounts" > /dev/null 2>&1; then
  echo "Starting limit-api..."
  (cd "$API_DIR" && source .venv/bin/activate && uvicorn main:app --host 0.0.0.0 --port $API_PORT > /tmp/limit-api.log 2>&1) &
  wait_for "http://127.0.0.1:$API_PORT/accounts" "limit-api"
else
  echo "limit-api already running."
fi

# Run the agent
cd "$SCRIPT_DIR"
source .venv/bin/activate
python agent.py "$@"
