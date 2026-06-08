#!/usr/bin/env bash
#
# Drik — one-command demo runner.
#
# Saves the vision model locally, starts the OpenAI-compatible server (reusing it
# if already running), serves the bundled demo page, and runs a spec — all from
# one terminal.
#
#   ./run.sh                 # run the bundled demo spec
#   ./run.sh specs/auth.md   # run a different spec against the demo page
#   PORT=1234 PAGE_PORT=8099 ./run.sh
#
# Set HF_TOKEN in your environment the first time if the model isn't downloaded
# yet (faster, un-rate-limited):  HF_TOKEN=hf_xxx ./run.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

# --- config ---------------------------------------------------------------
# Override the model with e.g.  MODEL_REPO=mlx-community/UI-TARS-1.5-7B-8bit ./run.sh
MODEL_REPO="${MODEL_REPO:-pipenetwork/Holo-3.1-4B-MLX-8bit}"
MODEL_DIR="models/$(basename "$MODEL_REPO")"   # model is saved here, inside the project

# Coordinate space is MODEL-DEPENDENT — the #1 cause of "clicks land in the wrong
# place". UI-TARS emits absolute pixels; the Qwen-VL grounding family (UI-Venus,
# Holo, Qwen2.5/3-VL) emits coordinates normalized to [0,1000]. Pick the right one
# from the model name (override with COORD_SPACE=... ./run.sh).
case "$MODEL_REPO" in
  *UI-TARS*|*ui-tars*) COORD_SPACE="${COORD_SPACE:-pixel}" ;;
  *)                   COORD_SPACE="${COORD_SPACE:-normalized_1000}" ;;
esac

PORT="${PORT:-1234}"
PAGE_PORT="${PAGE_PORT:-8099}"
SPEC="${1:-specs/demo.md}"
# HEADED=1 ./run.sh  → show the browser window so you can watch it click live.
HEADED_FLAG=""; [ -n "${HEADED:-}" ] && HEADED_FLAG="--headed"
# Per-step model timeout (seconds). Big models (9B 8-bit) need a generous value,
# especially for the first cold inference. Override with TIMEOUT=… ./run.sh
TIMEOUT="${TIMEOUT:-180}"
PY=".mlx-venv/bin/python"
ENDPOINT="http://localhost:${PORT}/v1"

if [ ! -x "$PY" ]; then
  echo "✗ mlx venv not found at $PY"
  echo "  Create it once with:"
  echo "    uv venv .mlx-venv --python 3.12 && uv pip install --python .mlx-venv mlx-vlm"
  exit 1
fi

# --- 1. save the model locally (idempotent; copies from HF cache if present) --
if [ -f "${MODEL_DIR}/config.json" ]; then
  echo "▶ Model already saved at ./${MODEL_DIR}"
else
  echo "▶ Saving ${MODEL_REPO} into ./${MODEL_DIR} (one-time)…"
  "$PY" - "$MODEL_REPO" "$MODEL_DIR" <<'PY'
import sys
from huggingface_hub import snapshot_download
repo, dest = sys.argv[1], sys.argv[2]
snapshot_download(repo_id=repo, local_dir=dest)
print("✓ saved to", dest)
PY
fi

# --- 2. start the model server (reuse if one is already up) ------------------
STARTED_SERVER=0
if curl -s "${ENDPOINT}/models" >/dev/null 2>&1; then
  echo "▶ Reusing model server already running on :${PORT}"
else
  echo "▶ Starting model server on :${PORT} (loads ~5 GB into memory, ~20–40s)…"
  nohup "$PY" -m mlx_vlm server --model "$MODEL_DIR" --port "$PORT" \
    >/tmp/drik-mlx-server.log 2>&1 &
  SERVER_PID=$!
  disown "$SERVER_PID" 2>/dev/null || true
  STARTED_SERVER=1
  until curl -s "${ENDPOINT}/models" >/dev/null 2>&1; do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
      echo "✗ model server failed to start. Last log lines:"; tail -20 /tmp/drik-mlx-server.log
      exit 1
    fi
    sleep 2
  done
  echo "✓ server ready"
fi

# --- 3. pick the target: a real site (BASE_URL=…) or the bundled demo page ----
if [ -n "${BASE_URL:-}" ]; then
  TARGET_URL="$BASE_URL"           # e.g. BASE_URL=https://news.ycombinator.com
  echo "▶ Target: ${TARGET_URL} (external)"
else
  python3 -m http.server "$PAGE_PORT" --directory examples/login-demo \
    >/tmp/drik-page.log 2>&1 &
  PAGE_PID=$!
  trap 'kill "$PAGE_PID" 2>/dev/null || true' EXIT
  sleep 1
  TARGET_URL="http://localhost:${PAGE_PORT}"
  echo "▶ Target: bundled demo page on :${PAGE_PORT}"
fi

# --- 4. run Drik -------------------------------------------------------------
echo "▶ Running Drik on ${SPEC}  (coord-space: ${COORD_SPACE})"
echo
set +e
uv run drik run "$SPEC" \
  --endpoint "$ENDPOINT" \
  --model "$MODEL_DIR" \
  --base-url "$TARGET_URL" \
  --coord-space "$COORD_SPACE" \
  --retries 2 \
  --timeout "$TIMEOUT" \
  $HEADED_FLAG \
  --report drik-artifacts/report.json
RC=$?
set -e

# --- 5. leave the model server up for fast re-runs ---------------------------
if [ "$STARTED_SERVER" = "1" ]; then
  echo
  echo "ℹ Model server left running (PID ${SERVER_PID:-?}) so re-runs are instant."
  echo "  Stop it with:  kill ${SERVER_PID:-<pid>}    (or: pkill -f 'mlx_vlm server')"
fi
exit $RC
