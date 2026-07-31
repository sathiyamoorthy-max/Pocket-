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
import m3u8
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import yt_dlp

load_dotenv()

# ==========================================
# ⚡ 1. CONFIG & PROXY SETUP (முக்கியமானது!)
# ==========================================
# 🔥 PROXY URL - இங்கே வேலை செய்யும் Proxy IP-யை போட்டால் 403 பிரச்சனை சரியாகிவிடும்!
# நீங்கள் Google-ல் "free proxy list" என்று தேடி ஒரு வேகமான IP:PORT-ஐ இங்கே போடவும் (எ.கா: 'http://123.45.67.89:8080')
PROXY_URL = None 

try:
    from curl_cffi import requests as curl_requests
    CURL_AVAILABLE = True
except ImportError:
    CURL_AVAILABLE = False
    import requests as curl_requests
    import requests

TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("No TELEGRAM_TOKEN found in environment variables")

WATERMARK = os.getenv("WATERMARK", "@UltraDownloaderBot")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

if CURL_AVAILABLE:
    logger.info("curl_cffi loaded. Cloudflare TLS Bypass is ACTIVE!")
else:
    logger.warning("curl_cffi not installed. Falling back to standard requests.")

flask_app = Flask(__name__)
@flask_app.route('/')
def index():
    return "World Class Pocket FM & Media Bot is online!"

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port)

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

def fetch_page(url, headers=None, timeout=30):
    proxies = {}
    # 🔥 PROXY SETTINGS
    if PROXY_URL:
        proxies = {
            'http': PROXY_URL,
            'https': PROXY_URL
        }
    
    if CURL_AVAILABLE:
        # curl_cffi-க்கு Proxy-ஐ பாஸ் செய்தல்
        return curl_requests.get(url, impersonate="chrome133", headers=headers, timeout=timeout, proxies=proxies)
    else:
        return session.get(url, headers=headers, timeout=timeout, proxies=proxies)

def resolve_onelink(onelink_url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'}
    try:
        proxies = {}
        if PROXY_URL:
            proxies = {'http': PROXY_URL, 'https': PROXY_URL}
        
        resp = curl_requests.get(onelink_url, impersonate="chrome133", headers=headers, allow_redirects=True, timeout=15, proxies=proxies) if CURL_AVAILABLE else requests.get(onelink_url, headers=headers, allow_redirects=True, timeout=15, proxies=proxies)
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
        except:
            return {}
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
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://pocketfm.com/',
        'Sec-Ch-Ua': '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Upgrade-Insecure-Requests': '1'
    }
    
    resp = fetch_page(api_url, headers=headers, timeout=30)
    if resp.status_code == 403:
        # 🔥 Proxy இல்லை என்றால் பைபாஸ் வழிகாட்டி!
        raise Exception(
            "❌ **Render IP Blocked by Cloudflare / 403 Forbidden.**\n\n"
            "💡 **Immediate Solution (100% Bypass - No Proxy needed):**\n"
            "1️⃣ Open this Show page in **Kiwi Browser**.\n"
            "2️⃣ Tap `...` (Menu) -> `Developer Tools` -> `Console`.\n"
            "3️⃣ Copy & Paste this JS code and hit Enter:\n"
            "`document.querySelectorAll('a[href*=\"/episode/\"]').forEach(a => { if(a.href) console.log(a.href); });`\n"
            "4️⃣ **Copy the output list** and **paste ALL links** here in ONE message. I will download them in BATCH mode!\n\n"
            "🛠️ *Or if you want the script to auto-bypass, get a free Proxy from Google and paste it in `PROXY_URL` inside the script.*"
        )

    html = resp.text
    title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    series_title = title_match.group(1).split("|")[0].strip() if title_match else "Pocket FM Series"
    json_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if not json_match:
        raise Exception("Could not extract Next.js JSON payload.")
    data = json.loads(json_match.group(1))
    
    episodes_map = []
    def extract_episodes(node):
        if isinstance(node, dict):
            ep_id = node.get('episodeId') or node.get('id')
            if ep_id and isinstance(ep_id, str) and len(ep_id) > 10:
                episodes_map.append({'url': f"https://pocketfm.com/episode/{ep_id}"})
            for k, v in node.items():
                extract_episodes(v)
        elif isinstance(node, list):
            for item in node:
                extract_episodes(item)
    extract_episodes(data)
    unique_episodes = {ep['url']: ep for ep in episodes_map}.values()
    ep_list = list(unique_episodes)
    if not ep_list:
        raise Exception("Episodes not found inside JSON structure.")
    return series_title, [ep['url'] for ep in ep_list]

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

