# telegram_bot.py - production-ready Telegram bot with SQLite persistence and logging
import asyncio, time, logging, os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from config import TELEGRAM_TOKEN, COINS, PUSH_INTERVAL_MINUTES, DB_PATH
from signal_generator import analyze_coin, label_and_update
from db import init_db, add_subscriber, remove_subscriber, list_subscribers, log as db_log
from coinbase_fetcher import list_usd_products

# logging setup
LOG_FILE = os.getenv('LOG_FILE','bot.log')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
logger = logging.getLogger(__name__)

init_db()

SIGNAL_EMOJI = {'BUY':'🚀🟢 BUY','SELL':'🔻🔴 SELL','HOLD':'⏸️🟡 HOLD'}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Hi — advanced Coinbase bot. Use /help.')

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('/signal <PRODUCT> <S|L> - e.g. /signal BTC-USD S\n/scan - quick scan\n/subscribe - subscribe to pushes\n/unsubscribe - stop pushes\n/status - model status')

async def signal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text('Usage: /signal <PRODUCT> <S|L>')
        return
    product = args[0].upper()
    mode = args[1].upper()
    try:
        res = analyze_coin(product, granularity=300)
        if 'error' in res:
            await update.message.reply_text(f"Error: {res['error']}")
            return
        if mode == 'S':
            p = res['conf_short']
            label = 'Short-term'
        else:
            p = res['conf_long']
            label = 'Long-term'
        text = (f"💎 {product} — {label}\nPrice: {res['price']:.6f}\nHeuristic: {res['heuristic']}\n"
                f"Confidence: {p:.2f}\nFeatures: {res['features']}")
        await update.message.reply_text(text)
    except Exception as e:
        logger.exception('signal_cmd error')
        await update.message.reply_text(f'Error computing signal: {e}')

async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Running quick scan...')
    products = COINS if COINS else list_usd_products()[:50]
    texts = []
    for pid in products:
        try:
            res = analyze_coin(pid, granularity=300)
            if 'error' in res:
                continue
            texts.append(f"{pid}: {res['heuristic']} | short:{res['conf_short']:.2f} long:{res['conf_long']:.2f}")
            if len(texts) >= 8:
                await update.message.reply_text('\n'.join(texts))
                texts = []
                await asyncio.sleep(0.6)
        except Exception as e:
            logger.exception('scan loop error for %s', pid)
    if texts:
        await update.message.reply_text('\n'.join(texts))

async def subscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    add_subscriber(cid)
    db_log('INFO', f'Added subscriber {cid}')
    await update.message.reply_text('✅ Subscribed to periodic pushes.')

async def unsubscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    remove_subscriber(cid)
    db_log('INFO', f'Removed subscriber {cid}')
    await update.message.reply_text('🛑 Unsubscribed.')

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subs = list_subscribers()
    await update.message.reply_text(f'Models loaded. Subscribers: {len(subs)}')

# background push loop
async def pusher(app):
    while True:
        try:
            subs = list_subscribers()
            if not subs:
                await asyncio.sleep(PUSH_INTERVAL_MINUTES*60)
                continue
            products = COINS if COINS else list_usd_products()[:50]
            picks = []
            for pid in products:
                try:
                    res = analyze_coin(pid, granularity=300)
                    if 'error' in res:
                        continue
                    if res['conf_short'] > 0.75:
                        picks.append((pid, res['conf_short'], 'S', res))
                    elif res['conf_long'] > 0.75:
                        picks.append((pid, res['conf_long'], 'L', res))
                except Exception as e:
                    logger.exception('pusher analyze error')
            # send top 5 picks
            picks.sort(key=lambda x: -x[1])
            top = picks[:5]
            if top:
                for cid in subs:
                    try:
                        lines = []
                        for pid, score, mode, res in top:
                            lines.append(f"{pid} | {mode} | {score:.2f} | {res['heuristic']} | {res['price']:.6f}")
                        await app.bot.send_message(chat_id=cid, text="⏰ Top picks:\n" + "\n".join(lines))
                    except Exception:
                        logger.exception('pusher send error to %s', cid)
        except Exception:
            logger.exception('pusher loop top error')
        await asyncio.sleep(PUSH_INTERVAL_MINUTES*60)
