# -*- coding: utf-8 -*-
"""
pcinfo - system tray launcher

Runs the pcinfo web server (server.py) in the background and shows a Windows
system-tray icon. Right-click the icon for a menu; double-click to open the
dashboard in your browser.

Run directly:
    python tray_app.py        (or pythonw tray_app.py for no console)

Build a standalone .exe:
    python build.py           (produces dist/pcinfo.exe)
"""

import os
import socket
import subprocess
import sys
import webbrowser

import pystray
from PIL import Image, ImageDraw

import server as srv

APP_NAME = "pcinfo"
APP_TITLE = "PC Info Monitor"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


# --- tray icon image --------------------------------------------------------
def make_image(size=64):
    """A small bar-chart glyph representing the four metrics."""
    img = Image.new("RGBA", (size, size), (15, 23, 42, 255))  # dark slate bg
    d = ImageDraw.Draw(img)
    colors = ["#38bdf8", "#f97316", "#a78bfa", "#22c55e"]  # cpu/mem/disk/gpu
    heights = [0.55, 0.78, 0.42, 0.9]
    bar_w = max(3, size // 7)
    gap = max(2, bar_w // 2)
    total = 4 * bar_w + 3 * gap
    x = (size - total) // 2
    base = int(size * 0.82)
    top_limit = size * 0.18
    for c, h in zip(colors, heights):
        top = int(top_limit + (1 - h) * (base - top_limit))
        d.rectangle([x, top, x + bar_w, base], fill=c)
        x += bar_w + gap
    return img


# --- network helpers --------------------------------------------------------
def primary_ip():
    """Best-effort primary LAN IPv4 address (no traffic actually sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def tailscale_ip():
    """Tailscale IPv4 address if the Tailscale CLI is available, else None."""
    candidates = ["tailscale", r"C:\Program Files\Tailscale\tailscale.exe"]
    for exe in candidates:
        try:
            out = subprocess.run(
                [exe, "ip", "-4"], capture_output=True, text=True,
                timeout=2, creationflags=_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0:
            line = out.stdout.strip().splitlines()
            if line and line[0].strip():
                return line[0].strip()
        return None
    return None


def port_in_use(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


# --- Windows "run at login" toggle (HKCU Run key) ---------------------------
def _autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    runner = pyw if os.path.exists(pyw) else sys.executable
    return f'"{runner}" "{os.path.abspath(__file__)}"'


def is_autostart_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False
    except ImportError:
        return False


def set_autostart(enable):
    try:
        import winreg
        if enable:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ,
                                  _autostart_command())
        else:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except OSError:
                    pass
    except (OSError, ImportError):
        pass


# --- menu actions -----------------------------------------------------------
def _open_dashboard(icon=None, item=None):
    webbrowser.open(f"http://localhost:{srv.PORT}")


def _build_menu(server):
    def on_quit(icon, item):
        try:
            server.shutdown()
        except Exception:
            pass
        icon.stop()

    def on_toggle_autostart(icon, item):
        set_autostart(not is_autostart_enabled())
        icon.update_menu()

    items = [
        pystray.MenuItem("ダッシュボードを開く", _open_dashboard, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(f"http://localhost:{srv.PORT}", _open_dashboard),
        pystray.MenuItem(f"LAN: http://{primary_ip()}:{srv.PORT}", None,
                         enabled=False),
    ]
    ts = tailscale_ip()
    if ts:
        items.append(pystray.MenuItem(
            f"Tailscale: http://{ts}:{srv.PORT}", None, enabled=False))
    items += [
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Windows起動時に自動実行", on_toggle_autostart,
                         checked=lambda item: is_autostart_enabled()),
        pystray.MenuItem("終了", on_quit),
    ]
    return pystray.Menu(*items)


def main():
    # If pcinfo is already running, just open the dashboard and exit so we
    # don't end up with two tray icons / a port conflict.
    if port_in_use(srv.PORT):
        _open_dashboard()
        return

    server = srv.serve_background()
    icon = pystray.Icon(
        APP_NAME, make_image(), f"{APP_TITLE} ({socket.gethostname()})",
        _build_menu(server),
    )
    icon.run()


if __name__ == "__main__":
    main()
