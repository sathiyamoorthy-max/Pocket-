import os
import telebot
import requests
import re
import subprocess
import uuid
import json

# ==========================================
# 🔥 1. Environment Variables (Render-ல் இவற்றைச் சேர்க்கவும்)
# ==========================================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")

# 📌 இங்கே உங்கள் புதிய Token-ஐ நேரடியாக ஒட்டவும் (அல்லது Render-ல் Environment Variable-ல் வைக்கவும்)
POCKET_TOKEN = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJjYXRlZ29yeSI6InJlZnJlc2giLCJkZXZpY2VfaWQiOiI5ODAxNjEwMGUzMjQxMTAzIiwiZXhwaXJ5IjoxNzk5NDkwMTgwLCJpYXQiOjE3ODM5MzgxODAsImxvY2FsZSI6IklOIiwicGxhdGZvcm0iOiJhbmRyb2lkIiwidGVuYW50IjoicG9ja2V0X2ZtIiwidWlkIjoiZTgxZTEyNmEyMzc3M2U3YTJkYmY4ZmVkM2VhY2VhZDkwMmE2ODg5OCIsInZlcnNpb24iOiJ2MiJ9.XXg9PgxSkUPXOxHXKeX7tddK0595zOfK1KzzFpFa7YU"

if not BOT_TOKEN or not POCKET_TOKEN:
    raise ValueError("TELEGRAM_TOKEN மற்றும் POCKET_TOKEN கண்டிப்பாக இருக்க வேண்டும்!")

# ==========================================
# ⚙️ 2. Mobile API Headers
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
user_states = {}

# ==========================================
# 🎬 4. Series API (v2)
# ==========================================
@bot.message_handler(regexp=r"pocketfm\.com/show/")
def handle_show_link(message):
    chat_id = message.chat.id
    url = message.text.strip()
    bot.reply_to(message, "🔍 v2 API மூலம் Series விவரங்களைப் பெறுகிறேன்...")
    
    try:
        match = re.search(r'/show/([a-zA-Z0-9_-]+)', url)
        if not match:
            bot.send_message(chat_id, "❌ தவறான Show லிங்க்.")
            return
            
        show_id = match.group(1).split('?')[0]
        api_url = f"https://api.pocketfm.com/v2/shows/{show_id}"
        resp = requests.get(api_url, headers=HEADERS, timeout=30)
        
        if resp.status_code == 401 or resp.status_code == 403:
            bot.send_message(chat_id, "❌ Token காலாவதியாகிவிட்டது. புதிய Token-ஐ DevTools-ல் இருந்து எடுக்கவும்.")
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
# 📊 5. Episode Range
# ==========================================
def process_episode_range(message):
    chat_id = message.chat.id
    text = message.text.strip()
    
    if chat_id not in user_states:
        bot.send_message(chat_id, "❌ Session முடிந்தது.")
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
                    bot.send_audio(chat_id, f, title=f"Ep {start_ep + i - 1} - {ep['title']}", performer="Pocket FM")
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
        api_url = f"https://api.pocketfm.com/v2/episodes/{episode_id}"
        resp = requests.get(api_url, headers=HEADERS, timeout=20)
        
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
    bot.reply_to(message, "👋 **வணக்கம்!** இது v2 API உடனான Ultimate Pocket FM Bot.")

print("🤖 Ultimate Pocket FM Bot Started...")
bot.polling(none_stop=True, skip_pending=True, interval=0)
