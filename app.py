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
import psutil
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

# Flask & Telegram
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# M3U8, Crypto & YT-DLP
import m3u8
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import yt_dlp

load_dotenv()

# ==========================================
# ⚡ 1. CONFIG, PROXY & TLS SETUP (100% FIXED)
# ==========================================
PROXY_URL = None  # உங்களது Proxy IP இருந்தால் இங்கே போடலாம் (எ.கா: 'http://ip:port')

# 🔥 FIXED: requests 라이ப்ரரி நிரந்தரமாக Import செய்யப்பட்டுள்ளது!
import requests

try:
    from curl_cffi import requests as curl_requests
    CURL_AVAILABLE = True
except ImportError:
    CURL_AVAILABLE = False
    curl_requests = requests  # curl_cffi இல்லை என்றால் சாதாரண requests-ஐப் பயன்படுத்தும்

TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("No TELEGRAM_TOKEN found in environment variables")

WATERMARK = os.getenv("WATERMARK", "@UltraDownloaderBot")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)
@flask_app.route('/')
def index():
    return "Advanced Pocket FM & Media Bot is Online!"

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port)

# இப்போது requests பிழை வராது!
session = requests.Session()
STATS = {"downloads": 0, "start_time": time.time()}
USER_CACHE = {}

def human_readable_size(size_bytes):
    if not size_bytes:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

# 🛠️ Universal Request Fetcher (Proxy Support)
def fetch_page(url, headers=None, timeout=30, stream=False):
    proxies = {'http': PROXY_URL, 'https': PROXY_URL} if PROXY_URL else None
    
    if CURL_AVAILABLE and not stream:
        return curl_requests.get(url, impersonate="chrome133", headers=headers, timeout=timeout, proxies=proxies)
    else:
        return session.get(url, headers=headers, timeout=timeout, proxies=proxies, stream=stream)

def resolve_onelink(onelink_url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    try:
        resp = fetch_page(onelink_url, headers=headers, timeout=15)
        if 'pocketfm.com/show/' in resp.url or 'pocketfm.com/episode/' in resp.url:
            return resp.url
        return None
    except:
        return None

# ==========================================
# 📅 2. FUTURE EPISODE TRACKER
# ==========================================
TRACKER_FILE = 'series_cache.json'
def load_tracker():
    if os.path.exists(TRACKER_FILE):
        try:
            with open(TRACKER_FILE, 'r') as f:
                return json.load(f)
        except: pass
    return {}

def save_tracker(data):
    with open(TRACKER_FILE, 'w') as f:
        json.dump(data, f)

# ==========================================
# 🔥 3. POCKET FM SERIES FETCHER
# ==========================================
def get_series_data(series_url):
    match = re.search(r'/show/([a-zA-Z0-9_-]+)', series_url)
    if not match:
        raise Exception("Invalid Show URL format.")
    show_id = match.group(1).split('?')[0]
    api_url = f"https://pocketfm.com/show/{show_id}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Referer': 'https://pocketfm.com/'
    }
    
    resp = fetch_page(api_url, headers=headers, timeout=30)
    if resp.status_code == 403:
        raise Exception("❌ **IP Blocked (403 Forbidden).** Please configure PROXY_URL or use Multi-Link Bypass.")

    html = resp.text
    title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    series_title = title_match.group(1).split("|")[0].strip() if title_match else "Pocket FM Series"
    
    json_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if not json_match:
        raise Exception("Could not extract JSON data.")
    
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
    unique_episodes = {ep['url']: ep for ep in episodes_map}.values()
    ep_list = list(unique_episodes)
    
    if not ep_list:
        raise Exception("Episodes not found.")
    return series_title, [ep['url'] for ep in ep_list]

# ==========================================
# 🎵 4. SECURE DOWNLOAD CORE (M3U8 & AES)
# ==========================================
def download_chunk(args):
    i, seg_url, base_url, aes_key, iv, tmpdir = args
    try:
        full_url = seg_url if seg_url.startswith('http') else f"{base_url}/{seg_url}"
        ts_path = os.path.join(tmpdir, f"chunk_{i:04d}.ts")
        
        response = fetch_page(full_url, timeout=20, stream=True)
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
    except Exception as e:
        logger.error(f"Chunk {i} failed: {e}")
        return i, None

