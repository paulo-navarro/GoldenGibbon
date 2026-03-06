# ──────────────────────────────────────────────
# Stage 1: base – shared runtime
# ──────────────────────────────────────────────
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# ──────────────────────────────────────────────
# Stage 2: builder – install deps in a venv
# ──────────────────────────────────────────────
FROM base AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir ".[dev]"

# ──────────────────────────────────────────────
# Stage 3: dev – full dev tooling
# ──────────────────────────────────────────────
FROM base AS dev

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY . .

CMD ["python", "main.py"]

# ──────────────────────────────────────────────
# Stage 4: prod – slim, no dev deps
# ──────────────────────────────────────────────
FROM base AS prod

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

COPY core/ core/
COPY config/ config/
COPY db/ db/
COPY api/ api/
COPY main.py .

RUN useradd --create-home appuser
USER appuser

CMD ["python", "main.py"]
