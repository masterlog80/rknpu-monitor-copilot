# ── Base image ────────────────────────────────────────────────────────────────
FROM python:3.9-slim AS base

RUN pip install gunicorn

# ── Dependencies stage ─────────────────────────────────────────────────────────
FROM base AS deps

# Install build tools for compiling psutil
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy and install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Final stage ────────────────────────────────────────────────────────────────
FROM base AS final

# Copy the installed packages from deps stage
COPY --from=deps /usr/local/lib/python3.9/site-packages /usr/local/lib/python3.9/site-packages

COPY . /app
WORKDIR /app

# Expose Flask port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/healthz')" || exit 1

# Run with gunicorn on port 5000 to match Flask default
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--threads", "4", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
