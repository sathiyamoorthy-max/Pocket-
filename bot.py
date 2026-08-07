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
POCKET_TOKEN = os.getenv("POCKET_TOKEN")

# API_ID, API_HASH (தேவையில்லை என்றாலும் வைத்துள்ளேன்)
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")

if not BOT_TOKEN or not POCKET_TOKEN:
    raise ValueError("TELEGRAM_TOKEN மற்றும் POCKET_TOKEN கண்டிப்பாக Environment Variables-ல் இருக்க வேண்டும்!")

# ==========================================
# ⚙️ 2. Mobile API Headers (Pocket FM-யின் உண்மையான API ஹெடர்ஸ்)
# ==========================================
HEADERS = {
    "Authorization": POCKET_TOKEN,
    "X-Device-Id": "OPPO_CPH2219",
    "User-Agent": "PocketFM/6.5.0 (Android; 13; SM-G991B)",
    "Content-Type": "application/json",
    "Accept": "application/json"
}

# ==========================================
# 🤖 3. Bot Setup
# ==========================================
bot = telebot.TeleBot(BOT_TOKEN)
user_states = {}  # பயனர் தரவுகளை தற்காலிகமாக சேமிக்க

# ==========================================
# 🎬 4. Series லிங்கை கையாளும் பகுதி
# ==========================================
@bot.message_handler(regexp=r"pocketfm\.com/show/")
def handle_show_link(message):
    chat_id = message.chat.id
    url = message.text.strip()
    
    bot.reply_to(message, "🔍 Series விவரங்களை API மூலம் பெறுகிறேன்...")
    
    try:
        match = re.search(r'/show/([a-zA-Z0-9_-]+)', url)
        if not match:
            bot.send_message(chat_id, "❌ தவறான Show லிங்க்.")
            return
            
        show_id = match.group(1).split('?')[0]
        
        # 🔥 Series-ன் Episode List-ஐ பெறுதல்
        api_url = f"https://api.pocketfm.com/api/v1/show/{show_id}"
        resp = requests.get(api_url, headers=HEADERS, timeout=30)
        
        if resp.status_code == 401 or resp.status_code == 403:
            bot.send_message(chat_id, "❌ Token காலாவதியாகிவிட்டது. புதிய Token-ஐ DevTools-ல் இருந்து எடுத்து Environment Variables-ல் மாற்றவும்.")
            return
        if resp.status_code != 200:
            bot.send_message(chat_id, f"❌ API Error: {resp.status_code}")
            return
            
        data = resp.json()
        series_title = data.get('title', 'Pocket FM Series')
        episodes = data.get('episodes', [])
        
        episode_data = [
            {'id': ep['id'], 'title': ep.get('title', f"Episode {i+1}")} 
            for i, ep in enumerate(episodes)
        ]
        
        user_states[chat_id] = {
            'show_id': show_id,
            'series_title': series_title,
            'episode_data': episode_data,
            'total': len(episode_data)
        }
        
        reply_text = f"🎧 **Series Selected:** {series_title}\n📊 **Total Episodes:** {len(episode_data)}\n\n💬 *Send track number(s):*\nSingle: `7`\nRange: `21 125`"
        msg = bot.send_message(chat_id, reply_text, parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_episode_range)
        
    except Exception as e:
        bot.send_message(chat_id, f"❌ பிழை: {str(e)}")

# ==========================================
# 📊 5. Episode Range-ஐ Smart ஆக பிரிக்கும் பகுதி
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
            msg = bot.send_message(chat_id, "❌ தவறான வடிவம். உதாரணம்: `7` அல்லது `1 15`")
            bot.register_next_step_handler(msg, process_episode_range)
            return
            
        if start_ep < 1: start_ep = 1
        if end_ep > total_eps: end_ep = total_eps
        if start_ep > end_ep:
            bot.send_message(chat_id, "❌ தொடக்க எண் இறுதி எண்ணை விட அதிகமாக இருக்கக்கூடாது.")
            return
            
        bot.send_message(chat_id, f"⏳ Downloading Ep {start_ep} to {end_ep}...")
        selected_episodes = episode_data[start_ep-1:end_ep]
        
        for i, ep in enumerate(selected_episodes, start=1):
            ep_id = ep['id']
            ep_title = ep['title']
            
            audio_file, error = download_episode_audio(ep_id)
            if error:
                bot.send_message(chat_id, f"⚠️ Ep {start_ep + i - 1} failed: {error}")
            else:
                with open(audio_file, 'rb') as f:
                    bot.send_audio(
                        chat_id, 
                        f, 
                        title=f"Ep {start_ep + i - 1} - {ep_title}",
                        performer="Pocket FM"
                    )
                if os.path.exists(audio_file):
                    os.remove(audio_file)
                    
        del user_states[chat_id]
        bot.send_message(chat_id, "✅ All requested episodes sent successfully!")
        
    except ValueError:
        msg = bot.send_message(chat_id, "❌ எண்களை மட்டும் அனுப்பவும் (எ.கா: `1 15`).")
        bot.register_next_step_handler(msg, process_episode_range)

# ==========================================
# 🎵 6. Core Downloader (M3U8 Fetch + FFmpeg)
# ==========================================
def download_episode_audio(episode_id):
    try:
        api_url = f"https://api.pocketfm.com/api/v1/episode/{episode_id}"
        resp = requests.get(api_url, headers=HEADERS, timeout=20)
        
        if resp.status_code != 200:
            return None, f"API Error: {resp.status_code}"
            
        data = resp.json()
        stream_url = data.get('audioUrl') or data.get('streamUrl')
        playback = data.get('playbackInfo')
        if not stream_url and playback:
            stream_url = playback.get('url')
            
        if not stream_url:
            return None, "M3U8 ஸ்ட்ரீம் URL கிடைக்கவில்லை."
            
        # FFmpeg Download & Convert
        unique_name = str(uuid.uuid4())[:8]
        output_file = f"downloads/{unique_name}.mp3"
        if not os.path.exists('downloads'):
            os.makedirs('downloads')
            
        cmd = ['ffmpeg', '-y', '-i', stream_url, '-c', 'copy', '-bsf:a', 'aac_adtstoasc', output_file]
        subprocess.run(cmd, check=True, capture_output=True)
        return output_file, None
        
    except subprocess.CalledProcessError:
        return None, "FFmpeg conversion failed. FFmpeg installed?"
    except Exception as e:
        return None, str(e)

# ==========================================
# 🚀 7. Start Command & Polling
# ==========================================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(
        message,
        "👋 **வணக்கம்!**\n\n"
        "இது **Ultimate Pocket FM Downloader Bot**.\n"
        "Pocket FM-ன் Series லிங்கை (URL) அனுப்பினால், எந்த 403 பிழையும் இல்லாமல் டவுன்லோட் செய்யும்!"
    )

print("🤖 Ultimate Pocket FM Downloader Started...")

# 🔥 இந்த ஒரு வரிதான் 409 Conflict பிரச்சனையை சரி செய்யும்!
bot.polling(none_stop=True, skip_pending=True)
