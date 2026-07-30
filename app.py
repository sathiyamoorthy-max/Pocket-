import os
import logging
import threading
import asyncio
import uuid
import requests
from PIL import Image
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("No TELEGRAM_TOKEN found in environment variables")

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    import pocketfm_dl
    DOWNLOADER_AVAILABLE = True
except ImportError:
    DOWNLOADER_AVAILABLE = False
    logger.error("🚨 pocketfm_dl missing!")

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! Send single or multiple Pocket FM links (one by one or in batch), and I will download them with title & thumbnail!"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    urls = [line.strip() for line in text.split('\n') if 'pocketfm.com/episode/' in line]

    if not urls:
        await update.message.reply_text("⚠️ Please send valid Pocket FM episode links.")
        return

    if not DOWNLOADER_AVAILABLE:
        await update.message.reply_text("❌ Downloader module missing.")
        return

    await update.message.reply_text(f"🚀 Found {len(urls)} episode(s). Starting batch download...")

    for i, url in enumerate(urls, 1):
        msg = await update.message.reply_text(f"⏳ Processing Episode {i}/{len(urls)}...")
        unique_id = str(uuid.uuid4())[:8]
        thumb_path = f"downloads/thumb_{unique_id}.jpg"
        audio_file_path = None
        
        try:
            audio_file_path, episode_title, thumb_url = await asyncio.to_thread(pocketfm_dl.download, url)
            
            if thumb_url:
                try:
                    img_data = requests.get(thumb_url, timeout=15).content
                    if not os.path.exists('downloads'):
                        os.makedirs('downloads')
                    with open(thumb_path, 'wb') as handler:
                        handler.write(img_data)
                    
                    im = Image.open(thumb_path).convert('RGB')
                    im.thumbnail((320, 320))
                    im.save(thumb_path, 'JPEG')
                except Exception as img_err:
                    logger.error(f"Thumbnail error: {img_err}")

            await msg.edit_text(f"📤 Uploading Episode {i}: {episode_title}")

            with open(audio_file_path, 'rb') as audio:
                thumb_file = open(thumb_path, 'rb') if os.path.exists(thumb_path) else None
                await update.message.reply_audio(
                    audio=audio,
                    title=episode_title,
                    performer="Pocket FM",
                    thumbnail=thumb_file
                )
                if thumb_file:
                    thumb_file.close()

            await msg.delete()

        except Exception as e:
            await msg.edit_text(f"❌ Failed Episode {i}. Error: {e}")
            logger.error(f"Error on {url}: {e}")

        if audio_file_path and os.path.exists(audio_file_path):
            os.remove(audio_file_path)
        if os.path.exists(thumb_path):
            os.remove(thumb_path)

    await update.message.reply_text("✨ Batch download completed successfully!")

def run_bot():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot started polling...")
    application.run_polling()

if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    run_bot()
