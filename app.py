import os
import re
import subprocess
import logging
import threading
import asyncio
import uuid
import tempfile
import json
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import m3u8
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

# --- Configuration ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("No TELEGRAM_TOKEN found in environment variables")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Global dictionary to track active downloads and cancellation flags
active_tasks = {}

# --- Flask App (Health check for Render) ---
flask_app = Flask(__name__)

@flask_app.route('/')
def index():
    return "Bot is running!"

@flask_app.route('/health')
def health():
    return "OK"

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port)

# --- Downloader & Smart Series Parser Core Logic ---
def load_cookies(cookie_file='cookies.txt'):
    cookies = {}
    if os.path.exists(cookie_file):
        with open(cookie_file, 'r') as f:
            for line in f:
                if not line.strip() or line.startswith('#'):
                    continue
                parts = line.strip().split('\t')
                if len(parts) >= 7:
                    cookies[parts[5]] = parts[6]
        logger.info("Cookies loaded successfully!")
    else:
        logger.warning("cookies.txt file not found! Download might fail.")
    return cookies

def extract_series_or_episode_links(url, session, limit_count):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
        'Referer': 'https://pocketfm.com/'
    }
    logger.info(f"Parsing URL: {url}")
    response = session.get(url, headers=headers, timeout=20)
    html_content = response.text
    
    # Try to extract Next.js data JSON for robust episode listing
    episode_links = []
    series_name = "Pocket FM Series"
    
    next_data_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html_content, re.DOTALL)
    if next_data_match:
        try:
            data = json.loads(next_data_match.group(1))
            # Traverse typical Pocket FM next data structure to find episodes
            # Fallback to regex if specific json path varies
        except Exception as e:
            logger.error(f"JSON parse error: {e}")

    # Fallback regex to find all episode links on the page if it's a show page
    found_urls = re.findall(r'href=["\'](https://pocketfm\.com/episode/[^"\']+)["\']', html_content)
    if not found_urls:
        found_urls = re.findall(r'href=["\'](/episode/[^"\']+)["\']', html_content)
        found_urls = [f"https://pocketfm.com{ep}" for ep in found_urls]

    # Deduplicate while preserving order
    seen = set()
    unique_eps = [ep for ep in found_urls if not (ep in seen or seen.add(ep))]

    if unique_eps:
        episode_links = unique_eps
    else:
        # If no list found, treat the input URL as a single episode
        episode_links = [url]

    # Apply batch limit
    if limit_count != 'all' and limit_count.isdigit():
        limit = int(limit_count)
        episode_links = episode_links[:limit]
    
    return episode_links

def get_episode_metadata_and_m3u8(episode_url, session):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
        'Referer': 'https://pocketfm.com/'
    }
    response = session.get(episode_url, headers=headers, timeout=20)
    html_content = response.text
    
    title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html_content)
    raw_title = title_match.group(1) if title_match else "Pocket FM Audio"
    
    series_name = "Pocket FM"
    episode_title = raw_title
    if "|" in raw_title:
        parts = raw_title.split("|")
        episode_title = parts[0].strip()
        series_name = parts[1].strip()
    elif "-" in raw_title:
        parts = raw_title.split("-")
        episode_title = parts[0].strip()
        series_name = parts[-1].strip()

    img_match = re.search(r'<meta property="og:image" content="([^"]+)"', html_content)
    thumbnail_url = img_match.group(1) if img_match else None
    
    m3u8_match = re.search(r'(https?://[^\s"\'<>]+\.cloudfront\.net[^\s"\'<>]*?\.m3u8[^\s"\'<>]*)', html_content)
    if not m3u8_match:
        m3u8_match = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html_content)
        
    if m3u8_match:
        clean_url = m3u8_match.group(1).replace('\\/', '/').replace('\\u002F', '/')
        return clean_url, episode_title, series_name, thumbnail_url
    else:
        raise Exception("M3U8 URL not found! Please check cookies.txt.")

