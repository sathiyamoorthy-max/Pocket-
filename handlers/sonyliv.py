from telegram import Update
from telegram.ext import ContextTypes

async def download_platform(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    await update.message.reply_text("⚠️ SonyLiv downloader is under development. Check back later.")
