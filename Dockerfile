# Use the official Python 3.11-slim image as the base for the final stage
FROM python:3.11-slim AS final

# Set the working directory
WORKDIR /app

# Copy the application code
COPY . .

# Install the necessary dependencies
RUN pip install --no-cache-dir -r requirements.txt


# Use the official image for the dependencies stage
FROM python:3.11-slim AS deps

# Install build-essential and python3-dev for psutil compilation
RUN apt-get update && \
    apt-get install -y build-essential python3-dev && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory for the deps stage
WORKDIR /app

# Copy the requirements file to install dependencies
COPY requirements.txt .

# Install dependencies in the deps stage
RUN pip install --no-cache-dir -r requirements.txt

# Copy dependencies from the deps stage to the final stage
FROM final AS final
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages

# Command to run the application (this can be adjusted as per your app requirements)
CMD ["python3", "your_app.py"]
