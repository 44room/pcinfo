# -*- coding: utf-8 -*-
"""
pcinfo - Windows PC info viewer (web)

Serves a small web UI showing live CPU usage (vertical bar chart) and
memory usage (doughnut chart), both refreshing every second.

Uses only the Python standard library plus psutil, so no extra install
is needed when psutil is already present.

Run on the Windows host for accurate Windows metrics:
    python server.py
Then open http://localhost:8000 in a browser.
"""

import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil

HOST = "0.0.0.0"
PORT = 8000
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Prime psutil so the first cpu_percent(interval=None) call has a baseline.
psutil.cpu_percent(interval=None)
psutil.cpu_percent(interval=None, percpu=True)


def collect_stats():
    """Collect current CPU / memory stats as a JSON-serializable dict."""
    cpu_total = psutil.cpu_percent(interval=None)
    cpu_per_core = psutil.cpu_percent(interval=None, percpu=True)
    vm = psutil.virtual_memory()

    freq = psutil.cpu_freq()
    return {
        "cpu": {
            "total": round(cpu_total, 1),
            "per_core": [round(c, 1) for c in cpu_per_core],
            "logical_count": psutil.cpu_count(logical=True),
            "physical_count": psutil.cpu_count(logical=False),
            "freq_mhz": round(freq.current) if freq else None,
        },
        "memory": {
            "total": vm.total,
            "used": vm.used,
            "available": vm.available,
            "percent": vm.percent,
        },
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
            except Exception as exc:  # noqa: BLE001 - surface any psutil error to client
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
