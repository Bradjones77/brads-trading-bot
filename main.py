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
# SETTINGS
# ======================
CHECK_EVERY_SECONDS = 900  # 15 minutes
MAJORS_TOP_N = 50
MEMES_TOP_N = 50
MAX_ALERTS_PER_LOOP = 6

MAJOR_MIN_24H = 2.0
MAJOR_MIN_1H = 0.6

MEME_MIN_24H = 4.0
MEME_MIN_1H = 1.2

MAJOR_COOLDOWN = 60 * 60
MEME_COOLDOWN = 2 * 60 * 60
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

def db_insert(conn, data):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO signals
        (ts_utc, category, symbol, coin_id, side, entry, stop_loss, tp1, tp2, tp3, confidence, chg1h, chg24)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, data)
    conn.commit()

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

def get_updates(offset=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()["result"]

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
        # If rate-limited, skip OHLC for this coin this loop
        if getattr(e.response, "status_code", None) == 429:
            print(f"Rate limited (429) on OHLC for {coin_id} — skipping this coin this loop.")
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

def format_msg(cat, sym, side, entry, sl, tp1, tp2, tp3, conf, chg1h, chg24, time_str):
    return (
        f"🚀 *{sym} {side} SIGNAL*\n"
        f"Entry: ${entry:.4f}\n"
        f"SL: ${sl:.4f}\n"
        f"TP1: ${tp1:.4f}\n"
        f"TP2: ${tp2:.4f}\n"
        f"TP3: ${tp3:.4f}\n"
        f"1h: {chg1h:+.2f}% | 24h: {chg24:+.2f}%\n"
        f"Confidence: {conf}/100\n"
        f"{time_str}"
    )

# ======================
# SCAN FUNCTION (reusable)
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

            alerts.append(format_msg(group, c["symbol"].upper(), side, entry, sl, tp1, tp2, tp3, conf, chg1h, chg24, now))

            if len(alerts) >= MAX_ALERTS_PER_LOOP:
                break

    return alerts

# ======================
# MAIN LOOP + COMMANDS
# ======================
def main():
    conn = db_connect()
    send_message("✅ Bot online. Auto scans every 15 minutes.\nType /help for commands.")
    offset = None

    while True:
        try:
            # Auto scan
            alerts = run_scan(conn)
            if alerts:
                send_message("🔥 *NEW SIGNALS*\n\n" + "\n\n".join(alerts))

            # Commands
            updates = get_updates(offset)
            for u in updates:
                offset = u.get("update_id", 0) + 1

                msg = u.get("message")
                if not msg:
                    continue

                # Ignore Telegram service/system messages and bot messages
                sender = msg.get("from")
                if not sender:
                    continue
                if sender.get("is_bot"):
                    continue

                text = (msg.get("text") or "").strip()
                if not text.startswith("/"):
                    continue

                if text == "/signals":
                    send_message("🔍 Running manual scan...")
                    alerts = run_scan(conn)
                    send_message("\n\n".join(alerts) if alerts else "No signals right now.")

                elif text == "/status":
                    uptime = int((time.time() - BOT_START_TIME) / 60)
                    send_message(f"🟢 Bot running\nUptime: {uptime} minutes\nMajors: 50 | Memes: 50")

                elif text == "/help":
                    send_message("/signals – run scan now\n/status – bot status\n/help – commands")

        except Exception as e:
            print("Error:", e)

        time.sleep(CHECK_EVERY_SECONDS)

if __name__ == "__main__":
    main()
