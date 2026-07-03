# Multi-stage Dockerfile: builder + slim runtime
FROM python:3.12-slim AS builder

WORKDIR /build

# System deps for building wheels (e.g. cffi for cryptography pulled in by python-gitlab)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# --- runtime stage ---
FROM python:3.12-slim

# Run as non-root for safety
RUN groupadd -r app && useradd -r -g app app

WORKDIR /app

# Install only the prebuilt wheels
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels \
    fastapi uvicorn[standard] jinja2 python-gitlab python-dotenv python-multipart \
    && rm -rf /wheels

# Copy application code
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

# Persistent data dir for the SQLite file
RUN mkdir -p /app/data && chown -R app:app /app
USER app

# Defaults; override via env / docker-compose
ENV WEB_HOST=0.0.0.0 \
    WEB_PORT=8000 \
    DB_PATH=/app/data/app.db \
    LOG_LEVEL=INFO \
    LOG_JSON=0 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen(\"http://127.0.0.1:8000/api/health\").read()" || exit 1

CMD ["python", "-m", "gitlab_issues_finder"]
