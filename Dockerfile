# syntax=docker/dockerfile:1.7
#
# Chicago Bike Advocacy Map — production + benchmark image.
#
# Two purposes:
#   1. Production runtime on Render Starter (512 MB / 1 vCPU): single
#      gunicorn worker, reads /var/data/bikemap.db (mounted persistent
#      disk), serves Flask app at :8000 with /health probe.
#   2. Linux RSS benchmark for spec §6.4 #9 480 MB ceiling. macOS
#      over-reports RSS by ~25-30% (Mach counts shared / file-backed
#      pages); Linux's working-set accounting is what Render reports.
#
# Build: docker build --platform=linux/amd64 -t chicago-bike-bench:local .
#
# Bench (memory-capped to mimic Render Starter):
#   docker run --rm --platform=linux/amd64 \
#     -v $(pwd)/data:/app/data:ro \
#     -e BIKEMAP_DB_PATH=/app/data/bikemap.db \
#     -e CACHE_DB_PATH=/tmp/cache.db \
#     -e NOMINATIM_USER_AGENT=bench/1.0 \
#     --memory=512m \
#     chicago-bike-bench:local \
#     pytest -m slow tests/app/test_smoke_real_db.py -v -s
#
# Production (Render mounts the persistent disk at /var/data):
#   docker run -p 8000:8000 \
#     -v render-disk:/var/data \
#     -e BIKEMAP_DB_PATH=/var/data/bikemap.db \
#     -e CACHE_DB_PATH=/var/data/cache.db \
#     -e NOMINATIM_USER_AGENT=chicago-bike-advocacy-map/1.0 \
#     -e APP_BOOTSTRAP=1 \
#     chicago-bike-bench:local

# ---------- Stage 1: builder ----------
# Full slim image with C/C++ toolchain for scipy / numpy / igraph wheels
# (most have manylinux wheels on amd64 but we keep the toolchain in case
# of source builds on other platforms).
FROM --platform=$BUILDPLATFORM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build deps. libxml2/libxslt aren't strictly required for our app deps
# but help if pip falls back to source for any transitive dep.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        g++ \
        libxml2-dev \
        libxslt1-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install runtime + bench dependencies into /install so the runtime stage
# can copy the tree wholesale. Pinning to the same versions used in
# .venv (see pyproject.toml + pip list) — the bench needs python-igraph
# and psutil which aren't in pyproject yet (psutil is a test-only dep).
RUN pip install --target=/install \
        "flask>=3.0,<4" \
        "flask-limiter>=3.5" \
        "gunicorn>=21.2,<27" \
        "scipy>=1.11" \
        "numpy>=2.0" \
        "shapely>=2.0,<3" \
        "pyproj>=3.7,<4" \
        "python-igraph>=0.11,<1" \
        "psutil>=5.9" \
        "pytest>=8.0" \
        "python-frontmatter>=1.1" \
        "pyyaml>=6.0" \
        "requests>=2.32,<3"

# ---------- Stage 2: runtime ----------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/install \
    PATH=/install/bin:$PATH \
    PORT=8000 \
    BIKEMAP_DB_PATH=/var/data/bikemap.db \
    CACHE_DB_PATH=/var/data/cache.db \
    NOMINATIM_USER_AGENT=chicago-bike-advocacy-map/1.0 \
    MIN_STREETS=10000

# Runtime libs only — no build-essential. libgomp is needed by scipy /
# igraph (OpenMP); libxml2 stays for any lxml fallbacks.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        libxml2 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --home-dir /app --shell /bin/bash app

# Copy installed packages from builder.
COPY --from=builder /install /install

WORKDIR /app

# Copy source. tests/ + prep/ included so the smoke test can run inside
# the container. treatments/ NOT included — bikemap.db already has them
# baked in, and rebuilding the DB inside the runtime image is out of
# scope for Plan 2A. Add `COPY treatments/ /app/treatments/` if Plan 2C
# later requires runtime DB rebuilds.
COPY app/ /app/app/
COPY prep/ /app/prep/
COPY tests/ /app/tests/
COPY pyproject.toml /app/pyproject.toml

# Render persistent disk mount point. The image creates the dir so a
# fresh `docker run` without a volume still has somewhere to write
# cache.db (it just won't persist).
RUN mkdir -p /var/data && chown app:app /var/data /app

USER app

EXPOSE 8000

# Production launch. APP_BOOTSTRAP=1 triggers the lazy-init block at
# the bottom of app/main.py — without it, gunicorn imports the module
# but never builds the Flask app. Single worker with 4 threads matches
# spec §3.10. Long timeout because graph load takes ~30s on cold boot.
ENV APP_BOOTSTRAP=1
CMD ["gunicorn", \
     "-w", "1", \
     "--threads", "4", \
     "-b", "0.0.0.0:8000", \
     "--timeout", "180", \
     "app.main:app"]
