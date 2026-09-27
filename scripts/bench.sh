#!/usr/bin/env bash
# Single-session benchmarks (scripted model, zero API calls):
#   1. interleaved A/B, Valkey vs PostgreSQL, 1 scratchpad write per agent step
#   2. write-frequency sweep, 1 / 10 / 100 writes per agent step
# Extra arguments go to both runs, e.g. `scripts/bench.sh --repeats 10`.
# For concurrent-user load tests use scripts/load_campaign.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python

echo "=== A/B: valkey vs postgres, 1 write per step ==="
$PY scripts/bench.py bench --arms valkey,postgres --writes 1 "$@"

echo "=== Write-frequency sweep: 1 / 10 / 100 writes per step ==="
$PY scripts/bench.py sweep --arms valkey,postgres --frequencies 1,10,100 "$@"
