# ── Base image ────────────────────────────────────────────────────────────────
# Use a slim Python image that supports arm64 (RK3566 / OrangePi CM4)
FROM python:3.11-slim AS base

# Install only what we need at runtime
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        procps \
    && rm -rf /var/lib/apt/lists/*

# ── Dependencies ──────────────────────────────────────────────────────────────
FROM base AS deps

WORKDIR /install

COPY requirements.txt .

RUN pip install --no-cache-dir --prefix=/install/pkg -r requirements.txt

# ── Final image ───────────────────────────────────────────────────────────────
FROM base AS final

WORKDIR /app

# Copy installed packages from deps stage
COPY --from=deps /install/pkg /usr/local

# Copy application code
COPY app.py .
COPY templates/ templates/
COPY static/ static/

# Persistent data directory
RUN mkdir -p /data

# Expose web port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/healthz')"

# Run with gunicorn for production
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--threads", "4", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
