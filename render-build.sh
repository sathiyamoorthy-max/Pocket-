#!/usr/bin/env bash

# 1. பைத்தான் பேக்கேஜ்களை இன்ஸ்டால் செய்தல்
pip install -r requirements.txt

# 2. Playwright பிரவுசரையும், சிஸ்டம் ஃபைல்களையும் இன்ஸ்டால் செய்தல்
python -m playwright install chromium
python -m playwright install-deps
