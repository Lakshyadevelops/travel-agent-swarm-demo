"""Drive a concurrent-user load test and report end-to-end latency.

Examples
  # the 1000 / 2000-user campaign (stores on the prod-like profile)
  scripts/load_campaign_scale.sh

  # a user-count sweep on whatever the stores are currently sized to
  .venv/bin/python scripts/loadtest.py --arms valkey,postgres --users 300,1000,2000

  # same load, Postgres resized live to 2 CPUs (restored afterwards)
  .venv/bin/python scripts/loadtest.py --arms postgres --users 500 --pg-cpus 2

Output: runs/load-<id>/results.json (+ raw per-worker records), summary.md, and
a line per level on stdout. Stores are left at the size they were started with
(docker-compose.yml, or docker-compose.load.yml for the campaign) unless
--pg-cpus / --valkey-cpus ask otherwise.
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

from app.bench.loadreport import (  # noqa: E402
    choose_procs,
    client_bound,
    lag_summary,
    pg_pool_per_proc,
    render_summary,
)
from app.bench.stats import compare, percentile  # noqa: E402

CONTAINERS = {
    "valkey": "travel-agent-swarm-valkey-1",
    "postgres": "travel-agent-swarm-postgres-1",
    "postgres_sync_off": "travel-agent-swarm-postgres-1",
    "postgres_cached": "travel-agent-swarm-postgres-1",
}
PG = CONTAINERS["postgres"]
VALKEY = CONTAINERS["valkey"]
PG_SETTINGS = ("max_connections", "shared_buffers", "effective_cache_size", "work_mem",
               "synchronous_commit", "max_worker_processes", "max_wal_size")
VALKEY_SETTINGS = ("io-threads", "maxmemory", "maxmemory-policy", "appendonly", "save")
SESSION_TABLES = ("adk_sessions", "adk_events", "scratchpad")
READY_TIMEOUT_S = 300.0


def _run(cmd: list[str], timeout: float = 30.0) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True,
                          timeout=timeout).stdout.strip()


# ---------------------------------------------------------------- environment
def docker_limits(container: str) -> dict[str, str]:
    nano, mem = _run(["docker", "inspect", "-f",
                      "{{.HostConfig.NanoCpus}} {{.HostConfig.Memory}}", container]).split()
    return {"cpus": f"{int(nano) / 1e9:g}" if int(nano) else "unlimited",
            "memory": f"{int(mem) / 2**30:g} GB" if int(mem) else "unlimited"}


def docker_update_cpus(container: str, cpus: float) -> None:
    _run(["docker", "update", "--cpus", str(cpus), container])


def pg_settings() -> dict[str, str]:
    cmd = ["docker", "exec", PG, "psql", "-U", "swarm", "-d", "swarm", "-At"]
    for name in PG_SETTINGS:
        cmd += ["-c", f"SHOW {name}"]
    return dict(zip(PG_SETTINGS, _run(cmd).splitlines()))


def valkey_settings() -> dict[str, str]:
    lines = _run(["docker", "exec", VALKEY, "valkey-cli", "CONFIG", "GET",
                  *VALKEY_SETTINGS]).splitlines()
    return dict(zip(lines[::2], lines[1::2]))


def vacuum_postgres() -> None:
    """Clear the previous level's dead rows so levels don't carry over."""
    cmd = ["docker", "exec", PG, "psql", "-U", "swarm", "-d", "swarm", "-q"]
    for table in SESSION_TABLES:
        cmd += ["-c", f"VACUUM ANALYZE {table}"]
    _run(cmd, timeout=300)


def git_commit() -> str:
    try:
        sha = _run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"])
        dirty = _run(["git", "-C", str(REPO), "status", "--porcelain",
                      "--untracked-files=no"])
        return sha + ("+dirty" if dirty else "")
    except Exception:  # noqa: BLE001
        return "unknown"


def environment() -> dict:
    stores = {}
    for name, container, settings in (("valkey", VALKEY, valkey_settings),
                                      ("postgres", PG, pg_settings)):
        stores[name] = {**docker_limits(container), "settings": settings()}
    return {"host_cores": os.cpu_count(), "git_commit": git_commit(), "stores": stores}


# ---------------------------------------------------------------- samplers
class StatsSampler(threading.Thread):
    """Samples a container's CPU% (of one core) and memory while a level runs."""

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


