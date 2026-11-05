#!/bin/bash
# Double-click me. Starts GoPro Recover and opens it in your browser.
cd "$(dirname "$0")"
echo "GoPro Recover"
echo "-------------"

# 1. Python 3 (macOS offers a one-click install of the developer tools if it is missing)
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is needed. macOS will offer to install it; accept, then double-click this file again."
  xcode-select --install 2>/dev/null
  read -n 1 -s -r -p "Press any key to close."; exit 1
fi

# 2. ffmpeg (used for previews). Use an installed one, or fetch a static build into ./bin
mkdir -p bin
if [ ! -x bin/ffmpeg ] && ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Downloading ffmpeg (one time, about 80 MB)..."
  arch=$(uname -m); [ "$arch" = "arm64" ] && asset=ffmpeg-darwin-arm64 || asset=ffmpeg-darwin-x64
  if [ "$(uname)" = "Linux" ]; then asset=ffmpeg-linux-x64; fi
  if curl -L --fail --progress-bar -o bin/ffmpeg "https://github.com/eugeneware/ffmpeg-static/releases/latest/download/$asset"; then
    chmod +x bin/ffmpeg; xattr -d com.apple.quarantine bin/ffmpeg 2>/dev/null
    echo "ffmpeg ready."
  else
    echo "Could not download ffmpeg. Previews will be unavailable until you install it (brew install ffmpeg)."
    rm -f bin/ffmpeg
  fi
fi

# 3. Run
echo "Starting... your browser will open. Keep this window open while you work; close it to quit."
exec python3 -I app.py
