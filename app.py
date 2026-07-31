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

TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("No TELEGRAM_TOKEN found in environment variables")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)

@flask_app.route('/')
def index():
    return "Bot is running with Ultimate Hybrid Features!"

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port)

# ==========================================
# 🔥 ADVANCED BATCH DOWNLOADER
# ==========================================

def extract_episodes_from_json(json_data, series_url):
    episodes = []
    if isinstance(json_data, dict):
        if 'episodeId' in json_data and json_data['episodeId']:
            episodes.append(f"https://pocketfm.com/episode/{json_data['episodeId']}")
        for key, value in json_data.items():
            if key in ['episodeId', 'id'] and isinstance(value, str) and len(value) > 10:
                episodes.append(f"https://pocketfm.com/episode/{value}")
            else:
                episodes.extend(extract_episodes_from_json(value, series_url))
    elif isinstance(json_data, list):
        for item in json_data:
            episodes.extend(extract_episodes_from_json(item, series_url))
    
    # 💡 வரிசை மாறாமல் Duplicate-ஐ நீக்கும் மேஜிக்!
    return list(dict.fromkeys(episodes))

def get_series_data(series_url):
    # 💡 Cloudflare-ஐ ஏமாற்ற மேம்படுத்தப்பட்ட Headers
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    resp = requests.get(series_url, headers=headers, timeout=30)
    html = resp.text
    
    title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    series_title = title_match.group(1).split("|")[0].strip() if title_match else "Pocket FM Series"

    json_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if not json_match:
        raise Exception("Cloudflare blocked the request or Next.js Data is missing. Try Multi-Link Mode.")
    
    data = json.loads(json_match.group(1))
    all_episodes = extract_episodes_from_json(data, series_url)
    return series_title, all_episodes

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
        
        if not os.path.exists('downloads'):
            os.makedirs('downloads')
            
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
    resp = requests.get(episode_url, headers=headers, timeout=20)
    html = resp.text
    
    title = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    img = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    m3u8 = re.search(r'(https?://[^\s"\'<>]+\.cloudfront\.net[^\s"\'<>]*?\.m3u8[^\s"\'<>]*)', html)
    if not m3u8:
        m3u8 = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html)
        
    if not m3u8:
        raise Exception("M3U8 not found")
        
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
                img_data = requests.get(thumb_url).content
                with open(thumb_path, 'wb') as f: f.write(img_data)
                Image.open(thumb_path).convert('RGB').thumbnail((320, 320)).save(thumb_path, 'JPEG')
            except:
                thumb_path = None

        display_title = f"{title}"
        if ep_number and total_eps:
            display_title = f"[{ep_number}/{total_eps}] {title}"

        with open(audio_path, 'rb') as audio:
            thumb_file = open(thumb_path, 'rb') if thumb_path and os.path.exists(thumb_path) else None
            await update.message.reply_audio(audio=audio, title=display_title, performer="Pocket FM", thumbnail=thumb_file)
            if thumb_file: thumb_file.close()

        if os.path.exists(audio_path): os.remove(audio_path)
        if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
        return True, title
    except Exception as e:
        return False, str(e)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 **Pocket FM Ultimate Bot**\n\n"
        "📌 **Features:**\n"
        "1. **Single Link:** `/episode/...`\n"
        "2. **Multi-Link Mode:** Send multiple `/episode/...` links in ONE message!\n"
        "3. **Full Series:** `/show/...`\n"
        "4. **Range Option:** `/show/... | 1 to 10`\n\n"
        "⚠️ **Note:** If Series link (`/show/`) gets blocked by Cloudflare, just use Multi-Link Mode!"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    msg = await update.message.reply_text("🔍 Analyzing link(s)...")

    if 'onelink.me' in text:
        await msg.edit_text("❌ **Error:** `onelink.me` links are App Store links.\nPlease get the actual `/episode/` or `/show/` link from the Share button.")
        return

    try:
        # 🟢 MULTI-LINK BATCH MODE (The Cloudflare Bypass)
        if text.count('pocketfm.com/episode/') > 1 or '\n' in text:
            urls = [line.strip() for line in text.split('\n') if 'pocketfm.com/episode/' in line]
            if not urls:
                await msg.edit_text("❌ No valid episode links found in message.")
                return
                
            total = len(urls)
            await msg.edit_text(f"🚀 **Multi-Link Batch Mode Active:** Found {total} links. Starting downloads...")
            
            for idx, url in enumerate(urls, 1):
                await msg.edit_text(f"⏳ Processing Episode {idx}/{total}...")
                success, result = await download_and_send_episode(update, context, url, ep_number=idx, total_eps=total)
                if not success:
                    await update.message.reply_text(f"⚠️ Failed to download link {idx}: {result}")
                    
            await msg.edit_text(f"✅ **Multi-Link Task Complete!**")
            return

        # 🔵 SERIES / BATCH DOWNLOAD
        elif '/show/' in text:
            range_match = re.search(r'\|?\s*(\d+)\s*to\s*(\d+)', text)
            url = text.split('|')[0].strip()

            await msg.edit_text("🔄 Fetching series data... (Bypassing Security)")
            series_title, all_episodes = await asyncio.to_thread(get_series_data, url)
            total_found = len(all_episodes)
            
            if range_match:
                start_ep = int(range_match.group(1))
                end_ep = int(range_match.group(2))
                if start_ep > total_found:
                    await msg.edit_text(f"❌ Range Error: Series only has {total_found} episodes.")
                    return
                all_episodes = all_episodes[start_ep-1:end_ep]
                await msg.edit_text(f"✅ Found **{len(all_episodes)}** episodes (Range {start_ep}-{end_ep}). Starting download...")
            else:
                await msg.edit_text(f"✅ Found **{total_found}** episodes. Starting full series download...")

            count = 1
            for ep_url in all_episodes:
                await msg.edit_text(f"📥 Downloading Episode {count}/{len(all_episodes)}...")
                success, result = await download_and_send_episode(update, context, ep_url, ep_number=count, total_eps=len(all_episodes))
                count += 1
                await asyncio.sleep(2)
            
            await msg.edit_text(f"🎉 **Batch Complete!** All {len(all_episodes)} episodes sent.")
            return

        # 🟢 SINGLE EPISODE DOWNLOAD
        elif '/episode/' in text:
            await msg.edit_text("⬇️ Downloading single episode...")
            success, result = await download_and_send_episode(update, context, text)
            if success:
                await msg.delete()
            else:
                await msg.edit_text(f"❌ Failed: {result}")
            return

        else:
            await msg.edit_text("❌ Invalid URL. Please send a valid `pocketfm.com` link.")

    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)}\n\n💡 Try **Multi-Link Mode** (Paste 10 single episode links at once).")

def run_bot():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    run_bot()
