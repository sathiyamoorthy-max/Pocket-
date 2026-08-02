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
import math
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

# Flask & Telegram
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Original Libraries (For Manual Chunks)
import requests
import m3u8
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("No TELEGRAM_TOKEN found in environment variables")

WATERMARK = os.getenv("WATERMARK", "@UltraDownloaderBot")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

flask_app = Flask(__name__)
@flask_app.route('/')
def index():
    return "Pocket FM Premium Ultimate Bot is Online!"

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port)

# ==========================================
# 🔓 THE MASTER HACK: AWS WAF & COOKIE INJECTION
# ==========================================
# நீங்கள் கொடுத்த Netscape Cookie File-ஐ Python-க்கு ஏற்றவாறு ஒருங்கிணைத்துள்ளேன்!
MY_SECRET_COOKIE = (
    "web_id=bbe19cce50374861bf56ac3fbdc06588; locale=IN; first_visit=true; language=hindi; "
    "_gcl_au=1.1.658255502.1785434862; _ga=GA1.1.1957822637.1785434862; afUserId=cd520227-a29c-4610-8b1b-95890d6b7a26-p; "
    "_fbp=fb.1.1785434863255.104128244822616339.AQYAAQIB; AF_SYNC=1785434865274; cookieBannerDismissed=true; "
    "isPlayerListVisible=true; muxData==undefined&mux_viewer_id=e4615458-1a85-4a56-a68d-9a92b43e58f3&msn=0.8593616164671181&sid=643a7827-5d28-471e-abdd-1243b43e5911&sst=1785435412753&sex=1785436946901; "
    "__Secure-authjs.session-token=eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2Q0JDLUhTNTEyIiwia2lkIjoiUXJmMlVPYlhIdDNtSnVrNi14QlpsT3RjSk90SGNwc09iQkZHNlNXYnFSV0ZmamZiRFQ2elRXV2JfU19QRGtacks1Wkoyc21iLU5NWVVFajVyTUFVVncifQ..3Bm3MWTKa0HHG_uvX-GxVA.0fWCRowBPJinOCWrHaJD84vO3fDCjfSt2x4rZK6fEdhgYoSdCfvWragwrz3qPHV87LUhP5Xla6EtS5IhnZfbfI85GFUVQ_uHpqc1eSISPKfsH8Sx6vmi0a5iUlQEDHjJK3xHH85x6KGtk1VxVZTggH11f75P92bNQZr3tL4tNrcQ8oPspmW7bYoGM7jXg3F_GagEowt6Eq0iHd72_BzXSkUUcCDX7m3MhvOF-FpwawDBEuzM958Uj3xu6nQ1KZmyQNKlowwZYKybnO7u_WBwhdmRJluZ8ECrEC1NUYU3GzEMdWustmLdFoyc_gDvxP1uxRcI-sgVjyjKrqTXOpBoadYBu6OseLh8zbip7LlRn5YD8jGLamzMDwde1iXBnnfZ95HcZyWduO6xBfiT1OkQN0oys_trFviGNhk1m8AeHierawYBGu2vpomanIhfun-kMxR96lXUMqlcnm3DdgefMHaRAfjkm3kHzT7AkamHAq5kWL3VB0_858op9aV-nO4ZGJZcwkp3M88U_B8jmyXrFdposmWweyqag2KEpDtWBxkEf7rWRVgd2WK4tU2lnVog6uW7CKfHrPlwXzyvTfWegyAQ5T2iPHEIP5NVt9eZE6-TTGwOWH8D3Xl3LR99f_70a2uXp6xRn7vkO9jcHxkBHOcbXChFf8ZS1lEtv3Kxp_8kiaf2Pwsng1cxkBIq3TAPBqQHr3gLcNW0iC_m9p-im6loRaw62gJdasxy0iuOTaCuA8S0nwl4gx7_rFIVd86yd2wMg2KRDyIDReokSpZjHi6T9u67VDEhUVELHVixG1mZGtljF3pedK08RhJ56w0d3kdj6iC5O0s5sYompexjlHeZiU3MmQg9rq2IDYZ0b_1gJ5tzULhZ6fjpWRA3xtrKnqKPQ3bEX0kAa4GDvgPtst7sJC_6zao4GAVVRf5wYmTaalcu9KC1kPpHzP7tBKn2SAdQeTtkHxR_mjNurmCnSepbuFzGNUpZVw3uSnPKjSxvPAJpMWLir-7qY2AYNzg_x9n_VM32g-zdkMhHn05jU7A6F0SWZDRtp9Iu_Hn4tQgf-hbQc85Ot8AwKxafYQUWJZKds99ddgL3yCLbnrY0EQWrcYF8Gegh4CZZRlXu2hqkIcQ23l52917666ZCnaJzdh3oU3wnv1n7tg5smdCrBkPBzqlI7bu-lS12TAsR7kLxINNsygNQm5ma3MAd8Drod-sxdSrwNdzIEG9EXdd_eLdEV9od105oiI0eY1kqf3CRZyLPylhkRfT8vspm4BJLT5AxShClz_pV3DU4XHWYY6Z7QnxIwTYMgcAwHJ0ayIIOo5h0lhExPK_DAnzPbjTjeLc1ifdTt5sm1TkM8H5a3rbArZNn1meNeBx3eqQ_0vE0zl8jvF3ZWHDrhF_2nvO-JF4rg7yxd9RbLZD2tRAAAiyp9GAnbrDftojiaqHT1UHy3tInKCVc0DHMuC5CAfgE7QdiO8AggkUBmiSp4JE11IEDz_9Ez3KFJ6Hf1LgKlJ2VkQvBu2lgk6oPj0UPIi2vzkuU-UrMijxwFQCWzbmjUHudaULpR3LGj30ZNBMsrE882WgTbNhQgk9bEoQKMnfW0Ul82PNvg7FCUT9lo283fvztgaMfOE6n_yy30PvCn6-LiAs-Oylt5iJFeYCctS-D7foFqlzHK2RRsy5A24UsmA.xLMzZPszAwfkc-wLc21ntyu1rgZbqum11q_xoEIZeZ4; "
    "auth-token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJjYXRlZ29yeSI6ImFjY2VzcyIsImRldmljZV9pZCI6Im1vYmlsZS13ZWIiLCJleHBpcnkiOjE3ODU2NDk3MzAsImlhdCI6MTc4NTQ3NjkzMCwibG9jYWxlIjoiIiwicGxhdGZvcm0iOiJ3ZWIiLCJyb2xlIjoiTGlzdGVuZXIiLCJ0ZW5hbnQiOiJwb2NrZXRfZm0iLCJ1aWQiOiIiLCJ2ZXJzaW9uIjoidjIifQ.VC0TGwd7kNyYR993gOfnjp4psKtiRuS3EjZgKu_szMo; "
    "_dd_s_v2=aid=08bea9dd-fd1c-4b7a-bbbb-77b9861960ca&id=4cc76409-77a0-4a93-a031-4251cb08c8a4&created=1785641061129&expire=1785641971090&c=0; "
    "AWSALB=XpbIH19DXxDHyY+oDYQCfuBkHUxA4Q8rn1H0fyF0pqwFAOg1rhEMZ+XXAD+2NRfML+31j2P0ShR0SIm8X/vFSsCtHKxHbYh062n5D8LVxG2ueJzMUhl/gL3oMA2F; "
    "AWSALBCORS=XpbIH19DXxDHyY+oDYQCfuBkHUxA4Q8rn1H0fyF0pqwFAOg1rhEMZ+XXAD+2NRfML+31j2P0ShR0SIm8X/vFSsCtHKxHbYh062n5D8LVxG2ueJzMUhl/gL3oMA2F; "
    "_ga_8SC2XL7K1M=GS2.1.s1785641064$o6$g1$t1785641104$j20$l0$h2096322289"
)