def get_episode_metadata(episode_url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://pocketfm.com/',
        'Sec-Ch-Ua': '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Upgrade-Insecure-Requests': '1'
    }
    
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
        raise Exception("M3U8 Stream not found")
    ep_title = title.group(1).split("|")[0].strip() if title else "Episode"
    return m3u8.group(1), ep_title, img.group(1) if img else None

async def download_and_send_episode(update, context, ep_url, ep_number=None, total_eps=None):
    try:
        m3u8_url, title, thumb_url = await asyncio.to_thread(get_episode_metadata, ep_url)
        audio_path = await asyncio.to_thread(download_audio_from_m3u8, m3u8_url)
        thumb_path = None
        if thumb_url:
            t_id = str(uuid.uuid4())[:8]
            thumb_path = f"downloads/thumb_{t_id}.jpg"
            try:
                img_data = curl_requests.get(thumb_url, impersonate="chrome133").content if CURL_AVAILABLE else requests.get(thumb_url).content
                with open(thumb_path, 'wb') as f: f.write(img_data)
                Image.open(thumb_path).convert('RGB').thumbnail((320, 320)).save(thumb_path, 'JPEG')
            except:
                thumb_path = None
        display_title = f"{title}"
        if ep_number and total_eps:
            display_title = f"[{ep_number}/{total_eps}] {title}"
        
        chat_id = update.effective_chat.id
        with open(audio_path, 'rb') as audio:
            thumb_file = open(thumb_path, 'rb') if thumb_path and os.path.exists(thumb_path) else None
            await context.bot.send_audio(chat_id=chat_id, audio=audio, title=display_title, performer="Pocket FM", thumbnail=thumb_file, parse_mode="Markdown")
            if thumb_file: thumb_file.close()

        STATS["downloads"] += 1
        if os.path.exists(audio_path): os.remove(audio_path)
        if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
        return True, title
    except Exception as e:
        return False, str(e)

