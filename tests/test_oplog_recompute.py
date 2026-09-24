"""The op log must be a faithful, complete record.

If the raw log cannot reproduce the live aggregates, then "recompute instead of
re-run" is a false promise and every published number depends on a process that
has already exited.
"""

from __future__ import annotations

import json
import uuid

from app.telemetry.instrument import (
    CURRENT_ITERATION,
    CURRENT_OPLOG,
    CURRENT_RUN,
    CURRENT_VARIANT,
    timed,
)
from app.telemetry.metrics import RunTelemetry, StateOp, interval_union_ms
from app.telemetry.oplog import OpLog


async def test_oplog_roundtrip_reproduces_aggregates(tmp_path):
    run_id = f"t-{uuid.uuid4().hex[:8]}"
    oplog = OpLog(run_id, base_dir=tmp_path)
    oplog.start()

    tel = RunTelemetry(run_id, "test", "variant-a")
    CURRENT_RUN.set(tel)
    CURRENT_OPLOG.set(oplog)
    CURRENT_VARIANT.set("variant-a")
    CURRENT_ITERATION.set(4)

    for i in range(10):
        async with timed("scratch_write", "test", f"k{i}") as box:
            box.payload_bytes = i * 10

    # An error must be captured, not swallowed.
    try:
        async with timed("scratch_read", "test", "boom"):
            raise RuntimeError("simulated failure")
    except RuntimeError:
        pass

    await oplog.close()
    CURRENT_RUN.set(None)
    CURRENT_OPLOG.set(None)

    lines = oplog.path.read_text().strip().split("\n")
    assert len(lines) == 11, "op log lost records"

    replayed = [StateOp(**json.loads(line)) for line in lines]

    # Every field the live aggregates depend on must survive the round trip.
    assert len(replayed) == len(tel.ops)
    assert [o.op_type for o in replayed] == [o.op_type for o in tel.ops]
    assert [round(o.duration_ms, 6) for o in replayed] == [
        round(o.duration_ms, 6) for o in tel.ops
    ]
    assert all(o.variant_id == "variant-a" for o in replayed)
    assert all(o.iteration == 4 for o in replayed)

    errored = [o for o in replayed if o.error]
    assert len(errored) == 1
    assert "simulated failure" in errored[0].error

    # And the derived metric recomputes identically.
    live_busy = interval_union_ms([o for o in tel.ops if o.error is None])
    replay_busy = interval_union_ms([o for o in replayed if o.error is None])
    assert abs(live_busy - replay_busy) < 1e-9


async def test_manifest_written(tmp_path):
    oplog = OpLog(f"t-{uuid.uuid4().hex[:8]}", base_dir=tmp_path)
    oplog.write_manifest({"arms": ["valkey"], "repeats": 30})
    data = json.loads((oplog.dir / "manifest.json").read_text())
    assert data["repeats"] == 30


async def test_warmups_are_recorded_but_distinguishable(tmp_path):
    """Warm-ups belong in the log; they just must not enter the statistics."""
    oplog = OpLog(f"t-{uuid.uuid4().hex[:8]}", base_dir=tmp_path)
    oplog.start()
    CURRENT_OPLOG.set(oplog)
    CURRENT_RUN.set(RunTelemetry("r", "test"))

    CURRENT_ITERATION.set(-1)
    async with timed("scratch_write", "test", "warm"):
        pass
    CURRENT_ITERATION.set(0)
    async with timed("scratch_write", "test", "real"):
        pass

    await oplog.close()
    CURRENT_OPLOG.set(None)
    CURRENT_RUN.set(None)

    ops = [StateOp(**json.loads(l)) for l in oplog.path.read_text().strip().split("\n")]
    assert len(ops) == 2
    assert sum(1 for o in ops if o.iteration < 0) == 1
    assert sum(1 for o in ops if o.iteration >= 0) == 1
