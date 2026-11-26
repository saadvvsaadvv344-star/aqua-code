#!/usr/bin/env bash
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then exec python3 aqua_app.py; else exec python aqua_app.py; fi
