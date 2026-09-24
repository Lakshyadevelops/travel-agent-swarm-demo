"""Drive a concurrent-user load test and report end-to-end latency.

Examples
  # user-count sweep, both stores at their default 1 CPU
  .venv/bin/python scripts/loadtest.py --arms valkey,postgres --users 50,200,500

  # same load, Postgres given 2 CPUs (container resized live via docker update)
  .venv/bin/python scripts/loadtest.py --arms postgres --users 500 --pg-cpus 2

Output: runs/load-<id>/results.json (+ raw per-session records) and a table.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app.bench.stats import compare, percentile  # noqa: E402

CONTAINERS = {
    "valkey": "travel-agent-swarm-valkey-1",
    "postgres": "travel-agent-swarm-postgres-1",
    "postgres_sync_off": "travel-agent-swarm-postgres-1",
    "postgres_cached": "travel-agent-swarm-postgres-1",
}
PG_CONNECTION_BUDGET = 40  # of max_connections=50; headroom for admin/health


def docker_update_cpus(container: str, cpus: float) -> None:
    subprocess.run(["docker", "update", "--cpus", str(cpus), container],
                   check=True, capture_output=True)


class StatsSampler(threading.Thread):
    """Samples a container's CPU% and memory while a level runs."""

    def __init__(self, container: str) -> None:
        super().__init__(daemon=True)
        self.container = container
        self.cpu: list[float] = []
        self.mem_mb: list[float] = []
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.is_set():
            try:
                out = subprocess.run(
                    ["docker", "stats", "--no-stream", "--format",
                     "{{.CPUPerc}}|{{.MemUsage}}", self.container],
                    capture_output=True, text=True, timeout=10).stdout.strip()
                cpu_s, mem_s = out.split("|")
                self.cpu.append(float(cpu_s.rstrip("%")))
                used = mem_s.split("/")[0].strip()
                val = float(used.rstrip("KMGiB"))
                self.mem_mb.append(val * (1024 if "GiB" in used else
                                          1 / 1024 if "KiB" in used else 1))
            except Exception:  # noqa: BLE001 - sampling must never kill the run
                pass
            self._halt.wait(2.0)

    def stop(self) -> None:
        self._halt.set()


def run_level(arm: str, users: int, args: argparse.Namespace, outdir: Path) -> dict:
    procs = max(1, min(args.procs, users))
    per = math.ceil(users / procs)
    env = dict(os.environ)
    pool = max(2, PG_CONNECTION_BUDGET // procs)
    env.update(POSTGRES_POOL_MAX=str(pool), POSTGRES_MAX_CONCURRENCY=str(pool),
               POSTGRES_POOL_MIN="1", PYTHONWARNINGS="ignore")

    t0 = time.time() + 8.0  # time for workers to import ADK and open pools
    sampler = StatsSampler(CONTAINERS[arm])
    children = []
    for p in range(procs):
        lo, hi = p * per, min(users, (p + 1) * per)
        if lo >= hi:
            continue
        out = outdir / f"{arm}-u{users}-p{p}.json"
        cmd = [sys.executable, "-m", "app.bench.loadtest", "worker",
               "--arm", arm, "--uid-start", str(lo), "--uid-end", str(hi),
               "--t0", str(t0), "--warmup", str(args.warmup),
               "--measure", str(args.measure), "--ramp", str(args.ramp),
               "--writes", str(args.writes), "--provider-ms", str(args.provider_ms),
               "--llm-scale", str(args.llm_scale), "--out", str(out)]
        children.append((subprocess.Popen(cmd, cwd=REPO, env=env,
                                          stdout=subprocess.DEVNULL,
                                          stderr=open(str(out) + ".log", "w")), out))

    while time.time() < t0 + args.warmup:
        time.sleep(0.5)
    sampler.start()  # sample only during the measured window
    for proc, _ in children:
        proc.wait()
    sampler.stop()

    sessions, cpu_s, wall_s, evicted, model = [], 0.0, 0.0, 0, None
    for proc, out in children:
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"worker failed, see {out}.log")
        r = json.loads(out.read_text())
        sessions += r["sessions"]
        cpu_s += r["cpu_s"]
        wall_s = max(wall_s, r["wall_s"])
        evicted = max(evicted, r["evicted_keys"])
        model = r["latency_model"]

    measured = [s for s in sessions if s["measured"]]
    ok = [s for s in measured if s["ok"] and s["e2e_ms"] is not None]
    e2e = [s["e2e_ms"] for s in ok]
    return {
        "arm": arm, "users": users, "procs": procs, "pg_pool_per_proc": pool,
        "n": len(ok), "failed": len(measured) - len(ok),
        "errors_sample": sorted({s.get("error", "state_error") for s in measured
                                 if not s["ok"]})[:5],
        "p50_s": round(percentile(e2e, 50) / 1000, 3),
        "p95_s": round(percentile(e2e, 95) / 1000, 3),
        "p99_s": round(percentile(e2e, 99) / 1000, 3),
        "max_s": round(max(e2e) / 1000, 3) if e2e else None,
        "sessions_per_min": round(len(ok) / (args.measure / 60), 1),
        "store_cpu_pct_avg": round(sum(sampler.cpu) / len(sampler.cpu), 1) if sampler.cpu else None,
        "store_cpu_pct_max": round(max(sampler.cpu), 1) if sampler.cpu else None,
        "store_mem_mb_max": round(max(sampler.mem_mb), 1) if sampler.mem_mb else None,
        "client_cores_used": round(cpu_s / wall_s, 2) if wall_s else None,
        "client_cores_available": procs,
        "evicted_keys": evicted,
        "latency_model": model,
        "_sessions": {f"{s['uid']}:{s['i']}": s["e2e_ms"] for s in ok},
    }