# ==========================================
# 🤖 5. TELEGRAM COMMAND HANDLERS
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.first_name
    msg = f"👋 Hello {user}!\n\nWelcome to *World Class Pocket FM & Media Bot*!\nSend me a Pocket FM link, or any YouTube/Twitter/Instagram link to download media.\n\nPowered by {WATERMARK}"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (f"📌 *Help & Usage Instructions*\n\n"
           f"1. Send any Pocket FM episode or show link.\n"
           f"2. For series, bot automatically tracks and downloads new episodes.\n"
           f"3. Multi-Link mode supported: Paste multiple links in one message.\n\n"
           f"*Commands:*\n"
           f"/start - Start Bot\n"
           f"/help - Show Help\n"
           f"/stats - System Statistics\n"
           f"/settings - Configuration Options\n\n"
           f"Channel: {WATERMARK}")
    await update.message.reply_text(msg, parse_mode="Markdown")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime_seconds = int(time.time() - STATS["start_time"])
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    msg = (f"📊 *Bot Statistics*\n\n"
           f"⏱ *Uptime:* `{hours}h {minutes}m {seconds}s`\n"
           f"📥 *Total Downloads:* `{STATS['downloads']}`\n"
           f"💻 *CPU Usage:* `{cpu}%`\n"
           f"🧠 *RAM Usage:* `{ram}%`")
    await update.message.reply_text(msg, parse_mode="Markdown")

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Auto Caption: Enabled ✅", callback_data="toggle_caption")],
        [InlineKeyboardButton("Watermark Footer: Enabled ✅", callback_data="toggle_watermark")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("⚙️ *Bot Settings*", reply_markup=reply_markup, parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("toggle_"):
        await query.edit_message_text("⚙️ Settings updated successfully.")
        return

    user_id = query.from_user.id
    cache = USER_CACHE.get(user_id)
    if not cache:
        await query.edit_message_text("⚠️ Session expired. Please send link again.")
        return

    url = cache['url']
    title = cache['title']
    choice = query.data
    await query.edit_message_text("⏳ Downloading media to server...")

    out_template = f"downloads/{user_id}_%(id)s.%(ext)s"
    ydl_opts = {'outtmpl': out_template, 'quiet': True}

    if choice == "dl_best":
        ydl_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
    elif choice == "dl_720":
        ydl_opts['format'] = 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]'
    elif choice == "dl_480":
        ydl_opts['format'] = 'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]'
    elif choice == "dl_audio":
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]

    try:
        loop = asyncio.get_event_loop()
        def download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info_dict)
                return filename, info_dict

        file_path, info = await loop.run_in_executor(None, download)
        if choice == "dl_audio" and not file_path.endswith('.mp3'):
            file_path = os.path.splitext(file_path)[0] + ".mp3"

        file_size = os.path.getsize(file_path)
        if file_size > 50 * 1024 * 1024:
            await query.edit_message_text(f"⚠️ File exceeds Telegram Bot 50MB size limit ({human_readable_size(file_size)}).")
            if os.path.exists(file_path): os.remove(file_path)
            return

        await query.edit_message_text("⬆️ Uploading to Telegram...")
        caption = f"📹 *{title}*\n\n⚡️ Downloaded via {WATERMARK}"
        
        with open(file_path, 'rb') as f:
            if choice == "dl_audio":
                await context.bot.send_audio(chat_id=query.message.chat_id, audio=f, caption=caption, parse_mode="Markdown")
            else:
                await context.bot.send_video(chat_id=query.message.chat_id, video=f, caption=caption, parse_mode="Markdown")

        STATS["downloads"] += 1
        await query.delete_message()
        if os.path.exists(file_path): os.remove(file_path)
    except Exception as e:
        logger.error(f"Download error: {e}")
        await query.edit_message_text(f"❌ An error occurred during processing: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if 'onelink.me' in text and 'pocketfm.com' not in text:
        msg = await update.message.reply_text("🔄 Resolving App Store redirect...")
        resolved = await asyncio.to_thread(resolve_onelink, text)
        if resolved:
            text = resolved
            await msg.edit_text(f"✅ Resolved to: `{text}`", parse_mode="Markdown")
        else:
            await msg.edit_text("❌ Could not resolve link.")
            return

    # Check if it's Pocket FM link
    if 'pocketfm.com' in text:
        msg = await update.message.reply_text("🔍 Processing Pocket FM link...")
        try:
            # Multi-Link Mode
            if text.count('pocketfm.com/episode/') > 1 or '\n' in text:
                urls = [line.strip() for line in text.split('\n') if 'pocketfm.com/episode/' in line]
                if not urls:
                    await msg.edit_text("❌ No valid episode links found.")
                    return
                await msg.edit_text(f"🚀 Found {len(urls)} links. Downloading batch...")
                for idx, url in enumerate(urls, 1):
                    await download_and_send_episode(update, context, url, ep_number=idx, total_eps=len(urls))
                await msg.edit_text(f"✅ Batch complete!")
                return

            # Series Mode
            elif '/show/' in text:
                url = text.split('|')[0].strip()
                await msg.edit_text("🔄 Bypassing Cloudflare & Fetching Series...")
                series_title, current_eps = await asyncio.to_thread(get_series_data, url)
                total = len(current_eps)
                
                tracker = load_tracker()
                show_id = url.split('/show/')[1].strip('/')
                old_eps = tracker.get(show_id, [])
                new_eps = [ep for ep in current_eps if ep not in old_eps]

                if not new_eps:
                    await msg.edit_text(f"📊 **{series_title}**\n📈 Total: {total}\n✅ Already downloaded. No new episodes.")
                    return

                await msg.edit_text(f"✅ Found {len(new_eps)} new episodes. Downloading...")
                for i, ep in enumerate(new_eps, 1):
                    await download_and_send_episode(update, context, ep, ep_number=i, total_eps=len(new_eps))
                    await asyncio.sleep(2)
                
                tracker[show_id] = current_eps
                save_tracker(tracker)
                await msg.edit_text(f"🎉 All {len(new_eps)} new episodes sent successfully!")
                return

            # Single Episode
            elif '/episode/' in text:
                await msg.edit_text("⬇️ Downloading Pocket FM episode...")
                success, result = await download_and_send_episode(update, context, text)
                if success:
                    await msg.delete()
                else:
                    await msg.edit_text(f"❌ Failed: {result}")
                return
        except Exception as e:
            await msg.edit_text(f"❌ Pocket FM Error: {e}")
            return

    # General Media Links (YouTube, Twitter, etc. via yt_dlp)
    if text.startswith(("http://", "https://")):
        status_msg = await update.message.reply_text("🔍 Fetching media info...")
        try:
            ydl_opts = {'quiet': True, 'no_warnings': True}
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(text, download=False))
            
            title = info.get('title', 'Media Content')
            USER_CACHE[update.effective_user.id] = {'url': text, 'title': title}

            keyboard = [
                [InlineKeyboardButton("🎥 1080p / Best Video", callback_data="dl_best")],
                [InlineKeyboardButton("🎥 720p HD", callback_data="dl_720")],
                [InlineKeyboardButton("🎥 480p SD", callback_data="dl_480")],
                [InlineKeyboardButton("🎵 MP3 Audio Only", callback_data="dl_audio")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await status_msg.edit_text(f"🎬 *Title:* `{title}`\n\nSelect desired download format:", reply_markup=reply_markup, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Info extraction error: {e}")
            await status_msg.edit_text(f"❌ Failed to extract media info. Ensure URL is supported.")
    else:
        await update.message.reply_text("⚠️ Please send a valid media URL.")

def main():
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info("Bot starting...")
    threading.Thread(target=run_flask, daemon=True).start()
    app.run_polling()

if __name__ == "__main__":
    main()
