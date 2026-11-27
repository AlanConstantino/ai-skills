#!/usr/bin/env bash
# Read-only verifier contract template.
# Lock this file with: python3 scripts/climb.py lock-verifier <work-dir> verify.sh
#
# Contract:
#   1. Verify fixed environment (idle host, AC power, expected build/commit).
#   2. Run hard correctness gates FIRST (exit non-zero on failure).
#   3. Run fixed-seed benchmark with warm-up discarded and >= 5 samples.
#   4. Print one JSON object with primary metric, samples, guardrails, and gate_pass.
set -euo pipefail

CONFIG="${1:-configs/baseline.json}"
if [ ! -f "$CONFIG" ]; then
  echo "missing config: $CONFIG" >&2
  exit 2
fi

# --- 1. Environment check (replace with real host checks) ---
# --- 2. Hard correctness gate (replace with real oracle) ---
# --- 3. Fixed-panel benchmark (replace with real measurement) ---

echo "{\"config\":\"${CONFIG}\",\"gate_pass\":true,\"metric\":\"score\",\"samples\":[1.0,1.0,1.0]}"
