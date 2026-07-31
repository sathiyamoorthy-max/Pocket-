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
    return "Ultimate Resolver Bot is online!"

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port)

# ==========================================
# 🔥 COOKIE LOADER (கண்டிப்பாக தேவை)
# ==========================================
COOKIE_FILE = 'cookies.txt'
session = requests.Session()

def load_cookies():
    if os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE, 'r') as f:
            for line in f:
                if not line.strip() or line.startswith('#'):
                    continue
                parts = line.strip().split('\t')
                if len(parts) >= 7:
                    session.cookies.set(parts[5], parts[6])
        return True
    return False

# ==========================================
# 🔥 SMART SERIES TRACKER & RESOLVER
# ==========================================
TRACKER_FILE = 'series_cache.json'

def load_tracker():
    if os.path.exists(TRACKER_FILE):
        with open(TRACKER_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_tracker(data):
    with open(TRACKER_FILE, 'w') as f:
        json.dump(data, f)

def extract_episodes_from_json(json_data):
    episodes = []
    if isinstance(json_data, dict):
        if 'episodeId' in json_data and json_data['episodeId']:
            episodes.append(f"https://pocketfm.com/episode/{json_data['episodeId']}")
        for key, value in json_data.items():
            if key in ['episodeId', 'id'] and isinstance(value, str) and len(value) > 10:
                episodes.append(f"https://pocketfm.com/episode/{value}")
            else:
                episodes.extend(extract_episodes_from_json(value))
    elif isinstance(json_data, list):
        for item in json_data:
            episodes.extend(extract_episodes_from_json(item))
    return list(dict.fromkeys(episodes))

def get_series_data(series_url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    }
    resp = session.get(series_url, headers=headers, timeout=30)
    html = resp.text
    
    title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    series_title = title_match.group(1).split("|")[0].strip() if title_match else "Pocket FM Series"

    json_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if not json_match:
        raise Exception("Cloudflare Block. Check if cookies.txt is valid.")
    
    data = json.loads(json_match.group(1))
    all_episodes = extract_episodes_from_json(data)
    return series_title, all_episodes

def download_chunk(args):
    i, seg_url, base_url, aes_key, iv, tmpdir = args
    try:
        full_url = seg_url if seg_url.startswith('http') else f"{base_url}/{seg_url}"
        ts_path = os.path.join(tmpdir, f"chunk_{i:04d}.ts")
        response = session.get(full_url, stream=True, timeout=15)
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
        aes_key = session.get(key_url).content

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
    resp = session.get(episode_url, headers=headers, timeout=20)
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
                img_data = session.get(thumb_url).content
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

# ==========================================
# 🕵️ SMART ONELINK RESOLVER
# ==========================================
def resolve_onelink(onelink_url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Mobile Safari/537.36'
    }
    try:
        # மொபைல் யூசர் ஏஜெண்ட் பயன்படுத்தி ரெடைரெக்ட் ஃபாலோ பண்ணுதல்
        resp = requests.get(onelink_url, headers=headers, allow_redirects=True, timeout=15)
        final_url = resp.url
        if 'pocketfm.com/show/' in final_url or 'pocketfm.com/episode/' in final_url:
            return final_url
        return None
    except Exception as e:
        logger.error(f"Deep link resolve error: {e}")
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cookie_status = "✅ Loaded" if load_cookies() else "❌ Not Found (Please upload cookies.txt via Render Shell)"
    await update.message.reply_text(
        f"🚀 **Pocket FM Ultimate Bot (Deep Link Resolver)**\n\n"
        f"🍪 Cookies Status: {cookie_status}\n\n"
        "📌 **Features:**\n"
        "1. **Smart Resolve:** Even `onelink.me` links will be auto-converted and downloaded!\n"
        "2. **Full Series:** `/show/...` (Track future eps)\n"
        "3. **Multi-Link Mode:** Paste multiple links in one message.\n\n"
        "⚠️ **Note:** Requires `cookies.txt` in Render Shell to avoid 403 Error."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    msg = await update.message.reply_text("🔍 Analyzing link(s)...")

    load_cookies() # Session-ஐ Cookies உடன் உருவாக்குதல்

    # 🕵️ ONELINK RESOLVER (இதுதான் புதிய மேஜிக்!)
    if 'onelink.me' in text:
        await msg.edit_text("🔄 Resolving App Store redirect...")
        resolved_url = await asyncio.to_thread(resolve_onelink, text)
        if resolved_url:
            text = resolved_url
            await msg.edit_text(f"✅ Resolved to: `{text}`. Proceeding to download...")
        else:
            await msg.edit_text("❌ Could not resolve `onelink.me` to a Pocket FM link. Please send the actual `/episode/` or `/show/` link.")
            return

    try:
        # 🟢 MULTI-LINK BATCH MODE
        if text.count('pocketfm.com/episode/') > 1 or '\n' in text:
            urls = [line.strip() for line in text.split('\n') if 'pocketfm.com/episode/' in line]
            if not urls:
                await msg.edit_text("❌ No valid episode links found.")
                return
                
            total = len(urls)
            await msg.edit_text(f"🚀 **Multi-Link Batch Active:** {total} links.")
            for idx, url in enumerate(urls, 1):
                await msg.edit_text(f"⏳ Processing {idx}/{total}...")
                await download_and_send_episode(update, context, url, ep_number=idx, total_eps=total)
            await msg.edit_text(f"✅ **Multi-Link Complete!**")
            return

        # 🔵 SERIES BATCH + FUTURE TRACKING
        elif '/show/' in text:
            show_id = text.split('/show/')[1].split('?')[0].strip('/')
            url = text.split('|')[0].strip()

            await msg.edit_text("🔄 Fetching series data...")
            series_title, current_episodes = await asyncio.to_thread(get_series_data, url)
            total_found = len(current_episodes)

            tracker = load_tracker()
            old_episodes = tracker.get(show_id, [])
            new_episodes = [ep for ep in current_episodes if ep not in old_episodes]

            if not new_episodes:
                await msg.edit_text(f"📊 **{series_title}**\n📈 Total: {total_found}\n✅ All episodes already downloaded. No new episodes found.")
                return

            range_match = re.search(r'\|?\s*(\d+)\s*to\s*(\d+)', text)
            if range_match:
                start_ep = int(range_match.group(1))
                end_ep = int(range_match.group(2))
                new_episodes = new_episodes[start_ep-1:end_ep]
                await msg.edit_text(f"✅ Found **{len(new_episodes)}** NEW episodes (Range). Downloading...")
            else:
                await msg.edit_text(f"✅ Found **{len(new_episodes)}** NEW episodes for '{series_title}'. Downloading...")

            count = 1
            for ep_url in new_episodes:
                await msg.edit_text(f"📥 Downloading {count}/{len(new_episodes)}...")
                await download_and_send_episode(update, context, ep_url, ep_number=count, total_eps=len(new_episodes))
                count += 1
                await asyncio.sleep(2)

            tracker[show_id] = current_episodes
            save_tracker(tracker)

            await msg.edit_text(f"🎉 **Complete!** {len(new_episodes)} new episodes sent. Send this same link later to get future episodes.")
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
            await msg.edit_text("❌ Invalid URL. Send a valid `pocketfm.com` link.")

    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)}\n\n💡 Make sure `cookies.txt` is uploaded in Render Shell.")

def run_bot():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    run_bot()
