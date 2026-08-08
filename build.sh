#!/bin/bash
set -e

# Install Python 3.11
apt-get update
apt-get install -y python3.11 python3.11-venv python3.11-dev

# Use Python 3.11 for the virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install requirements
pip install -r requirements.txt
