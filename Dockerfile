# syntax=docker/dockerfile:1.6
# ------------------------------------------------------------------
# Base images are pinned to manifest-list (multi-arch) digests. A tag
# alone is mutable — a digest is not. Bump these digests explicitly when
# you want to pick up upstream patches; Trivy runs on every release build
# and will flag the pinned image if a new CVE lands against it.
# ------------------------------------------------------------------
# Stage 1 — build the Vite/React frontend
# ------------------------------------------------------------------
FROM node:26-alpine@sha256:e71ac5e964b9201072425d59d2e876359efa25dc96bb1768cb73295728d6e4ea AS frontend-build
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ------------------------------------------------------------------
# Stage 2 — install Python dependencies into a venv
# ------------------------------------------------------------------
FROM python:3.11-slim-bookworm@sha256:9c6f90801e6b68e772b7c0ca74260cbf7af9f320acec894e26fccdaccfbe3b47 AS py-build
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        g++ \
        git \
        libgeos-dev \
        libproj-dev \
        proj-data \
        proj-bin \
        libspatialindex-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r /tmp/requirements.txt

# ------------------------------------------------------------------
# Stage 3 — slim runtime
# ------------------------------------------------------------------
FROM python:3.11-slim-bookworm@sha256:9c6f90801e6b68e772b7c0ca74260cbf7af9f320acec894e26fccdaccfbe3b47 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    HOST=0.0.0.0 \
    PORT=8000 \
    CACHE_DIR=/data/cache \
    ZARR_STORE=/data/cache/sst.zarr \
    DEMO_MODE=true

# Only runtime libs — geos/proj shared libs for shapely/pyproj/rioxarray.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgeos-c1v5 \
        libproj25 \
        proj-data \
        libspatialindex6 \
        libexpat1 \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 1001 --create-home --shell /usr/sbin/nologin mheat

COPY --from=py-build /opt/venv /opt/venv
COPY --from=frontend-build /fe/dist /srv/frontend

WORKDIR /srv/app
COPY backend/ /srv/app/
# Ship scripts/ so the daily-update CronJob and the live-mode bootstrap Job
# (helm chart) can invoke `python scripts/<x>.py` against the same image.
COPY scripts/ /srv/app/scripts/

RUN mkdir -p /data/cache && chown -R mheat:mheat /data /srv

USER mheat
EXPOSE 8000

ENV FRONTEND_DIR=/srv/frontend

# Kubernetes probes own deep liveness/readiness, but a built-in HEALTHCHECK
# makes `docker ps`, `docker compose`, and Trivy scanners all flag a
# broken container within 30 s without extra wiring.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
