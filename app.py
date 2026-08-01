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
import urllib.parse
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

# Flask & Telegram
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Cloudflare Bypass
try:
    from curl_cffi import requests as curl_requests
    CURL_AVAILABLE = True
except ImportError:
    CURL_AVAILABLE = False
    import requests as curl_requests

import requests
import m3u8
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import yt_dlp

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("No TELEGRAM_TOKEN found in environment variables")

WATERMARK = os.getenv("WATERMARK", "@UltraDownloaderBot")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)
@flask_app.route('/')
def index():
    return "Pocket FM Premium Interactive Bot is Online!"

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port)

# 🔥 1. PERSISTENT SESSION & HEADERS
if CURL_AVAILABLE:
    session = curl_requests.Session(impersonate="chrome116")
else:
    session = requests.Session()

STATS = {"downloads": 0, "start_time": time.time()}
USER_STATE = {}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://pocketfm.com/',
    'Origin': 'https://pocketfm.com'
}

def human_readable_size(size_bytes):
    if not size_bytes: return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    return f"{round(size_bytes / p, 2)} {size_name[i]}"

def fetch_page(url, headers=None, timeout=30):
    req_headers = headers if headers else HEADERS
    return session.get(url, headers=req_headers, timeout=timeout)

# ==========================================
# 🔥 POCKET FM SERIES FETCHER
# ==========================================
def get_series_data(series_url):
    match = re.search(r'/show/([a-zA-Z0-9_-]+)', series_url)
    if not match: raise Exception("Invalid Show URL format.")
    show_id = match.group(1).split('?')[0]
    api_url = f"https://pocketfm.com/show/{show_id}"
    
    resp = fetch_page(api_url, timeout=30)
    if resp.status_code == 403:
        raise Exception(f"Cloudflare blocked fetch. Status: {resp.status_code}")

    html = resp.text
    title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    series_title = title_match.group(1).split("|")[0].strip() if title_match else "Pocket FM Series"
    
    img_match = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    series_thumb = img_match.group(1) if img_match else None
    
    json_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if not json_match: raise Exception("Could not extract JSON payload.")
    
    data = json.loads(json_match.group(1))
    episodes_map = []
    
    def extract_episodes(node):
        if isinstance(node, dict):
            ep_id = node.get('episodeId') or node.get('id')
            if ep_id and isinstance(ep_id, str) and len(ep_id) > 10:
                episodes_map.append({'url': f"https://pocketfm.com/episode/{ep_id}"})
            for v in node.values(): extract_episodes(v)
        elif isinstance(node, list):
            for item in node: extract_episodes(item)
            
    extract_episodes(data)
    
    seen = set()
    ep_list = []
    for ep in episodes_map:
        if ep['url'] not in seen:
            seen.add(ep['url'])
            ep_list.append(ep['url'])
            
    ep_list.reverse()
    if not ep_list: raise Exception("Episodes not found inside JSON structure.")
    return series_title, series_thumb, ep_list

# 🔥 2. BULLETPROOF URL RESOLVER (SECURITY TOKEN INJECTOR)
def resolve_url(base_url, target_uri):
    joined_url = urllib.parse.urljoin(base_url, target_uri)
    base_parsed = urllib.parse.urlparse(base_url)
    joined_parsed = urllib.parse.urlparse(joined_url)
    
    # 403 எரரைத் தடுக்க, M3U8-ல் உள்ள செக்யூரிட்டி டோக்கனை Chunks-களுக்கு மாற்றுகிறோம்!
    if not joined_parsed.query and base_parsed.query:
        joined_parsed = joined_parsed._replace(query=base_parsed.query)
        
    return urllib.parse.urlunparse(joined_parsed)

