# ── Dependencies ──────────────────────────────────────────────────────────────
FROM base AS deps

WORKDIR /install

# Install build dependencies needed for psutil compilation
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --prefix=/install/pkg -r requirements.txt