def paired_delta(base: dict, other: dict) -> dict | None:
    keys = sorted(set(base["_sessions"]) & set(other["_sessions"]))
    if len(keys) < 10:
        return None
    b = [base["_sessions"][k] for k in keys]
    o = [other["_sessions"][k] for k in keys]
    d = compare(base["arm"], b, other["arm"], o, resamples=2000)
    return {"n_pairs": len(keys), "median_delta_ms": d.delta,
            "ci95_ms": [d.ci_low, d.ci_high], "significant": d.significant,
            "verdict": d.verdict}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="valkey,postgres")
    ap.add_argument("--users", default="50,200,500")
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--warmup", type=float, default=60)
    ap.add_argument("--measure", type=float, default=120)
    ap.add_argument("--ramp", type=float, default=45)
    ap.add_argument("--writes", type=int, default=1)
    ap.add_argument("--provider-ms", type=int, default=40)
    ap.add_argument("--llm-scale", type=float, default=1.0)
    ap.add_argument("--pg-cpus", type=float, default=1.0)
    ap.add_argument("--valkey-cpus", type=float, default=1.0)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    arms = args.arms.split(",")
    levels = [int(u) for u in args.users.split(",")]
    run_id = f"load-{uuid.uuid4().hex[:6]}" + (f"-{args.tag}" if args.tag else "")
    outdir = REPO / "runs" / run_id
    outdir.mkdir(parents=True)

    docker_update_cpus(CONTAINERS["postgres"], args.pg_cpus)
    docker_update_cpus(CONTAINERS["valkey"], args.valkey_cpus)

    results = []
    try:
        for li, users in enumerate(levels):
            # Alternate arm order per level so drift doesn't always favour one arm.
            order = arms if li % 2 == 0 else list(reversed(arms))
            level = {}
            for arm in order:
                print(f"[{time.strftime('%H:%M:%S')}] {arm} users={users} ...", flush=True)
                r = run_level(arm, users, args, outdir)
                level[arm] = r
                print(f"   p50={r['p50_s']}s p95={r['p95_s']}s p99={r['p99_s']}s "
                      f"n={r['n']} failed={r['failed']} store_cpu={r['store_cpu_pct_avg']}% "
                      f"(max {r['store_cpu_pct_max']}%) client_cores={r['client_cores_used']}"
                      f"/{r['client_cores_available']}", flush=True)
            deltas = {}
            if "postgres" in level:
                for arm in level:
                    if arm != "postgres":
                        deltas[f"postgres->{arm}"] = paired_delta(level["postgres"], level[arm])
            results.append({"users": users, "arms": level, "deltas": deltas})
            for k, v in deltas.items():
                if v:
                    print(f"   {k}: {v['verdict']}", flush=True)
    finally:
        # Restore the documented 1-CPU parity envelope.
        docker_update_cpus(CONTAINERS["postgres"], 1.0)
        docker_update_cpus(CONTAINERS["valkey"], 1.0)

    manifest = {k: v for k, v in vars(args).items()}
    manifest["run_id"] = run_id
    (outdir / "results.json").write_text(json.dumps(
        {"manifest": manifest, "levels": results}, indent=1))
    print(f"\nresults: {outdir / 'results.json'}")


if __name__ == "__main__":
    main()
