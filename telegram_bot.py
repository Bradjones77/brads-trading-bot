# telegram_bot.py
import os
from telegram import Bot

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = Bot(token=TELEGRAM_TOKEN)

def send_signal(signals: dict):
    """
    Send formatted signals to your Telegram chat.
    signals: dictionary like {"BTC": "📈 Long-term", "DOGE": "❌ Error"}
    """
    message = "📊 Crypto Signals 📊\n\n"
    for coin, signal in signals.items():
        message += f"{coin}: {signal}\n"

    # Replace CHAT_ID with your Telegram chat ID
    CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
    bot.send_message(chat_id=CHAT_ID, text=message)

    app.run_polling()
