# syntax=docker/dockerfile:1

# ── build ────────────────────────────────────────────────────────────────────
# Installs from uv.lock rather than a hand-maintained pip list. The previous
# Dockerfile duplicated the dependency set inline, which silently drifted from
# pyproject.toml every time a package was added.
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Lockfile first, source second, so editing a .py file doesn't reinstall
# every dependency.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

COPY . .

# ── runtime ──────────────────────────────────────────────────────────────────
FROM python:3.13-slim

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --no-create-home app

WORKDIR /app
COPY --from=builder --chown=app:app /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"]

# Exec form, no shell: uvicorn is PID 1 and receives SIGTERM directly, so
# Kubernetes gets a graceful shutdown instead of waiting out the grace period.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
