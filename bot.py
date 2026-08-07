import os
import re
import uuid
import threading
import subprocess
import requests
import time
from bs4 import BeautifulSoup
from http.cookiejar import MozillaCookieJar
from flask import Flask
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton

# ==========================================
# 1. Environment Setup
# ==========================================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_TOKEN is missing!")

bot = telebot.TeleBot(BOT_TOKEN)
user_states = {}

if not os.path.exists('downloads'):
    os.makedirs('downloads')

# ==========================================
# 2. 24/7 Web Server (Flask)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "🚀 Ultimate Pocket FM Downloader Bot is Active!"

def run_web_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_web_server, daemon=True).start()

# ==========================================
# 3. Ultimate Bypass Engine (Cookies + Auto Guest Token)
# ==========================================
session = requests.Session()
MOBILE_HEADERS = {
    "User-Agent": "PocketFM/6.5.0 (Android; 13; SM-G991B)",
    "Referer": "https://www.pocketfm.com/",
    "Origin": "https://www.pocketfm.com"
}
session.headers.update(MOBILE_HEADERS)

# 🔥 Auto Guest Token Generator (Fallback if cookies.txt fails)
def generate_guest_bearer_token():
    try:
        guest_url = "https://api.pocketfm.com/v2/users/guest"
        headers = {
            "User-Agent": "PocketFM/6.5.0 (Android; 13; SM-G991B)",
            "Content-Type": "application/json",
            "X-Device-Id": str(uuid.uuid4())
        }
        payload = {"deviceId": headers["X-Device-Id"], "platform": "android"}
        
        resp = requests.post(guest_url, json=payload, headers=headers, timeout=10)
        if resp.status_code in [200, 201]:
            data = resp.json()
            token = data.get("token") or data.get("accessToken")
            if token:
                print("✅ Auto-Generated Guest Bearer Token!")
                return token
    except Exception as e:
        print(f"⚠️ Guest Token Generation Failed: {e}")
    return None

# Load Cookies and Auth Token
def load_auth_engine():
    """Loads cookies and sets the Authorization Bearer token."""
    if os.path.exists('cookies.txt'):
        try:
            cj = MozillaCookieJar('cookies.txt')
            cj.load(ignore_discard=True, ignore_expires=True)
            session.cookies = cj
            for cookie in cj:
                if cookie.name == 'auth-token':
                    session.headers.update({"Authorization": f"Bearer {cookie.value}"})
            return True
        except Exception as e:
            print(f"Auth Error: {e}")
    
    # 🔥 Fallback: If cookies.txt fails, try Guest Token
    guest_token = generate_guest_bearer_token()
    if guest_token:
        session.headers.update({"Authorization": f"Bearer {guest_token}"})
        return True
    
    return False

load_auth_engine()

# ==========================================
# 4. Progress Bar Utility
# ==========================================
def get_progress_bar(current, total):
    percentage = current / total
    completed = int(percentage * 10)
    bar = "■" * completed + "□" * (10 - completed)
    return f"[{bar}] {int(percentage * 100)}%"

# ==========================================
# 5. Core Processing Engine (Multi-API)
# ==========================================
def fetch_metadata(url_or_id):
    ep_id = url_or_id.split('/')[-1].split('?')[0]
    # Check multiple API versions for stability
    for version in ['v2', 'v3', 'v4']:
        try:
            url = f"https://api.pocketfm.com/{version}/episodes/{ep_id}"
            resp = session.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                stream = data.get('audioUrl') or data.get('streamUrl') or data.get('mediaUrl')
                if not stream and 'playbackInfo' in data:
                    stream = data['playbackInfo'].get('url')
                
                if stream:
                    return {
                        'stream_url': stream,
                        'ep_title': data.get('title', f"Episode {ep_id}"),
                        'show_title': data.get('show', {}).get('title', "Pocket FM"),
                        'thumb_url': data.get('coverUrl') or data.get('show', {}).get('coverUrl')
                    }, None
        except: continue
    return None, "Episode is locked or API changed."

