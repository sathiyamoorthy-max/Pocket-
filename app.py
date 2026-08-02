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
import random
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

# Flask & Telegram
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# M3U8 & Crypto
import m3u8
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import yt_dlp

load_dotenv()

# ==========================================
# 🔥 1. ULTIMATE CLOUDFLARE TLS BYPASS (curl_cffi)
# ==========================================
try:
    from curl_cffi import requests as curl_requests
    CURL_AVAILABLE = True
    print("✅ curl_cffi Loaded! Cloudflare TLS Bypass Active.")
except ImportError:
    CURL_AVAILABLE = False
    import requests as curl_requests
    import requests
    print("⚠️ curl_cffi not installed. Falling back to requests.")

# ==========================================
# 🔥 2. SMART PROXY ROTATOR (தானாக Proxy மாற்றும் ஹேக்)
# ==========================================
PROXY_LIST = []
LAST_PROXY_FETCH = 0

def get_free_proxies():
    global PROXY_LIST, LAST_PROXY_FETCH
    if time.time() - LAST_PROXY_FETCH < 1800 and PROXY_LIST:
        return PROXY_LIST
    try:
        url = "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            proxies = response.text.strip().split('\n')
            PROXY_LIST = [p.strip() for p in proxies if re.match(r'\d+\.\d+\.\d+\.\d+:\d+', p.strip())]
            random.shuffle(PROXY_LIST)
            LAST_PROXY_FETCH = time.time()
            print(f"🔄 Refreshed {len(PROXY_LIST)} proxies.")
            return PROXY_LIST
    except:
        pass
    return ["103.163.118.217:8080", "45.79.66.195:3128"] # Fallback

def get_working_proxy():
    proxies = get_free_proxies()
    for proxy in proxies:
        try:
            test_resp = requests.get("https://pocketfm.com", proxies={'http': f'http://{proxy}', 'https': f'http://{proxy}'}, timeout=5)
            if test_resp.status_code == 200:
                print(f"✅ Found working proxy: {proxy}")
                return {'http': f'http://{proxy}', 'https': f'http://{proxy}'}
        except:
            continue
    return None

# 🌍 MASTER FETCHER (curl_cffi -> Proxy -> Fallback)
def fetch_page(url, headers=None, timeout=30):
    # 1. curl_cffi
    if CURL_AVAILABLE:
        try:
            return curl_requests.get(url, impersonate="chrome133", headers=headers, timeout=timeout)
        except Exception as e:
            print(f"⚠️ curl_cffi failed: {e}")
    
    # 2. Proxy Rotator
    proxy = get_working_proxy()
    if proxy:
        try:
            print(f"🛡️ Trying Proxy: {proxy['http']}")
            return requests.get(url, headers=headers, timeout=timeout, proxies=proxy)
        except Exception as e:
            print(f"⚠️ Proxy failed: {e}")
    
    # 3. நேரடி Render IP
    print("⚠️ Using direct Render IP (High Risk).")
    return requests.get(url, headers=headers, timeout=timeout)

# ==========================================
# 📅 3. CONFIG & FUTURE TRACKER
# ==========================================
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("No TELEGRAM_TOKEN found in environment variables")

WATERMARK = os.getenv("WATERMARK", "@UltraDownloaderBot")
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)
@flask_app.route('/')
def index():
    return "World Class Render Bot is online!"

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port)

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
# 🎵 4. DOWNLOAD CORE (M3U8, AES, FFMPEG)
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
            try: decrypted_data = unpad(decrypted_data, AES.block_size)
            except: pass
            with open(ts_path, 'wb') as f: f.write(decrypted_data)
        else:
            with open(ts_path, 'wb') as f: f.write(data)
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
        
        if not os.path.exists('downloads'): os.makedirs('downloads')
        unique_name = str(uuid.uuid4())[:8]
        output_file = f"downloads/audio_{unique_name}.mp3"
        list_path = os.path.join(tmpdir, "list.txt")
        with open(list_path, 'w') as f:
            for ts in ts_files:
                f.write(f"file '{ts}'\n")
        subprocess.run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c:a', 'libmp3lame', '-q:a', '2', output_file], check=True, capture_output=True)
        return output_file

# ==========================================
# 🔥 5. POCKET FM SERIES & EPISODE FETCHER
# ==========================================
def get_series_data(series_url):
    match = re.search(r'/show/([a-zA-Z0-9_-]+)', series_url)
    if not match: raise Exception("Invalid Show URL format.")
    show_id = match.group(1).split('?')[0]
    api_url = f"https://pocketfm.com/show/{show_id}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
    }
    
    resp = fetch_page(api_url, headers=headers, timeout=30)
    if resp.status_code == 403:
        raise Exception("❌ 403 Blocked. Use Multi-Link mode (Paste all episode links in one message).")
    
    html = resp.text
    title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    series_title = title_match.group(1).split("|")[0].strip() if title_match else "Pocket FM Series"
    json_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if not json_match: raise Exception("Next.js JSON payload not found.")
    data = json.loads(json_match.group(1))
    
    episodes_map = []
    def extract_episodes(node):
        if isinstance(node, dict):
            ep_id = node.get('episodeId') or node.get('id')
            if ep_id and isinstance(ep_id, str) and len(ep_id) > 10:
                episodes_map.append({'url': f"https://pocketfm.com/episode/{ep_id}"})
            for k, v in node.items(): extract_episodes(v)
        elif isinstance(node, list):
            for item in node: extract_episodes(item)
    extract_episodes(data)
    unique_episodes = {ep['url']: ep for ep in episodes_map}.values()
    return series_title, [ep['url'] for ep in unique_episodes]

