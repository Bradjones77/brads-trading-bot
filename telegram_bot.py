from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from signal_generator import run_signals
from config import TELEGRAM_TOKEN

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Welcome! Use /help to see commands.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start - Start the bot\n"
        "/help - Show this help message\n"
        "/signal - Get signals for all tracked coins"
    )

async def signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    signals = run_signals()
    message = "\n".join([f"{coin}: {signal}" for coin, signal in signals.items()])
    await update.message.reply_text(message)

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("signal", signal))
    
    app.run_polling()

if __name__ == "__main__":
    main()
