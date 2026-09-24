#!/usr/bin/env bash
# Phase C: how much CPU does Postgres need to match 1-CPU Valkey?
# Chatty workload (10 writes/step) at the user counts where 1-CPU Postgres
# saturated in Phase B. 1000 users is excluded: at that level the load
# generator itself slowed both arms, so it can't isolate the store.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
COMMON="--users 300,600 --procs 6 --warmup 60 --ramp 40 --measure 150 --writes 10"

echo "=== Phase C1: Valkey 1 CPU (re-baseline with blocking pool) ==="
$PY scripts/loadtest.py --arms valkey $COMMON --tag c-valkey1
for cpus in 2 4; do
  echo "=== Phase C: Postgres ${cpus} CPUs ==="
  $PY scripts/loadtest.py --arms postgres $COMMON --pg-cpus $cpus --tag c-pg${cpus}
done
