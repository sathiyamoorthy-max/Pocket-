import os
import re
import uuid
import threading
import subprocess
import requests
import m3u8
from bs4 import BeautifulSoup
from http.cookiejar import MozillaCookieJar
from flask import Flask
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton

# ==========================================
# 1. Environment Variables Setup
# ==========================================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_TOKEN is missing!")

bot = telebot.TeleBot(BOT_TOKEN)
user_states = {}

# ==========================================
# 2. Render / Koyeb 24/7 Web Server
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "🚀 World Best Pocket FM Downloader Bot is ALIVE 24/7!"

def run_web_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_web_server, daemon=True).start()

# ==========================================
# 3. Session & Cookie Loader
# ==========================================
session = requests.Session()
session.headers.update({
    "User-Agent": "PocketFM/6.5.0 (Android; 13; SM-G991B)",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9"
})

COOKIE_FILE = 'cookies.txt'
if os.path.exists(COOKIE_FILE):
    try:
        cj = MozillaCookieJar(COOKIE_FILE)
        cj.load()
        session.cookies = cj
        print("✅ cookies.txt Loaded Successfully!")
    except Exception as e:
        print(f"⚠️ Cookie Warning: {e}")

# ==========================================
# 4. Interactive UI Keyboards
# ==========================================
def get_main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    markup.add(KeyboardButton("🔍 Download Series"), KeyboardButton("📥 Single/Multi Episode"))
    markup.add(KeyboardButton("🔄 Refresh Cookies"), KeyboardButton("📊 About & Status"))
    return markup

@bot.message_handler(commands=['start', 'menu', 'help'])
def send_welcome(message):
    bot.send_message(
        message.chat.id,
        "👑 **Welcome to World Best Pocket FM Downloader Bot!**\n\n"
        "✨ **Features Included:**\n"
        "🟢 Multi-API & Scraper Bypass (404 Error Fix)\n"
        "🖼️ HD Cover Art / Thumbnail Support\n"
        "🎶 Track Title & Artist Metadata Tagging\n"
        "🎧 Single, Multi-Link & Full Series Range Download\n"
        "⚡ Powered by FFmpeg, m3u8 & yt-dlp Backend\n\n"
        "👇 *Choose an option below or send any Pocket FM link:*",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )

# ==========================================
# 5. Core Multi-API & Metadata Engine
# ==========================================
def fetch_episode_metadata(episode_id_or_url):
    ep_id = episode_id_or_url.split('/')[-1].split('?')[0]
    
    endpoints = [
        f"https://api.pocketfm.com/v2/episodes/{ep_id}",
        f"https://api.pocketfm.com/v3/episodes/{ep_id}",
        f"https://api.pocketfm.com/api/v1/episode/{ep_id}",
        f"https://api.pocketfm.com/v4/episodes/{ep_id}"
    ]
    
    for url in endpoints:
        try:
            resp = session.get(url, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                
                stream_url = data.get('audioUrl') or data.get('streamUrl') or data.get('mediaUrl')
                if not stream_url and isinstance(data.get('playbackInfo'), dict):
                    stream_url = data['playbackInfo'].get('url') or data['playbackInfo'].get('streamUrl')
                    
                ep_title = data.get('title', f"Episode - {ep_id}")
                show_title = "Pocket FM"
                if isinstance(data.get('show'), dict):
                    show_title = data['show'].get('title', 'Pocket FM')
                    
                thumb_url = data.get('coverUrl') or data.get('imageUrl')
                if not thumb_url and isinstance(data.get('show'), dict):
                    thumb_url = data['show'].get('coverUrl') or data['show'].get('imageUrl')
                    
                if stream_url:
                    return {
                        'stream_url': stream_url,
                        'ep_title': ep_title,
                        'show_title': show_title,
                        'thumb_url': thumb_url
                    }, None
        except Exception:
            continue
            
    # Web Scraper Fallback
    try:
        web_url = f"https://www.pocketfm.com/episode/{ep_id}" if not episode_id_or_url.startswith("http") else episode_id_or_url
        web_resp = session.get(web_url, timeout=15)
        if web_resp.status_code == 200:
            soup = BeautifulSoup(web_resp.text, 'html.parser')
            title = soup.find('meta', property='og:title')
            image = soup.find('meta', property='og:image')
            
            script_tags = soup.find_all('script')
            for script in script_tags:
                if script.string and ('m3u8' in script.string or 'mp3' in script.string):
                    match = re.search(r'https?://[^\s"]+\.(?:m3u8|mp3)[^\s"]*', script.string)
                    if match:
                        return {
                            'stream_url': match.group(0),
                            'ep_title': title['content'] if title else f"Episode {ep_id}",
                            'show_title': "Pocket FM",
                            'thumb_url': image['content'] if image else None
                        }, None
    except Exception as e:
        print(f"Scraper error: {e}")

    return None, "404 Error: Audio Stream எடுக்க முடியவில்லை. புது cookies.txt தேவைப்படலாம்."

def download_audio_and_thumb(ep_data):
    try:
        if not os.path.exists('downloads'):
            os.makedirs('downloads')
            
        unique_id = str(uuid.uuid4())[:8]
        audio_file = f"downloads/{unique_id}.mp3"
        thumb_file = f"downloads/{unique_id}.jpg" if ep_data.get('thumb_url') else None
        
        # Download Cover Image
        if ep_data.get('thumb_url'):
            try:
                img_bytes = session.get(ep_data['thumb_url'], timeout=10).content
                with open(thumb_file, 'wb') as f:
                    f.write(img_bytes)
            except Exception:
                thumb_file = None
                
        # FFmpeg Audio Converter
        cmd = ['ffmpeg', '-y', '-i', ep_data['stream_url'], '-c', 'copy', '-bsf:a', 'aac_adtstoasc', audio_file]
        subprocess.run(cmd, check=True, capture_output=True)
        
        return audio_file, thumb_file, None
    except Exception as e:
        return None, None, f"FFmpeg Error: {str(e)}"

# ==========================================
# 6. Episode & Multi-Link Handler
# ==========================================
@bot.message_handler(regexp=r"pocketfm\.com/episode/")
def handle_single_or_multi_episodes(message):
    chat_id = message.chat.id
    text = message.text.strip()
    links = [line.strip() for line in text.splitlines() if "pocketfm.com/episode/" in line]
    
    status_msg = bot.send_message(chat_id, f"⏳ {len(links)} எபிசோடு லிங்க்(கள்) கண்டறியப்பட்டது...")
    
    for idx, link in enumerate(links, start=1):
        try:
            bot.edit_message_text(f"⏳ டவுன்லோட் ஆகிறது ({idx}/{len(links)})...", chat_id, status_msg.message_id)
            
            ep_data, error = fetch_episode_metadata(link)
            if error:
                bot.send_message(chat_id, f"⚠️ Link {idx} Failed: {error}")
                continue
                
            audio_file, thumb_file, err = download_audio_and_thumb(ep_data)
            if err:
                bot.send_message(chat_id, f"⚠️ Link {idx} Download Error: {err}")
                continue
                
            with open(audio_file, 'rb') as audio:
                if thumb_file and os.path.exists(thumb_file):
                    with open(thumb_file, 'rb') as thumb:
                        bot.send_audio(chat_id, audio, title=ep_data['ep_title'], performer=ep_data['show_title'], thumb=thumb)
                else:
                    bot.send_audio(chat_id, audio, title=ep_data['ep_title'], performer=ep_data['show_title'])
                    
            if audio_file and os.path.exists(audio_file): os.remove(audio_file)
            if thumb_file and os.path.exists(thumb_file): os.remove(thumb_file)
            
        except Exception as e:
            bot.send_message(chat_id, f"❌ Link {idx} Error: {str(e)}")
            
    bot.delete_message(chat_id, status_msg.message_id)
    bot.send_message(chat_id, "✅ வெற்றிகரமாக அனுப்பி முடிக்கப்பட்டது!")

# ==========================================
# 7. Full Series (/show/) Handler
# ==========================================
@bot.message_handler(regexp=r"pocketfm\.com/show/")
def handle_show_link(message):
    chat_id = message.chat.id
    url = message.text.strip()
    
    msg = bot.send_message(chat_id, "🔍 Series விபரங்களைச் சேகரிக்கிறேன்...")
    
    try:
        match = re.search(r'/show/([a-zA-Z0-9_-]+)', url)
        if not match:
            bot.edit_message_text("❌ செல்லுபடியாகாத Show Link.", chat_id, msg.message_id)
            return
            
        show_id = match.group(1).split('?')[0]
        
        endpoints = [
            f"https://api.pocketfm.com/v3/shows/{show_id}",
            f"https://api.pocketfm.com/v4/shows/{show_id}",
            f"https://api.pocketfm.com/api/v1/show/{show_id}"
        ]
        
        data = None
        for api_url in endpoints:
            res = session.get(api_url, timeout=15)
            if res.status_code == 200:
                data = res.json()
                break
                
        if not data:
            bot.edit_message_text("❌ Series Data பெற முடியவில்லை. Cookies-ஐ புதுப்பிக்கவும்.", chat_id, msg.message_id)
            return
            
        series_title = data.get('title', 'Pocket FM Series')
        episodes = data.get('episodes', [])
        episode_data = [{'id': ep['id'], 'title': ep.get('title', f"Episode {i+1}")} for i, ep in enumerate(episodes)]
        
        user_states[chat_id] = {'series_title': series_title, 'episode_data': episode_data, 'total': len(episode_data)}
        reply_text = f"🎧 **Series Name:** {series_title}\n📊 **Total Episodes:** {len(episode_data)}\n\n💬 **எபிசோடு எண்களை அனுப்பவும்:**\nSingle: `7`\nRange: `1 15`"
        bot.edit_message_text(reply_text, chat_id, msg.message_id, parse_mode="Markdown")
        bot.register_next_step_handler(message, process_episode_range)
        
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)}", chat_id, msg.message_id)

