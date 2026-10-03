"""Merge several load-test runs into one Markdown report.

    .venv/bin/python scripts/load_matrix_table.py runs/load-*-matrix-*/results.json \
        > results/load_matrix_summary.md

Rows are keyed by (writes per step, store vCPUs, users, store). When two runs
share a key the later file on the command line wins, so re-runs override.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bench.loadreport import render_matrix  # noqa: E402


def main(paths: list[str]) -> None:
    if not paths:
        sys.exit(__doc__)
    docs = [json.loads(Path(p).read_text()) for p in sorted(paths, key=lambda p: Path(p).stat().st_mtime)]
    sys.stdout.write(render_matrix(docs))


if __name__ == "__main__":
    main(sys.argv[1:])
