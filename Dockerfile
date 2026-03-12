# Use an official Python runtime as a parent image
FROM python:3.9-slim AS base

# Set the working directory in the container
WORKDIR /app

# Install dependencies
FROM base AS deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the source code into the container
FROM base AS build
COPY . .

# Set the command to run the application
CMD ["python", "app.py"]