def get_episode_metadata(episode_url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'}
    resp = fetch_page(episode_url, headers=headers, timeout=20)
    if resp.status_code == 403:
        raise Exception("❌ 403 Blocked. Please send a different episode link.")
    
    html = resp.text
    title = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    img = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    m3u8 = re.search(r'(https?://[^\s"\'<>]+\.cloudfront\.net[^\s"\'<>]*?\.m3u8[^\s"\'<>]*)', html)
    if not m3u8: m3u8 = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html)
    if not m3u8: raise Exception("M3U8 Stream not found")
    ep_title = title.group(1).split("|")[0].strip() if title else "Episode"
    return m3u8.group(1), ep_title, img.group(1) if img else None

# ==========================================
# 🤖 6. BOT HANDLERS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔥 **Ultimate Render Bot is Online!**\n\n"
        "Send any Pocket FM `/show/` or `/episode/` link.\n"
        "Multi-Link & Future Tracking Supported.\n"
        "⚠️ If 403 occurs, use Multi-Link Mode."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    msg = await update.message.reply_text("🔍 Analyzing...")

    if 'onelink.me' in text and 'pocketfm.com' not in text:
        await msg.edit_text("🔄 Resolving...")
        try:
            resolved = curl_requests.get(text, impersonate="chrome133", allow_redirects=True, timeout=15).url if CURL_AVAILABLE else requests.get(text, allow_redirects=True, timeout=15).url
            if 'pocketfm.com' in resolved:
                text = resolved
                await msg.edit_text(f"✅ Resolved: `{text}`", parse_mode="Markdown")
            else:
                await msg.edit_text("❌ Could not resolve.")
                return
        except:
            await msg.edit_text("❌ Error resolving.")
            return

    try:
        # Multi-Link
        if text.count('/episode/') > 1 or '\n' in text:
            urls = [l.strip() for l in text.split('\n') if '/episode/' in l]
            await msg.edit_text(f"🚀 Batch: {len(urls)} links")
            for i, u in enumerate(urls, 1):
                await download_and_send_episode(update, context, u, i, len(urls))
            await msg.edit_text("✅ Batch Done!")
            return

        # Series
        elif '/show/' in text:
            url = text.split('|')[0].strip()
            await msg.edit_text("🔄 Fetching Series via Proxy...")
            title, eps = await asyncio.to_thread(get_series_data, url)
            total = len(eps)
            
            tracker = load_tracker()
            show_id = url.split('/show/')[1].strip('/')
            old_eps = tracker.get(show_id, [])
            new_eps = [ep for ep in eps if ep not in old_eps]

            if not new_eps:
                await msg.edit_text(f"📊 {title}\nTotal: {total}\n✅ No new episodes.")
                return

            await msg.edit_text(f"✅ Found {len(new_eps)} new eps. Downloading...")
            for i, ep in enumerate(new_eps, 1):
                await download_and_send_episode(update, context, ep, i, len(new_eps))
                await asyncio.sleep(2)
            
            tracker[show_id] = eps
            save_tracker(tracker)
            await msg.edit_text(f"🎉 Complete! {len(new_eps)} eps sent.")
            return

        # Single
        elif '/episode/' in text:
            await download_and_send_episode(update, context, text)
            await msg.delete()
            return

        else:
            await msg.edit_text("❌ Invalid Link.")

    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

async def download_and_send_episode(update, context, ep_url, ep_num=None, total=None):
    try:
        m3u8_url, title, thumb_url = await asyncio.to_thread(get_episode_metadata, ep_url)
        audio_path = await asyncio.to_thread(download_audio_from_m3u8, m3u8_url)
        thumb_path = None
        if thumb_url:
            t_id = str(uuid.uuid4())[:8]
            thumb_path = f"downloads/thumb_{t_id}.jpg"
            try:
                img_data = requests.get(thumb_url).content
                with open(thumb_path, 'wb') as f: f.write(img_data)
                Image.open(thumb_path).convert('RGB').thumbnail((320, 320)).save(thumb_path, 'JPEG')
            except: pass
        display_title = f"[{ep_num}/{total}] {title}" if ep_num and total else title
        with open(audio_path, 'rb') as audio:
            thumb_file = open(thumb_path, 'rb') if thumb_path and os.path.exists(thumb_path) else None
            await context.bot.send_audio(update.effective_chat.id, audio=audio, title=display_title, performer="Pocket FM", thumbnail=thumb_file)
            if thumb_file: thumb_file.close()
        if os.path.exists(audio_path): os.remove(audio_path)
        if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

def run_bot():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    run_bot()
