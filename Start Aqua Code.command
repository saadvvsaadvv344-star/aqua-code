#!/bin/bash
# Double-click on macOS (Intel and Apple Silicon). If it does not open, run once:  chmod +x "Start Aqua Code.command"
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then python3 aqua_app.py; else python aqua_app.py; fi