def download_single_chunk(args):
    i, seg_url, base_url, aes_key, iv, tmpdir, session = args
    try:
        full_seg_url = seg_url if seg_url.startswith('http') else f"{base_url}/{seg_url}"
        ts_path = os.path.join(tmpdir, f"chunk_{i:04d}.ts")
        
        response = session.get(full_seg_url, stream=True, timeout=15)
        encrypted_data = response.content
        
        if aes_key and iv:
            iv_bytes = bytes.fromhex(iv.replace('0x', '')) if isinstance(iv, str) else iv
            if len(iv_bytes) < 16:
                iv_bytes = iv_bytes.ljust(16, b'\0')
            cipher = AES.new(aes_key, AES.MODE_CBC, iv_bytes)
            decrypted_data = cipher.decrypt(encrypted_data)
            try:
                decrypted_data = unpad(decrypted_data, AES.block_size)
            except ValueError:
                pass
            with open(ts_path, 'wb') as f:
                f.write(decrypted_data)
        else:
            with open(ts_path, 'wb') as f:
                f.write(encrypted_data)
        return i, ts_path
    except Exception as e:
        logger.error(f"Chunk {i} download error: {e}")
        return i, None

def process_m3u8(m3u8_url, session, chat_id):
    try:
        playlist = m3u8.load(m3u8_url)
        base_url = '/'.join(m3u8_url.split('/')[:-1])
        
        if not playlist.segments and playlist.playlists:
            child_uri = playlist.playlists[0].uri
            child_url = child_uri if child_uri.startswith('http') else f"{base_url}/{child_uri}"
            playlist = m3u8.load(child_url)
            base_url = '/'.join(child_url.split('/')[:-1])
        
        key_uri, iv = None, None
        if playlist.keys:
            key_obj = playlist.keys[0]
            if key_obj and key_obj.uri:
                key_uri = key_obj.uri
                iv = key_obj.iv

        aes_key = None
        if key_uri:
            key_url = key_uri if key_uri.startswith('http') else f"{base_url}/{key_uri}"
            response = session.get(key_url, timeout=20)
            aes_key = response.content

        segment_urls = [seg.uri for seg in playlist.segments]
        if not segment_urls:
            raise Exception("No audio segments found.")
        
        if not os.path.exists('downloads'):
            os.makedirs('downloads')
            
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_args = []
            for i, seg_url in enumerate(segment_urls):
                tasks_args.append((i, seg_url, base_url, aes_key, iv, tmpdir, session))

            ts_files_dict = {}
            # Super Fast Parallel Chunk Downloading using ThreadPoolExecutor (10x Speedup)
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(download_single_chunk, arg) for arg in tasks_args]
                for future in as_completed(futures):
                    if active_tasks.get(chat_id) == "STOP":
                        raise Exception("Download stopped by user.")
                    idx, path = future.result()
                    if path:
                        ts_files_dict[idx] = path

            ts_files = [ts_files_dict[i] for i in sorted(ts_files_dict.keys())]
            if len(ts_files) != len(segment_urls):
                raise Exception("Some audio chunks failed to download.")
            
            unique_name = str(uuid.uuid4())[:8]
            output_file = f"downloads/audio_{unique_name}.mp3"
            list_path = os.path.join(tmpdir, "concat_list.txt")
            with open(list_path, 'w') as f:
                for f_path in ts_files:
                    f.write(f"file '{f_path}'\n")
            
            cmd = ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c:a', 'libmp3lame', '-q:a', '2', output_file]
            subprocess.run(cmd, check=True, capture_output=True)
            
            return output_file
            
    except Exception as e:
        logger.error(f"Error in process_m3u8: {e}")
        raise Exception(f"Download failed: {e}")

def download_episode(url, chat_id):
    session = requests.Session()
    session.cookies.update(load_cookies())
    m3u8_url, episode_title, series_name, thumb_url = get_episode_metadata_and_m3u8(url, session)
    audio_file = process_m3u8(m3u8_url, session, chat_id)
    return audio_file, episode_title, series_name, thumb_url

