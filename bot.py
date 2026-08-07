import os
import re
import uuid
import threading
import subprocess
import requests
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

# ==========================================
# 2. Render 24/7 Web Server
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "🚀 Ultimate Pocket FM Downloader Bot Active 24/7!"

def run_web_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_web_server, daemon=True).start()

# ==========================================
# 3. Multi-Layer Bypass Engine (Cookie + Guest Token)
# ==========================================
session = requests.Session()

MOBILE_HEADERS = {
    "User-Agent": "PocketFM/6.5.0 (Android; 13; SM-G991B)",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.pocketfm.com/"
}
session.headers.update(MOBILE_HEADERS)

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
# 4. Interactive Keyboards
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
        "👑 **Welcome to Ultimate Pocket FM Downloader Bot!**\n\n"
        "⚡ **Multi-Layer Bypass + yt-dlp Engine Enabled!**\n"
        "🚀 Ultra-Fast & 100% Reliable Download\n"
        "🎧 Full Range, Batch & Single Download Support\n"
        "🖼️ HD Cover Photo + Track Details\n\n"
        "👇 *Choose an option below:*",
        reply_markup=get_main_menu(),
        parse_mode="Markdown"
    )

# ==========================================
# 5. Fast Metadata Engine (Scraper + API)
# ==========================================
def fetch_episode_metadata(episode_id_or_url):
    ep_id = episode_id_or_url.split('/')[-1].split('?')[0]
    
    # Web Scraper
    try:
        web_url = f"https://www.pocketfm.com/episode/{ep_id}" if not episode_id_or_url.startswith("http") else episode_id_or_url
        web_resp = session.get(web_url, timeout=10)
        if web_resp.status_code == 200:
            soup = BeautifulSoup(web_resp.text, 'html.parser')
            title_tag = soup.find('meta', property='og:title')
            image_tag = soup.find('meta', property='og:image')
            
            ep_title = title_tag['content'] if title_tag else f"Episode - {ep_id}"
            thumb_url = image_tag['content'] if image_tag else None
            
            script_tags = soup.find_all('script')
            for script in script_tags:
                if script.string and ('m3u8' in script.string or 'cloudfront' in script.string or 'mp3' in script.string):
                    matches = re.findall(r'https?://[^\s"\']+\.(?:m3u8|mp3)[^\s"\']*', script.string)
                    if matches:
                        return {
                            'stream_url': matches[0],
                            'ep_title': ep_title,
                            'show_title': "Pocket FM",
                            'thumb_url': thumb_url
                        }, None
    except Exception as e:
        print(f"Scraper Error: {e}")

    # API Fallbacks
    endpoints = [
        f"https://api.pocketfm.com/v2/episodes/{ep_id}",
        f"https://api.pocketfm.com/api/v1/episode/{ep_id}",
        f"https://api.pocketfm.com/v3/episodes/{ep_id}"
    ]
    
    for url in endpoints:
        try:
            resp = session.get(url, timeout=8)
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
            
    return None, "Audio stream பெற முடியவில்லை."

# ==========================================
# 6. Ultra-Fast yt-dlp Downloader (Bypasses FFmpeg Exit 1)
# ==========================================
def download_audio_and_thumb(ep_data):
    try:
        if not os.path.exists('downloads'):
            os.makedirs('downloads')
            
        unique_id = str(uuid.uuid4())[:8]
        audio_file = f"downloads/{unique_id}.mp3"
        thumb_file = f"downloads/{unique_id}.jpg" if ep_data.get('thumb_url') else None
        
        clean_stream_url = ep_data['stream_url'].replace('\\', '').rstrip('/').strip()
        
        # Download Cover Image
        if ep_data.get('thumb_url'):
            try:
                img_bytes = session.get(ep_data['thumb_url'], timeout=8).content
                with open(thumb_file, 'wb') as f:
                    f.write(img_bytes)
            except Exception:
                thumb_file = None
                
        # ⚡ Ultra Fast yt-dlp (Bypasses FFmpeg exit 1 errors)
        cmd = [
            'yt-dlp',
            '--no-check-certificates',
            '--user-agent', MOBILE_HEADERS['User-Agent'],
            '--referer', MOBILE_HEADERS['Referer'],
            '--extract-audio',
            '--audio-format', 'mp3',
            '--audio-quality', '0',
            '-o', audio_file,
            clean_stream_url
        ]
        
        subprocess.run(cmd, check=True, capture_output=True, timeout=45)

        # Auto Compress for 50MB Limit
        if os.path.exists(audio_file):
            file_size_mb = os.path.getsize(audio_file) / (1024 * 1024)
            if file_size_mb >= 49.0:
                compressed_file = f"downloads/compressed_{unique_id}.mp3"
                compress_cmd = ['ffmpeg', '-y', '-i', audio_file, '-b:a', '64k', compressed_file]
                subprocess.run(compress_cmd, capture_output=True)
                if os.path.exists(compressed_file):
                    os.remove(audio_file)
                    audio_file = compressed_file

        return audio_file, thumb_file, None
        
    except subprocess.CalledProcessError as e:
        return None, None, f"yt-dlp Error: {e.stderr.decode('utf-8') if e.stderr else str(e)}"
    except Exception as e:
        return None, None, f"Download Error: {str(e)}"

