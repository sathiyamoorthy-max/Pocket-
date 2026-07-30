import os
import logging
import threading
import asyncio
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Configuration ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("No TELEGRAM_TOKEN found in environment variables")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Try to import the downloader (pocketfm_dl) ---
try:
    import pocketfm_dl
    DOWNLOADER_AVAILABLE = True
except ImportError:
    DOWNLOADER_AVAILABLE = False
    logger.error("🚨 CRITICAL ERROR: pocketfm_dl component is missing! Please install it or upload the file.")

# --- Flask App (Health check for Render) ---
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

# --- Telegram Bot Core Logic ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hello! Send me a direct Pocket FM story link, and I will try to download the audio for you."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text

    if 'pocketfm' not in user_message.lower():
        await update.message.reply_text("⚠️ Please send a valid Pocket FM link.")
        return

    if not DOWNLOADER_AVAILABLE:
        await update.message.reply_text("❌ Download component is not installed. Contact admin (pocketfm_dl is missing).")
        return

    msg = await update.message.reply_text("⏳ Processing your request, please wait...")

    try:
        # Using asyncio.to_thread to prevent bot freezing during download
        audio_file_path = await asyncio.to_thread(pocketfm_dl.download, user_message)
        
        await msg.edit_text("📤 Uploading audio to Telegram...")
        
        with open(audio_file_path, 'rb') as audio:
            await update.message.reply_audio(audio=audio, title="Downloaded Story")
            
        logger.info(f"Successfully processed link: {user_message}")
        
        # 🧹 IMPORTANT FIX: Delete the file after sending to save server memory!
        if os.path.exists(audio_file_path):
            os.remove(audio_file_path)
            
        await msg.delete()
        
    except Exception as e:
        await msg.edit_text(f"❌ Sorry, download failed. Error: {e}")
        logger.error(f"Error processing {user_message}: {e}")

def run_bot():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started polling...")
    application.run_polling()

# --- Main Entry Point ---
if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    run_bot()
