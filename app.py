#!/usr/bin/env python3
"""
RK3566 / OrangePi CM4 – CPU / Memory / NPU Monitor
Backend: Python Flask + SQLite
"""

import csv
import io
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import psutil
from flask import Flask, Response, jsonify, render_template, request

# ── Constants ─────────────────────────────────────────────────────────────────
TS_FORMAT = "%Y-%m-%dT%H:%M:%S"
NPU_PERCENTAGE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)%")
# Purge old records once every this many samples (~every PURGE_EVERY * POLL_INTERVAL seconds)
PURGE_EVERY = 100
# Downsample records older than 24 hours
DOWNSAMPLE_AFTER_HOURS = 24

# ── Configuration from environment variables ─────────────────────────────────
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "10"))       # seconds
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "14"))     # days
DB_PATH = os.environ.get("DB_PATH", "/data/metrics.db")
NPU_LOAD_PATH = os.environ.get("NPU_LOAD_PATH", "/sys/kernel/debug/rknpu/load")
PORT = int(os.environ.get("PORT", "5000"))
HOST = os.environ.get("HOST", "0.0.0.0")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

app = Flask(__name__)

# ── Database helpers ──────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    """Return a new SQLite connection."""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the metrics tables if they don't exist."""
    with get_db() as conn:
        # Fine-grained metrics (collected every POLL_INTERVAL)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metrics (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT    NOT NULL,
                cpu       REAL    NOT NULL,
                memory    REAL    NOT NULL,
                npu       REAL    NOT NULL
            )
            """
        )
        # Downsampled metrics (hourly averages for data older than 24 hours)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metrics_downsampled (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                hour      TEXT    NOT NULL UNIQUE,
                cpu       REAL    NOT NULL,
                memory    REAL    NOT NULL,
                npu       REAL    NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_timestamp ON metrics(timestamp)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hour ON metrics_downsampled(hour)"
        )
        conn.commit()
    log.info("Database initialised at %s", DB_PATH)


def downsample_old_metrics() -> None:
    """
    Downsample metrics older than DOWNSAMPLE_AFTER_HOURS into hourly averages.
    Replaces raw data with hourly aggregates to save space.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=DOWNSAMPLE_AFTER_HOURS)
    cutoff_str = cutoff.strftime(TS_FORMAT)

    with get_db() as conn:
        # Get all metrics older than cutoff that haven't been downsampled
        rows = conn.execute(
            "SELECT timestamp, cpu, memory, npu FROM metrics "
            "WHERE timestamp < ? "
            "ORDER BY timestamp ASC",
            (cutoff_str,),
        ).fetchall()

        if not rows:
            return

        # Group by hour and calculate averages
        hourly_data = {}
        for row in rows:
            # Extract hour from timestamp (YYYY-MM-DDTHH:00:00)
            ts = row["timestamp"]
            hour = ts[:13] + ":00:00"  # Truncate to hour
            
            if hour not in hourly_data:
                hourly_data[hour] = {
                    "cpu": [],
                    "memory": [],
                    "npu": [],
                }
            
            hourly_data[hour]["cpu"].append(row["cpu"])
            hourly_data[hour]["memory"].append(row["memory"])
            hourly_data[hour]["npu"].append(row["npu"])

        # Insert hourly averages into downsampled table
        for hour, data in hourly_data.items():
            avg_cpu = sum(data["cpu"]) / len(data["cpu"])
            avg_memory = sum(data["memory"]) / len(data["memory"])
            avg_npu = sum(data["npu"]) / len(data["npu"])

            try:
                conn.execute(
                    "INSERT OR IGNORE INTO metrics_downsampled (hour, cpu, memory, npu) "
                    "VALUES (?, ?, ?, ?)",
                    (hour, round(avg_cpu, 1), round(avg_memory, 1), round(avg_npu, 1)),
                )
            except sqlite3.IntegrityError:
                # Hour already exists, skip
                pass

        # Delete the raw metrics that have been downsampled
        conn.execute(
            "DELETE FROM metrics WHERE timestamp < ?",
            (cutoff_str,),
        )
        conn.commit()
        
        deleted_count = len(rows)
        log.info(
            "Downsampled %d metrics older than %s into hourly averages",
            deleted_count,
            cutoff_str,
        )


