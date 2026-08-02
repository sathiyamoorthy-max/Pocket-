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
import random
import requests
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

# Flask & Telegram
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# M3U8 & Crypto
import m3u8
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import yt_dlp

load_dotenv()

# ==========================================
# 🔥 1. TLS FINGERPRINT BYPASS (JA3 Impersonation)
# ==========================================
try:
    from curl_cffi import requests as curl_requests
    CURL_AVAILABLE = True
    print("✅ curl_cffi Loaded! Cloudflare JA3 Bypass Active.")
except ImportError:
    CURL_AVAILABLE = False
    import requests as curl_requests
    print("⚠️ curl_cffi missing. Falling back to requests.")

# ==========================================
# 🔥 2. INTELLIGENT PROXY ROTATOR (The Ultimate IP Bypass)
# ==========================================
PROXY_POOL = []
LAST_FETCH_TIME = 0

def fetch_and_validate_proxies():
    global PROXY_POOL, LAST_FETCH_TIME
    if time.time() - LAST_FETCH_TIME < 1800 and PROXY_POOL:
        return PROXY_POOL
    try:
        # உலகின் மிகப்பெரிய இலவச Proxy லிஸ்ட்
        url = "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            raw_list = response.text.strip().split('\n')
            valid_proxies = []
            for p in raw_list:
                p = p.strip()
                if re.match(r'\d+\.\d+\.\d+\.\d+:\d+', p):
                    # வேகமான Proxy-ஐ மட்டும் வடிகட்ட சிறிய டெஸ்ட்
                    try:
                        test = requests.get("https://pocketfm.com", proxies={'http': f'http://{p}', 'https': f'http://{p}'}, timeout=4)
                        if test.status_code == 200:
                            valid_proxies.append(p)
                    except:
                        pass
            PROXY_POOL = valid_proxies
            random.shuffle(PROXY_POOL)
            LAST_FETCH_TIME = time.time()
            print(f"🔄 Found {len(PROXY_POOL)} working proxies.")
            return PROXY_POOL
    except Exception as e:
        print(f"⚠️ Proxy List fetch error: {e}")
    return []

def get_random_proxy():
    proxies = fetch_and_validate_proxies()
    if proxies:
        p = random.choice(proxies)
        return {'http': f'http://{p}', 'https': f'http://{p}'}
    return None

# ==========================================
# 🌍 3. THE MASTER FETCHER (Curl -> Proxy -> Fallback)
# ==========================================
def fetch_page(url, headers=None, timeout=30, retries=3):
    # 1. முதலில் curl_cffi மூலம் முயற்சி (இது IP தேவையில்லை, JS ஹேண்ட்ஷேக்)
    if CURL_AVAILABLE:
        for _ in range(2): # 2 முறை முயற்சி
            try:
                return curl_requests.get(url, impersonate="chrome133", headers=headers, timeout=timeout)
            except Exception as e:
                print(f"⚠️ Curl attempt failed: {e}")
    
    # 2. அடுத்து Proxy Rotator (IP மாற்றம்)
    for _ in range(retries):
        proxy = get_random_proxy()
        if proxy:
            try:
                print(f"🛡️ Trying proxy: {proxy['http']}")
                return requests.get(url, headers=headers, timeout=timeout, proxies=proxy)
            except:
                continue
    
    # 3. கடைசி முயற்சி: Render இருந்து நேரடி IP
    print("⚠️ Trying direct Render IP (last resort).")
    return requests.get(url, headers=headers, timeout=timeout)

# ==========================================
# 📅 4. CONFIG & FUTURE TRACKER
# ==========================================
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN missing in env")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)
@flask_app.route('/')
def index():
    return "Ultimate Rotating Bot Online!"

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

def get_episode_metadata(episode_url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'}
    resp = fetch_page(episode_url, headers=headers, timeout=20)
    if resp.status_code == 403:
        raise Exception("403 Blocked. Try Multi-Link mode.")
    
    html = resp.text
    title = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    img = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    m3u8 = re.search(r'(https?://[^\s"\'<>]+\.cloudfront\.net[^\s"\'<>]*?\.m3u8[^\s"\'<>]*)', html)
    if not m3u8: m3u8 = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html)
    if not m3u8: raise Exception("M3U8 Stream not found")
    ep_title = title.group(1).split("|")[0].strip() if title else "Episode"
    return m3u8.group(1), ep_title, img.group(1) if img else None

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
        display_title = f"{title}" if not ep_num else f"[{ep_num}/{total}] {title}"
        with open(audio_path, 'rb') as audio:
            thumb_file = open(thumb_path, 'rb') if thumb_path and os.path.exists(thumb_path) else None
            await context.bot.send_audio(update.effective_chat.id, audio=audio, title=display_title, performer="Pocket FM", thumbnail=thumb_file)
            if thumb_file: thumb_file.close()
        if os.path.exists(audio_path): os.remove(audio_path)
        if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# ==========================================