# ==========================================
# 7. Single / Multi Episode Handler
# ==========================================
@bot.message_handler(regexp=r"pocketfm\.com/episode/")
def handle_single_or_multi_episodes(message):
    chat_id = message.chat.id
    text = message.text.strip()
    links = [line.strip() for line in text.splitlines() if "pocketfm.com/episode/" in line]
    
    status_msg = bot.send_message(chat_id, f"⚡ {len(links)} எபிசோடு டவுன்லோட் ஆகிறது...")
    
    for idx, link in enumerate(links, start=1):
        try:
            bot.edit_message_text(f"⏳ Downloading ({idx}/{len(links)})...", chat_id, status_msg.message_id)
            
            ep_data, error = fetch_episode_metadata(link)
            if error:
                bot.send_message(chat_id, f"⚠️ Link {idx} Failed: {error}")
                continue
                
            audio_file, thumb_file, err = download_audio_and_thumb(ep_data)
            if err:
                bot.send_message(chat_id, f"⚠️ Link {idx} Error: {err}")
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
# 8. Show Link & Range Processing
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
        
        web_resp = session.get(url, timeout=12)
        series_title = "Pocket FM Series"
        cover_photo = None
        episode_data = []

        if web_resp.status_code == 200:
            soup = BeautifulSoup(web_resp.text, 'html.parser')
            title_tag = soup.find('meta', property='og:title')
            image_tag = soup.find('meta', property='og:image')
            if title_tag: series_title = title_tag['content'].replace('- Listen on Pocket FM', '').strip()
            if image_tag: cover_photo = image_tag['content']
            
            ep_links = soup.find_all('a', href=re.compile(r'/episode/'))
            seen_ids = set()
            for a in ep_links:
                ep_match = re.search(r'/episode/([a-zA-Z0-9_-]+)', a.get('href', ''))
                if ep_match and ep_match.group(1) not in seen_ids:
                    seen_ids.add(ep_match.group(1))
                    episode_data.append({'id': ep_match.group(1), 'title': a.get_text(strip=True)})

        if not episode_data:
            endpoints = [
                f"https://api.pocketfm.com/v2/shows/{show_id}",
                f"https://api.pocketfm.com/v4/shows/{show_id}",
                f"https://api.pocketfm.com/v3/shows/{show_id}"
            ]
            for api_url in endpoints:
                res = session.get(api_url, timeout=10)
                if res.status_code == 200:
                    data = res.json()
                    series_title = data.get('title', series_title)
                    episodes = data.get('episodes', [])
                    episode_data = [{'id': ep['id'], 'title': ep.get('title', f"Episode {i+1}")} for i, ep in enumerate(episodes)]
                    if episode_data: break

        if not episode_data:
            bot.edit_message_text("❌ Series Data பெற முடியவில்லை. Cookies-ஐ புதுப்பிக்கவும்.", chat_id, msg.message_id)
            return

        user_states[chat_id] = {
            'awaiting_range': True,
            'series_title': series_title,
            'episode_data': episode_data,
            'total': len(episode_data)
        }
        
        reply_text = (
            f"🎧 **Series Selected:** {series_title}\n"
            f"📊 **Total Episodes:** {len(episode_data)}\n\n"
            f"💬 **எபிசோடு எண்களை அனுப்பவும்:**\nSingle: `1`\nRange: `1 15`"
        )
        
        bot.delete_message(chat_id, msg.message_id)
        if cover_photo:
            bot.send_photo(chat_id, cover_photo, caption=reply_text, parse_mode="Markdown")
        else:
            bot.send_message(chat_id, reply_text, parse_mode="Markdown")
            
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error: {str(e)}")

# ==========================================
# 9. Catch Range Numbers
# ==========================================
@bot.message_handler(func=lambda msg: user_states.get(msg.chat.id, {}).get('awaiting_range', False))
def process_range_text(message):
    chat_id = message.chat.id
    text = message.text.strip()
    data = user_states.get(chat_id)
    
    total_eps = data['total']
    episode_data = data['episode_data']
    
    try:
        numbers = text.split()
        if len(numbers) == 1:
            start_ep = end_ep = int(numbers[0])
        elif len(numbers) == 2:
            start_ep, end_ep = int(numbers[0]), int(numbers[1])
        else:
            bot.send_message(chat_id, "❌ தவறான வடிவம். எ.கா: `1` அல்லது `1 15`")
            return
            
        start_ep = max(1, start_ep)
        end_ep = min(total_eps, end_ep)
        
        total_to_download = (end_ep - start_ep) + 1
        prog_msg = bot.send_message(chat_id, f"⏳ 0/{total_to_download} டவுன்லோட் ஆகிறது...")
        selected_episodes = episode_data[start_ep-1:end_ep]
        
        for i, ep in enumerate(selected_episodes, start=1):
            bot.edit_message_text(f"⚡ Downloading {i}/{total_to_download}: Ep {start_ep + i - 1}", chat_id, prog_msg.message_id)
            
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
                
        user_states[chat_id]['awaiting_range'] = False
        bot.edit_message_text("✅ அனைத்து எபிசோடுகளும் வெற்றிகரமாக அனுப்பப்பட்டன!", chat_id, prog_msg.message_id)
        
    except ValueError:
        bot.send_message(chat_id, "❌ எண்களை மட்டும் அனுப்பவும்.")

# ==========================================
# 10. Menu Handlers
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
    bot.send_message(message.chat.id, "👑 **Ultimate Pocket FM Downloader Bot**\n\n⚡ yt-dlp + Multi-Layer Bypass Engine\n✅ 24/7 Web Server Enabled.\n✅ Auto Cookie & API Fallback.")

print("👑 Ultimate Master Bot Engine Started...")
bot.polling(none_stop=True, skip_pending=True)
