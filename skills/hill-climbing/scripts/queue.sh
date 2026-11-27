#!/usr/bin/env bash
# Portable (macOS + Linux) serialized exclusive-hardware experiment queue.
# Runs VERIFY_CMD for each named config in configs/<name>.json one at a time,
# preventing overlapping benchmarks on a shared GPU/CPU/device.
#
#   VERIFY_CMD="./verify.sh" bash queue.sh ctl-a cand-1 ctl-b
#
# Detach long queues so they survive agent/terminal restarts:
#   nohup env VERIFY_CMD="./verify.sh" bash queue.sh ctl-a cand-1 ctl-b > queue.log 2>&1 < /dev/null & disown
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "usage: [VERIFY_CMD=./verify.sh] $0 CONFIG_NAME [CONFIG_NAME ...]" >&2
  exit 2
fi

LOCK_DIR="${QUEUE_LOCK:-/tmp/hill-climbing.lock.d}"
CONFIGS_DIR="${CONFIGS_DIR:-configs}"
VERIFY_CMD="${VERIFY_CMD:-./verify.sh}"
LOG_DIR="${LOG_DIR:-.}"

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
  cfg="${CONFIGS_DIR}/${name}.json"
  if [ ! -f "$cfg" ]; then
    echo "missing config: $cfg" >&2
    exit 1
  fi
  log="${LOG_DIR}/run-${name}.log"
  # shellcheck disable=SC2086
  $VERIFY_CMD "$cfg" > "$log" 2>&1 || true
  summary="$(tail -c 300 "$log" | tr '\n' ' ' | cut -c1-200)"
  echo "done ${name}: ${summary}"
done

echo "QUEUE_DONE $*"
