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
CHECK_EVERY_SECONDS = 300
MAJORS_TOP_N = 30
MAX_ALERTS_PER_LOOP = 6

MAJOR_MIN_24H = 2.0
MAJOR_MIN_1H = 0.6

MEME_MIN_24H = 4.0
MEME_MIN_1H = 1.2

MEME_IDS = [
    "dogecoin", "shiba-inu", "pepe", "bonk", "dogwifcoin",
    "floki", "baby-doge-coin", "mog-coin", "book-of-meme"
]

MAJOR_COOLDOWN = 60 * 60
MEME_COOLDOWN = 2 * 60 * 60
last_alert_time = {}

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

# ======================
# MARKET DATA (CoinGecko)
# ======================
def fetch_markets(top_n=None, ids=None):
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "sparkline": "false",
        "price_change_percentage": "1h,24h"
    }
    if top_n:
        params["per_page"] = top_n
        params["page"] = 1
    if ids:
        params["ids"] = ",".join(ids)
        params["per_page"] = len(ids)

    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def fetch_ohlc(coin_id):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"
    r = requests.get(url, params={"vs_currency": "usd", "days": 1}, timeout=30)
    r.raise_for_status()
    return r.json()

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
    return min(100, int(abs(conf24)*6 + abs(conf1h)*30))

def build_levels(coin_id, entry, side):
    ohlc = fetch_ohlc(coin_id)
    highs = [x[2] for x in ohlc]
    lows = [x[3] for x in ohlc]

    if side == "BUY":
        sl = min(lows) * 0.997
        risk = entry - sl
        return sl, entry+risk, entry+2*risk, entry+3*risk
    else:
        sl = max(highs) * 1.003
        risk = sl - entry
        return sl, entry-risk, entry-2*risk, entry-3*risk

def fmt(p):
    return f"${p:,.6f}" if p < 1 else f"${p:,.2f}"

def format_msg(cat, sym, side, entry, sl, tp1, tp2, tp3, conf, chg1h, chg24, time_str):
    emoji = "🔥🚀💰" if cat == "MEME" else "🚨💎"
    arrow = "🟢📈" if side == "BUY" else "🔴📉"

    return (
        f"{emoji} *{sym} {side} SIGNAL* {emoji}\n"
        f"{arrow} *Action:* {side}\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎯 *Entry:* {fmt(entry)}\n"
        f"🛑 *Stop Loss:* {fmt(sl)}\n"
        f"✅ *TP1:* {fmt(tp1)}\n"
        f"✅ *TP2:* {fmt(tp2)}\n"
        f"✅ *TP3:* {fmt(tp3)}\n"
        f"━━━━━━━━━━━━━━\n"
        f"⚡ 1h: {chg1h:+.2f}% | 24h: {chg24:+.2f}%\n"
        f"🧠 *Confidence:* {conf}/100\n"
        f"⏰ {time_str}\n"
        f"_Not financial advice_"
    )

# ======================
# MAIN LOOP
# ======================
def main():
    conn = db_connect()
    send_message("📊✅ *Tracking Enabled!* Signals are now logged to PostgreSQL 🚀")

    while True:
        try:
            now = datetime.now(timezone.utc).strftime("%H:%M UTC")
            alerts = []

            majors = fetch_markets(top_n=MAJORS_TOP_N)
            memes = fetch_markets(ids=MEME_IDS)

            for group, data, min24, min1h, cd in [
                ("MAJOR", majors, MAJOR_MIN_24H, MAJOR_MIN_1H, MAJOR_COOLDOWN),
                ("MEME", memes, MEME_MIN_24H, MEME_MIN_1H, MEME_COOLDOWN),
            ]:
                for c in data:
                    sym = c["symbol"].upper()
                    chg1h = c["price_change_percentage_1h_in_currency"]
                    chg24 = c["price_change_percentage_24h"]

                    if chg1h is None or chg24 is None:
                        continue

                    side = "BUY" if chg24 > min24 and chg1h > min1h else \
                           "SELL" if chg24 < -min24 and chg1h < -min1h else None
                    if not side:
                        continue
                    if not should_alert(sym, side, cd):
                        continue

                    entry = float(c["current_price"])
                    sl, tp1, tp2, tp3 = build_levels(c["id"], entry, side)
                    conf = score(chg24, chg1h)

                    db_insert(conn, (
                        datetime.now(timezone.utc).isoformat(),
                        group, sym, c["id"], side,
                        entry, sl, tp1, tp2, tp3,
                        conf, chg1h, chg24
                    ))

                    alerts.append(format_msg(group, sym, side, entry, sl, tp1, tp2, tp3, conf, chg1h, chg24, now))

            if alerts:
                send_message("🚨🔥 *NEW TRADE SETUPS* 🔥🚨\n\n" + "\n\n".join(alerts[:MAX_ALERTS_PER_LOOP]))

        except Exception as e:
            print("Error:", e)

        time.sleep(CHECK_EVERY_SECONDS)

if __name__ == "__main__":
    main()
