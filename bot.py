import os
import telebot
import requests
import re
import subprocess
import uuid
import threading
from flask import Flask

# ==========================================
# 1. Environment Variables 
# ==========================================
BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
POCKET_TOKEN = os.environ.get("POCKET_TOKEN")

# ==========================================
# 2. Render Dummy Web Server
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Universe Coder Pocket FM Bot is ALIVE! 🚀"

def run_web_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_web_server, daemon=True).start()

# ==========================================
# 3. Mobile API Headers
# ==========================================
HEADERS = {
    "Authorization": POCKET_TOKEN,
    "X-Device-Id": "OPPO_CPH2219",
    "User-Agent": "PocketFM/6.5.0 (Android; 13; SM-G991B)",
    "Content-Type": "application/json",
    "Accept": "application/json"
}

bot = telebot.TeleBot(BOT_TOKEN)
user_states = {}

# ==========================================
# 4. 🔥 AUTO NUMERIC ID CONVERTER (The Magic Fix)
# ==========================================
def get_numeric_id(url, is_episode=False):
    try:
        # சாதாரண பிரவுசர் போல சென்று வெப்சைட்டிலிருந்து ID-ஐ திருடுகிறோம் 
        web_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"}
        html = requests.get(url, headers=web_headers, timeout=15).text
        
        if is_episode:
            match = re.search(r'"episode_?id"\s*:\s*"?(\d+)"?', html, re.IGNORECASE)
        else:
            match = re.search(r'"show_?id"\s*:\s*"?(\d+)"?', html, re.IGNORECASE)
            
        if match:
            return match.group(1)
        return None
    except Exception as e:
        print(f"Scraping Error: {e}")
        return None

# ==========================================
# 5. UNIVERSE LINK HANDLER
# ==========================================
@bot.message_handler(func=lambda message: "pocketfm.com" in message.text)
def handle_universal_link(message):
    chat_id = message.chat.id
    url = message.text.strip()
    
    status_msg = bot.reply_to(message, "🔍 யுனிவர்சல் கோடர்: லிங்கைச் சரிபார்க்கிறேன்...")
    
    try:
        clean_url = url.split('?')[0].strip('/')
        extracted_id = clean_url.split('/')[-1]
        is_episode = "/episode/" in clean_url
        
        # 🟢 ID எழுத்துக்களாக இருந்தால் (Hash), அதை எண்களாக மாற்றுகிறோம்
        if not extracted_id.isdigit():
            bot.edit_message_text("⚙️ Web Link கண்டறியப்பட்டது! Numeric ID-ஐ எடுக்கிறேன்...", chat_id, status_msg.message_id)
            numeric_id = get_numeric_id(url, is_episode)
            
            if not numeric_id:
                bot.edit_message_text("❌ பிழை: இந்த லிங்கிலிருந்து Numeric ID-ஐ எடுக்க முடியவில்லை.", chat_id, status_msg.message_id)
                return
            extracted_id = numeric_id
            
        # இப்போது extracted_id கண்டிப்பாக ஒரு எண்ணாக (Numeric) இருக்கும்!
        
        # 🟢 DIRECT EPISODE LINK
        if is_episode:
            bot.edit_message_text(f"⏳ எபிசோடு ID [{extracted_id}] டவுன்லோட் ஆகிறது...", chat_id, status_msg.message_id)
            audio_file, error = download_episode_audio(extracted_id)
            
            if error:
                bot.edit_message_text(f"❌ பிழை: {error}", chat_id, status_msg.message_id)
                return
                
            bot.edit_message_text("⬆️ அப்லோட் ஆகிறது...", chat_id, status_msg.message_id)
            try:
                with open(audio_file, 'rb') as f:
                    bot.send_audio(chat_id, f, title=f"Episode - {extracted_id}", performer="Pocket FM")
            finally:
                if audio_file and os.path.exists(audio_file):
                    os.remove(audio_file)
            bot.delete_message(chat_id, status_msg.message_id)
            
        # 🔵 FULL SHOW LINK
        elif "/show/" in clean_url:
            api_url = f"https://api.pocketfm.com/api/v1/show/{extracted_id}"
            resp = requests.get(api_url, headers=HEADERS, timeout=30)
            
            if resp.status_code in [401, 403]:
                bot.edit_message_text("❌ Token Error: புதிய Bearer Token-ஐ Render-ல் மாற்றவும்.", chat_id, status_msg.message_id)
                return
            if resp.status_code != 200:
                bot.edit_message_text(f"❌ API Error: {resp.status_code}", chat_id, status_msg.message_id)
                return
                
            data = resp.json()
            series_title = data.get('title', 'Pocket FM Series')
            episodes = data.get('episodes', [])
            episode_data = [{'id': ep['id'], 'title': ep.get('title', f"Episode {i+1}")} for i, ep in enumerate(episodes)]
            
            user_states[chat_id] = {
                'show_id': extracted_id,
                'series_title': series_title,
                'episode_data': episode_data,
                'total': len(episode_data)
            }
            
            reply_text = f"🎧 **Series:** {series_title}\n📊 **Total Episodes:** {len(episode_data)}\n\n💬 எபிசோடு எண்களை அனுப்பவும்.\n(உதாரணம்: `7` அல்லது `1 15`)"
            bot.edit_message_text(reply_text, chat_id, status_msg.message_id, parse_mode="Markdown")
            bot.register_next_step_handler(status_msg, process_episode_range)
            
    except Exception as e:
        bot.edit_message_text(f"❌ சர்வர் பிழை: {str(e)}", chat_id, status_msg.message_id)

