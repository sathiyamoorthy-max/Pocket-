#!/usr/bin/env bash
apt-get update
apt-get install -y ffmpeg build-essential python3-dev libssl-dev libffi-dev
pip install --no-cache-dir -r requirements.txt
