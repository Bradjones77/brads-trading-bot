# telegram_bot.py
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from config import TELEGRAM_TOKEN, EMOJIS
from signal_generator import get_all_signals, get_coin_signal
from portfolio import portfolio_status, add_investment, update_investment, remove_investment

# Initialize bot
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

# /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome! I am your Crypto Signal Bot. Use /help to see available commands."
    )

# /help command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
📌 *Available Commands:*

/start - Start the bot
/help - Show this help message
/signals - Get signals for all tracked coins
/signalcrypto - Get signals for main coins
/invest <COIN> <AMOUNT> <BUY_PRICE> - Add your investment
/portfolio - Show your current portfolio & P/L
/update <COIN> <NEW_PRICE> - Update a trade manually
/exit <COIN> - Remove a trade from portfolio
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")

# /signals command
async def signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    results = get_all_signals()
    msg = "\n".join(results)
    await update.message.reply_text(msg)

# /signalcrypto command
async def signalcrypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    coins = ["BTC","ETH","USDT","BNB"]
    msg_list = []
    for coin in coins:
        signal = get_coin_signal(coin)
        msg_list.append(signal)
    await update.message.reply_text("\n".join(msg_list))

# /invest command
async def invest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        coin = context.args[0].upper()
        amount = float(context.args[1])
        price = float(context.args[2])
        add_investment(coin, amount, price)
        await update.message.reply_text(f"✅ Investment added: {coin} {amount} @ {price}")
    except:
        await update.message.reply_text("❌ Usage: /invest <COIN> <AMOUNT> <BUY_PRICE>")

# /portfolio command
async def portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = portfolio_status()
    await update.message.reply_text(msg)

# /update command
async def update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        coin = context.args[0].upper()
        new_price = float(context.args[1])
        update_investment(coin, new_price)
        await update.message.reply_text(f"✅ Updated {coin} with new price {new_price}")
    except:
        await update.message.reply_text("❌ Usage: /update <COIN> <NEW_PRICE>")

# /exit command
async def exit_investment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        coin = context.args[0].upper()
        remove_investment(coin)
        await update.message.reply_text(f"✅ Removed {coin} from your portfolio")
    except:
        await update.message.reply_text("❌ Usage: /exit <COIN>")

# Add handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("signals", signals))
app.add_handler(CommandHandler("signalcrypto", signalcrypto))
app.add_handler(CommandHandler("invest", invest))
app.add_handler(CommandHandler("portfolio", portfolio))
app.add_handler(CommandHandler("update", update))
app.add_handler(CommandHandler("exit", exit_investment))

# Run bot
if __name__ == "__main__":
    print("Bot is running...")
    app.run_polling()