def download_audio_from_m3u8(m3u8_url):
    m3u8_content = fetch_page(m3u8_url, timeout=15).text
    playlist = m3u8.loads(m3u8_content, uri=m3u8_url)
    base_url = '/'.join(m3u8_url.split('/')[:-1])
    
    if not playlist.segments and playlist.playlists:
        m3u8_url = playlist.playlists[0].uri
        m3u8_url = m3u8_url if m3u8_url.startswith('http') else f"{base_url}/{m3u8_url}"
        m3u8_content = fetch_page(m3u8_url, timeout=15).text
        playlist = m3u8.loads(m3u8_content, uri=m3u8_url)
        base_url = '/'.join(m3u8_url.split('/')[:-1])
        
    aes_key, iv = None, None
    if playlist.keys and playlist.keys[0]:
        key_uri = playlist.keys[0].uri
        iv = playlist.keys[0].iv
        key_url = key_uri if key_uri.startswith('http') else f"{base_url}/{key_uri}"
        aes_key = fetch_page(key_url, timeout=15).content

    segments = [seg.uri for seg in playlist.segments]
    with tempfile.TemporaryDirectory() as tmpdir:
        tasks = [(i, seg, base_url, aes_key, iv, tmpdir) for i, seg in enumerate(segments)]
        ts_files_dict = {}
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(download_chunk, arg) for arg in tasks]
            for future in as_completed(futures):
                idx, path = future.result()
                if path: ts_files_dict[idx] = path
                
        ts_files = [ts_files_dict[i] for i in sorted(ts_files_dict.keys())]
        
        if not os.path.exists('downloads'): os.makedirs('downloads')
        output_file = f"downloads/audio_{uuid.uuid4().hex[:8]}.mp3"
        list_path = os.path.join(tmpdir, "list.txt")
        with open(list_path, 'w') as f:
            for ts in ts_files: f.write(f"file '{ts}'\n")
            
        subprocess.run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c:a', 'libmp3lame', '-q:a', '2', output_file], check=True, capture_output=True)
        return output_file

def get_episode_metadata(episode_url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    resp = fetch_page(episode_url, headers=headers, timeout=20)
    if resp.status_code == 403:
        raise Exception("Cloudflare 403 Forbidden. IP or Proxy is restricted.")
        
    html = resp.text
    title = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    img = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    m3u8 = re.search(r'(https?://[^\s"\'<>]+\.cloudfront\.net[^\s"\'<>]*?\.m3u8[^\s"\'<>]*)', html)
    if not m3u8:
        m3u8 = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html)
    if not m3u8:
        raise Exception("M3U8 Stream not found. Content might be premium or geoblocked.")
        
    ep_title = title.group(1).split("|")[0].strip() if title else "Episode"
    return m3u8.group(1), ep_title, img.group(1) if img else None

async def download_and_send_episode(update, context, ep_url, ep_number=None, total_eps=None):
    try:
        m3u8_url, title, thumb_url = await asyncio.to_thread(get_episode_metadata, ep_url)
        audio_path = await asyncio.to_thread(download_audio_from_m3u8, m3u8_url)
        
        thumb_path = None
        if thumb_url:
            thumb_path = f"downloads/thumb_{uuid.uuid4().hex[:8]}.jpg"
            try:
                img_data = fetch_page(thumb_url, timeout=15).content
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
        logger.error(f"Download Pipeline Error: {e}")
        return False, str(e)

