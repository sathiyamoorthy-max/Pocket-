import os
import telebot
import requests
import re
import subprocess
import uuid
import threading
from http.cookiejar import MozillaCookieJar
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from flask import Flask

# ==========================================
# 🔥 1. Environment Variables
# ==========================================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_TOKEN கண்டிப்பாக இருக்க வேண்டும்!")

# ==========================================
# 🌐 2. Render/Koyeb 24/7 Dummy Web Server
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Universe Coder Pocket FM Bot is ALIVE 24/7! 🚀"

def run_web_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# பின்னணியில் வெப் சர்வரை ஓடவிடுகிறோம்
threading.Thread(target=run_web_server, daemon=True).start()

# ==========================================
# 🍪 3. Cookie Loader
# ==========================================
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    "Accept": "application/json"
})

COOKIE_FILE = 'cookies.txt'
if os.path.exists(COOKIE_FILE):
    try:
        cj = MozillaCookieJar(COOKIE_FILE)
        cj.load()
        session.cookies = cj
        print("✅ cookies.txt Loaded Successfully!")
    except Exception as e:
        print(f"⚠️ Cookie Error: {e}")

# ==========================================
# 🤖 4. Bot Setup & Menu
# ==========================================
bot = telebot.TeleBot(BOT_TOKEN)
user_states = {}

def get_main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    markup.add(KeyboardButton("🔍 Download Series"), KeyboardButton("📥 Single/Multi Episode"))
    markup.add(KeyboardButton("🔄 Refresh Cookies"), KeyboardButton("📊 About"))
    return markup

@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    bot.send_message(
        message.chat.id,
        "👋 **Welcome to World Best Downloader Bot!**\n\n"
        "🔹 **Series Link:** Send any `/show/` link.\n"
        "🔹 **Episode Link:** Send `/episode/` link directly.\n\n"
        "👇 Choose an option below:",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )

# ==========================================
# 🎬 5. Show Link Handler
# ==========================================
@bot.message_handler(regexp=r"pocketfm\.com/show/")
def handle_show_link(message):
    chat_id = message.chat.id
    url = message.text.strip()
    
    msg = bot.send_message(chat_id, "🔍 Fetching Series details...")
    
    try:
        match = re.search(r'/show/([a-zA-Z0-9_-]+)', url)
        if not match:
            bot.edit_message_text("❌ Invalid Show link.", chat_id, msg.message_id)
            return
            
        show_id = match.group(1).split('?')[0]
        
        # Try v3 first, then v4
        api_url = f"https://api.pocketfm.com/v3/shows/{show_id}"
        resp = session.get(api_url, timeout=30)
        
        if resp.status_code == 404:
            api_url = f"https://api.pocketfm.com/v4/shows/{show_id}"
            resp = session.get(api_url, timeout=30)
            
        if resp.status_code != 200:
            bot.edit_message_text(f"❌ API Error: {resp.status_code}. Please update cookies.txt.", chat_id, msg.message_id)
            return
            
        data = resp.json()
        series_title = data.get('title', 'Pocket FM Series')
        episodes = data.get('episodes', [])
        episode_data = [{'id': ep['id'], 'title': ep.get('title', f"Episode {i+1}")} for i, ep in enumerate(episodes)]
        
        user_states[chat_id] = {'series_title': series_title, 'episode_data': episode_data, 'total': len(episode_data)}
        reply_text = f"🎧 **Series Selected:** {series_title}\n📊 **Total Episodes:** {len(episode_data)}\n\n💬 *Send track numbers:*\nSingle: `7`\nRange: `1 15`"
        bot.edit_message_text(reply_text, chat_id, msg.message_id, parse_mode="Markdown")
        bot.register_next_step_handler(message, process_episode_range)
        
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)}", chat_id, msg.message_id)

# ==========================================
# 🎵 6. Direct Single Episode Link Handler
# ==========================================
@bot.message_handler(regexp=r"pocketfm\.com/episode/")
def handle_single_episode(message):
    chat_id = message.chat.id
    url = message.text.strip()
    
    msg = bot.send_message(chat_id, "⏳ Processing Episode Link...")
    
    try:
        match = re.search(r'/episode/([a-zA-Z0-9_-]+)', url)
        if not match:
            bot.edit_message_text("❌ Invalid Episode link.", chat_id, msg.message_id)
            return
            
        ep_id = match.group(1).split('?')[0]
        bot.edit_message_text("⬇️ Downloading Audio...", chat_id, msg.message_id)
        
        audio_file, error = download_episode_audio(ep_id)
        if error:
            bot.edit_message_text(f"❌ Error: {error}", chat_id, msg.message_id)
            return
            
        bot.edit_message_text("⬆️ Uploading to Telegram...", chat_id, msg.message_id)
        with open(audio_file, 'rb') as f:
            bot.send_audio(chat_id, f, title=f"Episode - {ep_id}", performer="Pocket FM")
            
        if os.path.exists(audio_file):
            os.remove(audio_file)
            
        bot.delete_message(chat_id, msg.message_id)
        
    except Exception as e:
        bot.edit_message_text(f"❌ Server Error: {str(e)}", chat_id, msg.message_id)

