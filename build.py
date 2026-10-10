"""Package SamuraiAgent into a single windowed (no-console) executable.

This was previously merged into the same file as the agent itself, with a
leftover Tk "Samurai Agent is running" placeholder window that had nothing
to do with the real PyQt6 app in ui.py/main.py. Split out here so `python
build.py` only builds, and `python main.py` only runs the actual agent.

Usage:
    python build.py
"""
from __future__ import annotations

import ctypes
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
ENTRY_POINT = PROJECT_ROOT / "main.py"


def is_admin() -> bool:
    if os.name != "nt":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_with_admin() -> None:
    """On Windows, some antivirus/PyInstaller combos need admin to write
    the exe next to protected paths. Elevate only if not already admin."""
    if os.name != "nt" or is_admin():
        return

    script_path = os.path.abspath(sys.argv[0])
    params = " ".join(f'"{arg}"' if " " in arg else arg for arg in sys.argv[1:])
    powershell = os.environ.get(
        "COMSPEC", r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    )
    arguments = f'-NoProfile -ExecutionPolicy Bypass -File "{script_path}" {params}'.strip()

    print("Administrator rights requested for this build step. Approve the UAC prompt if it appears.")
    try:
        ctypes.windll.shell32.ShellExecuteW(None, "runas", powershell, arguments,
                                             str(Path(script_path).parent), 1)
        raise SystemExit(0)
    except Exception:
        print("Could not elevate; continuing without admin rights.")


def ensure_pyinstaller() -> None:
    if importlib.util.find_spec("PyInstaller") is None:
        print("PyInstaller not installed — installing now...")
        subprocess.run([sys.executable, "-m", "pip", "install", "PyInstaller"], check=True)


def build_app() -> int:
    dist_dir = PROJECT_ROOT / "dist"
    spec_path = PROJECT_ROOT / "SamuraiAgent.spec"

    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--windowed",          # no console window -> this is what makes the exe "background-friendly"
        "--name", "SamuraiAgent",
        "--add-data", f"{PROJECT_ROOT / 'assets'}{os.pathsep}assets",
        "--hidden-import", "pygame",
        "--distpath", str(dist_dir),
        str(ENTRY_POINT),
    ]

    print("Building the Samurai Agent executable...")
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)
    if spec_path.exists():
        spec_path.unlink(missing_ok=True)
    print(f"Build finished. App is in: {dist_dir}")
    return 0


def main() -> int:
    ensure_pyinstaller()
    return build_app()


if __name__ == "__main__":
    raise SystemExit(main())
