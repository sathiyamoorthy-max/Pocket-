#!/usr/bin/env bash

# 1. பைத்தான் பேக்கேஜ்களை இன்ஸ்டால் செய்தல்
pip install -r requirements.txt

# 2. Playwright பிரவுசரை மட்டும் இன்ஸ்டால் செய்தல் (dependencies வேண்டாம்)
python -m playwright install chromium
