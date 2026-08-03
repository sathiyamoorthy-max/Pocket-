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
import requests
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

import m3u8
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import yt_dlp

load_dotenv()

# ==========================================
# 🔥 CONFIGURATION
# ==========================================
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable missing.")

# 🔥 புதிய சரியான API எண்ட்பாயிண்ட்கள் (உங்கள் ஸ்கிரீன்ஷாட்டில் இருந்து எடுக்கப்பட்டது)
BASE_API = "https://pocketfm.com/api/v1"
LOGIN_URL = "https://pocketfm.com/api/auth/otp/request"
VERIFY_URL = "https://pocketfm.com/api/auth/callback/credentials?"  # 👈 இந்த URL தான் ரொம்ப முக்கியம்!

DEVICE_ID = os.getenv("DEVICE_ID", "OPPO_CPH2219")
TOKEN_FILE = "pocket_token.json"
DEFAULT_HEADERS = {
    "X-Device-Id": DEVICE_ID,
    "Content-Type": "application/json",
    "User-Agent": "PocketFM/6.5.0 (Android; 13; SM-G991B)"
}

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)
@flask_app.route('/')
def index():
    return "Ultimate Pocket FM Bot is online!"

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port)

# ==========================================
# 🔐 TOKEN MANAGEMENT
# ==========================================
def save_token(token):
    with open(TOKEN_FILE, 'w') as f:
        json.dump({"token": token, "timestamp": time.time()}, f)
    logger.info("Token saved successfully.")

def load_token():
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, 'r') as f:
                data = json.load(f)
                if time.time() - data['timestamp'] < 86400:  # 24 மணிநேரம் வரை வேலை செய்யும்
                    return data['token']
                else:
                    logger.warning("Token expired. Deleting.")
                    os.remove(TOKEN_FILE)
        except:
            pass
    return None

def get_headers():
    token = load_token()
    if token:
        headers = DEFAULT_HEADERS.copy()
        headers["Authorization"] = f"Bearer {token}"
        return headers
    return DEFAULT_HEADERS

# ==========================================
# 📞 LOGIN CONVERSATION (Phone + OTP)
# ==========================================
PHONE, OTP = range(2)

async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📞 **Login to Pocket FM**\n\n"
        "Send your mobile number with country code (e.g., `+919087821263`).\n"
        "We will send you an OTP."
    )
    return PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not re.match(r'^\+\d{10,15}$', phone):
        await update.message.reply_text("❌ Invalid format. Use country code like `+919087821263`.")
        return PHONE

    context.user_data['phone'] = phone
    try:
        resp = requests.post(LOGIN_URL, json={"phone": phone}, headers=DEFAULT_HEADERS, timeout=15)
        if resp.status_code == 200:
            await update.message.reply_text(f"✅ OTP sent to {phone}\n\nPlease enter the 6-digit OTP.")
            return OTP
        else:
            await update.message.reply_text(
                f"❌ OTP API Error: {resp.status_code}.\n\n"
                f"💡 **Quick Fix (Skip OTP):** Use `/settoken` command.\n"
                f"1. Open Kiwi Browser -> Login to `web.pocketfm.com`.\n"
                f"2. Go to Dev Tools -> Network -> Find `Authorization` Header.\n"
                f"3. Send `/settoken Bearer eyJ...` to me."
            )
            return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"❌ Connection Error: {e}")
        return ConversationHandler.END

async def verify_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    otp = update.message.text.strip()
    if not otp.isdigit() or len(otp) != 6:
        await update.message.reply_text("❌ Invalid OTP. Please enter 6 digits.")
        return OTP

    phone = context.user_data.get('phone')
    try:
        # 🔥 இங்கேதான் புதிய சரியான URL பயன்படுத்தப்படுகிறது!
        payload = {"phone": phone, "otp": otp}
        resp = requests.post(VERIFY_URL, json=payload, headers=DEFAULT_HEADERS, timeout=15)
        
        if resp.status_code == 200:
            data = resp.json()
            # API ரெஸ்பான்ஸில் Token-ஐ எடுக்கும் இடம் (இது மாறக்கூடும்)
            token = data.get('data', {}).get('token') or data.get('token')
            if token:
                save_token(token)
                await update.message.reply_text("🎉 **Login Successful!** Token saved. No more 403 errors!")
                return ConversationHandler.END
            else:
                await update.message.reply_text("❌ Token not found in API response. Try `/settoken` manually.")
                return ConversationHandler.END
        else:
            await update.message.reply_text(f"❌ Verification failed: {resp.status_code}. Try `/settoken` manually.")
            return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
        return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Login cancelled.")
    return ConversationHandler.END

