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
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

# Flask & Telegram
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# M3U8 & Crypto
import m3u8
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import yt_dlp

# ==========================================
# 🔥 1. CLOUDFLARE TLS BYPASS & HEADERS
# ==========================================
# எல்லா ரெக்வெஸ்ட்களுக்கும் இந்த குரோம் ஹெடர் கட்டாயம்!
BASE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
    'Referer': 'https://pocketfm.com/',
    'Origin': 'https://pocketfm.com'
}

try:
    from curl_cffi import requests as curl_requests
    CURL_AVAILABLE = True
    print("✅ curl_cffi Loaded! Cloudflare TLS Bypass Active.")
except ImportError:
    CURL_AVAILABLE = False
    print("⚠️ curl_cffi not installed. Falling back to requests.")

# ==========================================
# 🔥 2. SMART PROXY ROTATOR (IP BYPASS)
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
    return ["103.163.118.217:8080"]

def get_working_proxy():
    proxies = get_free_proxies()
    for proxy in proxies[:5]:  # முதல் 5 ப்ராக்ஸிகளை மட்டும் செக் செய்வோம்
        try:
            test_resp = requests.get("https://pocketfm.com", headers=BASE_HEADERS, proxies={'http': f'http://{proxy}', 'https': f'http://{proxy}'}, timeout=3)
            if test_resp.status_code == 200:
                print(f"✅ Found working proxy: {proxy}")
                return {'http': f'http://{proxy}', 'https': f'http://{proxy}'}
        except:
            continue
    return None

# 🌍 MASTER FETCHER (இப்போது ஸ்ட்ரீமிங்கும் சப்போர்ட் செய்யும்)
def fetch_page(url, headers=None, timeout=30, stream=False):
    req_headers = headers if headers else BASE_HEADERS
    
    if CURL_AVAILABLE and not stream:
        try:
            return curl_requests.get(url, impersonate="chrome133", headers=req_headers, timeout=timeout)
        except Exception:
            pass
    
    proxy = get_working_proxy()
    if proxy:
        try:
            return requests.get(url, headers=req_headers, timeout=timeout, proxies=proxy, stream=stream)
        except:
            pass
    
    return requests.get(url, headers=req_headers, timeout=timeout, stream=stream)

# ==========================================
# 📅 3. CONFIG
# ==========================================
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("No TELEGRAM_TOKEN found. Please add it to Render Environment Variables.")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)
@flask_app.route('/')
def index():
    return "Ultimate Hybrid Bot is online!"

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
# 🎵 4. CORE DOWNLOAD ENGINE (BUG FIXED)
# ==========================================
def download_chunk(args):
    i, seg_url, base_url, aes_key, iv, tmpdir = args
    try:
        full_url = seg_url if seg_url.startswith('http') else f"{base_url}/{seg_url}"
        ts_path = os.path.join(tmpdir, f"chunk_{i:04d}.ts")
        
        # 🔥 FIX: Chunks எடுக்கும்போதும் Headers கட்டாயம் தேவை!
        response = fetch_page(full_url, stream=True, timeout=15)
        if response.status_code != 200:
            return i, None
            
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
    # 🔥 FIX: m3u8.load() பைத்தானைக் காட்டிக் கொடுத்துவிடும். அதனால் fetch_page பயன்படுத்துகிறோம்.
    m3u8_content = fetch_page(m3u8_url).text
    playlist = m3u8.loads(m3u8_content, uri=m3u8_url)
    base_url = '/'.join(m3u8_url.split('/')[:-1])
    
    if not playlist.segments and playlist.playlists:
        m3u8_url = playlist.playlists[0].uri
        m3u8_url = m3u8_url if m3u8_url.startswith('http') else f"{base_url}/{m3u8_url}"
        m3u8_content = fetch_page(m3u8_url).text
        playlist = m3u8.loads(m3u8_content, uri=m3u8_url)
        base_url = '/'.join(m3u8_url.split('/')[:-1])
        
    aes_key, iv = None, None
    if playlist.keys and playlist.keys[0]:
        key_uri = playlist.keys[0].uri
        iv = playlist.keys[0].iv
        key_url = key_uri if key_uri.startswith('http') else f"{base_url}/{key_uri}"
        # 🔥 FIX: Key எடுக்கும்போதும் Headers கட்டாயம் தேவை!
        aes_key = fetch_page(key_url).content

    segments = [seg.uri for seg in playlist.segments]
    with tempfile.TemporaryDirectory() as tmpdir:
        tasks = [(i, seg, base_url, aes_key, iv, tmpdir) for i, seg in enumerate(segments)]
        ts_files_dict = {}
        with ThreadPoolExecutor(max_workers=5) as executor:  # 10 workers இருந்தால் AWS பிளாக் செய்யும், அதனால் 5 ஆக குறைத்துள்ளேன்
            futures = [executor.submit(download_chunk, arg) for arg in tasks]
            for future in as_completed(futures):
                idx, path = future.result()
                if path:
                    ts_files_dict[idx] = path
        ts_files = [ts_files_dict[i] for i in sorted(ts_files_dict.keys())]
        
        if not ts_files:
            raise Exception("CDN Blocked chunks. Fallback to Kiwi Hack.")
            
        if not os.path.exists('downloads'): os.makedirs('downloads')
        unique_name = str(uuid.uuid4())[:8]
        output_file = f"downloads/audio_{unique_name}.mp3"
        list_path = os.path.join(tmpdir, "list.txt")
        with open(list_path, 'w') as f:
            for ts in ts_files:
                f.write(f"file '{ts}'\n")
        subprocess.run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c:a', 'libmp3lame', '-q:a', '2', output_file], check=True, capture_output=True)
        return output_file

