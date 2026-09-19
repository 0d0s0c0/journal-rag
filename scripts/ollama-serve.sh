#!/usr/bin/env bash
#
# Start the local Ollama server with settings tuned for RAG work.
#
# Needed because launchd on this machine registers the brew service but never
# executes it (`runs = 0`), so `brew services start ollama` silently does
# nothing. Running in the foreground also means you can watch requests arrive,
# which is useful while building retrieval.
#
# Usage:  ./scripts/ollama-serve.sh
# Stop:   Ctrl-C
#
set -euo pipefail

PORT="${OLLAMA_PORT:-11434}"

# Already running? Don't start a second one — it would fail to bind the port
# anyway, but a clear message beats a confusing error.
if curl -s --max-time 2 "http://localhost:${PORT}/api/version" >/dev/null 2>&1; then
  echo "Ollama is already running on port ${PORT}:"
  curl -s "http://localhost:${PORT}/api/version"
  echo
  exit 0
fi

# ── Tuning ───────────────────────────────────────────────────────────────────
# Flash attention: faster attention with a smaller memory footprint. Matters
# most at long context, which is exactly what RAG produces — we stuff many
# retrieved journal entries into each prompt.
export OLLAMA_FLASH_ATTENTION=1

# Quantize the KV cache to 8-bit. The KV cache grows with context length and
# is often the real memory constraint, not the weights. q8_0 roughly halves it
# with negligible quality loss. (Both of these are what Homebrew's own service
# plist sets, so this matches the intended defaults.)
export OLLAMA_KV_CACHE_TYPE=q8_0

# How long a model stays resident after its last request. The default is 5
# minutes, which means a pause for coffee costs you a ~7.6 GB reload from disk
# on the next query. 30m fits an iterative work session better.
export OLLAMA_KEEP_ALIVE=30m

echo "Starting Ollama on port ${PORT}"
echo "  OLLAMA_FLASH_ATTENTION = ${OLLAMA_FLASH_ATTENTION}"
echo "  OLLAMA_KV_CACHE_TYPE   = ${OLLAMA_KV_CACHE_TYPE}"
echo "  OLLAMA_KEEP_ALIVE      = ${OLLAMA_KEEP_ALIVE}"
echo

# exec: replace this shell with ollama, so Ctrl-C reaches the server directly
# rather than killing the wrapper and orphaning the process.
exec ollama serve
