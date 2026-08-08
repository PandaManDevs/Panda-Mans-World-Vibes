#!/usr/bin/env bash
set -euo pipefail

# Render already provides Python 3.11. Do not create a second venv here.
# Install FFmpeg because Discord voice playback needs it.
apt-get update
apt-get install -y --no-install-recommends ffmpeg
rm -rf /var/lib/apt/lists/*

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
