# syntax=docker/dockerfile:1
# Build context is the REPO ROOT (set in docker-compose.yml).
# Every path below starts from the repo root.

# ── Stage 1: install packages ──
FROM python:3.10-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY backend/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install -r requirements.txt

# ── Stage 2: runtime ──
FROM python:3.10-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r nyaya && useradd -r -g nyaya -m -s /bin/bash nyaya

COPY --from=builder /install /usr/local

WORKDIR /home/nyaya/app

# FastAPI app: backend/app -> /home/nyaya/app/app
COPY --chown=nyaya:nyaya backend/app ./app

# Only the pipeline package the backend imports: /home/nyaya/app/ingestion/pipeline
COPY --chown=nyaya:nyaya ingestion/pipeline ./ingestion/pipeline

# Model cache folder, owned by the nyaya user (needed for the named volume)
RUN mkdir -p /home/nyaya/.cache/huggingface \
    && chown -R nyaya:nyaya /home/nyaya/.cache

USER nyaya

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --retries=3 --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]