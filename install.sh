#!/usr/bin/env bash
# Aqua Code installer for macOS (Intel and Apple Silicon) and Linux.
#   curl -fsSL https://raw.githubusercontent.com/saadvvsaadvv344-star/aqua-code/main/install.sh | bash
# Installs into ~/AquaCode (override with AQUA_DIR=...). Your keys, chats and workspace are never overwritten on update.
set -e
REPO="saadvvsaadvv344-star/aqua-code"
DEST="${AQUA_DIR:-$HOME/AquaCode}"
say() { printf '\033[1m%s\033[0m\n' "$*"; }

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "Aqua Code needs Python 3.9 or newer, and it was not found."
  echo "  macOS:  install it from https://www.python.org/downloads/  (or: brew install python)"
  echo "  Linux:  sudo apt install python3   (or your distribution's package manager)"
  exit 1
fi

say "Downloading Aqua Code..."
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "https://github.com/$REPO/archive/refs/heads/main.zip" -o "$TMP/aqua.zip"
if command -v unzip >/dev/null 2>&1; then unzip -q "$TMP/aqua.zip" -d "$TMP"; else "$PY" -m zipfile -e "$TMP/aqua.zip" "$TMP"; fi
SRC="$(find "$TMP" -maxdepth 1 -type d -name 'aqua-code-*' | head -n 1)"

mkdir -p "$DEST"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --exclude 'data' --exclude 'workspace' "$SRC"/ "$DEST"/
else
  ( cd "$SRC" && tar cf - --exclude=./data --exclude=./workspace . ) | ( cd "$DEST" && tar xf - )
fi
chmod +x "$DEST/start.sh" "$DEST/Start Aqua Code.command" 2>/dev/null || true

# a launcher you can double-click
if [ "$(uname)" = "Darwin" ] && [ -d "$HOME/Desktop" ]; then
  printf '#!/bin/bash\ncd "%s"\n%s aqua_app.py\n' "$DEST" "$PY" > "$HOME/Desktop/Aqua Code.command"
  chmod +x "$HOME/Desktop/Aqua Code.command"
  say "Added 'Aqua Code' to your Desktop (double-click it)."
elif [ -d "$HOME/.local/share/applications" ] || mkdir -p "$HOME/.local/share/applications" 2>/dev/null; then
  printf '[Desktop Entry]\nType=Application\nName=Aqua Code\nComment=Your own coding agent\nExec=%s %s/aqua_app.py\nPath=%s\nIcon=%s/assets/logo.png\nTerminal=false\nCategories=Development;\n' "$PY" "$DEST" "$DEST" "$DEST" > "$HOME/.local/share/applications/aqua-code.desktop"
fi

if [ "$(uname)" = "Darwin" ]; then
  echo "On first launch Aqua adds an 'Aqua Code' app with the logo to ~/Applications (Launchpad and Spotlight find it; drag it to the Dock)."
fi
say "Installed in $DEST"
echo "Start it with:   cd \"$DEST\" && $PY aqua_app.py"
echo "On first launch it asks for a free API key (Gemini, Groq, Mistral...) or lets you try without one."
if [ "$1" = "--run" ]; then cd "$DEST" && exec "$PY" aqua_app.py; fi