def purge_old_records() -> None:
    """Delete downsampled records older than RETENTION_DAYS."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    ).strftime(TS_FORMAT)
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM metrics_downsampled WHERE hour < ?", (cutoff,)
        )
        conn.commit()
    if cursor.rowcount:
        log.debug("Purged %d old downsampled records (cutoff: %s)", cursor.rowcount, cutoff)


# ── Metric collectors ─────────────────────────────────────────────────────────

def read_cpu() -> float:
    """Return CPU usage percentage (0–100)."""
    return psutil.cpu_percent(interval=1)


def read_memory() -> float:
    """Return memory usage percentage (0–100)."""
    return psutil.virtual_memory().percent


def read_npu() -> float:
    """
    Parse NPU load from /sys/kernel/debug/rknpu/load.
    File typically contains lines like:
        NPU load:  Core0: 12%, Core1:  0%, Core2:  0%,
    Returns the average load across all cores, or 0.0 if unavailable.
    """
    try:
        with open(NPU_LOAD_PATH, "r") as fh:
            content = fh.read()
        percentages = [float(m) for m in NPU_PERCENTAGE_PATTERN.findall(content)]
        return round(sum(percentages) / len(percentages), 1) if percentages else 0.0
    except FileNotFoundError:
        log.debug("NPU load file not found: %s – returning 0", NPU_LOAD_PATH)
        return 0.0
    except Exception as exc:
        log.warning("Failed to read NPU load: %s", exc)
        return 0.0


# ── Background collector thread ───────────────────────────────────────────────

def collect_metrics() -> None:
    """Continuously collect and store metrics, downsampling and purging old data periodically."""
    purge_counter = 0
    while True:
        try:
            cpu = read_cpu()
            memory = read_memory()
            npu = read_npu()
            ts = datetime.now(timezone.utc).strftime(TS_FORMAT)

            with get_db() as conn:
                conn.execute(
                    "INSERT INTO metrics (timestamp, cpu, memory, npu) VALUES (?,?,?,?)",
                    (ts, cpu, memory, npu),
                )
                conn.commit()

            log.debug("Stored: ts=%s cpu=%.1f mem=%.1f npu=%.1f", ts, cpu, memory, npu)

            # Downsample and purge once every PURGE_EVERY samples
            purge_counter += 1
            if purge_counter >= PURGE_EVERY:
                downsample_old_metrics()
                purge_old_records()
                purge_counter = 0

        except Exception as exc:
            log.error("Collection error: %s", exc)

        time.sleep(POLL_INTERVAL)


# ── REST API ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/metrics/latest")
def api_latest():
    """Return the most recent metric row."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM metrics ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return jsonify({"error": "no data yet"}), 503
    return jsonify(dict(row))


@app.route("/api/metrics/history")
def api_history():
    """
    Return metrics for a time window.
    Query params:
      hours  – look-back window in hours (default 1)
      start  – ISO-8601 start datetime (overrides hours)
      end    – ISO-8601 end datetime   (overrides hours)
    Automatically uses downsampled data for periods older than 24 hours.
    """
    now = datetime.now(timezone.utc)

    start_str = request.args.get("start")
    end_str = request.args.get("end")

    if start_str and end_str:
        start = start_str.replace("Z", "").split(".")[0]
        end = end_str.replace("Z", "").split(".")[0]
    else:
        hours = float(request.args.get("hours", "1"))
        start = (now - timedelta(hours=hours)).strftime(TS_FORMAT)
        end = now.strftime(TS_FORMAT)

    cutoff = (now - timedelta(hours=DOWNSAMPLE_AFTER_HOURS)).strftime(TS_FORMAT)

    with get_db() as conn:
        # Get recent fine-grained data
        recent_rows = conn.execute(
            "SELECT timestamp, cpu, memory, npu FROM metrics "
            "WHERE timestamp BETWEEN ? AND ? AND timestamp >= ? "
            "ORDER BY timestamp ASC",
            (start, end, cutoff),
        ).fetchall()

        # Get older downsampled data - normalize hour to timestamp
        older_rows = conn.execute(
            "SELECT hour as timestamp, cpu, memory, npu FROM metrics_downsampled "
            "WHERE hour BETWEEN ? AND ? AND hour < ? "
            "ORDER BY hour ASC",
            (start, end, cutoff),
        ).fetchall()

    # Combine both datasets
    all_rows = [dict(r) for r in older_rows] + [dict(r) for r in recent_rows]
    return jsonify(all_rows)