MY_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

HEADERS = {
    'User-Agent': MY_USER_AGENT,
    'Accept': '*/*',
    'Referer': 'https://pocketfm.com/',
    'Origin': 'https://pocketfm.com',
    'Cookie': MY_SECRET_COOKIE  # 🔥 AWS WAF & Cloudfront-ஐ உடைக்கும் சாவி!
}

session = requests.Session()
USER_STATE = {}

# ==========================================
# 🔥 POCKET FM SERIES FETCHER
# ==========================================
def get_series_data(series_url):
    match = re.search(r'/show/([a-zA-Z0-9_-]+)', series_url)
    if not match: raise Exception("Invalid Show URL format.")
    show_id = match.group(1).split('?')[0]
    api_url = f"https://pocketfm.com/show/{show_id}"
    
    resp = session.get(api_url, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        raise Exception(f"CDN Blocked Series Fetch. Status: {resp.status_code}")

    html = resp.text
    title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    series_title = title_match.group(1).split("|")[0].strip() if title_match else "Pocket FM Series"
    
    img_match = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    series_thumb = img_match.group(1) if img_match else None
    
    json_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if not json_match: raise Exception("Could not extract JSON payload.")
    
    data = json.loads(json_match.group(1))
    episodes_map = []
    
    def extract_episodes(node):
        if isinstance(node, dict):
            ep_id = node.get('episodeId') or node.get('id')
            if ep_id and isinstance(ep_id, str) and len(ep_id) > 10:
                episodes_map.append({'url': f"https://pocketfm.com/episode/{ep_id}"})
            for v in node.values(): extract_episodes(v)
        elif isinstance(node, list):
            for item in node: extract_episodes(item)
            
    extract_episodes(data)
    
    seen = set()
    ep_list = []
    for ep in episodes_map:
        if ep['url'] not in seen:
            seen.add(ep['url'])
            ep_list.append(ep['url'])
            
    ep_list.reverse()
    if not ep_list: raise Exception("Episodes not found inside JSON structure.")
    return series_title, series_thumb, ep_list

# ==========================================
# 🎵 ORIGINAL AUDIO DOWNLOADER
# ==========================================
def download_chunk(args):
    i, seg_url, base_url, aes_key, iv, tmpdir = args
    try:
        full_url = seg_url if seg_url.startswith('http') else f"{base_url}/{seg_url}"
        ts_path = os.path.join(tmpdir, f"chunk_{i:04d}.ts")
        
        response = session.get(full_url, headers=HEADERS, timeout=20, stream=True)
        data = response.content
        
        if b'<html' in data[:100].lower() or b'accessdenied' in data[:100].lower():
            logger.error(f"Chunk {i} blocked by AWS CDN.")
            return i, None
            
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
    m3u8_content = session.get(m3u8_url, headers=HEADERS, timeout=15).text
    playlist = m3u8.loads(m3u8_content, uri=m3u8_url)
    base_url = '/'.join(m3u8_url.split('/')[:-1])
    
    if not playlist.segments and playlist.playlists:
        m3u8_url = playlist.playlists[0].uri
        m3u8_url = m3u8_url if m3u8_url.startswith('http') else f"{base_url}/{m3u8_url}"
        m3u8_content = session.get(m3u8_url, headers=HEADERS, timeout=15).text
        playlist = m3u8.loads(m3u8_content, uri=m3u8_url)
        base_url = '/'.join(m3u8_url.split('/')[:-1])
        
    aes_key, iv = None, None
    if playlist.keys and playlist.keys[0]:
        key_uri = playlist.keys[0].uri
        iv = playlist.keys[0].iv
        key_url = key_uri if key_uri.startswith('http') else f"{base_url}/{key_uri}"
        aes_key = session.get(key_url, headers=HEADERS, timeout=15).content

    segments = [seg.uri for seg in playlist.segments]
    with tempfile.TemporaryDirectory() as tmpdir:
        tasks = [(i, seg, base_url, aes_key, iv, tmpdir) for i, seg in enumerate(segments)]
        ts_files_dict = {}
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(download_chunk, arg) for arg in tasks]
            for future in as_completed(futures):
                idx, path = future.result()
                if path: ts_files_dict[idx] = path
                
        ts_files = [ts_files_dict[i] for i in sorted(ts_files_dict.keys())]
        if not ts_files: raise Exception("AWS Firewall Blocked the audio chunks. Please provide a fresh cookie.")
        
        if not os.path.exists('downloads'): os.makedirs('downloads')
        output_file = f"downloads/audio_{uuid.uuid4().hex[:8]}.mp3"
        list_path = os.path.join(tmpdir, "list.txt")
        with open(list_path, 'w') as f:
            for ts in ts_files: f.write(f"file '{ts}'\n")
            
        subprocess.run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c:a', 'libmp3lame', '-q:a', '2', output_file], check=True, capture_output=True)
        return output_file

