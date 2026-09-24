# Travel Concierge Agent Swarm — Valkey vs PostgreSQL

A 6-agent travel planner built on Google's Agent Development Kit (ADK). It is used to answer one question with measurements:
**does an in-memory state store (Valkey) give users faster responses than PostgreSQL?**

## The agents

```
supervisor_intake
  └─ LoopAgent (max 3 rounds)
       ├─ ParallelAgent: destination_scout | transit_agent | stay_agent
       └─ budget_guardrail   (issues price caps → specialists re-plan)
itinerary_assembly
supervisor_final
```

The agents share findings through a scratchpad that every agent can read and write:
- **Stay agent:** waits for the scout's recommended neighbourhood.
- **Transit agent:** waits for the chosen hotel, then plans the airport transfer.
- **Budget guardrail:** prices every day of the trip. If the plan is over budget, it sends price caps to the stay and transit agents for the next round.
- **Itinerary agent:** produces timed stops, travel legs (distance, time, mode), sunrise/sunset/golden-hour scheduling, day trips and lunch.

## Storage arms

| Arm | Session log | Scratchpad |
|---|---|---|
| `valkey` | HASH + ZSET | HASH with EXPIRE, pipelined |
| `postgres` | JSONB, one statement per op | JSONB, `unnest` multi-row upsert |
| `postgres_cached` | Postgres | Postgres + separate Valkey cache (write-through) |

Both stores use **stock configs** capped at **1 CPU / 256 MB** each (`docker-compose.yml`).

## Quick start

```bash
docker compose up -d
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # add GOOGLE_API_KEY for real Gemini mode
.venv/bin/python -m uvicorn app.main:app --port 8080
.venv/bin/python -m pytest -q   # 72 tests
```

The UI has two tabs:
- **Tab 1:** the planner, with a live agent log and the itinerary.
- **Tab 2:** benchmark controls and results, a telemetry view of the scratchpad, and a methodology panel.

## Load testing without spending API quota

1. **Record** real model latency once:
   `scripts/calibrate_llm.py --runs 8`. That is about 120 Gemini calls, and it appends to `runs/llm_trace.jsonl`.
   A trace from 8 real `gemini-3.5-flash` sessions is already committed at `results/llm_trace.jsonl` and is used automatically.
2. **Replay:** the scripted fake model waits for a recorded latency pair from the same agent role before each response. The load test makes no API calls.
3. **Drive load:** run `scripts/loadtest.py --arms valkey,postgres --users 10,100,300,600 --writes 1`. It simulates closed-loop virtual users across several worker processes.
   - Session *i* of user *u* gets the same trip and the same replayed latencies on every arm. Deltas are computed on these matched sessions, with a bootstrap 95% CI.
   - Store CPU and memory, and the load generator's own CPU, are recorded next to latency.

## Results (end-to-end latency per planning session, ~37 s median)

Raw data: `results/load_w1_results.json` and `results/load_w10_results.json`; per-level log: `results/load_campaign.log`.

**App as built (1 scratchpad write per agent step):** no user-visible difference up to 600 users.

| Users | Valkey p50 / p95 | Postgres p50 / p95 | Store CPU (Valkey / PG) |
|---|---|---|---|
| 10 | 37.4 / 55.6 s | 37.5 / 55.8 s | 3% / 4% |
| 300 | 37.4 / 53.4 s | 37.5 / 53.3 s | 10% / 31% |
| 600 | 37.6 / 53.9 s | 37.8 / 54.0 s | 13% / 57% |

**Chatty agents (10 writes per step):** Postgres on 1 CPU hits its limit around 300 users.

| Users | Valkey p50 / p95 | Postgres p50 / p95 | Sessions finished in 150 s (Valkey / PG) |
|---|---|---|---|
| 300 | 38.1 / 54.0 s | 39.3 / 56.7 s | 1,113 / 1,079 |
| 600 | 38.9 / 56.3 s | **60.8 / 93.1 s** | 2,206 / **1,292** |

Caveats:
- The 1,000-user runs are in the raw data but are limited by the load generator: both arms slowed down while Valkey was at about 20% CPU.
- Valkey was never saturated on this 8-core machine.
- LLM latency is replayed, so Gemini's own rate limiting at scale is not modelled.

## Layout

```
app/agents/     swarm, prompts, scratchpad coordination, day planner
app/state/      Valkey / Postgres session services and scratchpads, backend registry
app/bench/      interleaved benchmark, sweeps, concurrent-user load test
app/llm/        fake model with latency replay, latency model, Gemini probe
app/telemetry/  per-op instrumentation, JSONL op log
scripts/        calibration, load test drivers
results/        committed benchmark data and latency trace
```