# ==========================================
# 🎵 CUSTOM BULLETPROOF AUDIO DOWNLOADER
# ==========================================
def download_chunk(args):
    i, full_url, aes_key, iv, tmpdir = args
    try:
        ts_path = os.path.join(tmpdir, f"chunk_{i:04d}.ts")
        
        response = fetch_page(full_url, timeout=20)
        
        if response.status_code != 200:
            logger.error(f"Chunk {i} blocked by CDN. HTTP: {response.status_code}")
            return i, None
            
        data = response.content
        if b'<html' in data[:50].lower() or b'accessdenied' in data[:50].lower():
            logger.error(f"Chunk {i} returned Access Denied.")
            return i, None
            
        if aes_key and iv:
            iv_bytes = bytes.fromhex(iv.replace('0x', '')) if isinstance(iv, str) else iv
            iv_bytes = iv_bytes.ljust(16, b'\0')
            cipher = AES.new(aes_key, AES.MODE_CBC, iv_bytes)
            decrypted_data = cipher.decrypt(data)
            try: decrypted_data = unpad(decrypted_data, AES.block_size)
            except: pass
            with open(ts_path, 'wb') as f: f.write(decrypted_data)
        else:
            with open(ts_path, 'wb') as f: f.write(data)
            
        return i, ts_path
    except Exception as e:
        logger.error(f"Chunk {i} Exception: {e}")
        return i, None

def download_audio_from_m3u8(m3u8_url):
    resp = fetch_page(m3u8_url, timeout=15)
    if resp.status_code != 200:
        raise Exception(f"Failed to fetch M3U8. HTTP {resp.status_code}")
        
    playlist = m3u8.loads(resp.text, uri=m3u8_url)
    
    if not playlist.segments and playlist.playlists:
        sub_url = resolve_url(m3u8_url, playlist.playlists[0].uri)
        resp = fetch_page(sub_url, timeout=15)
        playlist = m3u8.loads(resp.text, uri=sub_url)
        m3u8_url = sub_url  # Update base url for chunk resolving
        
    aes_key, iv = None, None
    if playlist.keys and playlist.keys[0] and playlist.keys[0].uri:
        key_url = resolve_url(m3u8_url, playlist.keys[0].uri)
        iv = playlist.keys[0].iv
        key_resp = fetch_page(key_url, timeout=15)
        if key_resp.status_code == 200:
            aes_key = key_resp.content
        else:
            raise Exception("Failed to fetch AES encryption key.")

    with tempfile.TemporaryDirectory() as tmpdir:
        tasks = []
        for i, seg in enumerate(playlist.segments):
            seg_url = resolve_url(m3u8_url, seg.uri)
            tasks.append((i, seg_url, aes_key, iv, tmpdir))
            
        ts_files_dict = {}
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(download_chunk, arg) for arg in tasks]
            for future in as_completed(futures):
                idx, path = future.result()
                if path: ts_files_dict[idx] = path
                
        ts_files = [ts_files_dict.get(i) for i in range(len(tasks)) if ts_files_dict.get(i)]
        
        if len(ts_files) == 0: 
            raise Exception("403 Forbidden: Cloudfront WAF completely blocked your Render IP.")
        
        if not os.path.exists('downloads'): os.makedirs('downloads')
        output_file = f"downloads/audio_{uuid.uuid4().hex[:8]}.mp3"
        list_path = os.path.join(tmpdir, "list.txt")
        with open(list_path, 'w') as f:
            for ts in ts_files: f.write(f"file '{ts}'\n")
            
        subprocess.run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c:a', 'libmp3lame', '-q:a', '2', output_file], check=True, capture_output=True)
        return output_file

def get_episode_metadata(episode_url):
    resp = fetch_page(episode_url, timeout=20)
    if resp.status_code == 403:
        raise Exception("Failed to fetch episode. Blocked by Cloudflare.")
        
    html = resp.text
    title = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    img = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    m3u8 = re.search(r'(https?://[^\s"\'<>]+\.cloudfront\.net[^\s"\'<>]*?\.m3u8[^\s"\'<>]*)', html)
    if not m3u8:
        m3u8 = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html)
    if not m3u8:
        raise Exception("Premium Episode or Blocked.")
    ep_title = title.group(1).split("|")[0].strip() if title else "Episode"
    return m3u8.group(1), ep_title, img.group(1) if img else None

