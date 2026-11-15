# -*- coding: utf-8 -*-
"""
Build a standalone Windows executable for the pcinfo tray app.

    python build.py

Produces:
    dist/pcinfo.exe      single-file, no console window, tray-resident

Requires the build dependencies:
    pip install -r requirements-tray.txt
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def make_ico(path):
    """Generate icon.ico from the same glyph used for the tray icon."""
    from tray_app import make_image
    img = make_image(256)
    img.save(path, format="ICO",
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                    (64, 64), (128, 128), (256, 256)])
    return path


def main():
    ico = make_ico(os.path.join(HERE, "icon.ico"))
    sep = ";" if os.name == "nt" else ":"
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--noconsole",            # tray app: no console window
        "--name", "pcinfo",
        "--icon", ico,
        "--add-data", f"index.html{sep}.",   # bundle the dashboard page
        "tray_app.py",
    ]
    print("Running:\n  " + " ".join(args) + "\n")
    subprocess.check_call(args, cwd=HERE)
    print("\nBuild complete -> " + os.path.join(HERE, "dist", "pcinfo.exe"))


if __name__ == "__main__":
    main()
