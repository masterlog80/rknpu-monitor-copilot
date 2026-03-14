# RK3566 / OrangePi CM4 – CPU · Memory · NPU Monitor

A lightweight, Docker-based monitoring solution for the **RK3566-powered OrangePi CM4**.
It collects CPU, Memory, and NPU usage every 10 seconds, stores the data locally in
SQLite, and exposes a live web dashboard with interactive charts and CSV export.

---

## Features

| Feature | Details |
|---|---|
| **Live Dashboard** | Real-time gauges + Chart.js line graphs |
| **Metrics** | CPU %, Memory %, NPU Load % |
| **NPU Source** | `/sys/kernel/debug/rknpu/load` |
| **Data Retention** | 14 days (configurable) |
| **Polling Interval** | 10 seconds (configurable) |
| **CSV Export** | User-selectable date/time range |
| **Prometheus Export** | `/metrics` endpoint in text exposition format |
| **Storage** | SQLite in a named Docker volume |
| **Architecture** | `linux/arm64` – native on RK3566 |

---

## Quick Start

### Prerequisites

- Docker ≥ 24 and Docker Compose v2
- OrangePi CM4 running a Linux distribution with the RK3566 NPU driver loaded

### 1 – Clone the repository

```bash
git clone https://github.com/masterlog80/rknpu-monitor-copilot.git
cd rknpu-monitor-copilot
```

### 2 – Create your environment file

```bash
cp .env.example .env
# Edit .env to change HOST_PORT, POLL_INTERVAL, RETENTION_DAYS, etc.
```

### 3 – Build and run

```bash
docker compose up -d --build
```

The dashboard is now available at **http://\<device-ip\>:8080**.

### 4 – View logs

```bash
docker compose logs -f
```

### 5 – Stop

```bash
docker compose down
```

---

## Configuration

All settings are controlled via environment variables (`.env` or inline in `docker-compose.yml`):

| Variable | Default | Description |
|---|---|---|
| `HOST_PORT` | `8080` | Host port mapped to the container's port 5000 |
| `POLL_INTERVAL` | `10` | Seconds between metric samples |
| `RETENTION_DAYS` | `14` | Days of history to keep |
| `LOG_LEVEL` | `INFO` | Log verbosity (`DEBUG` / `INFO` / `WARNING` / `ERROR`) |
| `NPU_LOAD_PATH` | `/sys/kernel/debug/rknpu/load` | Path to the NPU load file |
| `DB_PATH` | `/data/metrics.db` | Path to the SQLite database inside the container |

---

## NPU Access

The container is started with `privileged: true` and mounts `/sys/kernel/debug`
read-only so the backend can read the NPU load from
`/sys/kernel/debug/rknpu/load`.

> **Note:** If the NPU driver is not loaded or the file is absent, the NPU metric
> will be reported as `0%` and the dashboard will still function normally.

---

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Web dashboard |
| `/api/metrics/latest` | GET | Latest metric row (JSON) |
| `/api/metrics/history` | GET | History – params: `hours`, or `start`+`end` (ISO-8601) |
| `/api/metrics/export` | GET | Download CSV – params: `start`+`end` or `hours` |
| `/api/config` | GET | Runtime configuration |
| `/healthz` | GET | Health check |
| `/metrics` | GET | Prometheus metrics (text exposition format) |

---

## Prometheus Integration

The `/metrics` endpoint exposes metrics in the
[Prometheus text exposition format](https://prometheus.io/docs/instrumenting/exposition_formats/).

### Available metrics

| Metric | Type | Description |
|---|---|---|
| `rknpu_cpu_percent` | gauge | Current CPU usage percentage |
| `rknpu_memory_percent` | gauge | Current memory usage percentage |
| `rknpu_npu_percent` | gauge | Current NPU usage percentage |
| `rknpu_sample_timestamp_seconds` | gauge | Unix timestamp of the last collected sample |
| `rknpu_samples_total` | counter | Number of fine-grained samples currently in the database |
| `rknpu_database_records` | gauge | Total records in the database (fine-grained + downsampled) |

### Quick check

```bash
curl http://<device-ip>:5000/metrics
```

Example output:

```
# HELP rknpu_cpu_percent Current CPU usage percentage
# TYPE rknpu_cpu_percent gauge
rknpu_cpu_percent 12.5
# HELP rknpu_memory_percent Current memory usage percentage
# TYPE rknpu_memory_percent gauge
rknpu_memory_percent 45.2
# HELP rknpu_npu_percent Current NPU usage percentage
# TYPE rknpu_npu_percent gauge
rknpu_npu_percent 8.0
# HELP rknpu_sample_timestamp_seconds Unix timestamp of the last collected sample
# TYPE rknpu_sample_timestamp_seconds gauge
rknpu_sample_timestamp_seconds 1710374827.000
# HELP rknpu_samples_total Total number of metric samples stored in the database
# TYPE rknpu_samples_total counter
rknpu_samples_total 8640
# HELP rknpu_database_records Current number of records (fine-grained + downsampled) in the database
# TYPE rknpu_database_records gauge
rknpu_database_records 8640
```

### Prometheus scrape configuration

Add the following snippet to your `prometheus.yml` (see `.prometheus.yml` in
this repository for a complete example):

```yaml
scrape_configs:
  - job_name: rknpu-monitor
    scrape_interval: 15s
    static_configs:
      - targets:
          - "<device-ip>:5000"
        labels:
          instance: orangepi-cm4
          board: rk3566
```

---

## Data Persistence

Metrics are stored in a named Docker volume (`rknpu-data`) mounted at `/data`
inside the container. Data survives container restarts and updates.

To back up the database:

```bash
docker run --rm \
  -v rknpu-data:/data:ro \
  -v $(pwd):/backup \
  busybox cp /data/metrics.db /backup/metrics_backup.db
```

---

## Project Structure

```
.
├── app.py               # Python Flask backend + data collector
├── templates/
│   └── index.html       # Web dashboard (Chart.js)
├── Dockerfile           # Multi-stage, arm64-compatible image
├── docker-compose.yml   # Deployment configuration
├── .env.example         # Example environment variables
├── .prometheus.yml      # Example Prometheus scrape configuration
├── requirements.txt     # Python dependencies
└── README.md
```

---

## License

MIT
