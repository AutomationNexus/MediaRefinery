# Multi-stage build that compiles the React/Vite dashboard into the
# Python package before installing the wheel, then ships the service
# runtime. No secrets are baked into the image.

# ---------- Stage 1: build the dashboard bundle ----------
FROM node:24-bookworm-slim AS frontend

WORKDIR /build/frontend

# Install deps with a clean, reproducible lockfile resolution before
# copying sources so this layer caches across source-only changes.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Copy the frontend sources. .dockerignore excludes node_modules and
# the previous dist so the build always starts from a clean slate.
COPY frontend/ ./

# Vite's outDir is `../src/mediarefinery/web` relative to the frontend
# directory, so the bundle lands at /build/src/mediarefinery/web/.
RUN npm run build

# ---------- Stage 2: service-mode runtime ----------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
ARG MEDIAREFINERY_INSTALL_TARGET=".[service,onnx,ocr]"

RUN groupadd --system mediarefinery \
    && useradd --system --gid mediarefinery --home-dir /app mediarefinery

RUN apt-get update \
    && apt-get install --no-install-recommends --yes ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY docs/models ./docs/models

# Bake the freshly built dashboard bundle into the package so the
# wheel install picks it up under mediarefinery/web/.
COPY --from=frontend /build/src/mediarefinery/web/ ./src/mediarefinery/web/

RUN python -m pip install --no-cache-dir "${MEDIAREFINERY_INSTALL_TARGET}"

RUN mkdir -p /config /data/state /data/tmp /data/reports \
    && chown -R mediarefinery:mediarefinery /app /config /data

USER mediarefinery

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:8765/api/health || exit 1

# Service-mode default.
CMD ["uvicorn", "mediarefinery.service.app:create_app", \
     "--factory", "--host", "0.0.0.0", "--port", "8765"]
