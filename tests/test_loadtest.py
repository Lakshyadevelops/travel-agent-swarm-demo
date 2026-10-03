"""Load-test sizing, client-bound diagnosis and the summary report."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.agents import callbacks
from app.bench.loadreport import (
    choose_procs,
    client_bound,
    lag_summary,
    pg_pool_per_proc,
    render_matrix,
    render_summary,
)

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("users, cores, expected", [
    (50, 64, 4),      # small runs still spread over a few workers
    (300, 64, 4),
    (1000, 64, 10),   # ~100 users per worker
    (2000, 64, 20),
    (5000, 64, 32),   # never more than half the cores
    (2000, 8, 4),     # small host: half the cores, at least 4
    (3, 64, 3),       # never more workers than users
])
def test_about_a_hundred_users_per_worker(users, cores, expected):
    assert choose_procs(users, cores) == expected


def test_an_explicit_worker_count_wins():
    assert choose_procs(1000, 64, "6") == 6
    assert choose_procs(2, 64, 6) == 2


def test_postgres_connections_are_shared_out_with_headroom():
    assert pg_pool_per_proc(100, 10) == 9   # 90 of 100, 10 kept for admin
    assert pg_pool_per_proc(100, 20) == 4
    assert pg_pool_per_proc(50, 6) == 6     # the old 1-CPU profile: 40 of 50
    assert pg_pool_per_proc(100, 64) == 2   # never below 2


def test_lag_percentiles():
    lags = [1.0] * 197 + [150.0, 150.0, 400.0]
    assert lag_summary(lags) == {"p50_ms": 1.0, "p99_ms": 150.0, "max_ms": 400.0}
    assert lag_summary([]) == {"p50_ms": None, "p99_ms": None, "max_ms": None}


def test_a_level_is_client_bound_when_the_loop_lags_or_the_machine_is_full():
    assert client_bound(12.0, 40.0) == []
    assert client_bound(None, None) == []
    assert client_bound(250.0, 40.0) == ["event-loop lag p99 250 ms > 100 ms"]
    assert client_bound(12.0, 91.0) == ["machine CPU 91% > 85%"]
    assert len(client_bound(250.0, 91.0)) == 2


def _arm(p50: float, bound: list[str] | None = None) -> dict:
    return {"p50_s": p50, "p95_s": p50 * 1.4, "p99_s": p50 * 1.6, "sessions_per_min": 1234.5,
            "n": 1000, "failed": 0, "store_cpu_pct_avg": 55.0, "store_cpu_pct_max": 120.0,
            "store_mem_mb_max": 300.0, "client_cores_used": 4.2, "client_cores_available": 10,
            "loop_lag": {"p50_ms": 1.0, "p99_ms": 9.0, "max_ms": 30.0},
            "machine_cpu_pct_avg": 20.0, "client_bound": bound or []}


def test_the_summary_has_a_row_per_store_and_flags_client_bound_levels():
    doc = {
        "manifest": {"run_id": "load-abc-scale-w1", "writes": 1, "ramp": 60, "warmup": 105,
                     "measure": 180,
                     "environment": {"host_cores": 64, "git_commit": "d7d714d", "stores": {
                         "valkey": {"cpus": "4", "memory": "2 GB",
                                    "settings": {"io-threads": "4"}},
                         "postgres": {"cpus": "4", "memory": "2 GB",
                                      "settings": {"shared_buffers": "512MB"}}}}},
        "levels": [
            {"users": 300, "arms": {"valkey": _arm(37.5), "postgres": _arm(37.6)},
             "deltas": {"postgres->valkey": {"verdict": "no detectable difference"}}},
            {"users": 2000, "arms": {"valkey": _arm(45.0, ["machine CPU 91% > 85%"]),
                                     "postgres": _arm(75.2)},
             "deltas": {"postgres->valkey": {"verdict": "valkey is reliably faster"}}},
        ],
    }
    md = render_summary(doc)
    assert "valkey**: 4 CPU, 2 GB RAM; io-threads=4" in md
    assert md.count("\n| 300 |") == 2 and md.count("\n| 2,000 |") == 2
    assert "⚠ machine CPU 91% > 85%" in md
    assert "2,000 users: valkey is reliably faster (⚠ client-bound level" in md
    assert "300 users: no detectable difference\n" in md
    assert "postgres ×2.00" in md  # 75.2 / 37.6


def _doc(cpus: str, writes: int, levels: list[tuple[int, float, float, dict | None]]) -> dict:
    return {
        "manifest": {"run_id": f"load-x-{cpus}cpu-w{writes}", "writes": writes,
                     "environment": {"host_cores": 64, "git_commit": "abc", "stores": {
                         "valkey": {"cpus": cpus, "memory": "2 GB", "settings": {"io-threads": cpus}},
                         "postgres": {"cpus": cpus, "memory": "2 GB",
                                      "settings": {"max_worker_processes": cpus}}}}},
        "levels": [{"users": u, "arms": {"valkey": _arm(v), "postgres": _arm(p)},
                    "deltas": {"postgres->valkey": d}} for u, v, p, d in levels],
    }


def test_the_matrix_merges_sizes_and_levels_into_one_table():
    sig = {"median_delta_ms": -142.2, "ci95_ms": [-165.8, -115.7], "significant": True}
    huge = {"median_delta_ms": -27295.2, "ci95_ms": [-27485.1, -27068.5], "significant": True}
    ns = {"median_delta_ms": -31.0, "ci95_ms": [-47.0, 3.0], "significant": False}
    docs = [
        _doc("2", 10, [(1, 36.9, 36.9, None), (1000, 37.3, 37.4, sig), (2000, 37.4, 65.6, huge)]),
        _doc("4", 10, [(1, 36.9, 36.9, ns), (2000, 37.4, 37.5, sig)]),
        _doc("4", 10, [(2000, 37.4, 37.6, sig)]),  # a re-run of one level wins
    ]
    md = render_matrix(docs)
    assert "## 10 scratchpad writes per agent step" in md
    assert "**postgres 2 vCPU**: 2 GB RAM; max_worker_processes=2" in md
    header = next(line for line in md.splitlines() if line.startswith("| Users |"))
    assert header.split(" | ")[1:4] == ["Valkey 2 vCPU · p50", "Postgres 2 vCPU · p50",
                                        "Gap 2 vCPU (Valkey − Postgres)"]
    assert "| 1 | 36.90 | 36.90 | – | 36.90 | 36.90 | -31 ms (not significant) |" in md
    assert "| 1,000 | 37.30 | 37.40 | -142 ms [-166, -116] | – | – | – |" in md
    assert "| 2,000 | 37.40 | 65.60 | -27.3 s [-27.5, -27.1] | 37.40 | 37.60 |" in md
    # The detail table lists each measured cell once; the re-run replaced the first.
    assert md.count("| 4 | 2,000 | postgres | 37.6 /") == 1
    assert "| 4 | 2,000 | postgres | 37.5 /" not in md


def test_the_campaign_script_parses_and_the_profile_merges():
    subprocess.run(["bash", "-n", str(REPO / "scripts/load_campaign_scale.sh")], check=True)
    subprocess.run(["bash", "-n", str(REPO / "scripts/load_campaign_matrix.sh")], check=True)
    out = subprocess.run(
        ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.load.yml",
         "config", "--format", "json"],
        cwd=REPO, capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("docker compose not available")
    services = json.loads(out.stdout)["services"]
    for name in ("valkey", "postgres"):
        limits = services[name]["deploy"]["resources"]["limits"]
        assert float(limits["cpus"]) == 4.0 and int(limits["memory"]) == 2 * 2**30
    assert "--io-threads" in services["valkey"]["command"]
    assert "shared_buffers=512MB" in services["postgres"]["command"]
    assert "synchronous_commit=off" not in " ".join(services["postgres"]["command"])
    # The demo's own profile is untouched.
    base = json.loads(subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=REPO, capture_output=True, text=True, check=True).stdout)["services"]
    assert float(base["postgres"]["deploy"]["resources"]["limits"]["cpus"]) == 1.0


def test_the_driver_and_worker_parse_their_arguments():
    for cmd in (["scripts/loadtest.py", "--help"],
                ["-m", "app.bench.loadtest", "worker", "--help"]):
        out = subprocess.run([sys.executable, *cmd], cwd=REPO, capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
    assert "--procs" in subprocess.run(
        [sys.executable, "scripts/loadtest.py", "--help"], cwd=REPO,
        capture_output=True, text=True).stdout


def test_only_calibration_records_the_latency_trace(tmp_path, monkeypatch):
    """Demo runs (live research, retries) must not change what load tests replay."""
    from app.agents.runtime import CURRENT_RUN_ID
    from app.llm import latency

    trace = tmp_path / "llm_trace.jsonl"
    monkeypatch.setattr(latency, "TRACE_PATH", trace)
    run_token = CURRENT_RUN_ID.set("valkey-00000001")

    def one_invocation() -> None:
        callbacks._LLM[callbacks._key("stay_agent")] = {
            "tool_ms": 900.0, "final_ms": 1200.0, "calls": 2, "error": None}
        callbacks._flush_llm_trace("stay_agent")

    one_invocation()  # a demo run: nothing recorded
    assert not trace.exists()

    token = callbacks.RECORD_LLM_TRACE.set(True)  # what scripts/calibrate_llm.py does
    try:
        one_invocation()
    finally:
        callbacks.RECORD_LLM_TRACE.reset(token)
        CURRENT_RUN_ID.reset(run_token)
    row = json.loads(trace.read_text())
    assert (row["role"], row["tool_ms"], row["final_ms"]) == ("stay_agent", 900.0, 1200.0)


def test_the_load_test_replays_the_committed_trace_by_default():
    from app.llm.latency import LatencyModel, TRACE_PATH

    if TRACE_PATH.exists():
        pytest.skip("a local calibration trace exists and takes precedence by design")
    assert LatencyModel.default().source == "trace:results/llm_trace.jsonl"
