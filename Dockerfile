# Updated Dockerfile

# Base stage with runtime dependencies
FROM python:3.9-slim AS base

RUN pip install gunicorn

# Deps stage that installs build tools and compiles Python packages
FROM base AS deps

# Install build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy and install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Final stage that copies the compiled packages and application code
FROM base AS final

# Copy the installed packages and application code
COPY --from=deps /usr/local/lib/python3.9/site-packages /usr/local/lib/python3.9/site-packages
COPY . /app

# Specify the working directory
WORKDIR /app

# Command to run the application
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8000"]
