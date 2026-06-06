# -*- coding: utf-8 -*-
"""
pcinfo - Windows PC info viewer (web)

Serves a small web dashboard showing live system metrics, refreshing every
second:

    - CPU usage (total + per core, history bar chart)
    - Memory usage (doughnut chart)
    - Disk usage (per-drive capacity + read/write throughput + busy %)
    - GPU usage (NVIDIA via nvidia-smi: utilization, VRAM, temperature)

Uses only the Python standard library plus psutil, so no extra install is
needed when psutil is already present. GPU metrics use the `nvidia-smi`
command that ships with the NVIDIA driver (no extra pip dependency).

Run on the Windows host for accurate Windows metrics:
    python server.py
Then open http://localhost:8000 in a browser. To view from another device
over Tailscale, open http://<host-tailscale-ip>:8000 (see README.md).
"""

import json
import os
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil

HOST = "0.0.0.0"
PORT = 8000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# On Windows, prevent a console window from flashing each time nvidia-smi runs.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_LOGICAL = psutil.cpu_count(logical=True)
_PHYSICAL = psutil.cpu_count(logical=False)

# --- Background sampler -----------------------------------------------------
# CPU% and disk I/O rates are *deltas* measured between two samples. If they
# were computed per HTTP request, multiple concurrent viewers (or a browser
# tab that throttles its 1s timer in the background) would split the measuring
# window and produce erratic / near-zero readings. A single dedicated 1s
# sampler keeps these correct no matter how many clients poll, or how often.
# Memory, disk capacity and GPU are absolute values, so they are read fresh
# on each request instead.

SAMPLE_INTERVAL = 1.0  # seconds

_snapshot_lock = threading.Lock()
_snapshot = {
    "cpu": {
        "total": 0.0,
        "per_core": [0.0] * (_LOGICAL or 1),
        "logical_count": _LOGICAL,
        "physical_count": _PHYSICAL,
        "freq_mhz": None,
    },
    "disk_io": {"read_bps": 0, "write_bps": 0, "busy_percent": 0.0},
}


def _sampler_loop():
    """Sample rate-based metrics once per second into the shared snapshot."""
    # Prime the total-CPU counter so the first interval=None reading below
    # measures over a real window rather than since process start.
    psutil.cpu_percent(interval=None)
    prev_io = psutil.disk_io_counters()
    prev_t = time.monotonic()

    while True:
        # Blocks ~SAMPLE_INTERVAL and yields per-core CPU% over exactly that
        # window; the interval=None total call right after measures the same
        # elapsed time.
        per_core = psutil.cpu_percent(interval=SAMPLE_INTERVAL, percpu=True)
        total = psutil.cpu_percent(interval=None)
        freq = psutil.cpu_freq()
        cpu = {
            "total": round(total, 1),
            "per_core": [round(c, 1) for c in per_core],
            "logical_count": _LOGICAL,
            "physical_count": _PHYSICAL,
            "freq_mhz": round(freq.current) if freq else None,
        }

        cur = psutil.disk_io_counters()
        now = time.monotonic()
        dt = now - prev_t
        read_bps = write_bps = 0.0
        busy = 0.0
        if cur is not None and prev_io is not None and dt > 0:
            read_bps = max(0.0, (cur.read_bytes - prev_io.read_bytes) / dt)
            write_bps = max(0.0, (cur.write_bytes - prev_io.write_bytes) / dt)
            # read_time / write_time are cumulative ms spent on I/O; their delta
            # over the window approximates Task Manager's disk "busy" %.
            busy_ms = (cur.read_time - prev_io.read_time) + \
                      (cur.write_time - prev_io.write_time)
            busy = max(0.0, min(100.0, busy_ms / (dt * 1000.0) * 100.0))
        if cur is not None:
            prev_io, prev_t = cur, now

        with _snapshot_lock:
            _snapshot["cpu"] = cpu
            _snapshot["disk_io"] = {
                "read_bps": round(read_bps),
                "write_bps": round(write_bps),
                "busy_percent": round(busy, 1),
            }


def start_sampler():
    t = threading.Thread(target=_sampler_loop, name="pcinfo-sampler", daemon=True)
    t.start()
    return t


# --- GPU query cache --------------------------------------------------------
# nvidia-smi takes ~100-300ms to spawn; cache its result briefly so multiple
# concurrent viewers (or sub-second polls) don't multiply the cost.
_GPU_TTL = 0.8  # seconds
_gpu_cache = {"ts": 0.0, "data": None}


def collect_memory():
    vm = psutil.virtual_memory()
    return {
        "total": vm.total,
        "used": vm.used,
        "available": vm.available,
        "percent": vm.percent,
    }


def collect_drive_capacity():
    """Per-drive capacity (absolute, read fresh each request)."""
    drives = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        drives.append({
            "device": part.device,
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": usage.percent,
        })
    return drives


def _query_gpu():
    """Run nvidia-smi once and parse it. Returns the gpu dict (no caching)."""
    fields = "name,utilization.gpu,memory.used,memory.total,temperature.gpu"
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--query-gpu={fields}",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return {"available": False}

    if out.returncode != 0:
        return {"available": False}

    gpus = []
    for line in out.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        name, util, mem_used, mem_total, temp = parts[:5]

        def _num(v):
            try:
                return float(v)
            except ValueError:
                return None

        gpus.append({
            "name": name,
            "util": _num(util),
            "mem_used_mb": _num(mem_used),
            "mem_total_mb": _num(mem_total),
            "temp_c": _num(temp),
        })

    if not gpus:
        return {"available": False}
    return {"available": True, "gpus": gpus}


def collect_gpu():
    now = time.monotonic()
    if _gpu_cache["data"] is not None and now - _gpu_cache["ts"] < _GPU_TTL:
        return _gpu_cache["data"]
    data = _query_gpu()
    _gpu_cache["ts"] = now
    _gpu_cache["data"] = data
    return data


def collect_stats():
    """Collect current CPU / memory / disk / GPU stats as a JSON dict.

    CPU and disk I/O come from the background sampler's latest snapshot;
    memory, disk capacity and GPU are read fresh here.
    """
    with _snapshot_lock:
        cpu = dict(_snapshot["cpu"])
        disk_io = dict(_snapshot["disk_io"])
    return {
        "cpu": cpu,
        "memory": collect_memory(),
        "disk": {"drives": collect_drive_capacity(), "io": disk_io},
        "gpu": collect_gpu(),
        "host": socket.gethostname(),
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, content_type, body):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                with open(os.path.join(BASE_DIR, "index.html"), "rb") as f:
                    self._send(200, "text/html; charset=utf-8", f.read())
            except OSError:
                self._send(404, "text/plain; charset=utf-8", "index.html not found")
        elif self.path == "/api/stats":
            try:
                payload = json.dumps(collect_stats())
                self._send(200, "application/json; charset=utf-8", payload)
            except Exception as exc:  # noqa: BLE001 - surface any error to client
                self._send(
                    500,
                    "application/json; charset=utf-8",
                    json.dumps({"error": str(exc)}),
                )
        else:
            self._send(404, "text/plain; charset=utf-8", "Not Found")

    def log_message(self, fmt, *args):
        # Keep the console quiet (avoid a log line per 1s poll).
        pass


def main():
    start_sampler()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"pcinfo running -> http://localhost:{PORT}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
