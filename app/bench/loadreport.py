"""Pure helpers for the concurrent-user load test: sizing, diagnosis, report.

Kept free of I/O so tests can pin them down. scripts/loadtest.py drives the
run; app/bench/loadtest.py is the worker.
"""

from __future__ import annotations

import math
from typing import Any

from app.bench.stats import percentile

USERS_PER_PROC = 100  # ~100 virtual users keep one worker's event loop light
MIN_PROCS = 4
PG_ADMIN_HEADROOM = 10  # connections left for health checks, psql, VACUUM

# A level is "client-bound" when the load generator, not the store, set the
# latency: its event loops woke late, or the machine ran out of CPU.
LOOP_LAG_P99_LIMIT_MS = 100.0
MACHINE_CPU_LIMIT_PCT = 85.0


def choose_procs(users: int, cores: int, requested: str | int = "auto") -> int:
    """Worker processes for a level: ~100 users each, at most half the cores."""
    if str(requested) != "auto":
        return max(1, min(int(requested), users))
    cap = max(MIN_PROCS, cores // 2)
    return max(1, min(users, max(MIN_PROCS, min(cap, math.ceil(users / USERS_PER_PROC)))))


def pg_pool_per_proc(max_connections: int, procs: int) -> int:
    """Split the server's connection budget evenly across worker processes."""
    budget = max(2, max_connections - PG_ADMIN_HEADROOM)
    return max(2, budget // max(1, procs))


def lag_summary(lags_ms: list[float]) -> dict[str, float | None]:
    if not lags_ms:
        return {"p50_ms": None, "p99_ms": None, "max_ms": None}
    return {"p50_ms": round(percentile(lags_ms, 50), 1),
            "p99_ms": round(percentile(lags_ms, 99), 1),
            "max_ms": round(max(lags_ms), 1)}


def client_bound(loop_lag_p99_ms: float | None, machine_cpu_pct: float | None) -> list[str]:
    """Why the load generator may have set the latency; empty when it didn't."""
    reasons = []
    if loop_lag_p99_ms is not None and loop_lag_p99_ms > LOOP_LAG_P99_LIMIT_MS:
        reasons.append(f"event-loop lag p99 {loop_lag_p99_ms:.0f} ms > "
                       f"{LOOP_LAG_P99_LIMIT_MS:.0f} ms")
    if machine_cpu_pct is not None and machine_cpu_pct > MACHINE_CPU_LIMIT_PCT:
        reasons.append(f"machine CPU {machine_cpu_pct:.0f}% > {MACHINE_CPU_LIMIT_PCT:.0f}%")
    return reasons


def _fmt(v: Any, suffix: str = "") -> str:
    return "–" if v is None else f"{v}{suffix}"


def render_summary(results: dict[str, Any]) -> str:
    """results.json -> a Markdown report: one row per (level, store)."""
    m = results["manifest"]
    env = m.get("environment", {})
    lines = [f"# Load test {m['run_id']}", ""]
    lines.append(
        f"Workload: {m['writes']} scratchpad write(s) per agent step · ramp {m['ramp']:.0f} s · "
        f"warm-up {m['warmup']:.0f} s · measured {m['measure']:.0f} s · "
        f"host {env.get('host_cores', '?')} cores · commit {env.get('git_commit', '?')}")
    stores = env.get("stores", {})
    for name, s in stores.items():
        lines.append(f"- **{name}**: {s.get('cpus', '?')} CPU, {s.get('memory', '?')} RAM; "
                     + ", ".join(f"{k}={v}" for k, v in s.get("settings", {}).items()))
    lines += ["", "End-to-end latency per planning session (what a user waits for).", "",
              "| Users | Store | p50 / p95 / p99 (s) | Sessions/min | Failed | "
              "Store CPU avg / max | Store mem max | Client cores | Loop lag p99 | "
              "Machine CPU | Client-bound? |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    base_p50: dict[str, float] = {}
    for level in results["levels"]:
        for arm, r in level["arms"].items():
            base_p50.setdefault(arm, r["p50_s"])
            bound = r.get("client_bound") or []
            lines.append(
                f"| {level['users']:,} | {arm} | {r['p50_s']} / {r['p95_s']} / {r['p99_s']} | "
                f"{r['sessions_per_min']:,} | {r['failed']} | "
                f"{_fmt(r['store_cpu_pct_avg'], '%')} / {_fmt(r['store_cpu_pct_max'], '%')} | "
                f"{_fmt(r['store_mem_mb_max'], ' MB')} | "
                f"{_fmt(r['client_cores_used'])} / {r['client_cores_available']} | "
                f"{_fmt((r.get('loop_lag') or {}).get('p99_ms'), ' ms')} | "
                f"{_fmt(r.get('machine_cpu_pct_avg'), '%')} | "
                f"{'⚠ ' + '; '.join(bound) if bound else 'no'} |")
    lines += ["", "Paired Postgres → other store deltas (same trip, same replayed model "
              "latency; bootstrap 95% CI):", ""]
    for level in results["levels"]:
        flagged = any(r.get("client_bound") for r in level["arms"].values())
        for key, d in (level.get("deltas") or {}).items():
            if d:
                note = " (⚠ client-bound level: not a store result)" if flagged else ""
                lines.append(f"- {level['users']:,} users: {d['verdict']}{note}")
    lines += ["", "Slowdown vs the lowest level (p50):", ""]
    for level in results["levels"]:
        parts = [f"{arm} ×{r['p50_s'] / base_p50[arm]:.2f}"
                 for arm, r in level["arms"].items() if base_p50.get(arm)]
        lines.append(f"- {level['users']:,} users: " + ", ".join(parts))
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------- the matrix
def _store_cpus(manifest: dict[str, Any], arm: str) -> str:
    stores = (manifest.get("environment") or {}).get("stores") or {}
    return str((stores.get(arm) or {}).get("cpus", "?"))


def _gap(delta: dict[str, Any] | None) -> str:
    """Paired Valkey − Postgres median, as the reader sees it."""
    if not delta:
        return "–"
    ms = delta["median_delta_ms"]
    lo, hi = delta["ci95_ms"]
    if abs(ms) >= 1000:
        text, ci = f"{ms / 1000:+,.1f} s", f"[{lo / 1000:+,.1f}, {hi / 1000:+,.1f}]"
    else:
        text, ci = f"{ms:+,.0f} ms", f"[{lo:+,.0f}, {hi:+,.0f}]"
    if not delta.get("significant"):
        return f"{text} (not significant)"
    return f"{text} {ci}"


def render_matrix(docs: list[dict[str, Any]]) -> str:
    """Several results.json → one report across store sizes and user levels.

    Rows are keyed by (writes per step, store vCPUs, users, store); a later
    document overrides an earlier one for the same key, so re-runs win.
    """
    cells: dict[tuple[int, str, int, str], dict[str, Any]] = {}
    deltas: dict[tuple[int, str, int], dict[str, Any] | None] = {}
    meta: dict[tuple[int, str], dict[str, Any]] = {}
    for doc in docs:
        m = doc["manifest"]
        w = int(m["writes"])
        for level in doc["levels"]:
            users = int(level["users"])
            for arm, r in level["arms"].items():
                cpus = _store_cpus(m, arm)
                cells[(w, cpus, users, arm)] = r
                meta.setdefault((w, cpus), m)
            d = (level.get("deltas") or {}).get("postgres->valkey")
            if d or (w, _store_cpus(m, "postgres"), users) not in deltas:
                deltas[(w, _store_cpus(m, "postgres"), users)] = d
    if not cells:
        return "No levels found.\n"

    def num(s: str) -> float:
        try:
            return float(s)
        except ValueError:
            return math.inf

    workloads = sorted({k[0] for k in cells})
    sizes = sorted({k[1] for k in cells}, key=num)
    arms = ["valkey", "postgres"] + sorted({k[3] for k in cells} - {"valkey", "postgres"})
    arms = [a for a in arms if any(k[3] == a for k in cells)]
    env = next(iter(meta.values())).get("environment", {})
    lines = ["# Valkey vs Postgres across store sizes and concurrent users", "",
             f"Host {env.get('host_cores', '?')} cores · commit {env.get('git_commit', '?')} · "
             "end-to-end time per planning session (what a user waits for) · LLM latency "
             "replayed from the committed trace, zero API calls.", ""]
    for cpus in sizes:
        m = next(mm for (w, c), mm in meta.items() if c == cpus)
        for arm in arms:
            s = ((m.get("environment") or {}).get("stores") or {}).get(arm) or {}
            if str(s.get("cpus")) == cpus:
                lines.append(f"- **{arm} {cpus} vCPU**: {s.get('memory', '?')} RAM; "
                             + ", ".join(f"{k}={v}" for k, v in (s.get("settings") or {}).items()))
    lines.append("")

    for w in workloads:
        users_all = sorted({k[2] for k in cells if k[0] == w})
        lines += [f"## {w} scratchpad write{'s' if w != 1 else ''} per agent step", ""]
        # Pivot: median session time per (size, store), plus the paired gap.
        head = ["Users"]
        for cpus in sizes:
            head += [f"{a.capitalize()} {cpus} vCPU · p50" for a in arms]
            head.append(f"Gap {cpus} vCPU (Valkey − Postgres)")
        lines += ["Median session time, seconds. Gap = paired median difference on the same "
                  "trips, bootstrap 95% CI.", "",
                  "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
        for users in users_all:
            row = [f"{users:,}"]
            for cpus in sizes:
                for a in arms:
                    r = cells.get((w, cpus, users, a))
                    if not r:
                        row.append("–")
                        continue
                    flag = " ⚠" if r.get("client_bound") else ""
                    total = r["n"] + r["failed"]
                    if r["failed"]:
                        flag += f" ({100 * r['failed'] / total:.0f}% failed)"
                    row.append(f"{r['p50_s']:.2f}{flag}")
                row.append(_gap(deltas.get((w, cpus, users))))
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")
        # Detail: everything measured.
        lines += ["| Store vCPU | Users | Store | p50 / p95 / p99 (s) | Sessions/min | Failed | "
                  "Store CPU avg / max | Store mem max | Client-bound? |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for cpus in sizes:
            for users in users_all:
                for a in arms:
                    r = cells.get((w, cpus, users, a))
                    if not r:
                        continue
                    bound = r.get("client_bound") or []
                    lines.append(
                        f"| {cpus} | {users:,} | {a} | {r['p50_s']} / {r['p95_s']} / {r['p99_s']} | "
                        f"{r['sessions_per_min']:,} | {r['failed']} | "
                        f"{_fmt(r['store_cpu_pct_avg'], '%')} / {_fmt(r['store_cpu_pct_max'], '%')} | "
                        f"{_fmt(r['store_mem_mb_max'], ' MB')} | "
                        f"{'⚠ ' + '; '.join(bound) if bound else 'no'} |")
        lines.append("")
    lines.append("⚠ marks a level where the load generator, not the store, set the latency.")
    return "\n".join(lines) + "\n"