# --- Telegram Bot UI & Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📥 Single Episode", callback_data="single_menu"),
         InlineKeyboardButton("📚 Batch Series Download", callback_data="batch_menu")],
        [InlineKeyboardButton("🛑 Stop Active Download", callback_data="stop_download")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = (
        "👋 **Welcome to Pocket FM Master Bot!**\n\n"
        "Choose an option below or send any Pocket FM link to start lightning-fast downloading with title, series name, and thumbnail!"
    )
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.message.edit_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "single_menu":
        context.user_data['batch_limit'] = '1'
        await query.message.edit_text("🔗 **Single Episode Mode:**\n\nSimply send the direct Pocket FM episode link here, and I will download it instantly!")
    elif query.data == "batch_menu":
        keyboard = [
            [InlineKeyboardButton("10 Episodes", callback_data="batch_10"),
             InlineKeyboardButton("20 Episodes", callback_data="batch_20")],
            [InlineKeyboardButton("50 Episodes", callback_data="batch_50"),
             InlineKeyboardButton("All / Unlimited", callback_data="batch_all")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")]
        ]
        await query.message.edit_text("📚 **Batch Series Download:**\n\nSelect how many episodes you want to download in batch:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif query.data.startswith("batch_"):
        count = query.data.split("_")[1]
        context.user_data['batch_limit'] = count
        limit_text = "All Available" if count == "all" else f"{count} Episodes"
        await query.message.edit_text(f"✅ **Batch Limit Set to: {limit_text}**\n\nNow, send the Pocket FM series or show link, and I will extract and download all episodes automatically!")
    elif query.data == "stop_download":
        active_tasks[query.message.chat_id] = "STOP"
        await query.message.reply_text("🛑 Stop signal sent! Current download will halt shortly.")
    elif query.data == "back_home":
        await start(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.message.chat_id
    urls = [line.strip() for line in text.split('\n') if 'pocketfm.com/' in line]

    if not urls:
        await update.message.reply_text("⚠️ Please send a valid Pocket FM link.")
        return

    active_tasks[chat_id] = "RUNNING"
    batch_limit = context.user_data.get('batch_limit', '1')
    
    session = requests.Session()
    session.cookies.update(load_cookies())

    # Extract all target episode links based on batch selection
    target_urls = []
    for base_url in urls:
        extracted = extract_series_or_episode_links(base_url, session, batch_limit)
        target_urls.extend(extracted)

    # Deduplicate
    seen = set()
    target_urls = [u for u in target_urls if not (u in seen or seen.add(u))]

    if batch_limit != '1' and len(target_urls) > 1:
        await update.message.reply_text(f"📚 Found total series episodes: **{len(target_urls)}**. Starting lightning-fast batch download...")
    else:
        await update.message.reply_text(f"🚀 Processing link...")

    for i, url in enumerate(target_urls, 1):
        if active_tasks.get(chat_id) == "STOP":
            await update.message.reply_text("🛑 Download process terminated by user.")
            break

        msg = await update.message.reply_text(f"⏳ Processing Episode {i}/{len(target_urls)}...")
        unique_id = str(uuid.uuid4())[:8]
        thumb_path = f"downloads/thumb_{unique_id}.jpg"
        audio_file_path = None
        
        try:
            audio_file_path, episode_title, series_name, thumb_url = await asyncio.to_thread(download_episode, url, chat_id)
            
            if thumb_url:
                try:
                    img_data = requests.get(thumb_url, timeout=15).content
                    if not os.path.exists('downloads'):
                        os.makedirs('downloads')
                    with open(thumb_path, 'wb') as handler:
                        handler.write(img_data)
                    
                    im = Image.open(thumb_path).convert('RGB')
                    im.thumbnail((320, 320))
                    im.save(thumb_path, 'JPEG')
                except Exception as img_err:
                    logger.error(f"Thumbnail error: {img_err}")

            await msg.edit_text(f"📤 Uploading [{series_name}] - {episode_title}")

            with open(audio_file_path, 'rb') as audio:
                thumb_file = open(thumb_path, 'rb') if os.path.exists(thumb_path) else None
                await update.message.reply_audio(
                    audio=audio,
                    title=episode_title,
                    performer=series_name,
                    thumbnail=thumb_file
                )
                if thumb_file:
                    thumb_file.close()

            await msg.delete()

        except Exception as e:
            await msg.edit_text(f"❌ Failed Episode {i}. Error: {e}")
            logger.error(f"Error on {url}: {e}")

        if audio_file_path and os.path.exists(audio_file_path):
            os.remove(audio_file_path)
        if os.path.exists(thumb_path):
            os.remove(thumb_path)

    active_tasks[chat_id] = "IDLE"
    
    keyboard = [[InlineKeyboardButton("🏠 Main Menu", callback_data="back_home")]]
    await update.message.reply_text("✨ Batch download task completed successfully!", reply_markup=InlineKeyboardMarkup(keyboard))

def run_bot():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("Bot started polling...")
    application.run_polling()

if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    run_bot()
