#!/usr/bin/env bash

# 1. Install Python packages
pip install -r requirements.txt

# 2. Install Playwright browser
playwright install chromium

# 3. Download and Install FFmpeg for Render Linux Server
mkdir -p bin
wget https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
tar -xvf ffmpeg-release-amd64-static.tar.xz
mv ffmpeg-*-amd64-static/ffmpeg bin/