# ==========================================
# 🤖 5. TELEGRAM HANDLERS & YT-DLP LOGIC
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = f"👋 Hello {update.effective_user.first_name}!\n\nWelcome to *Advanced Media Downloader Bot*!\nSend any Pocket FM, YouTube, Twitter link.\n\nPowered by {WATERMARK}"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("toggle_"): return

    user_id = query.from_user.id
    cache = USER_CACHE.get(user_id)
    if not cache:
        await query.message.edit_text("⚠️ Session expired. Please send link again.")
        return

    url, title, choice = cache['url'], cache['title'], query.data
    await query.message.edit_text("⏳ Downloading media to server...")

    ydl_opts = {'outtmpl': f"downloads/{user_id}_%(id)s.%(ext)s", 'quiet': True}
    if PROXY_URL: ydl_opts['proxy'] = PROXY_URL
    
    if choice == "dl_best": ydl_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best'
    elif choice == "dl_720": ydl_opts['format'] = 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best'
    elif choice == "dl_audio":
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}]

    try:
        loop = asyncio.get_event_loop()
        def download_yt():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info_dict)

        file_path = await loop.run_in_executor(None, download_yt)
        if choice == "dl_audio" and not file_path.endswith('.mp3'):
            file_path = os.path.splitext(file_path)[0] + ".mp3"

        if os.path.getsize(file_path) > 50 * 1024 * 1024:
            await query.message.edit_text("⚠️ File exceeds Telegram's 50MB limit.")
            os.remove(file_path)
            return

        await query.message.edit_text("⬆️ Uploading to Telegram...")
        with open(file_path, 'rb') as f:
            if choice == "dl_audio":
                await context.bot.send_audio(chat_id=query.message.chat_id, audio=f, caption=title, read_timeout=120, write_timeout=120)
            else:
                await context.bot.send_video(chat_id=query.message.chat_id, video=f, caption=title, read_timeout=120, write_timeout=120)

        STATS["downloads"] += 1
        await query.message.delete()
        os.remove(file_path)
    except Exception as e:
        await query.message.edit_text(f"❌ Error: {str(e)}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if 'onelink.me' in text and 'pocketfm.com' not in text:
        msg = await update.message.reply_text("🔄 Resolving App Store redirect...")
        resolved = await asyncio.to_thread(resolve_onelink, text)
        if not resolved:
            await msg.edit_text("❌ Could not resolve link.")
            return
        text = resolved
        await msg.edit_text(f"✅ Resolved to: `{text}`")

    if 'pocketfm.com' in text:
        msg = await update.message.reply_text("🔍 Processing Pocket FM link...")
        try:
            if text.count('pocketfm.com/episode/') > 1 or '\n' in text:
                urls = [line.strip() for line in text.split('\n') if 'pocketfm.com/episode/' in line]
                if not urls: return await msg.edit_text("❌ No valid links.")
                await msg.edit_text(f"🚀 Found {len(urls)} links. Downloading batch...")
                for idx, url in enumerate(urls, 1):
                    await download_and_send_episode(update, context, url, ep_number=idx, total_eps=len(urls))
                return await msg.edit_text("✅ Batch complete!")

            elif '/show/' in text:
                url = text.split('|')[0].strip()
                await msg.edit_text("🔄 Fetching Series info...")
                series_title, current_eps = await asyncio.to_thread(get_series_data, url)
                
                tracker = load_tracker()
                show_id = url.split('/show/')[1].strip('/')
                new_eps = [ep for ep in current_eps if ep not in tracker.get(show_id, [])]

                if not new_eps: return await msg.edit_text(f"📊 **{series_title}**\n✅ Already up-to-date.")

                await msg.edit_text(f"✅ Found {len(new_eps)} new episodes. Downloading...")
                for i, ep in enumerate(new_eps, 1):
                    await download_and_send_episode(update, context, ep, ep_number=i, total_eps=len(new_eps))
                    await asyncio.sleep(2)
                
                tracker[show_id] = current_eps
                save_tracker(tracker)
                return await msg.edit_text(f"🎉 Series {series_title} updated!")

            elif '/episode/' in text:
                await msg.edit_text("⬇️ Downloading episode...")
                success, result = await download_and_send_episode(update, context, text)
                if success: await msg.delete()
                else: await msg.edit_text(f"❌ Failed: {result}")
                return
        except Exception as e:
            return await msg.edit_text(f"❌ Pocket FM Error: {e}")

    # General Media
    if text.startswith(("http://", "https://")):
        status_msg = await update.message.reply_text("🔍 Fetching media info...")
        try:
            ydl_opts = {'quiet': True, 'no_warnings': True}
            if PROXY_URL: ydl_opts['proxy'] = PROXY_URL
            info = await asyncio.get_event_loop().run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(text, download=False))
            
            USER_CACHE[update.effective_user.id] = {'url': text, 'title': info.get('title', 'Video')}
            keyboard = [[InlineKeyboardButton("🎥 1080p", callback_data="dl_best")],
                        [InlineKeyboardButton("🎥 720p", callback_data="dl_720")],
                        [InlineKeyboardButton("🎵 MP3", callback_data="dl_audio")]]
            await status_msg.edit_text("🎬 Select format:", reply_markup=InlineKeyboardMarkup(keyboard))
        except:
            await status_msg.edit_text("❌ Failed to extract media info.")

def main():
    if not os.path.exists("downloads"): os.makedirs("downloads")
    
    app = Application.builder().token(TOKEN).connect_timeout(60).read_timeout(60).write_timeout(60).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info("Bot is booting up...")
    threading.Thread(target=run_flask, daemon=True).start()
    app.run_polling()

if __name__ == "__main__":
    main()
