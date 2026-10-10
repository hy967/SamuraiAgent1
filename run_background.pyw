"""Double-click entry point for Windows: .pyw files are launched with
pythonw.exe automatically, so no console window ever appears.

On macOS/Linux, background running is normally handled by the autostart
files main.py writes (LaunchAgent / .desktop) or by `nohup python3 main.py &`.
"""
import runpy

runpy.run_module("main", run_name="__main__")
