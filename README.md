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
.venv/bin/python -m pytest -q   # ~217 tests; scripted model and canned research, no API calls
```

The UI has two tabs, one per audience. One event stream per run feeds both.
- **Experience** (customer demo):
  - The trip form, with structured fields plus free text. Any destination.
  - The travel team's progress in plain language. Each agent shows its key finding and what it searched for, and on budget re-plans shows *"Over by $300. Asking the team for cheaper options (round 2 of 3)"*.
  - The finished day-by-day itinerary, including travel days for long flights. The searches, sources and Google's Search Suggestions behind it are in *Under the Hood*.
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
| `scripts/load_campaign.sh` | Concurrent users on the demo's 1 CPU / 256 MB stores (results below), with recorded Gemini latency replayed. |
| `scripts/load_campaign_scale.sh` | 300 / 1,000 / 2,000 concurrent users: a small Valkey vs a small Postgres, both 4 vCPU / 2 GB with prod-like configs (`docker-compose.load.yml`), on the same machine. Restores the demo profile afterwards. |
| `scripts/load_campaign_matrix.sh` | The full grid (results below): both stores at 2 and 4 vCPU, 1 / 10 / 100 / 300 / 1,000 / 3,000 users, 1 and 10 writes per step. About 5.5 h. `scripts/load_matrix_table.py` merges any set of runs into one table. |
| `scripts/calibrate_llm.py` | Records real Gemini latency for the replay (makes API calls). |

## Load testing without spending API quota

1. **Record** real model latency once:
   `scripts/calibrate_llm.py --runs 8`. That is about 120 Gemini calls, and it appends to `runs/llm_trace.jsonl`.
   A trace from 8 real `gemini-3.5-flash` sessions is already committed at `results/llm_trace.jsonl` and is used automatically.
2. **Replay:** the scripted fake model waits for a recorded latency pair from the same agent role before each response. The load test makes no API calls.
3. **Drive load:** run `scripts/loadtest.py --arms valkey,postgres --users 10,100,300,600 --writes 1`. It simulates closed-loop virtual users across worker processes (about 100 users each by default, `--procs auto`).
   - Session *i* of user *u* gets the same trip and the same replayed latencies on every arm. Deltas are computed on these matched sessions, with a bootstrap 95% CI.
   - Store CPU and memory, the load generator's own CPU, machine CPU and each worker's event-loop lag are recorded next to latency.
   - **A level is marked client-bound** when the event-loop lag p99 exceeds 100 ms or the machine averages over 85% CPU. At that point the load generator, not the store, is setting the latency, so the level isn't a store result.
   - The stores stay at whatever size they were started with. The Postgres connection budget is read from the server (`max_connections` − 10, split across workers). Each run records store sizes and settings, host cores and the git commit in `results.json`, and writes a `summary.md`.
   - Size the prod-like profile with `LOAD_STORE_CPUS`, `LOAD_STORE_MEM` and friends (see the header of `docker-compose.load.yml`).

## Results: 1 to 3,000 users on 2 vCPU and 4 vCPU stores (2 GB each, prod-like configs)

`scripts/load_campaign_matrix.sh`, on a 64-core host. End-to-end latency per planning session (~37 s median, mostly replayed model time). Every level ran with zero failed sessions and none was client-bound (event-loop lag p99 ≤ 9 ms, machine CPU ≤ 25%). The 1- and 10-user levels were measured for 8 min (n = 11 and 121 sessions per store), the others for 3 min (n = 466 to 13,700). Gap = Valkey − Postgres, paired median on the same trips with a bootstrap 95% CI. Slim per-run data: `results/load_matrix/`; full tables: `results/load_matrix_summary.md`; logs: `results/campaign_matrix*.log`.

**App as built (1 scratchpad write per agent step).** Median seconds per session.

| Users | Valkey 2 vCPU | Postgres 2 vCPU | Gap at 2 vCPU | Valkey 4 vCPU | Postgres 4 vCPU | Gap at 4 vCPU |
|---|---|---|---|---|---|---|
| 1 | 36.88 | 36.92 | −40 ms [−52, −21] | 36.89 | 36.92 | −35 ms [−100, −24] |
| 10 | 36.97 | 37.00 | −28 ms, not significant | 36.98 | 37.00 | −26 ms, not significant |
| 100 | 37.48 | 37.51 | −23 ms [−43, −18] | 37.48 | 37.52 | −39 ms [−87, −27] |
| 300 | 37.35 | 37.37 | −21 ms, not significant | 37.34 | 37.36 | −28 ms [−53, −8] |
| 1,000 | 37.23 | 37.28 | −42 ms [−54, −23] | 37.23 | 37.28 | −43 ms [−59, −26] |
| 2,000 | 37.35 | 37.41 | −65 ms [−83, −44] | 37.35 | 37.39 | −35 ms [−53, −28] |
| 3,000 | 37.36 | **38.17** | **−808 ms** [−856, −762] | 37.36 | 37.41 | −50 ms [−55, −33] |

Store CPU at 3,000 users: Valkey 24% of one core at either size; Postgres 174% of its 2 vCPUs (saturating, hence the 0.8 s) and 162% at 4 vCPU.

**Chatty agents (10 writes per step).**

| Users | Valkey 2 vCPU | Postgres 2 vCPU | Gap at 2 vCPU | Valkey 4 vCPU | Postgres 4 vCPU | Gap at 4 vCPU |
|---|---|---|---|---|---|---|
| 1 | 36.91 | 36.99 | −75 ms [−148, −65] | 36.91 | 36.98 | −78 ms [−241, −58] |
| 10 | 36.99 | 37.07 | −79 ms [−99, −20] | 36.98 | 37.06 | −76 ms, not significant |
| 100 | 37.51 | 37.58 | −71 ms [−118, −38] | 37.50 | 37.58 | −77 ms [−116, −38] |
| 300 | 37.36 | 37.46 | −100 ms [−135, −76] | 37.36 | 37.47 | −105 ms [−139, −72] |
| 1,000 | 37.27 | 37.52 | −258 ms [−290, −206] | 37.26 | 37.39 | −136 ms [−154, −112] |
| 2,000 | 37.38 | **66.91** | **−29.0 s** [−29.2, −28.8] | 37.39 | 37.57 | −177 ms [−207, −162] |
| 3,000 | 37.42 | **99.89** | **−54.0 s** [−54.7, −53.5] | 37.41 | **51.69** | **−14.3 s** [−14.4, −14.2] |

Store CPU and memory at the top levels (avg CPU as % of one core / max memory):

| Workload | Users | Valkey 2 vCPU | Postgres 2 vCPU | Valkey 4 vCPU | Postgres 4 vCPU |
|---|---|---|---|---|---|
| 1 write | 2,000 | 17% / 67 MB | 110% / 633 MB | 17% / 68 MB | 111% / 626 MB |
| 1 write | 3,000 | 24% / 89 MB | 174% / 808 MB | 25% / 99 MB | 162% / 801 MB |
| 10 writes | 1,000 | 19% / 118 MB | 156% / 859 MB | 19% / 112 MB | 153% / 868 MB |
| 10 writes | 2,000 | 89% / 195 MB | 202% / 919 MB | 100% / 195 MB | 317% / 912 MB |
| 10 writes | 3,000 | 147% / 295 MB | 201% / 933 MB | 146% / 276 MB | 401% / 953 MB |

What this says:
- Up to 1,000 users the store is invisible to a user on either size: at most 0.26 s on a 37 s wait. For the app as built that holds through 3,000 users on 4 vCPU and 2,000 on 2 vCPU.
- The difference is headroom. Postgres saturates 2 vCPU at 2,000 chatty users (29 s slower, p99 134 s) and 4 vCPU at 3,000 (14 s slower); on 2 vCPU at 3,000 it is 54 s slower with a p99 of 200 s, and even the app as built loses 0.8 s. Valkey holds 37.4 s in every cell, using at most 1.5 cores and under 300 MB where Postgres uses every core it has and about 950 MB.
- An earlier pass of the 3,000-user Postgres levels failed 90% of sessions on `too many clients already`: each worker's unused sync-off pool held one idle connection, and 30 workers overran `max_connections`. That pool is now lazy; those four levels were re-run and the tables above use the re-runs.

## Results: 1 CPU / 256 MB stores (the demo's stock profile)

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
