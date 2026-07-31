import os
import re
import subprocess
import logging
import asyncio
import uuid
import tempfile
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import m3u8
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import threading

TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("No TELEGRAM_TOKEN found in environment variables")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)

@flask_app.route('/')
def index():
    return "Bot is running perfectly!"

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port)

def get_episode_data(episode_url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'
    }
    response = requests.get(episode_url, headers=headers, timeout=20)
    html_content = response.text
    
    # Extract Title & Series Name
    title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html_content)
    raw_title = title_match.group(1) if title_match else "Pocket FM Audio"
    series_name = "Pocket FM"
    episode_title = raw_title
    if "|" in raw_title:
        parts = raw_title.split("|")
        episode_title = parts[0].strip()
        series_name = parts[1].strip()
        
    # Extract Thumbnail
    img_match = re.search(r'<meta property="og:image" content="([^"]+)"', html_content)
    thumbnail_url = img_match.group(1) if img_match else None
    
    # Extract m3u8 link
    m3u8_match = re.search(r'(https?://[^\s"\'<>]+\.cloudfront\.net[^\s"\'<>]*?\.m3u8[^\s"\'<>]*)', html_content)
    if not m3u8_match:
        m3u8_match = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html_content)
        
    if m3u8_match:
        m3u8_url = m3u8_match.group(1).replace('\\/', '/').replace('\\u002F', '/')
        return m3u8_url, episode_title, series_name, thumbnail_url
    else:
        raise Exception("M3U8 Audio link not found! Check if the link is correct.")

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
    except Exception:
        return i, None

def download_audio(m3u8_url):
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
        
        unique_name = str(uuid.uuid4())[:8]
        if not os.path.exists('downloads'): os.makedirs('downloads')
        output_file = f"downloads/audio_{unique_name}.mp3"
        list_path = os.path.join(tmpdir, "list.txt")
        
        with open(list_path, 'w') as f:
            for ts in ts_files:
                f.write(f"file '{ts}'\n")
                
        subprocess.run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c:a', 'libmp3lame', '-q:a', '2', output_file], check=True, capture_output=True)
        return output_file

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 **Pocket FM Bot is Ready!**\n\nSend me a single episode link to download.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if 'pocketfm.com' not in url:
        return
        
    msg = await update.message.reply_text("🚀 Processing episode...")
    thumb_path = None
    audio_path = None
    
    try:
        m3u8_url, title, series, thumb_url = await asyncio.to_thread(get_episode_data, url)
        
        if thumb_url:
            unique_id = str(uuid.uuid4())[:8]
            thumb_path = f"downloads/thumb_{unique_id}.jpg"
            img_data = requests.get(thumb_url).content
            with open(thumb_path, 'wb') as f: f.write(img_data)
            im = Image.open(thumb_path).convert('RGB')
            im.thumbnail((320, 320))
            im.save(thumb_path, 'JPEG')
            
        await msg.edit_text(f"📥 Downloading Audio...\n\n**Series:** {series}\n**Episode:** {title}")
        audio_path = await asyncio.to_thread(download_audio, m3u8_url)
        
        await msg.edit_text("📤 Uploading to Telegram...")
        
        with open(audio_path, 'rb') as audio:
            thumb_file = open(thumb_path, 'rb') if thumb_path and os.path.exists(thumb_path) else None
            await update.message.reply_audio(audio=audio, title=title, performer=series, thumbnail=thumb_file)
            if thumb_file: thumb_file.close()
            
        await msg.delete()
        
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")
        
    finally:
        if audio_path and os.path.exists(audio_path): os.remove(audio_path)
        if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)

def run_bot():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    run_bot()