# ==========================================
# 8. Series Episode Range Processor
# ==========================================
def process_episode_range(message):
    chat_id = message.chat.id
    text = message.text.strip()
    
    if chat_id not in user_states:
        bot.send_message(chat_id, "❌ Session முடிந்தது. மீண்டும் Link-ஐ அனுப்பவும்.")
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
            msg = bot.send_message(chat_id, "❌ தவறான வடிவம். எ.கா: `7` அல்லது `1 15`")
            bot.register_next_step_handler(msg, process_episode_range)
            return
            
        start_ep = max(1, start_ep)
        end_ep = min(total_eps, end_ep)
        
        total_to_download = (end_ep - start_ep) + 1
        prog_msg = bot.send_message(chat_id, f"⏳ 0/{total_to_download} டவுன்லோட் ஆகிறது...")
        selected_episodes = episode_data[start_ep-1:end_ep]
        
        for i, ep in enumerate(selected_episodes, start=1):
            bot.edit_message_text(f"⏳ Downloading {i}/{total_to_download}: Ep {start_ep + i - 1}", chat_id, prog_msg.message_id)
            
            ep_data, error = fetch_episode_metadata(str(ep['id']))
            if error:
                bot.send_message(chat_id, f"⚠️ Ep {start_ep + i - 1} Failed: {error}")
                continue
                
            audio_file, thumb_file, err = download_audio_and_thumb(ep_data)
            if err:
                bot.send_message(chat_id, f"⚠️ Ep {start_ep + i - 1} Error: {err}")
                continue
                
            title_to_send = ep_data['ep_title'] if ep_data['ep_title'] else f"Ep {start_ep + i - 1} - {ep['title']}"
            performer_to_send = ep_data['show_title'] if ep_data['show_title'] else data['series_title']
            
            with open(audio_file, 'rb') as audio:
                if thumb_file and os.path.exists(thumb_file):
                    with open(thumb_file, 'rb') as thumb:
                        bot.send_audio(chat_id, audio, title=title_to_send, performer=performer_to_send, thumb=thumb)
                else:
                    bot.send_audio(chat_id, audio, title=title_to_send, performer=performer_to_send)
                    
            if audio_file and os.path.exists(audio_file): os.remove(audio_file)
            if thumb_file and os.path.exists(thumb_file): os.remove(thumb_file)
                
        del user_states[chat_id]
        bot.edit_message_text("✅ அனைத்து எபிசோடுகளும் வெற்றிகரமாக அனுப்பப்பட்டன!", chat_id, prog_msg.message_id)
        
    except ValueError:
        msg = bot.send_message(chat_id, "❌ எண்களை மட்டும் அனுப்பவும்.")
        bot.register_next_step_handler(msg, process_episode_range)

# ==========================================
# 9. UI Button Handlers
# ==========================================
@bot.message_handler(func=lambda message: message.text == "📥 Single/Multi Episode")
def handle_single_button(message):
    bot.send_message(message.chat.id, "📥 ஒன்று அல்லது பல `/episode/` லிங்குகளை ஒரே மெசேஜில் அனுப்பவும்.")

@bot.message_handler(func=lambda message: message.text == "🔍 Download Series")
def handle_series_button(message):
    bot.send_message(message.chat.id, "🔍 தொடரின் `/show/` லிங்கை அனுப்பவும்.")

@bot.message_handler(func=lambda message: message.text == "🔄 Refresh Cookies")
def handle_refresh_button(message):
    bot.send_message(message.chat.id, "🔄 `cookies.txt` ஃபைலை GitHub-ல் அப்டேட் செய்து Redeploy செய்யவும்.")

@bot.message_handler(func=lambda message: message.text == "📊 About & Status")
def handle_about_button(message):
    bot.send_message(message.chat.id, "👑 **World Best Pocket FM Downloader Bot**\n\n✅ Multi-API Engine\n✅ Full Scraper & m3u8 Fallback\n✅ HD Thumbnail & Track Title\n✅ 24/7 Alive Web Server.")

# ==========================================
# 10. Start Polling
# ==========================================
print("👑 Ultimate World Best Bot Started...")
bot.polling(none_stop=True, skip_pending=True)
