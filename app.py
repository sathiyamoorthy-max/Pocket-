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

# M3U8 & Crypto
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
    return "Pocket FM Stable Bot is Online!"

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port)

session = requests.Session()
STATS = {"downloads": 0, "start_time": time.time()}
USER_CACHE = {}

# 🌐 STABLE BROWSER HEADERS
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://pocketfm.com/'
}

def human_readable_size(size_bytes):
    if not size_bytes: return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    return f"{round(size_bytes / p, 2)} {size_name[i]}"

def resolve_onelink(onelink_url):
    try:
        resp = session.get(onelink_url, headers=HEADERS, timeout=15, allow_redirects=True)
        if 'pocketfm.com/show/' in resp.url or 'pocketfm.com/episode/' in resp.url:
            return resp.url
        return None
    except:
        return None

# ==========================================
# 📅 EPISODE TRACKER
# ==========================================
TRACKER_FILE = 'series_cache.json'
def load_tracker():
    if os.path.exists(TRACKER_FILE):
        try:
            with open(TRACKER_FILE, 'r') as f: return json.load(f)
        except: pass
    return {}

def save_tracker(data):
    with open(TRACKER_FILE, 'w') as f:
        json.dump(data, f)

# ==========================================
# 🔥 POCKET FM SERIES FETCHER
# ==========================================
def get_series_data(series_url):
    match = re.search(r'/show/([a-zA-Z0-9_-]+)', series_url)
    if not match: raise Exception("Invalid Show URL format.")
    show_id = match.group(1).split('?')[0]
    api_url = f"https://pocketfm.com/show/{show_id}"
    
    resp = session.get(api_url, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        raise Exception(f"Failed to fetch show page. Status code: {resp.status_code}")

    html = resp.text
    title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    series_title = title_match.group(1).split("|")[0].strip() if title_match else "Pocket FM Series"
    
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
    unique_episodes = {ep['url']: ep for ep in episodes_map}.values()
    ep_list = list(unique_episodes)
    
    if not ep_list: raise Exception("Episodes not found inside JSON structure.")
    return series_title, [ep['url'] for ep in ep_list]

# ==========================================
# 🎵 DOWNLOAD CORE (M3U8, AES, FFMPEG)
# ==========================================
def download_chunk(args):
    i, seg_url, base_url, aes_key, iv, tmpdir = args
    try:
        full_url = seg_url if seg_url.startswith('http') else f"{base_url}/{seg_url}"
        ts_path = os.path.join(tmpdir, f"chunk_{i:04d}.ts")
        
        # 🔥 AUDIO CDN-ஐ ஏமாற்ற பிரத்யேக Headers
        chunk_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Origin': 'https://pocketfm.com',
            'Referer': 'https://pocketfm.com/'
        }
        
        response = session.get(full_url, headers=chunk_headers, timeout=20, stream=True)
        data = response.content
        
        # கிளவுட்ஃப்ளேர் தடுத்து HTML பேஜை அனுப்பினால் அதை நிராகரிக்க
        if b'<html' in data[:100].lower():
            logger.error(f"Chunk {i} blocked by Cloudflare CDN.")
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
        logger.error(f"Chunk error: {e}")
        return i, None

def download_audio_from_m3u8(m3u8_url):
    m3u8_content = session.get(m3u8_url, headers=HEADERS, timeout=15).text
    playlist = m3u8.loads(m3u8_content, uri=m3u8_url)
    base_url = '/'.join(m3u8_url.split('/')[:-1])
    
    if not playlist.segments and playlist.playlists:
        m3u8_url = playlist.playlists[0].uri
        m3u8_url = m3u8_url if m3u8_url.startswith('http') else f"{base_url}/{m3u8_url}"
        m3u8_content = session.get(m3u8_url, headers=HEADERS, timeout=15).text
        playlist = m3u8.loads(m3u8_content, uri=m3u8_url)
        base_url = '/'.join(m3u8_url.split('/')[:-1])
        
    aes_key, iv = None, None
    if playlist.keys and playlist.keys[0]:
        key_uri = playlist.keys[0].uri
        iv = playlist.keys[0].iv
        key_url = key_uri if key_uri.startswith('http') else f"{base_url}/{key_uri}"
        aes_key = session.get(key_url, headers=HEADERS, timeout=15).content

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
        if not ts_files: raise Exception("Failed to download audio chunks.")
        
        if not os.path.exists('downloads'): os.makedirs('downloads')
        output_file = f"downloads/audio_{uuid.uuid4().hex[:8]}.mp3"
        list_path = os.path.join(tmpdir, "list.txt")
        with open(list_path, 'w') as f:
            for ts in ts_files: f.write(f"file '{ts}'\n")
            
        subprocess.run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c:a', 'libmp3lame', '-q:a', '2', output_file], check=True, capture_output=True)
        return output_file

def get_episode_metadata(episode_url):
    resp = session.get(episode_url, headers=HEADERS, timeout=20)
    if resp.status_code != 200:
        raise Exception(f"Failed to fetch episode page. Status: {resp.status_code}")
        
    html = resp.text
    title = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    img = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    m3u8 = re.search(r'(https?://[^\s"\'<>]+\.cloudfront\.net[^\s"\'<>]*?\.m3u8[^\s"\'<>]*)', html)
    if not m3u8:
        m3u8 = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html)
    if not m3u8:
        raise Exception("M3U8 Stream not found.")
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
                img_data = session.get(thumb_url, headers=HEADERS, timeout=15).content
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
        logger.error(f"Download Error: {e}")
        return False, str(e)

# ==========================================
# 🤖 TELEGRAM HANDLERS
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Modified simple start message
    await update.message.reply_text("🔗 Send any Pocket FM link.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if 'onelink.me' in text and 'pocketfm.com' not in text:
        msg = await update.message.reply_text("🔄 Resolving...")
        resolved = await asyncio.to_thread(resolve_onelink, text)
        if not resolved:
            return await msg.edit_text("❌ Could not resolve link.")
        text = resolved
        await msg.edit_text(f"✅ Resolved to: `{text}`")

    if 'pocketfm.com' in text:
        msg = await update.message.reply_text("🔍 Processing...")
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

    # General Media via yt_dlp
    if text.startswith(("http://", "https://")):
        status_msg = await update.message.reply_text("🔍 Fetching media info...")
        try:
            ydl_opts = {'quiet': True, 'no_warnings': True}
            info = await asyncio.get_event_loop().run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(text, download=False))
            title = info.get('title', 'Video')
            USER_CACHE[update.effective_user.id] = {'url': text, 'title': title}
            keyboard = [[InlineKeyboardButton("🎥 Best Video", callback_data="dl_best")],
                        [InlineKeyboardButton("🎵 MP3 Audio", callback_data="dl_audio")]]
            await status_msg.edit_text(f"🎬 *{title}* select format:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        except:
            await status_msg.edit_text("❌ Failed to extract media info.")

def main():
    if not os.path.exists("downloads"): os.makedirs("downloads")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    threading.Thread(target=run_flask, daemon=True).start()
    app.run_polling()

if __name__ == "__main__":
    main()
