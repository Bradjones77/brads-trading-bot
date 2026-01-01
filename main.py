import os
import time
import requests
import psycopg2
from datetime import datetime, timezone

# ======================
# ENVIRONMENT VARIABLES
# ======================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN or not CHAT_ID or not DATABASE_URL:
    raise RuntimeError("BOT_TOKEN, CHAT_ID, or DATABASE_URL missing")

# ======================
# SETTINGS (BALANCED – MORE SIGNALS)
# ======================
CHECK_EVERY_SECONDS = 600  # 10 minutes
MAJORS_TOP_N = 50
MEMES_TOP_N = 50
MAX_ALERTS_PER_LOOP = 10

# Easier thresholds = more signals
MAJOR_MIN_24H = 1.5
MAJOR_MIN_1H = 0.4

MEME_MIN_24H = 3.0
MEME_MIN_1H = 0.8

# Shorter cooldowns
MAJOR_COOLDOWN = 30 * 60      # 30 minutes
MEME_COOLDOWN = 60 * 60       # 1 hour
last_alert_time = {}

MEME_CATEGORY = "meme-token"
BOT_START_TIME = time.time()

# ======================
# DATABASE
# ======================
def db_connect():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id SERIAL PRIMARY KEY,
            ts_utc TEXT NOT NULL,
            category TEXT NOT NULL,
            symbol TEXT NOT NULL,
            coin_id TEXT NOT NULL,
            side TEXT NOT NULL,
            entry DOUBLE PRECISION NOT NULL,
            stop_loss DOUBLE PRECISION NOT NULL,
            tp1 DOUBLE PRECISION NOT NULL,
            tp2 DOUBLE PRECISION NOT NULL,
            tp3 DOUBLE PRECISION NOT NULL,
            confidence INTEGER NOT NULL,
            chg1h DOUBLE PRECISION,
            chg24 DOUBLE PRECISION
        )
    """)
    conn.commit()
    return conn

# ======================
# TELEGRAM
# ======================
def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    requests.post(url, json=payload, timeout=20)

# ======================
# MARKET DATA
# ======================
def fetch_markets(top_n=None, category=None):
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "sparkline": "false",
        "price_change_percentage": "1h,24h",
        "per_page": top_n or 50,
        "page": 1,
    }
    if category:
        params["category"] = category

    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def fetch_ohlc_cached(coin_id, cache):
    if coin_id in cache:
        return cache[coin_id]

    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"
    try:
        r = requests.get(url, params={"vs_currency": "usd", "days": 1}, timeout=30)
        r.raise_for_status()
        cache[coin_id] = r.json()
        return cache[coin_id]
    except requests.exceptions.HTTPError as e:
        if getattr(e.response, "status_code", None) == 429:
            cache[coin_id] = None
            return None
        raise

# ======================
# LOGIC
# ======================
def should_alert(symbol, side, cooldown):
    now = int(time.time())
    key = f"{symbol}:{side}"
    if now - last_alert_time.get(key, 0) < cooldown:
        return False
    last_alert_time[key] = now
    return True

def score(conf24, conf1h):
    return min(100, int(abs(conf24) * 6 + abs(conf1h) * 30))

def build_levels(coin_id, entry, side, ohlc_cache):
    ohlc = fetch_ohlc_cached(coin_id, ohlc_cache)
    if not ohlc:
        return None

    highs = [x[2] for x in ohlc]
    lows = [x[3] for x in ohlc]

    if side == "BUY":
        sl = min(lows) * 0.997
        risk = entry - sl
        return sl, entry + risk, entry + 2 * risk, entry + 3 * risk
    else:
        sl = max(highs) * 1.003
        risk = sl - entry
        return sl, entry - risk, entry - 2 * risk, entry - 3 * risk

# ======================
# ⭐ SIGNAL FORMAT ⭐
# ======================
def format_msg(cat, sym, side, entry, sl, tp1, tp2, tp3, conf, chg1h, chg24, time_str):
    market = "🧊 *MAJOR*" if cat == "MAJOR" else "🐸 *MEME*"
    action = "🟢 *BUY*" if side == "BUY" else "🔴 *SELL*"

    def fmt(p):
        return f"${p:,.6f}" if p < 1 else f"${p:,.2f}"

    return (
        f"🚨 *TRADE SIGNAL* 🚨\n"
        f"{market} | *{sym}*\n"
        f"{action} • *Confidence:* {conf}/100\n"
        f"⏰ *Time:* {time_str}\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎯 *Entry:* `{fmt(entry)}`\n"
        f"🛑 *Stop Loss:* `{fmt(sl)}`\n"
        f"✅ *TP1:* `{fmt(tp1)}`\n"
        f"✅ *TP2:* `{fmt(tp2)}`\n"
        f"✅ *TP3:* `{fmt(tp3)}`\n"
        f"━━━━━━━━━━━━━━\n"
        f"📈 *Momentum:* 1h `{chg1h:+.2f}%` | 24h `{chg24:+.2f}%`\n"
        f"_Not financial advice_"
    )

# ======================
# SCAN FUNCTION
# ======================
def run_scan(conn):
    alerts = []
    ohlc_cache = {}
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")

    majors = fetch_markets(MAJORS_TOP_N)
    memes = fetch_markets(MEMES_TOP_N, MEME_CATEGORY)

    for group, data, min24, min1h, cd in [
        ("MAJOR", majors, MAJOR_MIN_24H, MAJOR_MIN_1H, MAJOR_COOLDOWN),
        ("MEME", memes, MEME_MIN_24H, MEME_MIN_1H, MEME_COOLDOWN),
    ]:
        for c in data:
            chg1h = c.get("price_change_percentage_1h_in_currency")
            chg24 = c.get("price_change_percentage_24h")
            if chg1h is None or chg24 is None:
                continue

            side = "BUY" if chg24 > min24 and chg1h > min1h else \
                   "SELL" if chg24 < -min24 and chg1h < -min1h else None
            if not side or not should_alert(c["symbol"], side, cd):
                continue

            entry = c["current_price"]
            levels = build_levels(c["id"], entry, side, ohlc_cache)
            if not levels:
                continue

            sl, tp1, tp2, tp3 = levels
            conf = score(chg24, chg1h)

            alerts.append(
                format_msg(group, c["symbol"].upper(), side, entry, sl, tp1, tp2, tp3, conf, chg1h, chg24, now)
            )

            if len(alerts) >= MAX_ALERTS_PER_LOOP:
                break

    return alerts

# ======================
# MAIN LOOP
# ======================
def main():
    conn = db_connect()
    send_message("✅ Bot online. Auto scans every 10 minutes.\nBalanced mode enabled.")

    while True:
        try:
            alerts = run_scan(conn)
            if alerts:
                send_message("🔥 *NEW TRADE SETUPS*\n━━━━━━━━━━━━━━\n\n" + "\n\n".join(alerts))
        except Exception as e:
            print("Error:", e)

        time.sleep(CHECK_EVERY_SECONDS)

if __name__ == "__main__":
    main()