# ==========================================
# 6. Ultra-Fast Downloader (FFmpeg + 50MB Auto Compress)
# ==========================================
def download_audio(ep_data, chat_id, status_msg):
    unique_id = str(uuid.uuid4())[:8]
    audio_file = f"downloads/{unique_id}.mp3"
    thumb_file = f"downloads/{unique_id}.jpg"
    
    # 1. Download Thumbnail
    if ep_data.get('thumb_url'):
        try:
            r = session.get(ep_data['thumb_url'], timeout=5)
            with open(thumb_file, 'wb') as f: f.write(r.content)
        except: thumb_file = None

    # 2. Fast Download using FFmpeg
    bot.edit_message_text(f"📥 **Downloading:**\n{ep_data['ep_title']}\n{get_progress_bar(30, 100)}", chat_id, status_msg)
    
    try:
        cmd = [
            'ffmpeg', '-y', '-user_agent', MOBILE_HEADERS['User-Agent'],
            '-i', ep_data['stream_url'], '-c', 'copy', '-bsf:a', 'aac_adtstoasc', audio_file
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        
        # 🔥 Auto 50MB Compression for Telegram Limit
        if os.path.exists(audio_file):
            file_size_mb = os.path.getsize(audio_file) / (1024 * 1024)
            if file_size_mb >= 49.0:
                compressed_file = f"downloads/compressed_{unique_id}.mp3"
                compress_cmd = ['ffmpeg', '-y', '-i', audio_file, '-b:a', '64k', compressed_file]
                subprocess.run(compress_cmd, capture_output=True)
                if os.path.exists(compressed_file):
                    os.remove(audio_file)
                    audio_file = compressed_file
        
        bot.edit_message_text(f"📤 **Uploading to Telegram...**\n{get_progress_bar(80, 100)}", chat_id, status_msg)
        return audio_file, thumb_file, None
    except Exception as e:
        return None, None, str(e)

# ==========================================
# 7. Telegram Handlers
# ==========================================
def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("🔍 Download Series"), KeyboardButton("📥 Episode Link"))
    markup.add(KeyboardButton("🔄 Refresh Auth"), KeyboardButton("📊 Bot Status"))
    return markup

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "👑 **Ultimate Pocket FM Downloader**\n\n✅ Auto Guest Token & Cookie Bypass Enabled!\nSend a `/show/` or `/episode/` link.", reply_markup=main_menu())

@bot.message_handler(func=lambda m: m.text == "🔄 Refresh Auth")
def refresh(message):
    if load_auth_engine():
        bot.send_message(message.chat.id, "✅ Auth successfully refreshed from cookies.txt/Guest Token!")
    else:
        bot.send_message(message.chat.id, "❌ Authentication failed. cookies.txt or Guest Token not available.")

@bot.message_handler(func=lambda m: "pocketfm.com/episode/" in m.text)
def single_ep(message):
    url = re.search(r'(https?://[^\s]+)', message.text).group(0)
    status = bot.send_message(message.chat.id, "🔍 Searching...")
    
    ep_data, err = fetch_metadata(url)
    if err:
        bot.edit_message_text(f"❌ Error: {err}", message.chat.id, status.message_id)
        return

    file, thumb, d_err = download_audio(ep_data, message.chat.id, status.message_id)
    if not d_err:
        with open(file, 'rb') as f:
            t = open(thumb, 'rb') if thumb else None
            bot.send_audio(message.chat.id, f, title=ep_data['ep_title'], 
                           performer=ep_data['show_title'], thumb=t,
                           caption=f"✅ **Downloaded:** {ep_data['ep_title']}\n🌟 **Show:** {ep_data['show_title']}")
            if t: t.close()
    
    if os.path.exists(file): os.remove(file)
    if thumb and os.path.exists(thumb): os.remove(thumb)
    bot.delete_message(message.chat.id, status.message_id)

@bot.message_handler(func=lambda m: "pocketfm.com/show/" in m.text)
def show_handler(message):
    url = message.text.strip()
    status = bot.send_message(message.chat.id, "🔍 Fetching Series List...")
    
    try:
        res = session.get(url, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        links = soup.find_all('a', href=re.compile(r'/episode/'))
        
        episodes = []
        seen = set()
        for l in links:
            eid = l['href'].split('/')[-1]
            if eid not in seen:
                episodes.append(eid)
                seen.add(eid)
        
        if not episodes:
            bot.edit_message_text("❌ No episodes found. Is the link correct?", message.chat.id, status.message_id)
            return

        # 🔥 Fix: Store complete state (Title + List + Total)
        user_states[message.chat.id] = {
            'list': episodes,
            'title': soup.title.text,
            'total': len(episodes)
        }
        bot.send_message(message.chat.id, f"🎧 **Found {len(episodes)} Episodes.**\n\nSend the range you want to download:\nExample: `1 10` (for first 10 episodes)")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Show Error: {e}")

@bot.message_handler(func=lambda m: m.chat.id in user_states and re.match(r'^\d+\s*\d*$', m.text))
def batch_download(message):
    data = user_states.get(message.chat.id)
    nums = [int(x) for x in message.text.split()]
    start = nums[0]
    end = nums[1] if len(nums) > 1 else start
    
    selected = data['list'][start-1 : end]
    status = bot.send_message(message.chat.id, f"🚀 Preparing Batch {start} to {end}...")

    for i, eid in enumerate(selected, 1):
        bot.edit_message_text(f"⏳ **Batch Progress:** {i}/{len(selected)}", message.chat.id, status.message_id)
        ep_data, _ = fetch_metadata(eid)
        if ep_data:
            file, thumb, _ = download_audio(ep_data, message.chat.id, status.message_id)
            if file:
                with open(file, 'rb') as f:
                    t = open(thumb, 'rb') if thumb else None
                    bot.send_audio(message.chat.id, f, title=ep_data['ep_title'], performer=ep_data['show_title'], thumb=t)
                    if t: t.close()
                os.remove(file)
                if thumb: os.remove(thumb)
    
    bot.send_message(message.chat.id, "✅ Batch Download Finished!")
    del user_states[message.chat.id]

# ==========================================
# 8. Start Bot
# ==========================================
print("🚀 Ultimate Bot Started!")
bot.polling(none_stop=True, skip_pending=True)
