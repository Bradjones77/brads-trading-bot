# telegram_bot.py
from telegram import Bot
from telegram.ext import ApplicationBuilder, CommandHandler
from config import TELEGRAM_BOT_TOKEN

bot = Bot(token=TELEGRAM_BOT_TOKEN)
CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"  # replace with your chat ID

def send_signal(message):
    try:
        bot.send_message(chat_id=CHAT_ID, text=message)
    except Exception as e:
        print(f"❌ Error sending Telegram message: {e}")

def start(update, context):
    context.bot.send_message(chat_id=update.effective_chat.id, text="Welcome! Use /signal to get signals.")

def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.run_polling()
