#!/usr/bin/env bash
#
# Prove the pipeline never talks to anything but this machine.
#
#   ./scripts/verify-offline.sh              # check a real question
#   ./scripts/verify-offline.sh --self-test  # prove the detector can fail
#   ./scripts/verify-offline.sh "some other question"
#
# The privacy claim for this project is not "we chose a local model" — it is
# "journal text never leaves the machine". Those are different claims, and only
# the second matters. A local model still leaks if a library reports telemetry,
# fetches a tokenizer on first use, or resolves a remote URL.
#
# Two checks, because either alone is weak:
#
#   static   every URL in src/ points at loopback. Catches an endpoint someone
#            adds later without thinking about it.
#   runtime  sample the process tree's own sockets while a real question is
#            answered, and fail on any peer that is not loopback. Catches what
#            the static check cannot — a dependency opening its own connection.
#
# Pulling the network cable tests something weaker: it proves the pipeline
# SURVIVES without a network, not that it stays silent when one is available.
#
# --self-test exists because a check that cannot fail is worthless. The runtime
# check first reported "0 connections" while watching the wrong pid — `uv run`
# execs python as a child, and the child holds the socket. That looked exactly
# like a pass. The self-test binds a listener to this machine's own LAN address
# and connects to it: a genuinely non-loopback peer, no packet leaving the host,
# and the detector must flag it.
#
set -uo pipefail
cd "$(dirname "$0")/.."

PEERS=$(mktemp); OUT=$(mktemp)
trap 'rm -f "$PEERS" "$OUT"' EXIT

# `uv run` execs python as a child, so the whole subtree has to be watched.
descendants() {
  local roots="$1" next
  while :; do
    next=$(ps -Ao pid=,ppid= | awk -v r="$roots" '
      BEGIN { n = split(r, a, " "); for (i = 1; i <= n; i++) want[a[i]] = 1 }
      want[$2] && !($1 in want) { print $1 }')
    [ -z "$next" ] && break
    roots="$roots $next"
  done
  echo "$roots"
}

# Run "$@" in the background, sampling every TCP peer its process tree holds.
sample_peers() {
  : >"$PEERS"
  "$@" >"$OUT" 2>&1 &
  local pid=$! p
  while kill -0 "$pid" 2>/dev/null; do
    for p in $(descendants "$pid"); do
      lsof -nP -iTCP -a -p "$p" 2>/dev/null | awk 'NR>1 {print $9}' >>"$PEERS"
    done
    sleep 0.2
  done
  wait "$pid"
}

# Peers seen that were not loopback.
non_loopback() {
  grep '\->' "$PEERS" 2>/dev/null \
    | sed -E 's/^.*->//; s/:[0-9]+$//' | sort -u \
    | grep -vE '^(127\.0\.0\.1|\[::1\]|::1|localhost)$' || true
}

# ── --self-test: the detector must catch a non-loopback peer ────────────────
if [ "${1:-}" = "--self-test" ]; then
  lan=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
  if [ -z "$lan" ]; then
    echo "no LAN address available — cannot self-test the detector"; exit 2
  fi
  echo "── self-test: connecting to this host's own LAN address ($lan) ────"
  echo "   nothing leaves the machine; the peer is simply not 127.0.0.1"
  sample_peers python3 -c '
import socket, sys, time
lan = sys.argv[1]
srv = socket.socket(); srv.bind((lan, 0)); srv.listen(1)
conn = socket.create_connection(srv.getsockname()); srv.accept()
time.sleep(3)          # hold it open long enough to be sampled
' "$lan"
  found=$(non_loopback)
  if [ -n "$found" ]; then
    echo "  ok     detector flagged:"; printf '           %s\n' $found
    echo; echo "PASS — the runtime check is capable of failing."
    exit 0
  fi
  echo "  MISSED — the detector saw nothing. The runtime check below proves"
  echo "           nothing until this is fixed."
  exit 1
fi

fail=0

echo "── static: URLs referenced in src/ ───────────────────────────────"
urls=$(grep -rhoE --include='*.py' --exclude-dir=__pycache__ \
         'https?://[A-Za-z0-9._:/-]+' src/ 2>/dev/null | sort -u || true)
if [ -z "$urls" ]; then
  echo "  none found"
else
  while read -r u; do
    host=$(printf '%s' "$u" | sed -E 's#^https?://##; s#[:/].*$##')
    case "$host" in
      localhost|127.0.0.1|0.0.0.0|::1) printf '  ok     %s\n' "$u" ;;
      *)                               printf '  REMOTE %s\n' "$u"; fail=1 ;;
    esac
  done <<< "$urls"
fi

echo
echo "── runtime: sockets opened while answering a question ────────────"
q="${1:-what did I do on a rainy day}"
sample_peers uv run python -u -m src.ask "$q" --quiet
rc=$?

echo "  connections observed:"
sort -u "$PEERS" | grep '\->' | sed -E 's/^/    /' || echo "    (none)"

remote=$(non_loopback)
if [ -n "$remote" ]; then
  echo; echo "  NON-LOOPBACK PEERS:"; printf '    %s\n' $remote; fail=1
else
  echo "  ok     every peer was loopback"
fi

echo
if [ "$rc" -ne 0 ]; then
  echo "  NOTE: the query itself failed (exit $rc), so the socket check above is"
  echo "        weak evidence. Output:"
  tail -5 "$OUT" | sed -E 's/^/    /'
  fail=1
else
  echo "  answer produced ok ($(wc -c <"$OUT" | tr -d ' ') bytes)"
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "PASS — no journal text could have left this machine."
  echo "       Confirm the check is live with: $0 --self-test"
else
  echo "FAIL — see above."
fi
exit "$fail"