# 🔥 6. SMART SERIES PARSER WITH ULTIMATE FALLBACK
# ==========================================
def get_series_data(series_url):
    match = re.search(r'/show/([a-zA-Z0-9_-]+)', series_url)
    show_id = match.group(1).split('?')[0]
    api_url = f"https://pocketfm.com/show/{show_id}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'}
    
    resp = fetch_page(api_url, headers=headers, timeout=30)
    
    # 🔥 ULTIMATE 100% GUARANTEED FALLBACK
    if resp.status_code == 403:
        raise Exception(
            "❌ **All Render Proxy Layers Blocked (IP Blacklisted).**\n\n"
            "💡 **The Unbreakable Multi-Link Bypass Guide:**\n"
            "1. Open this Series page in your **Kiwi Browser**.\n"
            "2. Tap `...` (Menu) -> `Developer Tools` -> `Console`.\n"
            "3. Copy & Paste this JS code and press Enter:\n"
            "   `document.querySelectorAll('a[href*=\"/episode/\"]').forEach(a => { if(a.href) console.log(a.href); });`\n"
            "4. **Copy ALL the links** from the console output and **paste them ALL in ONE message** here. I will download them in BATCH mode instantly!"
        )
    
    html = resp.text
    title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    series_title = title_match.group(1).split("|")[0].strip() if title_match else "Pocket FM Series"
    json_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if not json_match: raise Exception("Next.js JSON not found.")
    data = json.loads(json_match.group(1))
    
    episodes = []
    def extract(node):
        if isinstance(node, dict):
            eid = node.get('episodeId') or node.get('id')
            if eid and len(str(eid)) > 10:
                episodes.append(f"https://pocketfm.com/episode/{eid}")
            for k, v in node.items(): extract(v)
        elif isinstance(node, list):
            for item in node: extract(item)
    extract(data)
    return series_title, list(dict.fromkeys(episodes))

# ==========================================
# 🤖 7. TELEGRAM HANDLERS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔥 **World Class Rotating Bot is Online!**\n\n"
        "✅ Uses **JA3 TLS Impersonation** + **IP Proxy Rotation**.\n"
        "✅ If completely blocked, it will give you the **Kiwi Browser Manual Hack** guide.\n\n"
        "📌 Send any `/show/` or `/episode/` link."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    msg = await update.message.reply_text("🔍 Analysing...")

    try:
        # ✨ Batch Multi-Link (The Ultimate Safe Mode)
        if text.count('/episode/') > 1 or '\n' in text:
            urls = [l.strip() for l in text.split('\n') if '/episode/' in l]
            if not urls:
                await msg.edit_text("❌ No valid links found.")
                return
            await msg.edit_text(f"🚀 Found {len(urls)} links. Batch downloading...")
            for i, u in enumerate(urls, 1):
                await download_and_send_episode(update, context, u, i, len(urls))
            await msg.edit_text(f"✅ Batch Complete!")
            return

        # 🎬 Series Mode
        elif '/show/' in text:
            url = text.split('|')[0].strip()
            await msg.edit_text("🔄 Bypassing with TLS + Rotating Proxies...")
            title, eps = await asyncio.to_thread(get_series_data, url)
            total = len(eps)
            
            tracker = load_tracker()
            show_id = url.split('/show/')[1].strip('/')
            old = tracker.get(show_id, [])
            new = [ep for ep in eps if ep not in old]

            if not new:
                await msg.edit_text(f"📊 {title}\nTotal: {total}\n✅ No new episodes.")
                return

            await msg.edit_text(f"✅ Found {len(new)} new eps. Starting download...")
            for i, ep in enumerate(new, 1):
                await download_and_send_episode(update, context, ep, i, len(new))
                await asyncio.sleep(2)
            
            tracker[show_id] = eps
            save_tracker(tracker)
            await msg.edit_text(f"🎉 Complete! {len(new)} eps sent.")
            return

        # 🎵 Single Episode
        elif '/episode/' in text:
            await download_and_send_episode(update, context, text)
            await msg.delete()
            return

        else:
            await msg.edit_text("❌ Invalid Link. Send `/episode/...` or `/show/...`.")

    except Exception as e:
        # இந்த பிழை மெசேஜில் Kiwi Browser வழிகாட்டி வரும்
        await msg.edit_text(str(e))

def run_bot():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    run_bot()
