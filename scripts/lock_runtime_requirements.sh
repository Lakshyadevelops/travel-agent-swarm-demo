#!/usr/bin/env bash
# Regenerate requirements-runtime.txt: every transitive dependency pinned with
# sha256 hashes, so the container build installs with --require-hashes
# (go/pip-install-remediation). Run after changing requirements-runtime.in.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-.venv/bin/python}"
TOOLS=.tools/piptools-venv
if [ ! -x "$TOOLS/bin/pip-compile" ]; then
  "$PY" -m venv "$TOOLS"
  "$TOOLS/bin/pip" install --quiet "pip-tools>=7.4"
fi

"$TOOLS/bin/pip-compile" \
  --generate-hashes --strip-extras --no-header --quiet \
  --output-file requirements-runtime.txt requirements-runtime.in

echo "wrote requirements-runtime.txt ($(grep -c '==' requirements-runtime.txt) pinned packages)"
