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

active_tasks = {}
user_pending_links = {}

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

# --- Advanced Pocket FM Parser Core Logic ---
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

def parse_pocketfm_page(url, session):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
        'Referer': 'https://pocketfm.com/'
    }
    logger.info(f"Fetching page: {url}")
    response = session.get(url, headers=headers, timeout=20)
    html_content = response.text
    
    episode_links = []
    series_name = "Pocket FM Series"
    
    # Extract Next.js JSON data to accurately pull all episodes
    next_data_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html_content, re.DOTALL)
    if next_data_match:
        try:
            data = json.loads(next_data_match.group(1))
            props = data.get('props', {}).get('pageProps', {})
            
            # Check for show or episodes array in props
            show_data = props.get('show', {})
            if isinstance(show_data, dict):
                series_name = show_data.get('title', series_name)
                episodes_list = show_data.get('episodes', [])
                for ep in episodes_list:
                    ep_id = ep.get('id') or ep.get('uuid')
                    if ep_id:
                        episode_links.append(f"https://pocketfm.com/episode/{ep_id}")
        except Exception as e:
            logger.error(f"Next.js JSON parse error: {e}")

    # Fallback: Extract all episode links using regex if JSON traversal fails
    if not episode_links:
        found_urls = re.findall(r'href=["\'](https://pocketfm\.com/episode/[^"\']+)["\']', html_content)
        if not found_urls:
            found_urls = re.findall(r'href=["\'](/episode/[^"\']+)["\']', html_content)
            found_urls = [f"https://pocketfm.com{ep}" for ep in found_urls]
        
        seen = set()
        episode_links = [ep for ep in found_urls if not (ep in seen or seen.add(ep))]

    if not episode_links:
        episode_links = [url]

    return episode_links, series_name

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
        [InlineKeyboardButton("📥 Single Episode", callback_data="mode_single"),
         InlineKeyboardButton("📚 Batch Series Download", callback_data="mode_batch")],
        [InlineKeyboardButton("🛑 Stop Active Download", callback_data="stop_download")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = (
        "👋 **Welcome to Pocket FM Master Bot!**\n\n"
        "Send any Pocket FM link. I will scan total episodes and let you choose your batch range instantly!"
    )
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.message.edit_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    chat_id = query.message.chat_id

    if data == "mode_single":
        context.user_data['mode'] = 'single'
        await query.message.edit_text("🔗 **Single Episode Mode:**\n\nSend any Pocket FM link to download instantly!")
    elif data == "mode_batch":
        context.user_data['mode'] = 'batch'
        keyboard = [
            [InlineKeyboardButton("Next 10 Episodes", callback_data="range_10"),
             InlineKeyboardButton("Next 20 Episodes", callback_data="range_20")],
            [InlineKeyboardButton("Next 50 Episodes", callback_data="range_50"),
             InlineKeyboardButton("Full Series / Unlimited", callback_data="range_all")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_home")]
        ]
        await query.message.edit_text("📚 **Batch Range Selector:**\n\nSelect your download range:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("range_"):
        rng = data.split("_")[1]
        context.user_data['batch_range'] = rng
        range_label = "All Available" if rng == "all" else f"Next {rng} Episodes"
        
        if chat_id in user_pending_links:
            url = user_pending_links[chat_id]
            await query.message.edit_text(f"✅ **Range Set: {range_label}**\n\n🚀 Starting download sequence...")
            context.application.create_task(process_download_sequence(update, context, chat_id, url, rng))
        else:
            await query.message.edit_text(f"✅ **Range Set to: {range_label}**\n\nNow send your Pocket FM link!")
    elif data == "stop_download":
        active_tasks[chat_id] = "STOP"
        await query.message.reply_text("🛑 Stop signal sent! Current download will halt shortly.")
    elif data == "back_home":
        await start(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.message.chat_id
    urls = [line.strip() for line in text.split('\n') if 'pocketfm.com/' in line]

    if not urls:
        await update.message.reply_text("⚠️ Please send a valid Pocket FM link.")
        return

    url = urls[0]
    session = requests.Session()
    session.cookies.update(load_cookies())

    await update.message.reply_text("🔍 Scanning series and calculating total available episodes...")
    episode_links, series_name = parse_pocketfm_page(url, session)
    total_found = len(episode_links)

    start_idx = 0
    for idx, ep_url in enumerate(episode_links):
        if url in ep_url or ep_url in url:
            start_idx = idx
            break

    pending_count = total_found - start_idx

    mode = context.user_data.get('mode', 'batch')
    if mode == 'single':
        target_urls = [url]
        await update.message.reply_text(f"🎯 **Series:** {series_name}\n📊 **Total Released:** {total_found}\n\n🚀 Processing single episode...")
        context.application.create_task(execute_downloads(update, context, chat_id, target_urls))
    else:
        user_pending_links[chat_id] = url
        context.user_data['episode_links'] = episode_links
        context.user_data['start_idx'] = start_idx

        keyboard = [
            [InlineKeyboardButton("Next 10 Episodes", callback_data="range_10"),
             InlineKeyboardButton("Next 20 Episodes", callback_data="range_20")],
            [InlineKeyboardButton("Next 50 Episodes", callback_data="range_50"),
             InlineKeyboardButton("Full Series / Unlimited", callback_data="range_all")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        info_text = (
            f"📚 **Series Detected:** {series_name}\n"
            f"🔢 **Total Released Episodes:** {total_found}\n"
            f"📍 **Current Episode Position:** #{start_idx + 1}\n"
            f"⏳ **Pending Episodes from here:** {pending_count}\n\n"
            f"Please choose your download range:"
        )
        await update.message.reply_text(info_text, reply_markup=reply_markup, parse_mode="Markdown")

async def process_download_sequence(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id, url, rng):
    episode_links = context.user_data.get('episode_links', [url])
    start_idx = context.user_data.get('start_idx', 0)

    sub_list = episode_links[start_idx:]
    if rng != 'all' and rng.isdigit():
        limit = int(rng)
        sub_list = sub_list[:limit]

    await execute_downloads(update, context, chat_id, sub_list)

async def execute_downloads(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id, target_urls):
    active_tasks[chat_id] = "RUNNING"
    target_msg = update.message if update.message else update.callback_query.message

    await target_msg.reply_text(f"🚀 Starting lightning-fast download of **{len(target_urls)}** episode(s)...", parse_mode="Markdown")

    for i, url in enumerate(target_urls, 1):
        if active_tasks.get(chat_id) == "STOP":
            await target_msg.reply_text("🛑 Download process terminated by user.")
            break

        msg = await target_msg.reply_text(f"⏳ Processing Episode {i}/{len(target_urls)}...")
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
                await target_msg.reply_audio(
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
    await target_msg.reply_text("✨ Batch download task completed successfully!", reply_markup=InlineKeyboardMarkup(keyboard))

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
