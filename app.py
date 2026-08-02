import os
import re
import subprocess
import logging
import asyncio
import uuid
import tempfile
import json
import threading
import time
import math
import requests
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

import m3u8
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import yt_dlp

load_dotenv()

# ==========================================
# 🔥 1. CONFIGURATION (TOKEN & DEVICE ID)
# ==========================================
TOKEN = os.getenv("TELEGRAM_TOKEN")
POCKET_TOKEN = os.getenv("POCKET_TOKEN")  # "Bearer eyJ..." Full token
DEVICE_ID = os.getenv("DEVICE_ID", "OPPO_CPH2219")

if not TOKEN or not POCKET_TOKEN:
    raise ValueError("TELEGRAM_TOKEN and POCKET_TOKEN must be set in environment variables.")

# ==========================================
# 📱 2. POCKET FM MOBILE API SETUP
# ==========================================
POCKET_API_BASE = "https://api.pocketfm.com/api/v1"
POCKET_HEADERS = {
    "X-Device-Id": DEVICE_ID,
    "Authorization": POCKET_TOKEN,
    "Content-Type": "application/json",
    "User-Agent": "PocketFM/6.5.0 (Android; 13; SM-G991B)"
}

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)
@flask_app.route('/')
def index():
    return "Ultimate Pocket FM Bot is online!"

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port)

# ==========================================
# 📅 3. FUTURE EPISODE TRACKER
# ==========================================
TRACKER_FILE = 'series_cache.json'
def load_tracker():
    if os.path.exists(TRACKER_FILE):
        try:
            with open(TRACKER_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_tracker(data):
    with open(TRACKER_FILE, 'w') as f:
        json.dump(data, f)

# ==========================================
# 🔥 4. API FUNCTIONS (Get Series & Episode Data)
# ==========================================
def fetch_series_data(series_url):
    """Get series info and episode list via API."""
    match = re.search(r'/show/([a-zA-Z0-9_-]+)', series_url)
    if not match:
        raise Exception("Invalid series URL format.")
    show_id = match.group(1).split('?')[0]
    
    api_url = f"{POCKET_API_BASE}/show/{show_id}"
    resp = requests.get(api_url, headers=POCKET_HEADERS, timeout=30)
    if resp.status_code == 401:
        raise Exception("POCKET_TOKEN expired or invalid. Use /renew to update.")
    if resp.status_code != 200:
        raise Exception(f"API error: {resp.status_code}")
    
    data = resp.json()
    series_title = data.get('title', 'Unknown Series')
    
    # Get episodes (might be paginated, but we'll take first page)
    episodes = data.get('episodes', [])
    episode_urls = [f"https://pocketfm.com/episode/{ep['id']}" for ep in episodes if ep.get('id')]
    return series_title, episode_urls

def fetch_episode_stream(episode_url):
    """Fetch M3U8 stream URL and metadata using API."""
    match = re.search(r'/episode/([a-zA-Z0-9_-]+)', episode_url)
    if not match:
        raise Exception("Invalid episode URL.")
    episode_id = match.group(1).split('?')[0]
    
    api_url = f"{POCKET_API_BASE}/episode/{episode_id}"
    resp = requests.get(api_url, headers=POCKET_HEADERS, timeout=20)
    if resp.status_code == 401:
        raise Exception("Token expired.")
    if resp.status_code != 200:
        raise Exception(f"API error: {resp.status_code}")
    
    data = resp.json()
    title = data.get('title', 'Episode')
    thumbnail = data.get('imageUrl') or data.get('thumbnail')
    # The audio URL is usually in data['audioUrl'] or data['streamUrl']
    stream_url = data.get('audioUrl') or data.get('streamUrl')
    if not stream_url:
        # Fallback to M3U8 from embedded data
        # Sometimes it's under data['playbackInfo']['url']
        playback = data.get('playbackInfo')
        if playback:
            stream_url = playback.get('url')
    if not stream_url:
        raise Exception("No audio stream URL found in API response.")
    return stream_url, title, thumbnail

# ==========================================
# 🎵 5. DOWNLOAD ENGINE (M3U8, AES, FFMPEG)
# ==========================================
def download_chunk(args):
    i, seg_url, base_url, aes_key, iv, tmpdir = args
    try:
        full_url = seg_url if seg_url.startswith('http') else f"{base_url}/{seg_url}"
        ts_path = os.path.join(tmpdir, f"chunk_{i:04d}.ts")
        response = requests.get(full_url, stream=True, timeout=15)
        data = response.content
        if aes_key and iv:
            iv_bytes = bytes.fromhex(iv.replace('0x', '')) if isinstance(iv, str) else iv
            iv_bytes = iv_bytes.ljust(16, b'\0')
            cipher = AES.new(aes_key, AES.MODE_CBC, iv_bytes)
            decrypted_data = cipher.decrypt(data)
            try:
                decrypted_data = unpad(decrypted_data, AES.block_size)
            except:
                pass
            with open(ts_path, 'wb') as f:
                f.write(decrypted_data)
        else:
            with open(ts_path, 'wb') as f:
                f.write(data)
        return i, ts_path
    except:
        return i, None

def download_audio_from_m3u8(m3u8_url):
    playlist = m3u8.load(m3u8_url)
    base_url = '/'.join(m3u8_url.split('/')[:-1])
    if not playlist.segments and playlist.playlists:
        m3u8_url = playlist.playlists[0].uri
        m3u8_url = m3u8_url if m3u8_url.startswith('http') else f"{base_url}/{m3u8_url}"
        playlist = m3u8.load(m3u8_url)
        base_url = '/'.join(m3u8_url.split('/')[:-1])
        
    aes_key, iv = None, None
    if playlist.keys and playlist.keys[0]:
        key_uri = playlist.keys[0].uri
        iv = playlist.keys[0].iv
        key_url = key_uri if key_uri.startswith('http') else f"{base_url}/{key_uri}"
        aes_key = requests.get(key_url).content

    segments = [seg.uri for seg in playlist.segments]
    with tempfile.TemporaryDirectory() as tmpdir:
        tasks = [(i, seg, base_url, aes_key, iv, tmpdir) for i, seg in enumerate(segments)]
        ts_files_dict = {}
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(download_chunk, arg) for arg in tasks]
            for future in as_completed(futures):
                idx, path = future.result()
                if path:
                    ts_files_dict[idx] = path
        ts_files = [ts_files_dict[i] for i in sorted(ts_files_dict.keys())]
        
        if not os.path.exists('downloads'):
            os.makedirs('downloads')
        unique_name = str(uuid.uuid4())[:8]
        output_file = f"downloads/audio_{unique_name}.mp3"
        list_path = os.path.join(tmpdir, "list.txt")
        with open(list_path, 'w') as f:
            for ts in ts_files:
                f.write(f"file '{ts}'\n")
        subprocess.run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c:a', 'libmp3lame', '-q:a', '2', output_file], check=True, capture_output=True)
        return output_file