# ==========================================
# 📦 API FUNCTIONS (Series & Episode)
# ==========================================
def api_request(url, method="GET", data=None):
    headers = get_headers()
    try:
        if method == "GET":
            resp = requests.get(url, headers=headers, timeout=30)
        else:
            resp = requests.post(url, headers=headers, json=data, timeout=30)
            
        if resp.status_code == 401:
            # Token expired
            if os.path.exists(TOKEN_FILE): os.remove(TOKEN_FILE)
            raise Exception("TOKEN_EXPIRED")
        return resp
    except requests.exceptions.RequestException as e:
        raise Exception(f"Network Error: {e}")

def fetch_series_data(series_url):
    match = re.search(r'/show/([a-zA-Z0-9_-]+)', series_url)
    if not match: raise Exception("Invalid series URL format.")
    show_id = match.group(1).split('?')[0]
    
    url = f"{BASE_API}/show/{show_id}"
    resp = api_request(url)
    
    if resp.status_code != 200: raise Exception(f"API Error: {resp.status_code}")
    
    data = resp.json()
    series_title = data.get('title', 'Unknown Series')
    episodes = data.get('episodes', [])
    episode_urls = [f"https://pocketfm.com/episode/{ep['id']}" for ep in episodes if ep.get('id')]
    return series_title, episode_urls

def fetch_episode_stream(episode_url):
    match = re.search(r'/episode/([a-zA-Z0-9_-]+)', episode_url)
    if not match: raise Exception("Invalid episode URL.")
    episode_id = match.group(1).split('?')[0]
    
    url = f"{BASE_API}/episode/{episode_id}"
    resp = api_request(url)
    
    if resp.status_code != 200: raise Exception(f"API Error: {resp.status_code}")
    
    data = resp.json()
    title = data.get('title', 'Episode')
    thumbnail = data.get('imageUrl') or data.get('thumbnail')
    
    # ஸ்ட்ரீம் URL-ஐ தேடுதல் (அவுட் புட் வித்தியாசமாக இருக்கலாம்)
    stream_url = data.get('audioUrl') or data.get('streamUrl')
    if not stream_url:
        playback = data.get('playbackInfo')
        if playback:
            stream_url = playback.get('url')
    if not stream_url:
        raise Exception("No audio stream URL found in API response.")
        
    return stream_url, title, thumbnail

# ==========================================
# 🎵 DOWNLOAD ENGINE (M3U8 + AES + FFMPEG)
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
            for ts in ts_files:
                f.write(f"file '{ts}'\n")
        subprocess.run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c:a', 'libmp3lame', '-q:a', '2', output_file], check=True, capture_output=True)
        return output_file

# ==========================================
# 🤖 TELEGRAM HANDLERS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔥 **Ultimate Pocket FM Bot (Re-written)**\n\n"
        "✅ **Features:**\n"
        "• `/login` – Auto Login with phone & OTP\n"
        "• `/settoken <token>` – Manually set JWT Token (Skip OTP)\n"
        "• Single episode: `/episode/...`\n"
        "• Full series: `/show/...` (with range: `/show/... | 1 to 10`)\n"
        "• Batch download: paste multiple episode links in one message\n\n"
        "⚠️ **If you get 403/404:** Use `/settoken Bearer eyJ...` obtained from DevTools."
    )

async def set_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = update.message.text.replace('/settoken', '').strip()
    if not token:
        await update.message.reply_text("❌ Usage: `/settoken <your_token>`")
        return
    save_token(token)
    await update.message.reply_text("✅ Token saved successfully! No 403 errors anymore.")

