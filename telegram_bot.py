# telegram_bot.py
from telegram import Bot
from config import TELEGRAM_BOT_TOKEN

bot = Bot(token=TELEGRAM_BOT_TOKEN)
CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"  # replace with your Telegram user/chat ID

def send_signal(message):
    try:
        bot.send_message(chat_id=CHAT_ID, text=message)
    except Exception as e:
        print(f"❌ Error sending Telegram message: {e}")