# ==========================================
# 🤖 6. TELEGRAM HANDLERS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔥 **Ultimate Pocket FM Bot (API Mode)**\n\n"
        "✅ Uses your personal JWT token to bypass all restrictions.\n"
        "✅ Supports:\n"
        "• Single episode download: `/episode/...`\n"
        "• Full series download: `/show/...`\n"
        "• Batch multiple episodes: paste many `/episode/` links in one message\n"
        "• Range: `/show/... | 1 to 10`\n"
        "• Future episode tracking: new episodes auto-downloaded when you send the series link again.\n\n"
        "⚠️ If token expires, use /renew to update."
    )

async def renew(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔄 **How to get a new JWT Token:**\n"
        "1. Go to the `@PocketFMNotificationBot` on Telegram.\n"
        "2. Use `/pbtoken` to get your fresh token.\n"
        "3. Copy the entire `eyJ...` token.\n"
        "4. Set it as environment variable `POCKET_TOKEN` in Render and restart the bot."
    )

async def download_and_send_episode(update, context, ep_url, ep_number=None, total_eps=None):
    try:
        m3u8_url, title, thumb_url = await asyncio.to_thread(fetch_episode_stream, ep_url)
        audio_path = await asyncio.to_thread(download_audio_from_m3u8, m3u8_url)
        
        thumb_path = None
        if thumb_url:
            t_id = str(uuid.uuid4())[:8]
            thumb_path = f"downloads/thumb_{t_id}.jpg"
            try:
                img_data = requests.get(thumb_url).content
                with open(thumb_path, 'wb') as f:
                    f.write(img_data)
                Image.open(thumb_path).convert('RGB').thumbnail((320, 320)).save(thumb_path, 'JPEG')
            except:
                thumb_path = None
        
        display_title = f"[{ep_number}/{total_eps}] {title}" if ep_number and total_eps else title
        with open(audio_path, 'rb') as audio:
            thumb_file = open(thumb_path, 'rb') if thumb_path else None
            await context.bot.send_audio(
                chat_id=update.effective_chat.id,
                audio=audio,
                title=display_title,
                performer="Pocket FM",
                thumbnail=thumb_file
            )
            if thumb_file:
                thumb_file.close()
        if os.path.exists(audio_path):
            os.remove(audio_path)
        if thumb_path and os.path.exists(thumb_path):
            os.remove(thumb_path)
        return True, title
    except Exception as e:
        return False, str(e)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    msg = await update.message.reply_text("🔍 Processing...")

    # If user sent onelink, resolve it (optional)
    if 'onelink.me' in text and 'pocketfm.com' not in text:
        # Simple redirect follow
        try:
            resp = requests.get(text, allow_redirects=True, timeout=10)
            if 'pocketfm.com' in resp.url:
                text = resp.url
                await msg.edit_text(f"✅ Resolved: `{text}`")
            else:
                await msg.edit_text("❌ Could not resolve to a Pocket FM link.")
                return
        except:
            await msg.edit_text("❌ Error resolving.")
            return

    try:
        # 🟢 Multi-Link Batch Mode
        if text.count('/episode/') > 1 or '\n' in text:
            urls = [line.strip() for line in text.split('\n') if '/episode/' in line]
            if not urls:
                await msg.edit_text("❌ No valid episode links found.")
                return
            await msg.edit_text(f"🚀 Found {len(urls)} episode links. Downloading batch...")
            for i, url in enumerate(urls, 1):
                await download_and_send_episode(update, context, url, ep_number=i, total_eps=len(urls))
            await msg.edit_text(f"✅ Batch complete! {len(urls)} episodes sent.")
            return

        # 🔵 Series Mode with Range
        elif '/show/' in text:
            # Check for range
            range_match = re.search(r'\|?\s*(\d+)\s*to\s*(\d+)', text)
            url = text.split('|')[0].strip()
            
            await msg.edit_text("🔄 Fetching series data from API...")
            series_title, episode_urls = await asyncio.to_thread(fetch_series_data, url)
            total_found = len(episode_urls)
            
            # Apply range if specified
            if range_match:
                start_ep = int(range_match.group(1))
                end_ep = int(range_match.group(2))
                if start_ep > total_found or end_ep > total_found:
                    await msg.edit_text(f"❌ Range error: Series has only {total_found} episodes.")
                    return
                episode_urls = episode_urls[start_ep-1:end_ep]
                await msg.edit_text(f"✅ Found {len(episode_urls)} episodes (range {start_ep}-{end_ep}). Downloading...")
            else:
                await msg.edit_text(f"✅ Found {total_found} episodes in '{series_title}'. Downloading...")
            
            # Future tracking: only download new episodes
            tracker = load_tracker()
            show_id = url.split('/show/')[1].strip('/')
            old_eps = tracker.get(show_id, [])
            new_eps = [ep for ep in episode_urls if ep not in old_eps]
            
            if not new_eps and not range_match:
                await msg.edit_text(f"📊 **{series_title}**\nTotal: {total_found}\n✅ All episodes already downloaded. No new episodes.")
                return
            
            # If range is specified, we download those even if already downloaded
            download_list = new_eps if not range_match else episode_urls
            if range_match:
                download_list = episode_urls  # download all in range regardless of previous

            for i, ep in enumerate(download_list, 1):
                await download_and_send_episode(update, context, ep, ep_number=i, total_eps=len(download_list))
                await asyncio.sleep(2)
            
            # Update tracker only if not a range request
            if not range_match:
                tracker[show_id] = episode_urls
                save_tracker(tracker)
            
            await msg.edit_text(f"🎉 Complete! {len(download_list)} episodes sent.")
            return

        # 🟢 Single Episode
        elif '/episode/' in text:
            await download_and_send_episode(update, context, text)
            await msg.delete()
            return

        else:
            await msg.edit_text("❌ Invalid URL. Send a valid Pocket FM link.")

    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}\n\n💡 If token expired, use /renew to get a new one.")

def run_bot():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("renew", renew))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    run_bot()
