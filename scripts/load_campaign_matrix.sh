#!/usr/bin/env bash
# Store-size matrix: a small Valkey vs a small Postgres at 2 and 4 vCPU (2 GB
# each, prod-like configs) on the same machine, from 1 to 3,000 concurrent
# users. End-to-end latency only; LLM latency replayed from the committed
# trace, zero API calls. Store sizing: see docker-compose.load.yml.
#
# The 1- and 10-user levels finish few sessions per minute, so they are
# measured for 8 min instead of 3 to get enough paired trips. About 80 min per
# (workload, size); the default matrix takes about 5.5 h. Extra arguments go
# to every loadtest run. Afterwards:
#   .venv/bin/python scripts/load_matrix_table.py runs/load-*-matrix-*/results.json
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
WORKLOADS=${WORKLOADS:-"1 10"}
SIZES=${SIZES-"2 4"}                 # store vCPUs
SMALL=${SMALL-"1,10"}                # levels measured for 8 min; SMALL= skips them
LARGE=${LARGE-"100,300,1000,3000"}   # levels measured for 3 min; LARGE= skips them
TAG=${TAG:-matrix}
ARMS="--arms valkey,postgres --procs auto"

# Back to the demo's profile however the campaign ends.
trap 'echo "=== Stores: back to the demo profile ==="; docker compose up -d --force-recreate --wait valkey postgres' EXIT

for w in $WORKLOADS; do
  for c in $SIZES; do
    echo "=== Stores: ${c} vCPU / ${LOAD_STORE_MEM:-2G} each ==="
    LOAD_STORE_CPUS="${c}.0" LOAD_VALKEY_IO_THREADS="$c" LOAD_PG_WORKERS="$c" \
      docker compose -f docker-compose.yml -f docker-compose.load.yml \
      up -d --force-recreate --wait valkey postgres
    if [ -n "$SMALL" ]; then
      echo "=== ${w} write(s) per step · ${c} vCPU · users ${SMALL} ==="
      $PY scripts/loadtest.py $ARMS --writes "$w" --users "$SMALL" \
        --ramp 10 --warmup 45 --measure 480 --tag "${TAG}-${c}cpu-w${w}-small" "$@"
    fi
    if [ -n "$LARGE" ]; then
      echo "=== ${w} write(s) per step · ${c} vCPU · users ${LARGE} ==="
      $PY scripts/loadtest.py $ARMS --writes "$w" --users "$LARGE" \
        --ramp 60 --warmup 105 --measure 180 --tag "${TAG}-${c}cpu-w${w}" "$@"
    fi
  done
done
