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

### Where the facts come from

**In the UI (live Gemini), any destination works.** Nothing is looked up in a saved list:
- **Supervisor:** identifies the destination and both airports from the model's own knowledge. An unknown place stops the run with *"We couldn't find that destination"*.
- **Scout, stay and transit agents:** research places to see, places to stay and flights with **Google Search grounding**, in parallel (`app/providers/research.py`).
  - Each is two calls: a grounded call writes research notes, then a second call turns them into a fixed JSON schema. Search can't run in a call that has a response schema.
  - The server validates every entry before it is posted: places near the destination, prices within bounds, and flight times that are physically possible.
  - Findings are shared within one run, so budget re-plans don't search again. Nothing is kept across runs.
  - A trip close to home (the same airport, or airports under 100 km apart) needs no flight, so the transit agent doesn't search.
  - The stay agent aims for a room at about 30% of the budget per night near the scout's base, not simply the cheapest one. If flights leave less room, the budget guardrail's price caps bring it down in the next round.
  - Gemini sometimes answers "busy" (503 UNAVAILABLE under high demand, or 429). Every model call, by an agent or by research, then tries up to 7 times, pausing about 2, 4, 8, 16, 20 and 20 s in between, so a busy spell of about a minute only slows a run down. A research call that timed out is retried once. The policy lives in `app/config.py` (`MODEL_RETRY_*`).
  - Google's Search Suggestions and the sources are shown with the itinerary. Hotel rates and fares are typical prices found online, not quotes.

**Benchmarks and tests use the scripted model and a small curated catalog**, so they make no network calls. Both paths issue the same store operations; `tests/test_research.py` checks the op counts match.

## Storage arms

| Arm | Session log | Scratchpad |
|---|---|---|
| `valkey` | HASH + ZSET | HASH with EXPIRE, pipelined |
| `postgres` | JSONB, one statement per op | JSONB, `unnest` multi-row upsert |
| `postgres_cached` | Postgres | Postgres + separate Valkey cache (write-through) |

Both stores use **stock configs** capped at **1 CPU / 256 MB** each (`docker-compose.yml`).

## Quick start

```bash
docker compose up -d            # stores listen on 127.0.0.1 only
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # add GOOGLE_API_KEY: the UI always plans with live Gemini
.venv/bin/python -m uvicorn app.main:app --port 8080
.venv/bin/python -m pytest -q   # ~200 tests; scripted model and canned research, no API calls
```

The UI has two tabs, one per audience. One event stream per run feeds both.
- **Experience** (customer demo):
  - The trip form, with structured fields plus free text. Any destination.
  - The travel team's progress in plain language. Each agent shows its key finding and what it searched for, and on budget re-plans shows *"Over by $300. Asking the team for cheaper options (round 2 of 3)"*.
  - The finished day-by-day itinerary, including travel days for long flights, with *Researched with Google Search* (Google's Search Suggestions and the sources).
  - No timings, store names or model settings.
- **Under the Hood** (developers):
  - **Timeline:** every session and scratchpad operation of the run, plus agent start/finish, Google Search research steps and blackboard waits.
  - **Store panels:** the session (state + ADK events) and the shared scratchpad (value, writer, version, round, readers), shown **as they were at the selected step**.
  - **Controls:** Prev/Next, Replay and Live.
  - **Step detail:** the store's physical command, round trips, bytes and duration for that operation. For a research step: the searches Google ran, the sources read, and the search and structuring times.
  - **Raw store contents:** a raw read of the keys or rows the run left behind (`/api/inspect/{run_id}`).
  - **Store switch:** Valkey / PostgreSQL for the next run.

URL hooks for demos: `?tab=hood`, `?backend=postgres`, `?autorun=1`.

## Benchmarks (shell only)

The UI runs no benchmarks. All of these use the scripted model, so they make no API calls:

| Script | What it measures |
|---|---|
| `scripts/bench.sh` | Single session, interleaved A/B (Valkey vs Postgres) with warm-ups discarded and a bootstrap CI, then the 1/10/100 writes-per-step sweep. Wraps `scripts/bench.py` (`bench`, `sweep`, `probe`). |
| `scripts/load_campaign.sh` | Concurrent users (below), with recorded Gemini latency replayed. |
| `scripts/calibrate_llm.py` | Records real Gemini latency for the replay (makes API calls). |

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
app/providers/  live research (Gemini + Google Search), catalog and mock flights for
                benchmarks, travel legs, sun times
app/state/      Valkey / Postgres session services and scratchpads, backend registry,
                inspector (store layout + raw snapshot for Under the Hood)
app/bench/      interleaved benchmark, sweeps, concurrent-user load test
app/llm/        fake model with latency replay, latency model, Gemini probe
app/telemetry/  per-op instrumentation, JSONL op log, live state feed for the UI
app/static/     the two-tab UI (DOM APIs only, strict CSP)
scripts/        benchmarks, calibration, load test drivers
results/        committed benchmark data and latency trace
```