# ==========================================
# 📊 7. Episode Range Processor
# ==========================================
def process_episode_range(message):
    chat_id = message.chat.id
    text = message.text.strip()
    
    if chat_id not in user_states:
        bot.send_message(chat_id, "❌ Session expired. Send the Show link again.")
        return
        
    data = user_states[chat_id]
    total_eps = data['total']
    episode_data = data['episode_data']
    
    try:
        numbers = text.split()
        if len(numbers) == 1:
            start_ep = end_ep = int(numbers[0])
        elif len(numbers) == 2:
            start_ep, end_ep = int(numbers[0]), int(numbers[1])
        else:
            msg = bot.send_message(chat_id, "❌ Invalid format. Use `7` or `1 15`")
            bot.register_next_step_handler(msg, process_episode_range)
            return
            
        start_ep = max(1, start_ep)
        end_ep = min(total_eps, end_ep)
        
        total_to_download = (end_ep - start_ep) + 1
        prog_msg = bot.send_message(chat_id, f"⏳ 0/{total_to_download} Downloading...")
        selected_episodes = episode_data[start_ep-1:end_ep]
        
        for i, ep in enumerate(selected_episodes, start=1):
            bot.edit_message_text(f"⏳ Downloading {i}/{total_to_download}: Ep {start_ep + i - 1}", chat_id, prog_msg.message_id)
            
            audio_file, error = download_episode_audio(ep['id'])
            if error:
                bot.send_message(chat_id, f"⚠️ Ep {start_ep + i - 1} failed: {error}")
            else:
                with open(audio_file, 'rb') as f:
                    bot.send_audio(
                        chat_id, 
                        f, 
                        title=f"Ep {start_ep + i - 1} - {ep['title']}",
                        performer=data['series_title']
                    )
                if os.path.exists(audio_file):
                    os.remove(audio_file)
                    
        del user_states[chat_id]
        bot.edit_message_text("✅ All episodes uploaded successfully!", chat_id, prog_msg.message_id)
        
    except ValueError:
        msg = bot.send_message(chat_id, "❌ Send numbers only.")
        bot.register_next_step_handler(msg, process_episode_range)

# ==========================================
# 🎧 8. Audio Downloader Core (FFmpeg)
# ==========================================
def download_episode_audio(episode_id):
    try:
        api_url = f"https://api.pocketfm.com/v2/episodes/{episode_id}"
        resp = session.get(api_url, timeout=20)
        
        if resp.status_code != 200:
            return None, f"API Error: {resp.status_code}"
            
        data = resp.json()
        stream_url = data.get('audioUrl') or data.get('streamUrl')
        if not stream_url and data.get('playbackInfo'):
            stream_url = data['playbackInfo'].get('url')
            
        if not stream_url:
            return None, "M3U8 Stream URL not found."
            
        if not os.path.exists('downloads'): 
            os.makedirs('downloads')
            
        unique_name = str(uuid.uuid4())[:8]
        output_file = f"downloads/{unique_name}.mp3"
        
        cmd = ['ffmpeg', '-y', '-i', stream_url, '-c', 'copy', '-bsf:a', 'aac_adtstoasc', output_file]
        subprocess.run(cmd, check=True, capture_output=True)
        return output_file, None
        
    except subprocess.CalledProcessError:
        return None, "FFmpeg processing failed."
    except Exception as e:
        return None, str(e)

# ==========================================
# 🔘 9. Menu Buttons Handlers
# ==========================================
@bot.message_handler(func=lambda message: message.text == "📥 Single/Multi Episode")
def handle_single_button(message):
    bot.send_message(message.chat.id, "📥 Send any single or multiple `/episode/` links.")

@bot.message_handler(func=lambda message: message.text == "🔄 Refresh Cookies")
def handle_refresh_button(message):
    bot.send_message(message.chat.id, "🔄 To refresh cookies, update the `cookies.txt` file in your GitHub repository and re-deploy.")

@bot.message_handler(func=lambda message: message.text == "📊 About")
def handle_about_button(message):
    bot.send_message(message.chat.id, "🤖 **Universe Coder Downloader Bot**\n\n✅ 24/7 Online Web Service.\n✅ Uses `cookies.txt` bypass.\n✅ Powered by Python & FFmpeg.")

@bot.message_handler(func=lambda message: message.text == "🔍 Download Series")
def handle_series_button(message):
    bot.send_message(message.chat.id, "🔍 Send a valid `pocketfm.com/show/...` link to start.")

# ==========================================
# 🚀 10. Start Polling
# ==========================================
print("🤖 Universe Coder Bot Started...")
bot.polling(none_stop=True, skip_pending=True)