async def download_and_send_episode(update, context, ep_url, ep_number=None, total_eps=None, series_thumb=None):
    try:
        m3u8_url, title, thumb_url = await asyncio.to_thread(get_episode_metadata, ep_url)
        audio_path = await asyncio.to_thread(download_audio_from_m3u8, m3u8_url)
        
        final_thumb_url = thumb_url if thumb_url else series_thumb
        thumb_path = None
        
        if final_thumb_url:
            thumb_path = f"downloads/thumb_{uuid.uuid4().hex[:8]}.jpg"
            try:
                img_data = fetch_page(final_thumb_url, timeout=15).content
                with open(thumb_path, 'wb') as f: f.write(img_data)
                Image.open(thumb_path).convert('RGB').thumbnail((320, 320)).save(thumb_path, 'JPEG')
            except:
                thumb_path = None
                
        display_title = f"[{ep_number}/{total_eps}] {title}" if ep_number and total_eps else title
        chat_id = update.effective_chat.id
        
        with open(audio_path, 'rb') as audio:
            thumb_file = open(thumb_path, 'rb') if thumb_path and os.path.exists(thumb_path) else None
            await context.bot.send_audio(
                chat_id=chat_id, audio=audio, title=display_title, 
                performer="Pocket FM", thumbnail=thumb_file, parse_mode="Markdown",
                read_timeout=120, write_timeout=120
            )
            if thumb_file: thumb_file.close()

        STATS["downloads"] += 1
        if os.path.exists(audio_path): os.remove(audio_path)
        if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
        return True, title
    except Exception as e:
        return False, str(e)

# ==========================================
# 🤖 TELEGRAM HANDLERS (PREMIUM INTERACTIVE)
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔗 Send any Pocket FM link.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    if user_id in USER_STATE and USER_STATE[user_id].get('state') == 'WAITING_FOR_TRACK':
        state_data = USER_STATE[user_id]
        eps = state_data['eps']
        series_thumb = state_data['thumb']
        
        try:
            parts = text.replace('-', ' ').split()
            tracks_to_dl = []
            
            if len(parts) == 1 and parts[0].isdigit():
                tracks_to_dl = [int(parts[0])]
            elif len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                start = min(int(parts[0]), int(parts[1]))
                end = max(int(parts[0]), int(parts[1]))
                tracks_to_dl = list(range(start, end + 1))
            else:
                await update.message.reply_text("⚠️ Invalid format. Send like '7' or '1 15'")
                return
                
            await update.message.reply_text(f"🚀 Downloading {len(tracks_to_dl)} tracks...")
            
            for t in tracks_to_dl:
                if 1 <= t <= len(eps):
                    ep_url = eps[t-1]
                    success, res = await download_and_send_episode(update, context, ep_url, ep_number=t, total_eps=len(eps), series_thumb=series_thumb)
                    if not success:
                        await update.message.reply_text(f"❌ Failed Track {t}: {res}")
                else:
                    await update.message.reply_text(f"❌ Track {t} is out of range.")
                    
            await update.message.reply_text("✅ All requested tracks processed!")
            del USER_STATE[user_id]
            return
            
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
            del USER_STATE[user_id]
            return

    if 'pocketfm.com/show/' in text:
        url = text.split('|')[0].strip()
        msg = await update.message.reply_text("🔄 Fetching Series info...")
        try:
            series_title, series_thumb, ep_list = await asyncio.to_thread(get_series_data, url)
            
            USER_STATE[user_id] = {
                'state': 'WAITING_FOR_TRACK',
                'eps': ep_list,
                'title': series_title,
                'thumb': series_thumb
            }
            
            caption = (
                f"✅ 100% ஒரிஜினல் தரத்தில் தயாராக உள்ளது!\n\n"
                f"🎧 Series Selected: {series_title}\n"
                f"🌎 Language: Tamil\n"
                f"📊 Total Episodes: {len(ep_list)}\n\n"
                f"💬 *Send the track number(s) you wish to fetch.*\n"
                f"Single: 7\n"
                f"Range: 1 15"
            )
            
            if series_thumb:
                await update.message.reply_photo(photo=series_thumb, caption=caption, parse_mode="Markdown")
            else:
                await update.message.reply_text(caption, parse_mode="Markdown")
                
            await msg.delete()
        except Exception as e:
            await msg.edit_text(f"❌ Error fetching series: {e}")
            return
            
    elif 'pocketfm.com/episode/' in text:
        msg = await update.message.reply_text("⬇️ Downloading single episode...")
        success, result = await download_and_send_episode(update, context, text)
        if success: await msg.delete()
        else: await msg.edit_text(f"❌ Failed: {result}")

    elif text.startswith(("http://", "https://")):
        await update.message.reply_text("⚠️ Please send Pocket FM links only.")

def main():
    if not os.path.exists("downloads"): os.makedirs("downloads")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    threading.Thread(target=run_flask, daemon=True).start()
    app.run_polling()

if __name__ == "__main__":
    main()
