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
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil

HOST = "0.0.0.0"
PORT = 8000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# On Windows, prevent a console window from flashing each time nvidia-smi runs.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Prime psutil so the first cpu_percent(interval=None) call has a baseline.
psutil.cpu_percent(interval=None)
psutil.cpu_percent(interval=None, percpu=True)

# --- Disk I/O rate tracking -------------------------------------------------
# disk_io_counters() returns cumulative totals; we keep the previous sample to
# derive read/write throughput (bytes/sec) and an approximate busy percentage.
_prev_disk_io = psutil.disk_io_counters()
_prev_disk_time = time.monotonic()

# --- GPU query cache --------------------------------------------------------
# nvidia-smi takes ~100-300ms to spawn; cache its result briefly so multiple
# concurrent viewers (or sub-second polls) don't multiply the cost.
_GPU_TTL = 0.8  # seconds
_gpu_cache = {"ts": 0.0, "data": None}


def collect_cpu():
    cpu_total = psutil.cpu_percent(interval=None)
    cpu_per_core = psutil.cpu_percent(interval=None, percpu=True)
    freq = psutil.cpu_freq()
    return {
        "total": round(cpu_total, 1),
        "per_core": [round(c, 1) for c in cpu_per_core],
        "logical_count": psutil.cpu_count(logical=True),
        "physical_count": psutil.cpu_count(logical=False),
        "freq_mhz": round(freq.current) if freq else None,
    }


def collect_memory():
    vm = psutil.virtual_memory()
    return {
        "total": vm.total,
        "used": vm.used,
        "available": vm.available,
        "percent": vm.percent,
    }


def collect_disk():
    """Per-drive capacity plus overall I/O throughput and busy percentage."""
    global _prev_disk_io, _prev_disk_time

    # Capacity per mounted drive (skip CD-ROMs / empty / inaccessible drives).
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

    # Throughput + approximate busy % from cumulative counter deltas.
    read_bps = write_bps = 0.0
    busy_percent = 0.0
    cur = psutil.disk_io_counters()
    now = time.monotonic()
    dt = now - _prev_disk_time
    if cur is not None and _prev_disk_io is not None and dt > 0:
        read_bps = (cur.read_bytes - _prev_disk_io.read_bytes) / dt
        write_bps = (cur.write_bytes - _prev_disk_io.write_bytes) / dt
        # read_time / write_time are cumulative milliseconds spent on I/O.
        busy_ms = (cur.read_time - _prev_disk_io.read_time) + \
                  (cur.write_time - _prev_disk_io.write_time)
        busy_percent = max(0.0, min(100.0, busy_ms / (dt * 1000.0) * 100.0))
    if cur is not None:
        _prev_disk_io = cur
        _prev_disk_time = now

    return {
        "drives": drives,
        "io": {
            "read_bps": max(0.0, round(read_bps)),
            "write_bps": max(0.0, round(write_bps)),
            "busy_percent": round(busy_percent, 1),
        },
    }


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
    """Collect current CPU / memory / disk / GPU stats as a JSON dict."""
    return {
        "cpu": collect_cpu(),
        "memory": collect_memory(),
        "disk": collect_disk(),
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
