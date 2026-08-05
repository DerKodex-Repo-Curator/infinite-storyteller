#!/bin/bash
# Double-click this in Finder if the .app is blocked by macOS offline.
# Opens in Terminal so any error is visible. Works fully offline.

cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:/Applications/Ollama.app/Contents/Resources:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$PATH"

echo "The Infinite Storyteller — offline launcher"
echo "Project: $(pwd)"
echo

if [[ -d "/Applications/Ollama.app" ]]; then
  echo "Starting Ollama…"
  open -a Ollama >/dev/null 2>&1 || true
fi

pkill -f "$(pwd)/gui_web.py" >/dev/null 2>&1 || true
sleep 0.4

echo "Opening http://127.0.0.1:8765 …"
echo "(Leave this Terminal window open while you play. Press Ctrl+C to quit.)"
echo
/usr/bin/python3 gui_web.py
echo
echo "Game stopped. Press Return to close."
read -r _