# ==========================================
# 6. Range Processing (எபிசோடு எண்களை டவுன்லோட் செய்ய)
# ==========================================
def process_episode_range(message):
    chat_id = message.chat.id
    text = message.text.strip()
    
    if chat_id not in user_states:
        bot.send_message(chat_id, "❌ Session முடிந்தது. மீண்டும் லிங்கை அனுப்பவும்.")
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
            
        start_ep = max(1, start_ep)
        end_ep = min(total_eps, end_ep)
        
        if start_ep > end_ep:
            bot.send_message(chat_id, "❌ தொடக்க எண் இறுதி எண்ணை விட அதிகமாக இருக்கக்கூடாது.")
            return
            
        total_to_download = (end_ep - start_ep) + 1
        progress_msg = bot.send_message(chat_id, f"⏳ 0/{total_to_download} டவுன்லோட் ஆகிறது...")
        selected_episodes = episode_data[start_ep-1:end_ep]
        
        for i, ep in enumerate(selected_episodes, start=1):
            ep_id = ep['id']
            ep_title = ep['title']
            
            bot.edit_message_text(f"⏳ டவுன்லோட் ஆகிறது: {i}/{total_to_download}\n(Ep {start_ep + i - 1}: {ep_title})", chat_id, progress_msg.message_id)
            
            audio_file, error = download_episode_audio(ep_id)
            if error:
                bot.send_message(chat_id, f"⚠️ Ep {start_ep + i - 1} failed: {error}")
            else:
                try:
                    with open(audio_file, 'rb') as f:
                        bot.send_audio(chat_id, f, title=f"Ep {start_ep + i - 1} - {ep_title}", performer="Pocket FM")
                finally:
                    if audio_file and os.path.exists(audio_file):
                        os.remove(audio_file)
                        
        del user_states[chat_id]
        bot.edit_message_text("✅ அனைத்து எபிசோடுகளும் வெற்றிகரமாக அனுப்பப்பட்டன!", chat_id, progress_msg.message_id)
        
    except ValueError:
        msg = bot.send_message(chat_id, "❌ எண்களை மட்டும் அனுப்பவும் (எ.கா: `1 15`).")
        bot.register_next_step_handler(msg, process_episode_range)

# ==========================================
# 7. Core Downloader (FFmpeg)
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
            return None, "M3U8 URL கிடைக்கவில்லை."
            
        unique_name = str(uuid.uuid4())[:8]
        output_file = f"downloads/{unique_name}.mp3"
        
        if not os.path.exists('downloads'):
            os.makedirs('downloads')
            
        cmd = ['ffmpeg', '-y', '-i', stream_url, '-c', 'copy', '-bsf:a', 'aac_adtstoasc', output_file]
        subprocess.run(cmd, check=True, capture_output=True)
        return output_file, None
        
    except Exception as e:
        return None, str(e)

# ==========================================
# 8. Bot Start
# ==========================================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "👋 **வணக்கம்!**\n\nநான் Universe Coder Pocket FM Bot.\nகதையின் முழு லிங்க் அல்லது சிங்கிள் எபிசோடு லிங்கை அனுப்புங்கள்.")

print("🤖 Universe Coder Bot Started...")
bot.polling(none_stop=True, skip_pending=True)
