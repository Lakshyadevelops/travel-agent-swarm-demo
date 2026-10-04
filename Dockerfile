# Travel Concierge Swarm -- the demo server as one container.
#
# The image serves the UI and the agents; the stores are external (Valkey +
# Postgres), configured through environment variables exactly as on a laptop.
# Cloud Run supplies PORT and the service account's credentials; nothing is
# baked in.

FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# Hashed lockfile: every transitive dependency is pinned and verified, so the
# index only has to serve the right bytes. PIP_INDEX_URL/PIP_TRUSTED_HOST are
# build args for machines that must use a PyPI mirror (corp Airlock); unset,
# pip uses pypi.org.
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
COPY requirements-runtime.txt ./
RUN pip install --require-hashes --no-deps -r requirements-runtime.txt

# Code plus the one committed data file the app reads at runtime: the recorded
# model-latency trace that load tests replay (app/llm/latency.py). Keep the
# repo-relative layout so REPO_ROOT resolution in app/config.py is unchanged.
COPY app ./app
COPY results/llm_trace.jsonl ./results/llm_trace.jsonl

# Per-run telemetry goes to a writable scratch path, not the code tree.
ENV RUNS_DIR=/tmp/runs \
    PORT=8080

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin swarm
USER swarm
EXPOSE 8080

# Single uvicorn worker: the app is asyncio end to end and Cloud Run scales by
# adding instances. --proxy-headers so request.url.scheme reflects the HTTPS
# edge (Cloud Run / the load balancer) rather than the plain-HTTP hop into the
# container. Shell form so $PORT is honoured.
CMD exec python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" \
    --proxy-headers --forwarded-allow-ips="*" --log-level info
