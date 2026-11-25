#!/usr/bin/env bash
# Portable (macOS + Linux) serialized single-device experiment queue.
# Runs probe.py on each config in configs/<name>.json one at a time across all callers.
#
#   bash queue.sh ctl-a cand-1 ctl-b
#   PROBE_FLAGS="--allow-running" bash queue.sh cand-1
#
# Detach long queues so they survive agent/terminal restarts:
#   nohup bash queue.sh ctl-a cand-1 ctl-b > queue.log 2>&1 < /dev/null & disown
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "usage: $0 CONFIG_NAME [CONFIG_NAME ...]" >&2
  exit 2
fi

LOCK_DIR="${QUEUE_LOCK:-/tmp/android-port-hillclimb.lock.d}"
PROBE="${PROBE:-$(cd "$(dirname "$0")" && pwd)/probe.py}"

acquire_lock() {
  while ! mkdir "$LOCK_DIR" 2>/dev/null; do
    if [ -f "$LOCK_DIR/pid" ]; then
      owner="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
      if [ -n "$owner" ] && ! kill -0 "$owner" 2>/dev/null; then
        rm -rf "$LOCK_DIR"
        continue
      fi
    fi
    sleep 1
  done
  echo "$$" > "$LOCK_DIR/pid"
}

release_lock() {
  rm -rf "$LOCK_DIR"
}

acquire_lock
trap release_lock EXIT INT TERM

for name in "$@"; do
  cfg="configs/${name}.json"
  if [ ! -f "$cfg" ]; then
    echo "missing config: $cfg" >&2
    exit 1
  fi
  # shellcheck disable=SC2086
  python3 "$PROBE" run "$cfg" ${PROBE_FLAGS:-} > "runs-${name}.log" 2>&1 || true
  summary="$(tail -c 300 "runs-${name}.log" | tr '\n' ' ' | cut -c1-200)"
  echo "done ${name}: ${summary}"
done

echo "QUEUE_DONE $*"