def get_episode_metadata(episode_url):
    resp = session.get(episode_url, headers=HEADERS, timeout=20)
    if resp.status_code != 200:
        raise Exception("Failed to fetch episode. Check your Cookie.")
        
    html = resp.text
    title = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    img = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    m3u8 = re.search(r'(https?://[^\s"\'<>]+\.cloudfront\.net[^\s"\'<>]*?\.m3u8[^\s"\'<>]*)', html)
    if not m3u8:
        m3u8 = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html)
    if not m3u8:
        raise Exception("Premium Episode or Blocked.")
    ep_title = title.group(1).split("|")[0].strip() if title else "Episode"
    return m3u8.group(1), ep_title, img.group(1) if img else None

async def download_and_send_episode(update, context, ep_url, ep_number=None, total_eps=None, series_thumb=None):
    try:
        m3u8_url, title, thumb_url = await asyncio.to_thread(get_episode_metadata, ep_url)
        audio_path = await asyncio.to_thread(download_audio_from_m3u8, m3u8_url)
        
        final_thumb_url = thumb_url if thumb_url else series_thumb
        thumb_path = None
        
        if final_thumb_url:
            thumb_path = f"downloads/thumb_{uuid.uuid4().hex[:8]}.jpg"
            try:
                img_data = session.get(final_thumb_url, headers=HEADERS, timeout=15).content
                with open(thumb_path, 'wb') as f: f.write(img_data)
                Image.open(thumb_path).convert('RGB').thumbnail((320, 320)).save(thumb_path, 'JPEG')
            except:
                thumb_path = None
                
        display_title = f"[{ep_number}/{total_eps}] {title}" if ep_number and total_eps else title
        chat_id = update.effective_chat.id
        
        with open(audio_path, 'rb') as audio:
            thumb_file = open(thumb_path, 'rb') if thumb_path and os.path.exists(thumb_path) else None
            await context.bot.send_audio(
                chat_id=chat_id, audio=audio, title=display_title, 
                performer="Pocket FM", thumbnail=thumb_file, parse_mode="Markdown",
                read_timeout=120, write_timeout=120
            )
            if thumb_file: thumb_file.close()

        if os.path.exists(audio_path): os.remove(audio_path)
        if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
        return True, title
    except Exception as e:
        return False, str(e)

