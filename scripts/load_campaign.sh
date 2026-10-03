#!/usr/bin/env bash
# Concurrent-user load campaign. End-to-end latency only; LLM latency replayed
# from results/llm_trace.jsonl (8 real gemini-3.5-flash sessions), zero API calls.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
COMMON="--arms valkey,postgres --users 10,100,300,600,1000 --procs 6 --warmup 60 --ramp 40 --measure 150"

echo "=== Phase A: app as designed (1 scratchpad write per agent step) ==="
$PY scripts/loadtest.py $COMMON --writes 1 --tag w1

echo "=== Phase B: chatty agents (10 scratchpad writes per agent step) ==="
$PY scripts/loadtest.py $COMMON --writes 10 --tag w10