def _cpu_times() -> tuple[int, int]:
    """(busy, total) jiffies across all cores, from /proc/stat."""
    fields = [int(x) for x in Path("/proc/stat").read_text().split("\n", 1)[0].split()[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)  # idle + iowait
    return sum(fields) - idle, sum(fields)


class MachineCpuSampler(threading.Thread):
    """Whole-machine CPU busy %: stores, load generator and everything else."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.pct: list[float] = []
        self._halt = threading.Event()

    def run(self) -> None:
        busy0, total0 = _cpu_times()
        while not self._halt.wait(2.0):
            busy1, total1 = _cpu_times()
            if total1 > total0:
                self.pct.append(100.0 * (busy1 - busy0) / (total1 - total0))
            busy0, total0 = busy1, total1

    def stop(self) -> None:
        self._halt.set()


# ---------------------------------------------------------------- one level
def _start_workers(arm: str, users: int, procs: int, pool: int,
                   args: argparse.Namespace, outdir: Path):
    per = math.ceil(users / procs)
    env = dict(os.environ)
    env.update(POSTGRES_POOL_MAX=str(pool), POSTGRES_MAX_CONCURRENCY=str(pool),
               POSTGRES_POOL_MIN="1", PYTHONWARNINGS="ignore")
    start_file = outdir / f"start-{arm}-u{users}.json"
    children = []
    for p in range(procs):
        lo, hi = p * per, min(users, (p + 1) * per)
        if lo >= hi:
            continue
        out = outdir / f"{arm}-u{users}-p{p}.json"
        cmd = [sys.executable, "-m", "app.bench.loadtest", "worker",
               "--arm", arm, "--uid-start", str(lo), "--uid-end", str(hi),
               "--start-file", str(start_file), "--ready-timeout", str(READY_TIMEOUT_S),
               "--warmup", str(args.warmup), "--measure", str(args.measure),
               "--ramp", str(args.ramp), "--writes", str(args.writes),
               "--provider-ms", str(args.provider_ms), "--llm-scale", str(args.llm_scale),
               "--out", str(out)]
        log = open(str(out) + ".log", "w")  # noqa: SIM115 - closed with the child
        children.append((subprocess.Popen(cmd, cwd=REPO, env=env,
                                          stdout=subprocess.DEVNULL, stderr=log), out, log))
    return children, start_file


def _release(children, start_file: Path) -> float:
    """Wait until every worker is ready, then give them one shared start time."""
    deadline = time.time() + READY_TIMEOUT_S
    while not all(Path(str(out) + ".ready").exists() for _, out, _ in children):
        for proc, out, _ in children:
            if proc.poll() is not None:
                raise RuntimeError(f"worker exited before starting, see {out}.log")
        if time.time() > deadline:
            raise RuntimeError("workers not ready in time")
        time.sleep(0.2)
    t0 = time.time() + 1.0
    tmp = start_file.with_suffix(".tmp")
    tmp.write_text(json.dumps({"t0": t0}))
    tmp.replace(start_file)  # atomic: workers never read a half-written file
    return t0


def run_level(arm: str, users: int, args: argparse.Namespace, outdir: Path,
              max_connections: int) -> dict:
    procs = choose_procs(users, os.cpu_count() or 1, args.procs)
    pool = pg_pool_per_proc(max_connections, procs)
    if arm.startswith("postgres"):
        vacuum_postgres()

    children, start_file = _start_workers(arm, users, procs, pool, args, outdir)
    try:
        t0 = _release(children, start_file)
    except Exception:
        for proc, _, _ in children:
            proc.kill()
        raise
    store = StatsSampler(CONTAINERS[arm])
    machine = MachineCpuSampler()
    while time.time() < t0 + args.warmup:
        time.sleep(0.5)
    store.start()  # sample only during the measured window
    machine.start()
    while time.time() < t0 + args.warmup + args.measure:
        time.sleep(0.5)
    store.stop()
    machine.stop()
    for proc, _, log in children:
        proc.wait()
        log.close()

    sessions, lags, cpu_s, wall_s, evicted, model = [], [], 0.0, 0.0, 0, None
    for proc, out, _ in children:
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"worker failed, see {out}.log")
        r = json.loads(out.read_text())
        sessions += r["sessions"]
        lags += r.get("loop_lag_ms", [])
        cpu_s += r["cpu_s"]
        wall_s = max(wall_s, r["wall_s"])
        evicted = max(evicted, r["evicted_keys"])
        model = r["latency_model"]

    measured = [s for s in sessions if s["measured"]]
    ok = [s for s in measured if s["ok"] and s["e2e_ms"] is not None]
    e2e = [s["e2e_ms"] for s in ok]
    lag = lag_summary(lags)
    machine_avg = round(sum(machine.pct) / len(machine.pct), 1) if machine.pct else None
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
        "store_cpu_pct_avg": round(sum(store.cpu) / len(store.cpu), 1) if store.cpu else None,
        "store_cpu_pct_max": round(max(store.cpu), 1) if store.cpu else None,
        "store_mem_mb_max": round(max(store.mem_mb), 1) if store.mem_mb else None,
        "client_cores_used": round(cpu_s / wall_s, 2) if wall_s else None,
        "client_cores_available": procs,
        "loop_lag": lag,
        "machine_cpu_pct_avg": machine_avg,
        "client_bound": client_bound(lag["p99_ms"], machine_avg),
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


def _write(outdir: Path, manifest: dict, results: list) -> None:
    doc = {"manifest": manifest, "levels": results}
    (outdir / "results.json").write_text(json.dumps(doc, indent=1))
    (outdir / "summary.md").write_text(render_summary(doc))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="valkey,postgres")
    ap.add_argument("--users", default="50,200,500")
    ap.add_argument("--procs", default="auto",
                    help="worker processes per level, or 'auto' (~100 users each)")
    ap.add_argument("--warmup", type=float, default=60)
    ap.add_argument("--measure", type=float, default=120)
    ap.add_argument("--ramp", type=float, default=45)
    ap.add_argument("--writes", type=int, default=1)
    ap.add_argument("--provider-ms", type=int, default=40)
    ap.add_argument("--llm-scale", type=float, default=1.0)
    ap.add_argument("--pg-cpus", type=float, default=None,
                    help="resize Postgres for this run (restored afterwards)")
    ap.add_argument("--valkey-cpus", type=float, default=None,
                    help="resize Valkey for this run (restored afterwards)")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    arms = args.arms.split(",")
    levels = [int(u) for u in args.users.split(",")]
    run_id = f"load-{uuid.uuid4().hex[:6]}" + (f"-{args.tag}" if args.tag else "")
    outdir = REPO / "runs" / run_id
    outdir.mkdir(parents=True)

    restore: list[tuple[str, float]] = []
    for container, cpus in ((PG, args.pg_cpus), (VALKEY, args.valkey_cpus)):
        if cpus is not None:
            before = docker_limits(container)["cpus"]
            restore.append((container, 0.0 if before == "unlimited" else float(before)))
            docker_update_cpus(container, cpus)

    manifest = {k: v for k, v in vars(args).items()}
    manifest["run_id"] = run_id
    manifest["environment"] = environment()
    max_connections = int(manifest["environment"]["stores"]["postgres"]["settings"]
                          .get("max_connections", 50))
    print(f"[{time.strftime('%H:%M:%S')}] {run_id}: host {os.cpu_count()} cores; "
          + "; ".join(f"{k} {v['cpus']} CPU / {v['memory']}"
                      for k, v in manifest["environment"]["stores"].items()), flush=True)

    results = []
    try:
        for li, users in enumerate(levels):
            # Alternate arm order per level so drift doesn't always favour one arm.
            order = arms if li % 2 == 0 else list(reversed(arms))
            level = {}
            for arm in order:
                print(f"[{time.strftime('%H:%M:%S')}] {arm} users={users} ...", flush=True)
                r = run_level(arm, users, args, outdir, max_connections)
                level[arm] = r
                flag = ("  ⚠ client-bound: " + "; ".join(r["client_bound"])
                        if r["client_bound"] else "")
                print(f"   p50={r['p50_s']}s p95={r['p95_s']}s p99={r['p99_s']}s "
                      f"n={r['n']} failed={r['failed']} store_cpu={r['store_cpu_pct_avg']}% "
                      f"(max {r['store_cpu_pct_max']}%) client_cores={r['client_cores_used']}"
                      f"/{r['client_cores_available']} loop_lag_p99={r['loop_lag']['p99_ms']}ms "
                      f"machine_cpu={r['machine_cpu_pct_avg']}%{flag}", flush=True)
            deltas = {}
            if "postgres" in level:
                for arm in level:
                    if arm != "postgres":
                        deltas[f"postgres->{arm}"] = paired_delta(level["postgres"], level[arm])
            results.append({"users": users, "arms": level, "deltas": deltas})
            for k, v in deltas.items():
                if v:
                    print(f"   {k}: {v['verdict']}", flush=True)
            _write(outdir, manifest, results)  # partial results survive a crash
    finally:
        for container, cpus in restore:
            docker_update_cpus(container, cpus)

    _write(outdir, manifest, results)
    print(f"\nresults: {outdir / 'results.json'}\nsummary: {outdir / 'summary.md'}")


if __name__ == "__main__":
    main()
