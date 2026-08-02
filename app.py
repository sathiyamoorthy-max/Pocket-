import os
import re
import subprocess
import logging
import asyncio
import uuid
import tempfile
import requests
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import m3u8
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import threading

# உங்கள் போட் டோக்கனை இங்கே போடவும்
TOKEN = "YOUR_TELEGRAM_BOT_TOKEN_HERE" 

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# PC-ல் Flask வேண்டாம் என்பதால் அதை நீக்கிவிடலாம், ஆனால் வைத்திருக்கலாம்
flask_app = Flask(__name__)
@flask_app.route('/')
def index(): return "PC Bot is running!"
def run_flask():
    flask_app.run(host='0.0.0.0', port=5000)

# ==========================================
# 🔥 SERIES PARSER (STANDARD REQUESTS)
# ==========================================
def get_series_data(series_url):
    match = re.search(r'/show/([a-zA-Z0-9_-]+)', series_url)
    show_id = match.group(1).split('?')[0]
    api_url = f"https://pocketfm.com/show/{show_id}"
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'}
    resp = requests.get(api_url, headers=headers, timeout=30)
    html = resp.text
    
    title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    series_title = title_match.group(1).split("|")[0].strip() if title_match else "Pocket FM Series"
    json_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    data = json.loads(json_match.group(1))
    
    eps = []
    def extract(node):
        if isinstance(node, dict):
            eid = node.get('episodeId') or node.get('id')
            if eid and len(str(eid)) > 10: eps.append(f"https://pocketfm.com/episode/{eid}")
            for k, v in node.items(): extract(v)
        elif isinstance(node, list):
            for i in node: extract(i)
    extract(data)
    return series_title, list(dict.fromkeys(eps))

# ==========================================
# 🎵 DOWNLOAD ENGINE (M3U8, AES, FFMPEG)
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
                if path: ts_files_dict[idx] = path
        ts_files = [ts_files_dict[i] for i in sorted(ts_files_dict.keys())]
        
        if not os.path.exists('downloads'): os.makedirs('downloads')
        unique_name = str(uuid.uuid4())[:8]
        output_file = f"downloads/audio_{unique_name}.mp3"
        list_path = os.path.join(tmpdir, "list.txt")
        with open(list_path, 'w') as f:
            for ts in ts_files: f.write(f"file '{ts}'\n")
        subprocess.run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c:a', 'libmp3lame', '-q:a', '2', output_file], check=True, capture_output=True)
        return output_file

def get_episode_metadata(episode_url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'}
    resp = requests.get(episode_url, headers=headers, timeout=20)
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
            thumb_file = open(thumb_path, 'rb') if thumb_path else None
            await context.bot.send_audio(update.effective_chat.id, audio=audio, title=display_title, performer="Pocket FM", thumbnail=thumb_file)
            if thumb_file: thumb_file.close()
        if os.path.exists(audio_path): os.remove(audio_path)
        if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔥 **PC Hosted Bot is Online!**\n\nSend any `/show/` or `/episode/` link. No 403 Errors here!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    msg = await update.message.reply_text("🔍 Processing...")
    try:
        if text.count('/episode/') > 1 or '\n' in text:
            urls = [l.strip() for l in text.split('\n') if '/episode/' in l]
            await msg.edit_text(f"🚀 Found {len(urls)} links.")
            for i, u in enumerate(urls, 1):
                await download_and_send_episode(update, context, u, i, len(urls))
            await msg.edit_text(f"✅ Batch Done!")
            return
        elif '/show/' in text:
            url = text.split('|')[0].strip()
            await msg.edit_text("🔄 Fetching Series via your Home IP...")
            title, eps = await asyncio.to_thread(get_series_data, url)
            tracker = {} # PC-ல் ட்ராக்கிங் தேவையில்லை என்றால் விட்டுவிடலாம், புதிய லிஸ்ட் எடுக்கலாம்
            await msg.edit_text(f"✅ Found {len(eps)} eps. Downloading...")
            for i, ep in enumerate(eps, 1):
                await download_and_send_episode(update, context, ep, i, len(eps))
                await asyncio.sleep(2)
            await msg.edit_text(f"🎉 Complete! {len(eps)} eps sent.")
            return
        elif '/episode/' in text:
            await download_and_send_episode(update, context, text)
            await msg.delete()
            return
        else:
            await msg.edit_text("❌ Invalid Link.")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

def run_bot():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    run_bot()
