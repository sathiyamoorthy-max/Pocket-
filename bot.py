import os
import re
import importlib
import threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN:
    raise ValueError("No TELEGRAM_TOKEN found in environment variables")

flask_app = Flask(__name__)
@flask_app.route('/')
def index():
    return "Multi-Platform OTT Bot Online!"

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port)

# ==========================================
# 🔥 1. PLATFORM DETECTOR
# ==========================================
def detect_platform(url):
    platforms = {
        'pocketfm': ['pocketfm.com', 'pocketfm.in'],
        'zee5': ['zee5.com'],
        'sonyliv': ['sonyliv.com'],
        'sunnxt': ['sunnxt.com'],
        'hotstar': ['hotstar.com']
    }
    for platform, domains in platforms.items():
        for domain in domains:
            if domain in url:
                return platform
    return None

# ==========================================
# 🤖 2. BOT HANDLERS
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🎵 Pocket FM", callback_data="pocketfm")],
        [InlineKeyboardButton("📺 Zee5 (Experimental)", callback_data="zee5")],
        [InlineKeyboardButton("🎬 SonyLiv (Coming Soon)", callback_data="sonyliv")],
        [InlineKeyboardButton("📀 Hotstar (DRM Locked)", callback_data="hotstar")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🔥 **All OTT Downloader Menu**\n\nSelect a platform to enable custom downloads:",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    platform = query.data
    if platform == "pocketfm":
        await query.edit_message_text("✅ Pocket FM Mode Active! Send any `/show/` or `/episode/` link.")
    elif platform in ["zee5", "sonyliv", "hotstar"]:
        await query.edit_message_text("⚠️ This platform is under development or requires advanced DRM bypass techniques.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.startswith("http"):
        await update.message.reply_text("⚠️ Please send a valid URL.")
        return

    platform = detect_platform(text)
    if not platform:
        await update.message.reply_text("❌ Unsupported platform. Only PocketFM, Zee5, SonyLiv, Hotstar are supported.")
        return

    try:
        module = importlib.import_module(f"handlers.{platform}")
        await module.download_platform(update, context, text)
    except ImportError:
        await update.message.reply_text(f"❌ {platform} module not implemented yet.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# ==========================================
# 🚀 MAIN
# ==========================================
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    threading.Thread(target=run_flask, daemon=True).start()
    app.run_polling()

if __name__ == '__main__':
    main()
