#!/usr/bin/env bash
set -euo pipefail

apt-get update
apt-get install -y --no-install-recommends ffmpeg ca-certificates
rm -rf /var/lib/apt/lists/*

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

ffmpeg -version >/dev/null
printf 'FFmpeg installation verified.\\n'