# ==========================================
# 🤖 TELEGRAM HANDLERS (PREMIUM INTERACTIVE)
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔗 Send any Pocket FM link.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    # 🌟 TRACK SELECTION LOGIC
    if user_id in USER_STATE and USER_STATE[user_id].get('state') == 'WAITING_FOR_TRACK':
        state_data = USER_STATE[user_id]
        eps = state_data['eps']
        series_thumb = state_data['thumb']
        
        try:
            parts = text.replace('-', ' ').split()
            tracks_to_dl = []
            
            if len(parts) == 1 and parts[0].isdigit():
                tracks_to_dl = [int(parts[0])]
            elif len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                start = min(int(parts[0]), int(parts[1]))
                end = max(int(parts[0]), int(parts[1]))
                tracks_to_dl = list(range(start, end + 1))
            else:
                await update.message.reply_text("⚠️ Invalid format. Send like '7' or '1 15'")
                return
                
            await update.message.reply_text(f"🚀 Downloading {len(tracks_to_dl)} tracks...")
            
            for t in tracks_to_dl:
                if 1 <= t <= len(eps):
                    ep_url = eps[t-1]
                    success, res = await download_and_send_episode(update, context, ep_url, ep_number=t, total_eps=len(eps), series_thumb=series_thumb)
                    if not success:
                        await update.message.reply_text(f"❌ Failed Track {t}: {res}")
                else:
                    await update.message.reply_text(f"❌ Track {t} is out of range.")
                    
            await update.message.reply_text("✅ All requested tracks processed!")
            del USER_STATE[user_id]
            return
            
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
            del USER_STATE[user_id]
            return

    # 🌟 SERIES LINK PROCESSING
    if 'pocketfm.com/show/' in text:
        url = text.split('|')[0].strip()
        msg = await update.message.reply_text("🔄 Fetching Series info...")
        try:
            series_title, series_thumb, ep_list = await asyncio.to_thread(get_series_data, url)
            
            USER_STATE[user_id] = {
                'state': 'WAITING_FOR_TRACK',
                'eps': ep_list,
                'title': series_title,
                'thumb': series_thumb
            }
            
            caption = (
                f"✅ 100% ஒரிஜினல் தரத்தில் தயாராக உள்ளது!\n\n"
                f"🎧 Series Selected: {series_title}\n"
                f"🌎 Language: Tamil\n"
                f"📊 Total Episodes: {len(ep_list)}\n\n"
                f"💬 *Send the track number(s) you wish to fetch.*\n"
                f"Single: 7\n"
                f"Range: 1 15"
            )
            
            if series_thumb:
                await update.message.reply_photo(photo=series_thumb, caption=caption, parse_mode="Markdown")
            else:
                await update.message.reply_text(caption, parse_mode="Markdown")
                
            await msg.delete()
        except Exception as e:
            await msg.edit_text(f"❌ Error fetching series: {e}")
            return
            
    # 🌟 SINGLE EPISODE PROCESSING
    elif 'pocketfm.com/episode/' in text:
        msg = await update.message.reply_text("⬇️ Downloading single episode...")
        success, result = await download_and_send_episode(update, context, text)
        if success: await msg.delete()
        else: await msg.edit_text(f"❌ Failed: {result}")

    elif text.startswith(("http://", "https://")):
        await update.message.reply_text("⚠️ Please send Pocket FM links only.")

def main():
    if not os.path.exists("downloads"): os.makedirs("downloads")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    threading.Thread(target=run_flask, daemon=True).start()
    app.run_polling()

if __name__ == "__main__":
    main()
