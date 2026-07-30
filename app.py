import os
import logging
import threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Configuration ---
# Read the bot token from environment variables (set this on Render)
TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("No TELEGRAM_TOKEN found in environment variables")

# Enable logging so you can see what's happening
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Flask App (Required for Render to keep the service alive) ---
flask_app = Flask(__name__)

@flask_app.route('/')
def index():
    return "Bot is running!"

@flask_app.route('/health')
def health():
    return "OK"

def run_flask():
    """Runs Flask in a separate thread to keep Render's port active."""
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port)

# --- Telegram Bot Core Logic ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Replies to the /start command."""
    await update.message.reply_text(
        "👋 Hello! Send me a direct Pocket FM story link, and I will try to download the audio for you."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the user's message. Expects a Pocket FM URL."""
    user_message = update.message.text

    # Simple check to make sure it's a Pocket FM link
    if 'pocketfm' not in user_message.lower():
        await update.message.reply_text("⚠️ Please send a valid Pocket FM link.")
        return

    await update.message.reply_text("⏳ Processing your request, please wait...")

    try:
        # --- THE CORE DOWNLOADER (Using pocketfm-dl) ---
        import pocketfm_dl
        
        # This library tries to download the audio and returns the local file path.
        # Note: If the library fails, the bot will crash here or throw an exception.
        audio_file_path = pocketfm_dl.download(user_message)

        # Send the downloaded audio file back to the user
        with open(audio_file_path, 'rb') as audio:
            await update.message.reply_audio(audio=audio, title="Downloaded Story")

        logger.info(f"Successfully processed link: {user_message}")

    except ImportError:
        await update.message.reply_text("❌ Download component is not installed. Contact admin.")
        logger.error("pocketfm_dl library not found")
    except Exception as e:
        # Catch any error (like invalid link, network issues, etc.)
        await update.message.reply_text(f"❌ Sorry, download failed. Error: {e}")
        logger.error(f"Error processing {user_message}: {e}")

def run_bot():
    """Configures and starts the Telegram bot."""
    application = Application.builder().token(TOKEN).build()

    # Register command handlers
    application.add_handler(CommandHandler("start", start))
    # Register message handler (catches all non-command text messages)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start polling Telegram for new messages
    logger.info("Bot started polling...")
    application.run_polling()

# --- Main Entry Point ---
if __name__ == '__main__':
    # Start Flask in a background thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    # Start the bot in the main thread
    run_bot()
