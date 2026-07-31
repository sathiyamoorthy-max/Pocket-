import os
import logging
import threading
import asyncio
import uuid
import glob
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import yt_dlp
from PIL import Image

TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("No TELEGRAM_TOKEN found in environment variables")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

active_tasks = {}

flask_app = Flask(__name__)

@flask_app.route('/')
def index():
    return "Bot is running!"

@flask_app.route('/health')
def health():
    return "OK"

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port)

def download_with_ytdlp(url, output_template):
    cookie_path = 'cookies.txt' if os.path.exists('cookies.txt') else None
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
    }
    if cookie_path:
        ydl_opts['cookiefile'] = cookie_path

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get('title', 'Pocket FM Audio')
        uploader = info.get('uploader') or info.get('series') or 'Pocket FM'
        thumbnail = info.get('thumbnail')
        return title, uploader, thumbnail

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🛑 Stop Active Download", callback_data="stop_download")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    welcome_text = (
        "👋 **Welcome to Pocket FM Pro Bot!**\n\n"
        "Send any Pocket FM link directly to download high-quality audio with title and thumbnail instantly!"
    )
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.message.edit_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "stop_download":
        active_tasks[query.message.chat_id] = "STOP"
        await query.message.reply_text("🛑 Stop signal sent!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.message.chat_id
    urls = [line.strip() for line in text.split('\n') if 'http' in line]

    if not urls:
        await update.message.reply_text("⚠️ Please send a valid link.")
        return

    active_tasks[chat_id] = "RUNNING"
    target_url = urls[0]

    msg = await update.message.reply_text("🚀 Processing download using advanced engine...")
    unique_id = str(uuid.uuid4())[:8]
    output_tmpl = f"downloads/audio_{unique_id}.%(ext)s"
    thumb_path = f"downloads/thumb_{unique_id}.jpg"
    audio_file_path = None

    try:
        if not os.path.exists('downloads'):
            os.makedirs('downloads')

        title, performer, thumb_url = await asyncio.to_thread(download_with_ytdlp, target_url, output_tmpl)
        
        mp3_files = glob.glob(f"downloads/audio_{unique_id}*.mp3")
        if mp3_files:
            audio_file_path = mp3_files[0]
        else:
            raise Exception("Audio conversion failed.")

        if thumb_url:
            try:
                import requests
                img_data = requests.get(thumb_url, timeout=15).content
                with open(thumb_path, 'wb') as handler:
                    handler.write(img_data)
                im = Image.open(thumb_path).convert('RGB')
                im.thumbnail((320, 320))
                im.save(thumb_path, 'JPEG')
            except Exception as img_err:
                logger.error(f"Thumbnail error: {img_err}")

        await msg.edit_text(f"📤 Uploading [{performer}] - {title}")

        with open(audio_file_path, 'rb') as audio:
            thumb_file = open(thumb_path, 'rb') if os.path.exists(thumb_path) else None
            await update.message.reply_audio(
                audio=audio,
                title=title,
                performer=performer,
                thumbnail=thumb_file
            )
            if thumb_file:
                thumb_file.close()

        await msg.delete()

    except Exception as e:
        await msg.edit_text(f"❌ Download Failed. Error: {e}")
        logger.error(f"Error on {target_url}: {e}")

    if audio_file_path and os.path.exists(audio_file_path):
        os.remove(audio_file_path)
    if os.path.exists(thumb_path):
        os.remove(thumb_path)

    active_tasks[chat_id] = "IDLE"
    await update.message.reply_text("✨ Task completed successfully!")

def run_bot():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("Bot started polling...")
    application.run_polling()

if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    run_bot()
