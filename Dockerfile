# =============================================================================
# Multi-Stage Production Dockerfile for Smart Student Management System
# Base Image: Python 3.12 Slim (Linux Debian-based)
# Security: Multi-stage build, unprivileged appuser, strict HEALTHCHECK
# =============================================================================

# --- Stage 1: Build Dependencies ---
FROM python:3.12-slim AS builder

WORKDIR /build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt gunicorn


# --- Stage 2: Final Production Runtime ---
FROM python:3.12-slim AS runner

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_ENV=production \
    PATH=/home/appuser/.local/bin:$PATH

# Install curl for container healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create unprivileged application user
RUN addgroup --system appgroup && adduser --system --ingroup appgroup --home /home/appuser appuser

# Copy installed Python packages from builder
COPY --from=builder /root/.local /home/appuser/.local

# Copy application source code
COPY . /app/

# Create runtime directories with proper permissions (/app/database is code only)
RUN mkdir -p /app/data /app/logs /app/frontend/static/uploads && \
    chown -R appuser:appgroup /app /home/appuser

USER appuser

EXPOSE 5000

# Healthcheck monitoring the JSON health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:5000/health || exit 1

# Production Entrypoint: Run migrations and start Gunicorn WSGI server
CMD ["sh", "-c", "python -c 'from database.init_db import seed_database; seed_database()' && gunicorn --bind 0.0.0.0:5000 --workers 3 --threads 2 --timeout 60 app:app"]
