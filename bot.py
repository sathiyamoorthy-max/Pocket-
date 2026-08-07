import os
import telebot
import requests
import re
import subprocess
import uuid
import json
from http.cookiejar import MozillaCookieJar

# ==========================================
# 🔥 1. Environment Variables (Render-ல் இவற்றைச் சேர்க்கவும்)
# ==========================================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_TOKEN கண்டிப்பாக Environment Variables-ல் இருக்க வேண்டும்!")

# ==========================================
# 🍪 2. Cookie Session Loader (இதுதான் 403/404-ஐ சரி செய்யும்!)
# ==========================================
session = requests.Session()
COOKIE_FILE = 'cookies.txt'

if os.path.exists(COOKIE_FILE):
    try:
        cj = MozillaCookieJar(COOKIE_FILE)
        cj.load()
        session.cookies = cj
        print("✅ cookies.txt loaded successfully! No more 403/404 errors!")
    except Exception as e:
        print(f"⚠️ cookies.txt load error: {e}")
else:
    print("⚠️ cookies.txt file not found. Place it in the root folder.")

# ==========================================
# 🤖 3. Bot Setup
# ==========================================
bot = telebot.TeleBot(BOT_TOKEN)
user_states = {}  # பயனர் தரவுகளை தற்காலிகமாக சேமிக்க

# ==========================================
# 🎬 4. Series API (v3)
# ==========================================
@bot.message_handler(regexp=r"pocketfm\.com/show/")
def handle_show_link(message):
    chat_id = message.chat.id
    url = message.text.strip()
    
    bot.reply_to(message, "🔍 Cookie மூலம் Series விவரங்களைப் பெறுகிறேன்...")
    
    try:
        match = re.search(r'/show/([a-zA-Z0-9_-]+)', url)
        if not match:
            bot.send_message(chat_id, "❌ தவறான Show லிங்க்.")
            return
            
        show_id = match.group(1).split('?')[0]
        
        # 🔥 v3 Series API
        api_url = f"https://api.pocketfm.com/v3/shows/{show_id}"
        resp = session.get(api_url, timeout=30)  # Cookie தானாகவே போகும்
        
        if resp.status_code == 401 or resp.status_code == 403:
            bot.send_message(chat_id, "❌ Cookies காலாவதியாகிவிட்டது. புதிய cookies.txt-ஐ Kiwi-யில் இருந்து எடுத்து Render-ல் அப்லோட் செய்யவும்.")
            return
        if resp.status_code != 200:
            bot.send_message(chat_id, f"❌ API Error: {resp.status_code}")
            return
            
        data = resp.json()
        series_title = data.get('title', 'Pocket FM Series')
        episodes = data.get('episodes', [])
        episode_data = [{'id': ep['id'], 'title': ep.get('title', f"Episode {i+1}")} for i, ep in enumerate(episodes)]
        
        user_states[chat_id] = {'series_title': series_title, 'episode_data': episode_data, 'total': len(episode_data)}
        reply_text = f"🎧 **Series Selected:** {series_title}\n📊 **Total Episodes:** {len(episode_data)}\n\n💬 *Send track number(s):*\nSingle: `7`\nRange: `21 125`"
        msg = bot.send_message(chat_id, reply_text, parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_episode_range)
        
    except Exception as e:
        bot.send_message(chat_id, f"❌ பிழை: {str(e)}")

# ==========================================
# 📊 5. Episode Range (Batch & Single)
# ==========================================
def process_episode_range(message):
    chat_id = message.chat.id
    text = message.text.strip()
    
    if chat_id not in user_states:
        bot.send_message(chat_id, "❌ Session முடிந்தது. மீண்டும் Show லிங்கை அனுப்பவும்.")
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
            msg = bot.send_message(chat_id, "❌ தவறான வடிவம். `7` அல்லது `1 15`")
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
        bot.send_message(chat_id, "✅ முடிந்தது!")
        
    except ValueError:
        msg = bot.send_message(chat_id, "❌ எண்களை மட்டும் அனுப்பவும்.")
        bot.register_next_step_handler(msg, process_episode_range)

# ==========================================
# 🎵 6. Audio Downloader (v2)
# ==========================================
def download_episode_audio(episode_id):
    try:
        # 🔥 Episode-க்கு v2 API
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
            return None, "M3U8 ஸ்ட்ரீம் URL கிடைக்கவில்லை."
            
        if not os.path.exists('downloads'): os.makedirs('downloads')
        unique_name = str(uuid.uuid4())[:8]
        output_file = f"downloads/{unique_name}.mp3"
        subprocess.run(['ffmpeg', '-y', '-i', stream_url, '-c', 'copy', '-bsf:a', 'aac_adtstoasc', output_file], check=True, capture_output=True)
        return output_file, None
        
    except subprocess.CalledProcessError:
        return None, "FFmpeg conversion failed."
    except Exception as e:
        return None, str(e)

# ==========================================
# 🚀 7. Start Command & Polling
# ==========================================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(
        message,
        "👋 **வணக்கம்!** இது Cookies-Based Ultimate Pocket FM Bot.\n\n"
        "✅ Single Episode, Batch Download, Range Download எல்லாம் 100% வேலை செய்யும்!\n\n"
        "⚠️ `cookies.txt` காலாவதியானால், Kiwi Browser-ல் இருந்து புதியதை எடுத்து Render-ல் அப்லோட் செய்யவும்."
    )

print("🤖 Ultimate Cookie-Based Bot Started...")

# 🔥 409 Conflict பிரச்சனையைச் சரி செய்யும் வரி
bot.polling(none_stop=True, skip_pending=True, interval=0)
