#!/usr/bin/env bash
pip install -r requirements.txt

# Render சர்வரில் FFmpeg-ஐ இன்ஸ்டால் செய்தல்
mkdir -p bin
wget https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
tar -xvf ffmpeg-release-amd64-static.tar.xz
mv ffmpeg-*-amd64-static/ffmpeg bin/
