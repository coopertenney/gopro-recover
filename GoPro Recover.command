#!/bin/bash
# Double-click me. Starts GoPro Recover and opens it in your browser.
#
# If macOS refuses to open this file:
#   macOS 14 and earlier: right-click it, choose Open, then Open again.
#   macOS 15 and later:   open System Settings > Privacy & Security, scroll down, click "Open Anyway",
#                         then double-click this file again.
cd "$(dirname "$0")"
echo "GoPro Recover"
echo "-------------"

pause() { echo; read -n 1 -s -r -p "Press any key to close this window."; echo; }

# 1. Python 3. macOS offers a one-click install of its developer tools if it's missing.
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
  echo "Python 3 is needed and isn't installed yet."
  echo "A macOS dialog will offer to install it (a few minutes). Click Install, wait for it to finish,"
  echo "then double-click 'GoPro Recover.command' again."
  xcode-select --install 2>/dev/null
  pause; exit 1
fi

# 2. ffmpeg, used only for previews. Use an installed copy, or fetch a static build once into ./bin
mkdir -p bin
if [ ! -x bin/ffmpeg ] && ! command -v ffmpeg >/dev/null 2>&1 && [ ! -x /opt/homebrew/bin/ffmpeg ] && [ ! -x /usr/local/bin/ffmpeg ]; then
  if curl -s --max-time 5 -o /dev/null https://github.com; then
    echo "First run: downloading ffmpeg (about 80 MB)..."
    arch=$(uname -m); [ "$arch" = "arm64" ] && asset=ffmpeg-darwin-arm64 || asset=ffmpeg-darwin-x64
    [ "$(uname)" = "Linux" ] && asset=ffmpeg-linux-x64
    if curl -L --fail --progress-bar -o bin/ffmpeg "https://github.com/eugeneware/ffmpeg-static/releases/latest/download/$asset"; then
      chmod +x bin/ffmpeg; xattr -d com.apple.quarantine bin/ffmpeg 2>/dev/null
      echo "ffmpeg ready."
    else
      echo "The ffmpeg download failed. You can still scan and recover; previews will be unavailable."
      echo "To fix later: install Homebrew (brew.sh) and run 'brew install ffmpeg', or re-run this with internet."
      rm -f bin/ffmpeg
    fi
  else
    echo "No internet connection, so ffmpeg can't be downloaded right now."
    echo "You can still scan and recover; previews will be unavailable until you run this again online."
  fi
fi

# 3. Run
echo
echo "Starting. Your browser will open at http://127.0.0.1:8765"
echo "Keep this window open while you work. Close it (or press Ctrl-C) to quit."
echo
python3 -I app.py
status=$?
if [ $status -ne 0 ]; then
  echo
  echo "GoPro Recover stopped with an error (code $status). The messages above say why."
  echo "If it's not obvious, take a screenshot of this window and send it to whoever gave you the tool."
  pause
fi
