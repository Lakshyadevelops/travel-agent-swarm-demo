#!/usr/bin/env bash
# Concurrent-user campaign at 300 / 1000 / 2000 users: a small Valkey vs a
# small Postgres (default 4 vCPU / 2 GB each, prod-like configs) on the same
# machine. End-to-end latency only; LLM latency replayed from the committed
# trace, zero API calls. Store sizing: see docker-compose.load.yml.
#
# Takes about 40 min per workload. Extra arguments go to every loadtest run,
# e.g. `scripts/load_campaign_scale.sh --measure 120`. Set WORKLOADS="1" to run
# only the app-as-built workload. Set TAG to name the runs, e.g. for a 2 vCPU
# campaign: LOAD_STORE_CPUS=2.0 LOAD_VALKEY_IO_THREADS=2 LOAD_PG_WORKERS=2 \
#   TAG=scale2cpu scripts/load_campaign_scale.sh --users 1000,2000
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
WORKLOADS=${WORKLOADS:-"1 10"}
TAG=${TAG:-scale}

echo "=== Stores: prod-like profile ==="
docker compose -f docker-compose.yml -f docker-compose.load.yml \
  up -d --force-recreate --wait valkey postgres
# Back to the demo's profile however the campaign ends.
trap 'echo "=== Stores: back to the demo profile ==="; docker compose up -d --force-recreate --wait valkey postgres' EXIT

COMMON="--arms valkey,postgres --users 300,1000,2000 --procs auto --ramp 60 --warmup 105 --measure 180"
for w in $WORKLOADS; do
  echo "=== Workload: ${w} scratchpad write(s) per agent step ==="
  $PY scripts/loadtest.py $COMMON --writes "$w" --tag "${TAG}-w${w}" "$@"
done
