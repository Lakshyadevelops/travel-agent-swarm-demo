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

# The lockfile must not name an index: on a corp machine pip-compile would
# otherwise write the Airlock mirror into it, and the image is also built by
# Cloud Build inside Argolis projects (click-to-deploy), where that mirror is
# unreachable. Hashes are index-independent; corp builds pass the mirror as the
# Dockerfile's PIP_INDEX_URL / PIP_TRUSTED_HOST build args instead.
"$TOOLS/bin/pip-compile" \
  --generate-hashes --strip-extras --no-header --quiet \
  --no-emit-index-url --no-emit-trusted-host \
  --output-file requirements-runtime.txt requirements-runtime.in

if grep -q -E '^--(extra-)?index-url|^--trusted-host' requirements-runtime.txt; then
  echo "requirements-runtime.txt names an index; it must stay index-neutral" >&2
  exit 1
fi
echo "wrote requirements-runtime.txt ($(grep -c '==' requirements-runtime.txt) pinned packages)"