async def download_and_send_episode(update, context, ep_url, ep_number=None, total_eps=None):
    try:
        m3u8_url, title, thumb_url = await asyncio.to_thread(fetch_episode_stream, ep_url)
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
        
        display_title = f"[{ep_number}/{total_eps}] {title}" if ep_number and total_eps else title
        with open(audio_path, 'rb') as audio:
            thumb_file = open(thumb_path, 'rb') if thumb_path else None
            await context.bot.send_audio(
                chat_id=update.effective_chat.id,
                audio=audio,
                title=display_title,
                performer="Pocket FM",
                thumbnail=thumb_file
            )
            if thumb_file: thumb_file.close()
        if os.path.exists(audio_path): os.remove(audio_path)
        if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
        return True, title
    except Exception as e:
        if str(e) == "TOKEN_EXPIRED":
            return False, "Token expired! Please use `/settoken` to update it."
        return False, str(e)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    msg = await update.message.reply_text("🔍 Processing...")

    if 'onelink.me' in text and 'pocketfm.com' not in text:
        try:
            resp = requests.get(text, allow_redirects=True, timeout=10)
            if 'pocketfm.com' in resp.url:
                text = resp.url
                await msg.edit_text(f"✅ Resolved: `{text}`")
            else:
                await msg.edit_text("❌ Could not resolve.")
                return
        except:
            await msg.edit_text("❌ Error resolving.")
            return

    try:
        # Multi-Link Batch
        if text.count('/episode/') > 1 or '\n' in text:
            urls = [line.strip() for line in text.split('\n') if '/episode/' in line]
            if not urls:
                await msg.edit_text("❌ No valid episode links found.")
                return
            await msg.edit_text(f"🚀 Found {len(urls)} links. Downloading batch...")
            for i, url in enumerate(urls, 1):
                await download_and_send_episode(update, context, url, ep_number=i, total_eps=len(urls))
            await msg.edit_text(f"✅ Batch complete! {len(urls)} episodes sent.")
            return

        # Series with Range
        elif '/show/' in text:
            range_match = re.search(r'\|?\s*(\d+)\s*to\s*(\d+)', text)
            url = text.split('|')[0].strip()
            
            await msg.edit_text("🔄 Fetching series data from API...")
            series_title, episode_urls = await asyncio.to_thread(fetch_series_data, url)
            total_found = len(episode_urls)
            
            if range_match:
                start_ep = int(range_match.group(1))
                end_ep = int(range_match.group(2))
                if start_ep > total_found or end_ep > total_found:
                    await msg.edit_text(f"❌ Range error: Series has only {total_found} episodes.")
                    return
                episode_urls = episode_urls[start_ep-1:end_ep]
                await msg.edit_text(f"✅ Found {len(episode_urls)} episodes (range {start_ep}-{end_ep}). Downloading...")
            else:
                await msg.edit_text(f"✅ Found {total_found} episodes in '{series_title}'. Downloading...")
            
            # Future tracking
            tracker_file = 'series_cache.json'
            tracker = {}
            if os.path.exists(tracker_file):
                with open(tracker_file, 'r') as f:
                    tracker = json.load(f)
            show_id = url.split('/show/')[1].strip('/')
            old = tracker.get(show_id, [])
            new_eps = [ep for ep in episode_urls if ep not in old]
            
            if not new_eps and not range_match:
                await msg.edit_text(f"📊 **{series_title}**\nTotal: {total_found}\n✅ All episodes already downloaded. No new episodes.")
                return
            
            download_list = new_eps if not range_match else episode_urls
            for i, ep in enumerate(download_list, 1):
                await download_and_send_episode(update, context, ep, ep_number=i, total_eps=len(download_list))
                await asyncio.sleep(2)
            
            if not range_match:
                tracker[show_id] = episode_urls
                with open(tracker_file, 'w') as f:
                    json.dump(tracker, f)
            
            await msg.edit_text(f"🎉 Complete! {len(download_list)} episodes sent.")
            return

        # Single Episode
        elif '/episode/' in text:
            await download_and_send_episode(update, context, text)
            await msg.delete()
            return

        else:
            await msg.edit_text("❌ Invalid URL. Send a valid Pocket FM link.")

    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}\n\n💡 Token expired? Use `/settoken` command.")

def run_bot():
    app = Application.builder().token(TOKEN).build()
    
    login_conv = ConversationHandler(
        entry_points=[CommandHandler('login', login_start)],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, verify_otp)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("settoken", set_token))
    app.add_handler(login_conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    app.run_polling()

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    run_bot()
