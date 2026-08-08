#!/usr/bin/env bash
set -e

echo "Installing Python dependencies..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo "Checking FFmpeg..."
if command -v ffmpeg >/dev/null 2>&1; then
  ffmpeg -version | head -n 1
else
  echo "WARNING: FFmpeg is not installed in this Render environment."
  echo "The bot will need an FFmpeg binary available at runtime."
fi

echo "Build complete!"
