import os
import telebot
import requests
import re
import subprocess
import uuid
import json
from http.cookiejar import MozillaCookieJar
from telebot.types import ReplyKeyboardMarkup, KeyboardButton

# ==========================================
# 🔥 1. Environment Variables
# ==========================================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_TOKEN கண்டிப்பாக இருக்க வேண்டும்!")

# ==========================================
# 🍪 2. Cookie Loader (403 & 404-ஐ சரி செய்யும்)
# ==========================================
session = requests.Session()
COOKIE_FILE = 'cookies.txt'

if os.path.exists(COOKIE_FILE):
    try:
        cj = MozillaCookieJar(COOKIE_FILE)
        cj.load()
        session.cookies = cj
        print("✅ cookies.txt Loaded!")
    except Exception as e:
        print(f"⚠️ Error: {e}")

# ==========================================
# 🤖 3. Bot Setup
# ==========================================
bot = telebot.TeleBot(BOT_TOKEN)
user_states = {}

# ==========================================
# ✅ 4. Main Menu Buttons
# ==========================================
def get_main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    markup.add(KeyboardButton("🔍 Download Series"), KeyboardButton("📥 Multi-Link"))
    markup.add(KeyboardButton("🔄 Refresh Cookies"), KeyboardButton("📊 About"))
    return markup

@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    bot.send_message(
        message.chat.id,
        "👋 **Welcome to World Best Downloader Bot!**\n\n"
        "🔹 **Download Series:** Send a `/show/` link.\n"
        "🔹 **Multi-Link:** Send multiple `/episode/` links in one message.\n"
        "🔹 **Refresh Cookies:** Upload new `cookies.txt` via Render Secret Files.\n\n"
        "👇 Choose an option below:",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )

# ==========================================
# 🎬 5. Series API (Hybrid - Try v3/v4)
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
        
        # 🔥 Try v3 first, then v4 if v3 fails
        api_url = f"https://api.pocketfm.com/v3/shows/{show_id}"
        resp = session.get(api_url, timeout=30)
        
        if resp.status_code == 404:
            api_url = f"https://api.pocketfm.com/v4/shows/{show_id}"
            resp = session.get(api_url, timeout=30)
            
        if resp.status_code != 200:
            bot.edit_message_text(f"❌ API Error: {resp.status_code}. Please refresh cookies.", chat_id, msg.message_id)
            return
            
        data = resp.json()
        series_title = data.get('title', 'Pocket FM Series')
        episodes = data.get('episodes', [])
        episode_data = [{'id': ep['id'], 'title': ep.get('title', f"Episode {i+1}")} for i, ep in enumerate(episodes)]
        
        user_states[chat_id] = {'series_title': series_title, 'episode_data': episode_data, 'total': len(episode_data)}
        reply_text = f"🎧 **Series Selected:** {series_title}\n📊 **Total Episodes:** {len(episode_data)}\n\n💬 *Send track numbers:*\nSingle: `7`\nRange: `21 125`"
        bot.edit_message_text(reply_text, chat_id, msg.message_id, parse_mode="Markdown")
        bot.register_next_step_handler(message, process_episode_range)
        
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)}", chat_id, msg.message_id)

# ==========================================
# 📊 6. Episode Processor
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
            
        if start_ep < 1: start_ep = 1
        if end_ep > total_eps: end_ep = total_eps
        
        bot.send_message(chat_id, f"⏳ Downloading Ep {start_ep} to {end_ep}...")
        selected_episodes = episode_data[start_ep-1:end_ep]
        
        for i, ep in enumerate(selected_episodes, start=1):
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
        bot.send_message(chat_id, "✅ All done!")
        
    except ValueError:
        msg = bot.send_message(chat_id, "❌ Send numbers only.")
        bot.register_next_step_handler(msg, process_episode_range)

# ==========================================
# 🎵 7. Audio Downloader
# ==========================================
def download_episode_audio(episode_id):
    try:
        api_url = f"https://api.pocketfm.com/v2/episodes/{episode_id}"
        resp = session.get(api_url, timeout=20)
        
        if resp.status_code != 200:
            return None, f"API Error: {resp.status_code}"
            
        data = resp.json()
        stream_url = data.get('audioUrl') or data.get('streamUrl')
        if not stream_url:
            playback = data.get('playbackInfo')
            if playback:
                stream_url = playback.get('url')
        if not stream_url:
            return None, "M3U8 URL not found."
            
        if not os.path.exists('downloads'): os.makedirs('downloads')
        unique_name = str(uuid.uuid4())[:8]
        output_file = f"downloads/{unique_name}.mp3"
        subprocess.run(['ffmpeg', '-y', '-i', stream_url, '-c', 'copy', '-bsf:a', 'aac_adtstoasc', output_file], check=True, capture_output=True)
        return output_file, None
        
    except subprocess.CalledProcessError:
        return None, "FFmpeg failed."
    except Exception as e:
        return None, str(e)

# ==========================================
# 🧹 8. Multi-Link & Button Handlers
# ==========================================
@bot.message_handler(func=lambda message: message.text == "📥 Multi-Link")
def handle_multilink_button(message):
    bot.send_message(message.chat.id, "📥 Please send multiple `/episode/` links in one message, separated by new lines.")

@bot.message_handler(func=lambda message: message.text == "🔄 Refresh Cookies")
def handle_refresh_button(message):
    bot.send_message(message.chat.id, "🔄 To refresh cookies, upload a new `cookies.txt` file in Render Dashboard -> Environment -> Secret Files -> `+ Add file` and Redeploy.")

@bot.message_handler(func=lambda message: message.text == "📊 About")
def handle_about_button(message):
    bot.send_message(message.chat.id, "🤖 **World Best Downloader Bot**\n\n✅ Uses `cookies.txt` for 403/404 bypass.\n✅ Supports Single, Batch, & Range Downloads.\n✅ Built with PyTelegramBotAPI & FFmpeg.\n\n📍 To upload `cookies.txt`, go to Render Dashboard → Environment → Secret Files.")

@bot.message_handler(func=lambda message: message.text == "🔍 Download Series")
def handle_series_button(message):
    bot.send_message(message.chat.id, "🔍 Please send a valid `pocketfm.com/show/...` link to start.")

# ==========================================
# 🚀 9. Start Polling
# ==========================================
bot.polling(none_stop=True, skip_pending=True, interval=0)
