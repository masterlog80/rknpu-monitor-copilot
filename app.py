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
    """Create the metrics table if it doesn't exist."""
    with get_db() as conn:
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_timestamp ON metrics(timestamp)"
        )
        conn.commit()
    log.info("Database initialised at %s", DB_PATH)


def purge_old_records() -> None:
    """Delete records older than RETENTION_DAYS."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    ).strftime(TS_FORMAT)
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM metrics WHERE timestamp < ?", (cutoff,)
        )
        conn.commit()
    if cursor.rowcount:
        log.debug("Purged %d old records (cutoff: %s)", cursor.rowcount, cutoff)


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


# ── Background collector thread ────────────────��──────────────────────────────

def collect_metrics() -> None:
    """Continuously collect and store metrics, purging old data periodically."""
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

            # Purge once every PURGE_EVERY samples
            purge_counter += 1
            if purge_counter >= PURGE_EVERY:
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

    with get_db() as conn:
        rows = conn.execute(
            "SELECT timestamp, cpu, memory, npu FROM metrics "
            "WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp ASC",
            (start, end),
        ).fetchall()

    return jsonify([dict(r) for r in rows])


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

    with get_db() as conn:
        rows = conn.execute(
            "SELECT timestamp, cpu, memory, npu FROM metrics "
            "WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp ASC",
            (start, end),
        ).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "cpu_percent", "memory_percent", "npu_percent"])
    for row in rows:
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
        }
    )


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


# ── Initialize database and start collector on module load ────────────────────
# This ensures database is initialized when gunicorn imports the app module
init_db()

collector = threading.Thread(target=collect_metrics, daemon=True, name="collector")
collector.start()
log.info(
    "Started collector thread (interval=%ds, retention=%dd)",
    POLL_INTERVAL,
    RETENTION_DAYS,
)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False)
