import os
import time
import requests
import psycopg2
from datetime import datetime, timezone, timedelta

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
SCAN_EVERY_SECONDS = 600          # 10 minutes (analyse 24/7)
SEND_ONCE_PER_HOUR = True         # only send Telegram once per hour
TOP_N_COINS = 150                 # Top 150 only (CoinGecko)
CONFIDENCE_MIN = 65               # only send signals >= 65 confidence
MAX_SIGNALS_PER_HOUR = 10         # prevent spam

# Momentum thresholds (tweakable)
MIN_24H = 1.5
MIN_1H = 0.4

# Cooldown to avoid repeats within same hour per symbol+side
ALERT_COOLDOWN_SECONDS = 60 * 60  # 1 hour
last_alert_time = {}

BOT_START_TIME = time.time()

# Pending signals bucket (collect during the hour, send hourly)
pending_signals = []
pending_keys = set()  # to dedupe signals in the hour: (symbol, side)

# ======================
# DATABASE
# ======================
def db_connect():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    # Store each signal as a "trade" to track win/loss later
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id SERIAL PRIMARY KEY,
            ts_utc TEXT NOT NULL,
            symbol TEXT NOT NULL,
            coin_id TEXT NOT NULL,
            coin_name TEXT NOT NULL,
            side TEXT NOT NULL,             -- LONG / SHORT
            entry DOUBLE PRECISION NOT NULL,
            stop_loss DOUBLE PRECISION NOT NULL,
            tp1 DOUBLE PRECISION NOT NULL,
            tp2 DOUBLE PRECISION NOT NULL,
            tp3 DOUBLE PRECISION NOT NULL,
            confidence INTEGER NOT NULL,
            chg1h DOUBLE PRECISION,
            chg24 DOUBLE PRECISION,
            status TEXT NOT NULL DEFAULT 'OPEN',  -- OPEN / CLOSED
            result TEXT,                          -- WIN / LOSS
            closed_ts_utc TEXT
        )
    """)
    conn.commit()
    return conn

def insert_trade(conn, ts_utc, symbol, coin_id, coin_name, side, entry, sl, tp1, tp2, tp3, conf, chg1h, chg24):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO trades (ts_utc, symbol, coin_id, coin_name, side, entry, stop_loss, tp1, tp2, tp3, confidence, chg1h, chg24)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (ts_utc, symbol, coin_id, coin_name, side, entry, sl, tp1, tp2, tp3, conf, chg1h, chg24))
    conn.commit()

def get_win_stats(conn):
    """
    Returns: (all_time_win_pct, all_time_total, last7_win_pct, last7_total)
    """
    cur = conn.cursor()

    # All-time closed
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE status='CLOSED') AS total,
            COUNT(*) FILTER (WHERE status='CLOSED' AND result='WIN') AS wins
        FROM trades
    """)
    total, wins = cur.fetchone()
    total = total or 0
    wins = wins or 0
    all_time_win_pct = (wins / total * 100.0) if total > 0 else 0.0

    # Last 7 days closed
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    cur.execute("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE result='WIN') AS wins
        FROM trades
        WHERE status='CLOSED'
          AND closed_ts_utc IS NOT NULL
          AND closed_ts_utc >= %s
    """, (seven_days_ago.isoformat(),))
    total7, wins7 = cur.fetchone()
    total7 = total7 or 0
    wins7 = wins7 or 0
    last7_win_pct = (wins7 / total7 * 100.0) if total7 > 0 else 0.0

    return all_time_win_pct, total, last7_win_pct, total7

def close_trade(conn, trade_id, result):
    cur = conn.cursor()
    now_iso = datetime.now(timezone.utc).isoformat()
    cur.execute("""
        UPDATE trades
        SET status='CLOSED',
            result=%s,
            closed_ts_utc=%s
        WHERE id=%s
    """, (result, now_iso, trade_id))
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
def fetch_top_markets(top_n=150):
    """
    Uses /coins/markets which already returns:
    - current_price
    - price_change_percentage_1h_in_currency
    - price_change_percentage_24h
    - high_24h / low_24h
    This avoids hammering OHLC endpoint and massively reduces 429 errors.
    """
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "sparkline": "false",
        "price_change_percentage": "1h,24h",
        "per_page": top_n,
        "page": 1,
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def fetch_simple_price_usd(coin_ids):
    """
    For monitoring open trades without hitting heavy endpoints.
    Returns dict: {coin_id: price_usd}
    """
    if not coin_ids:
        return {}
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {
        "ids": ",".join(coin_ids),
        "vs_currencies": "usd"
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    return {k: v.get("usd") for k, v in data.items()}

# ======================
# LOGIC
# ======================
def should_alert(symbol, side):
    now = int(time.time())
    key = f"{symbol}:{side}"
    if now - last_alert_time.get(key, 0) < ALERT_COOLDOWN_SECONDS:
        return False
    last_alert_time[key] = now
    return True

def score(chg24, chg1h):
    """
    Your confidence score (0-100). Keep your style but enforce >= 65.
    """
    return max(0, min(100, int(abs(chg24) * 6 + abs(chg1h) * 30)))

def build_levels(entry, side, high_24h, low_24h):
    """
    Uses high_24h/low_24h so we DO NOT call /ohlc (reduces 429 massively).
    TP spacing rules:
      - LONG: wider ladder
      - SHORT: closer together
    """
    if entry is None or high_24h is None or low_24h is None:
        return None

    # Basic sanity
    if low_24h <= 0 or high_24h <= 0:
        return None

    if side == "LONG":
        sl = low_24h * 0.997
        risk = entry - sl
        if risk <= 0:
            return None
        # Wider targets for LONG
        tp1 = entry + 1.0 * risk
        tp2 = entry + 2.0 * risk
        tp3 = entry + 3.0 * risk
        return sl, tp1, tp2, tp3

    else:  # SHORT
        sl = high_24h * 1.003
        risk = sl - entry
        if risk <= 0:
            return None
        # Closer targets for SHORT
        tp1 = entry - 0.8 * risk
        tp2 = entry - 1.1 * risk
        tp3 = entry - 1.4 * risk
        return sl, tp1, tp2, tp3

# ======================
# MESSAGE FORMAT
# ======================
def fmt_price(p):
    return f"${p:,.6f}" if p < 1 else f"${p:,.2f}"

def format_signal_msg(coin_name, sym, side, entry, sl, tp1, tp2, tp3, conf, chg1h, chg24, time_str):
    direction = "🟢 *LONG*" if side == "LONG" else "🔴 *SHORT*"

    return (
        f"🚨 *TRADE SIGNAL* 🚨\n"
        f"*{coin_name}* `({sym})`\n"
        f"{direction} • *Confidence:* {conf}/100\n"
        f"⏰ *Time:* {time_str}\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎯 *Entry:* `{fmt_price(entry)}`\n"
        f"🛑 *Stop Loss:* `{fmt_price(sl)}`\n"
        f"✅ *TP1:* `{fmt_price(tp1)}`\n"
        f"✅ *TP2:* `{fmt_price(tp2)}`\n"
        f"✅ *TP3:* `{fmt_price(tp3)}`\n"
        f"━━━━━━━━━━━━━━\n"
        f"📈 *Momentum:* 1h `{chg1h:+.2f}%` | 24h `{chg24:+.2f}%`\n"
        f"_Not financial advice_"
    )

def format_hourly_header(conn, time_str):
    all_win, all_total, win7, total7 = get_win_stats(conn)

    return (
        f"🧠 *Hourly Market Scan* ({time_str})\n"
        f"📊 *Win Rate:* All-time `{all_win:.1f}%` ({all_total} trades) | Last 7D `{win7:.1f}%` ({total7} trades)\n"
        f"━━━━━━━━━━━━━━"
    )

# ======================
# TRADE OUTCOME TRACKING
# ======================
def update_open_trades(conn):
    """
    Very simple outcome tracker:
    - WIN if price has crossed TP1 in the correct direction
    - LOSS if price has crossed SL
    This is not perfect (no intrabar history), but it gives you a real running win %.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT id, coin_id, side, stop_loss, tp1
        FROM trades
        WHERE status='OPEN'
        ORDER BY id ASC
        LIMIT 200
    """)
    rows = cur.fetchall()
    if not rows:
        return

    coin_ids = list({r[1] for r in rows})
    prices = fetch_simple_price_usd(coin_ids)

    for trade_id, coin_id, side, sl, tp1 in rows:
        px = prices.get(coin_id)
        if px is None:
            continue

        if side == "LONG":
            if px <= sl:
                close_trade(conn, trade_id, "LOSS")
            elif px >= tp1:
                close_trade(conn, trade_id, "WIN")
        else:  # SHORT
            if px >= sl:
                close_trade(conn, trade_id, "LOSS")
            elif px <= tp1:
                close_trade(conn, trade_id, "WIN")

# ======================
# SCAN FUNCTION
# ======================
def scan_and_collect(conn):
    global pending_signals, pending_keys

    markets = fetch_top_markets(TOP_N_COINS)
    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    ts_iso = datetime.now(timezone.utc).isoformat()

    for c in markets:
        chg1h = c.get("price_change_percentage_1h_in_currency")
        chg24 = c.get("price_change_percentage_24h")
        entry = c.get("current_price")
        high_24h = c.get("high_24h")
        low_24h = c.get("low_24h")

        if chg1h is None or chg24 is None or entry is None:
            continue

        # Direction decision (LONG / SHORT)
        side = None
        if chg24 > MIN_24H and chg1h > MIN_1H:
            side = "LONG"
        elif chg24 < -MIN_24H and chg1h < -MIN_1H:
            side = "SHORT"

        if not side:
            continue

        sym = (c.get("symbol") or "").upper()
        coin_id = c.get("id")
        coin_name = c.get("name") or sym

        if not sym or not coin_id:
            continue

        # cooldown per symbol+side
        if not should_alert(sym, side):
            continue

        conf = score(chg24, chg1h)
        if conf < CONFIDENCE_MIN:
            continue

        levels = build_levels(entry, side, high_24h, low_24h)
        if not levels:
            continue

        sl, tp1, tp2, tp3 = levels

        # Deduplicate within the hour
        key = (sym, side)
        if key in pending_keys:
            continue

        # Save trade to DB for win tracking
        insert_trade(conn, ts_iso, sym, coin_id, coin_name, side, entry, sl, tp1, tp2, tp3, conf, chg1h, chg24)

        # Add to pending to be sent on the hour
        pending_keys.add(key)
        pending_signals.append(
            format_signal_msg(coin_name, sym, side, entry, sl, tp1, tp2, tp3, conf, chg1h, chg24, now_str)
        )

        if len(pending_signals) >= MAX_SIGNALS_PER_HOUR:
            break

# ======================
# HOURLY SEND CONTROL
# ======================
def should_send_now(last_sent_hour):
    """
    Send once per UTC hour.
    """
    now = datetime.now(timezone.utc)
    hour_key = now.strftime("%Y-%m-%d %H")  # unique per hour
    if hour_key != last_sent_hour:
        return True, hour_key
    return False, last_sent_hour

def send_hourly_update(conn):
    global pending_signals, pending_keys

    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    header = format_hourly_header(conn, now_str)

    if pending_signals:
        body = "\n\n".join(pending_signals)
        msg = f"{header}\n\n{body}"
        send_message(msg)
    else:
        send_message(f"{header}\n\n❌ *No coins worth investing in.*\n\n_Not financial advice_")

    # Reset bucket for next hour
    pending_signals = []
    pending_keys = set()

# ======================
# MAIN LOOP
# ======================
def main():
    conn = db_connect()
    send_message("✅ Bot online. Analysing 24/7.\n⏳ Signals are sent once per hour.\n_Not financial advice_")

    last_sent_hour = None

    while True:
        try:
            # 1) Update trade outcomes (so win% stays live)
            update_open_trades(conn)

            # 2) Keep analysing (collect signals into hourly bucket)
            scan_and_collect(conn)

            # 3) Send once per hour
            do_send, last_sent_hour = should_send_now(last_sent_hour)
            if do_send:
                send_hourly_update(conn)

        except Exception as e:
            print("Error:", e)

        time.sleep(SCAN_EVERY_SECONDS)

if __name__ == "__main__":
    main()