@app.route("/api/metrics/export")
def api_export():
    """
    Export metrics as CSV.
    Query params: start, end (ISO-8601) or hours (look-back window).
    """
    now = datetime.now(timezone.utc)

    start_str = request.args.get("start")
    end_str = request.args.get("end")

    if start_str and end_str:
        start = start_str.replace("Z", "").split(".")[0]
        end = end_str.replace("Z", "").split(".")[0]
    else:
        hours = float(request.args.get("hours", "24"))
        start = (now - timedelta(hours=hours)).strftime(TS_FORMAT)
        end = now.strftime(TS_FORMAT)

    cutoff = (now - timedelta(hours=DOWNSAMPLE_AFTER_HOURS)).strftime(TS_FORMAT)

    with get_db() as conn:
        recent_rows = conn.execute(
            "SELECT timestamp, cpu, memory, npu FROM metrics "
            "WHERE timestamp BETWEEN ? AND ? AND timestamp >= ? "
            "ORDER BY timestamp ASC",
            (start, end, cutoff),
        ).fetchall()

        older_rows = conn.execute(
            "SELECT hour as timestamp, cpu, memory, npu FROM metrics_downsampled "
            "WHERE hour BETWEEN ? AND ? AND hour < ? "
            "ORDER BY hour ASC",
            (start, end, cutoff),
        ).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "cpu_percent", "memory_percent", "npu_percent"])
    
    for row in older_rows + recent_rows:
        writer.writerow([row["timestamp"], row["cpu"], row["memory"], row["npu"]])

    filename = f"metrics_{start[:10]}_{end[:10]}.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/api/config")
def api_config():
    """Return runtime configuration (for the frontend)."""
    return jsonify(
        {
            "poll_interval": POLL_INTERVAL,
            "retention_days": RETENTION_DAYS,
            "npu_load_path": NPU_LOAD_PATH,
            "downsample_after_hours": DOWNSAMPLE_AFTER_HOURS,
        }
    )


@app.route("/metrics")
def metrics():
    """
    Prometheus text exposition format endpoint.
    Exposes rknpu_* gauge/counter metrics for scraping by Prometheus.
    """
    now_ts = time.time()

    with get_db() as conn:
        row = conn.execute(
            "SELECT timestamp, cpu, memory, npu FROM metrics ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        total_fine = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        total_down = conn.execute("SELECT COUNT(*) FROM metrics_downsampled").fetchone()[0]

    total_records = total_fine + total_down

    if row is not None:
        cpu = row["cpu"]
        memory = row["memory"]
        npu = row["npu"]
        # Parse ISO-8601 timestamp to Unix epoch
        try:
            sample_dt = datetime.strptime(row["timestamp"], TS_FORMAT).replace(
                tzinfo=timezone.utc
            )
            sample_ts = sample_dt.timestamp()
        except ValueError:
            sample_ts = now_ts
    else:
        cpu = 0.0
        memory = 0.0
        npu = 0.0
        sample_ts = now_ts

    lines = [
        "# HELP rknpu_cpu_percent Current CPU usage percentage",
        "# TYPE rknpu_cpu_percent gauge",
        f"rknpu_cpu_percent {cpu}",
        "# HELP rknpu_memory_percent Current memory usage percentage",
        "# TYPE rknpu_memory_percent gauge",
        f"rknpu_memory_percent {memory}",
        "# HELP rknpu_npu_percent Current NPU usage percentage",
        "# TYPE rknpu_npu_percent gauge",
        f"rknpu_npu_percent {npu}",
        "# HELP rknpu_sample_timestamp_seconds Unix timestamp of the last collected sample",
        "# TYPE rknpu_sample_timestamp_seconds gauge",
        f"rknpu_sample_timestamp_seconds {sample_ts:.3f}",
        "# HELP rknpu_samples_total Total number of fine-grained samples currently in the database",
        "# TYPE rknpu_samples_total counter",
        f"rknpu_samples_total {total_fine}",
        "# HELP rknpu_database_records Total number of records in the database (fine-grained + downsampled)",
        "# TYPE rknpu_database_records gauge",
        f"rknpu_database_records {total_records}",
        "",
    ]
    return Response("\n".join(lines), mimetype="text/plain; version=0.0.4; charset=utf-8")


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


# ── Initialize database and start collector on module load ────────────────────
# This ensures database is initialized when gunicorn imports the app module
init_db()

collector = threading.Thread(target=collect_metrics, daemon=True, name="collector")
collector.start()
log.info(
    "Started collector thread (interval=%ds, retention=%dd, downsample after %dh)",
    POLL_INTERVAL,
    RETENTION_DAYS,
    DOWNSAMPLE_AFTER_HOURS,
)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False)