def get_episode_metadata(episode_url):
    resp = fetch_page(episode_url, timeout=20)
    if resp.status_code == 403:
        raise Exception("403 Blocked. Try Multi-Link mode.")
    
    html = resp.text
    title = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    img = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    m3u8_match = re.search(r'(https?://[^\s"\'<>]+\.cloudfront\.net[^\s"\'<>]*?\.m3u8[^\s"\'<>]*)', html)
    if not m3u8_match: m3u8_match = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html)
    if not m3u8_match: raise Exception("M3U8 Stream not found")
    ep_title = title.group(1).split("|")[0].strip() if title else "Episode"
    return m3u8_match.group(1), ep_title, img.group(1) if img else None

# ==========================================
# 🔥 5. SERIES FETCHER + GUARANTEED FALLBACK
# ==========================================
def get_series_data(series_url):
    match = re.search(r'/show/([a-zA-Z0-9_-]+)', series_url)
    show_id = match.group(1).split('?')[0]
    api_url = f"https://pocketfm.com/show/{show_id}"
    
    resp = fetch_page(api_url, timeout=30)
    
    if resp.status_code == 403:
        raise Exception(
            "❌ **All Bypass Layers Failed (TLS & Proxy). But don't worry, we have a 100% guarantee!**\n\n"
            "💡 **The Ultimate Kiwi Browser Console Hack:**\n"
            "1. Open the Show page in **Kiwi Browser**.\n"
            "2. Tap `...` -> `Developer Tools` -> `Console`.\n"
            "3. Copy & Paste the code below and hit Enter:\n"
            "`document.querySelectorAll('a[href*=\"/episode/\"]').forEach(a => { if(a.href) console.log(a.href); });`\n"
            "4. **Copy the output list** (Ep 1, Ep 2, Ep 3...) and **paste ALL links** here in ONE message.\n"
            "⚡ The bot will automatically download them ALL in Batch mode!"
        )

    html = resp.text
    title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    series_title = title_match.group(1).split("|")[0].strip() if title_match else "Pocket FM Series"
    json_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if not json_match: raise Exception("JSON not found.")
    data = json.loads(json_match.group(1))
    
    eps = []
    def extract(node):
        if isinstance(node, dict):
            eid = node.get('episodeId') or node.get('id')
            if eid and len(str(eid)) > 10:
                eps.append(f"https://pocketfm.com/episode/{eid}")
            for k, v in node.items(): extract(v)
        elif isinstance(node, list):
            for i in node: extract(i)
    extract(data)
    return series_title, list(dict.fromkeys(eps))

async def download_and_send_episode(update, context, ep_url, ep_num=None, total=None):
    try:
        m3u8_url, title, thumb_url = await asyncio.to_thread(get_episode_metadata, ep_url)
        audio_path = await asyncio.to_thread(download_audio_from_m3u8, m3u8_url)
        thumb_path = None
        if thumb_url:
            t_id = str(uuid.uuid4())[:8]
            thumb_path = f"downloads/thumb_{t_id}.jpg"
            try:
                img_data = fetch_page(thumb_url).content
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

# ==========================================
# 🤖 6. BOT HANDLERS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔥 **Ultimate Hybrid Bot is Online!**\n\n"
        "✅ TLS & Proxy bypass active.\n"
        "✅ If blocked, the bot will guide you to the **100% guaranteed Kiwi Console hack**.\n"
        "Send a Pocket FM `/show/` or `/episode/` link."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    msg = await update.message.reply_text("🔍 Analysing...")

    try:
        # Multi-Link Batch (The Safe Bypass)
        if text.count('/episode/') > 1 or '\n' in text:
            urls = [l.strip() for l in text.split('\n') if '/episode/' in l]
            await msg.edit_text(f"🚀 Found {len(urls)} links. Downloading batch...")
            for i, u in enumerate(urls, 1):
                await download_and_send_episode(update, context, u, i, len(urls))
            await msg.edit_text(f"✅ Batch Done!")
            return

        # Series Mode
        elif '/show/' in text:
            url = text.split('|')[0].strip()
            await msg.edit_text("🔄 Trying TLS & Proxy layers...")
            title, eps = await asyncio.to_thread(get_series_data, url)
            total = len(eps)
            
            tracker = load_tracker()
            show_id = url.split('/show/')[1].strip('/')
            old = tracker.get(show_id, [])
            new = [ep for ep in eps if ep not in old]

            if not new:
                await msg.edit_text(f"📊 {title}\nTotal: {total}\n✅ No new episodes.")
                return

            await msg.edit_text(f"✅ Found {len(new)} new eps. Downloading...")
            for i, ep in enumerate(new, 1):
                await download_and_send_episode(update, context, ep, i, len(new))
                await asyncio.sleep(2)
            
            tracker[show_id] = eps
            save_tracker(tracker)
            await msg.edit_text(f"🎉 Complete! {len(new)} eps sent.")
            return

        elif '/episode/' in text:
            await download_and_send_episode(update, context, text)
            await msg.delete()
            return

        else:
            await msg.edit_text("❌ Invalid Link.")

    except Exception as e:
        await msg.edit_text(f"❌ System Message: {e}")

def run_bot():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    run_bot()
