import os
import re
import subprocess
import logging
import asyncio
import uuid
import tempfile
import json
import threading
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

# Logging setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Bypass logic (cloudscraper fallback)
try:
    import cloudscraper
    SCRAPER = cloudscraper.create_scraper()
    logger.info("Cloudscraper loaded. Bypass active.")
except ImportError:
    import requests
    SCRAPER = requests
    logger.warning("Cloudscraper not installed. Using requests (may get blocked).")

TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("No TELEGRAM_TOKEN found in environment variables")

flask_app = Flask(__name__)

@flask_app.route('/')
def index():
    return "Ultimate Bot Online!"

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port)

# ==========================================
# 🔥 ULTIMATE CORE LOGIC (ZERO REPEAT REQUEST)
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

def extract_all_episode_ids(data):
    ids = []
    if isinstance(data, dict):
        if 'episodeId' in data and data['episodeId']:
            ids.append(data['episodeId'])
        for key, value in data.items():
            if key in ['episodeId', 'id'] and isinstance(value, str) and len(value) > 10:
                ids.append(value)
            else:
                ids.extend(extract_all_episode_ids(value))
    elif isinstance(data, list):
        for item in data:
            ids.extend(extract_all_episode_ids(item))
    return list(dict.fromkeys(ids))

def get_series_data(series_url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'
    }
    try:
        resp = SCRAPER.get(series_url, headers=headers, timeout=30)
        html = resp.text
        
        title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
        series_title = title_match.group(1).split("|")[0].strip() if title_match else "Unknown Series"
        
        # Next.js Data
        json_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
        if not json_match:
            raise Exception("Cloudflare Blocked. Please use Multi-Link Mode (paste multiple links in one message).")
        
        data = json.loads(json_match.group(1))
        all_ids = extract_all_episode_ids(data)
        return series_title, all_ids
    except Exception as e:
        logger.error(f"Series fetch error: {e}")
        raise e

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
    resp = SCRAPER.get(episode_url, headers=headers, timeout=20)
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

# ==========================================
# 🤖 BOT HANDLERS
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 **Ultimate Pocket FM Bot**\n\n"
        "இனி ஒரு லிங்க் மட்டும் போதும்! மீண்டும் மீண்டும் கேட்க வேண்டாம்.\n\n"
        "📌 **Commands:**\n"
        "1️⃣ `/episode/...` -> ஒரு எபிசோட் மட்டும்.\n"
        "2️⃣ `/show/...` -> மொத்த சீரியஸையும் முழுதும் டவுன்லோட் செய்யும்.\n"
        "   ⏳ நாளை புது எபிசோட் வந்தால், அதே `/show/` லிங்கை மீண்டும் அனுப்புங்கள். புதிய எபிசோட் மட்டும் வந்து சேரும்!\n"
        "3️⃣ Multi-Link: ஒரு மெசேஜில் பல எபிசோட் லிங்க்களை புதிய வரியில் அனுப்புங்கள்."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    msg = await update.message.reply_text("🔍 Processing input...")

    if 'onelink.me' in text:
        await msg.edit_text("❌ `onelink` ஆப் ஸ்டோர் லிங்க். `pocketfm.com/episode/` அல்லது `/show/` லிங்கை அனுப்பவும்.")
        return

    try:
        # ✅ MULTI-LINK BATCH
        lines = [line.strip() for line in text.split('\n') if 'pocketfm.com/episode/' in line]
        if len(lines) > 1:
            total = len(lines)
            await msg.edit_text(f"🚀 {total} லிங்க்குகள் கண்டறியப்பட்டன. தொடர்ச்சியாக டவுன்லோட் செய்யப்படுகிறது...")
            for idx, url in enumerate(lines, 1):
                await msg.edit_text(f"⏳ Episode {idx}/{total}...")
                await download_and_send_episode(update, context, url, ep_number=idx, total_eps=total)
            await msg.edit_text(f"✅ முடிந்தது! {total} கோப்புகள் அனுப்பப்பட்டன.")
            return

        # ✅ SINGLE EPISODE
        if '/episode/' in text:
            await msg.edit_text("⬇️ ஒரு எபிசோட் டவுன்லோட் செய்யப்படுகிறது...")
            success, result = await download_and_send_episode(update, context, text)
            if success:
                await msg.delete()
            else:
                await msg.edit_text(f"❌ பிழை: {result}")
            return

        # ✅ SERIES BATCH
        elif '/show/' in text:
            show_id = text.split('/show/')[1].split('?')[0].strip('/')
            await msg.edit_text("🔄 சீரிஸ் டேட்டா ஸ்கேன் செய்யப்படுகிறது. Cloudflare பைபாஸ் செய்யப்படுகிறது...")

            series_title, current_ids = await asyncio.to_thread(get_series_data, text)
            total_found = len(current_ids)

            tracker = load_tracker()
            cached_ids = tracker.get(show_id, [])
            
            new_ids = [eid for eid in current_ids if eid not in cached_ids]
            
            if not new_ids:
                await msg.edit_text(
                    f"📊 **{series_title}**\n"
                    f"📈 மொத்த எபிசோடுகள்: {total_found}\n"
                    f"✅ எல்லா எபிசோடுகளும் ஏற்கனவே டவுன்லோட் ஆகிவிட்டன. புதிய எபிசோட் எதுவும் இல்லை."
                )
                return
            
            await msg.edit_text(
                f"📊 **{series_title}**\n"
                f"📈 மொத்த எபிசோடுகள்: {total_found}\n"
                f"🆕 புதியதாக கண்டுபிடிக்கப்பட்டவை: {len(new_ids)}\n\n"
                f"📥 இவை பேட்ச் ஆக டவுன்லோட் செய்யப்படுகின்றன..."
            )

            count = 1
            for ep_url in [f"https://pocketfm.com/episode/{eid}" for eid in new_ids]:
                await msg.edit_text(f"📥 டவுன்லோட் செய்யப்படுகிறது {count}/{len(new_ids)}...")
                await download_and_send_episode(update, context, ep_url, ep_number=count, total_eps=len(new_ids))
                count += 1
                await asyncio.sleep(2)

            tracker[show_id] = current_ids
            save_tracker(tracker)

            await msg.edit_text(f"🎉 **பேட்ச் முழுமையடைந்தது!** {len(new_ids)} புதிய எபிசோடுகள் அனுப்பப்பட்டன.")
            return

        else:
            await msg.edit_text("❌ சரியான லிங்க் இல்லை. `/episode/` அல்லது `/show/` லிங்க் மட்டும் அனுப்பவும்.")

    except Exception as e:
        await msg.edit_text(f"❌ **பிழை ஏற்பட்டுள்ளது:** {str(e)}\n\n💡 **தீர்வு:** Cloudflare பிளாக் ஆனால், ஒரே மெசேஜில் அத்தனை எபிசோட் லிங்க்களையும் அனுப்பி மல்டி-Link பயன்படுத்தவும்.")

def run_bot():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    run_bot()
