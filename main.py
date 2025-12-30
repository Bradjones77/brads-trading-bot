import os
import time
import requests
from datetime import datetime, timezone

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

CHECK_EVERY_SECONDS = 300
MAJORS_TOP_N = 30
MAX_ALERTS_PER_LOOP = 8

# Major thresholds (stricter)
MAJOR_MIN_24H = 2.0
MAJOR_MIN_1H  = 0.6

# Meme thresholds (looser = more alerts)
MEME_MIN_24H = 4.0
MEME_MIN_1H  = 1.2

# Meme coins (CoinGecko IDs) – we can expand later
MEME_IDS = [
    "dogecoin",
    "shiba-inu",
    "pepe",
    "bonk",
    "dogwifcoin",
    "floki",
    "baby-doge-coin",
    "memecoin-2",
    "book-of-meme",
    "mog-coin",
]

# Cooldowns
MAJOR_COOLDOWN = 60 * 60          # 1 hour
MEME_COOLDOWN  = 2 * 60 * 60      # 2 hours (memes spam more)

last_alert_time = {}  # key: "BTC:BUY" -> unix time

def send_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    r = requests.post(url, json=payload, timeout=20)
    print("Telegram:", r.status_code)

def fetch_markets_top(top_n: int):
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": top_n,
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "1h,24h",
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def fetch_markets_ids(ids: list[str]):
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "ids": ",".join(ids),
        "order": "market_cap_desc",
        "per_page": len(ids),
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "1h,24h",
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def fetch_ohlc(coin_id: str, days: int = 1):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"
    params = {"vs_currency": "usd", "days": days}
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def pretty_price(x: float) -> str:
    return f"${x:,.6f}" if x < 1 else f"${x:,.2f}"

def clamp(n, lo, hi):
    return max(lo, min(hi, n))

def score_signal(chg24: float, chg1h: float, side: str) -> int:
    trend_pts = clamp(int(abs(chg24) * 6), 0, 60)
    mom_pts   = clamp(int(abs(chg1h) * 30), 0, 30)
    aligned = (chg24 > 0 and chg1h > 0 and side == "BUY") or (chg24 < 0 and chg1h < 0 and side == "SELL")
    bonus = 10 if aligned else -10
    return clamp(trend_pts + mom_pts + bonus, 0, 100)

def should_alert(symbol: str, side: str, cooldown: int) -> bool:
    now = int(time.time())
    key = f"{symbol}:{side}"
    last = last_alert_time.get(key, 0)
    if now - last < cooldown:
        return False
    last_alert_time[key] = now
    return True

def build_trade_levels(coin_id: str, entry: float, side: str):
    ohlc = fetch_ohlc(coin_id, days=1)
    if not ohlc or len(ohlc) < 30:
        return None

    recent = ohlc[-144:] if len(ohlc) >= 144 else ohlc  # ~12h
    highs = [c[2] for c in recent]
    lows  = [c[3] for c in recent]
    swing_high = max(highs)
    swing_low = min(lows)

    buffer_pct = 0.003  # 0.3% (memes wick more, still ok for majors)

    if side == "BUY":
        sl = swing_low * (1 - buffer_pct)
        risk = entry - sl
        if risk <= 0:
            return None
        tp1 = entry + 1 * risk
        tp2 = entry + 2 * risk
        tp3 = entry + 3 * risk
    else:
        sl = swing_high * (1 + buffer_pct)
        risk = sl - entry
        if risk <= 0:
            return None
        tp1 = entry - 1 * risk
        tp2 = entry - 2 * risk
        tp3 = entry - 3 * risk

    return {"sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3, "risk_pct": (risk / entry) * 100.0}

def format_signal(sym: str, side: str, entry: float, levels: dict, chg1h: float, chg24: float, conf: int, now: str, category: str) -> str:
    if category == "MEME":
        tag = "💥🐶 *MEME COIN* 🐸💥"
        header = f"🔥🚀💰 *{sym} {side} SIGNAL* 💰🚀🔥"
    else:
        tag = "🏛️📊 *MAJOR COIN* 📊🏛️"
        header = f"🚨💎 *{sym} {side} SETUP* 💎🚨"

    side_emoji = "🟢📈" if side == "BUY" else "🔴📉"

    return (
        f"{tag}\n"
        f"{header}\n"
        f"{side_emoji} *Action:* {side}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *Entry:* {pretty_price(entry)}\n"
        f"🛑 *Stop Loss:* {pretty_price(levels['sl'])}\n"
        f"✅ *TP1:* {pretty_price(levels['tp1'])}\n"
        f"✅ *TP2:* {pretty_price(levels['tp2'])}\n"
        f"✅ *TP3:* {pretty_price(levels['tp3'])}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⚡ *Momentum:* 1h {chg1h:+.2f}% | 24h {chg24:+.2f}%\n"
        f"🧠 *Confidence:* {conf}/100\n"
        f"⚖️ *Risk:* ~{levels['risk_pct']:.2f}%\n"
        f"⏰ *Time:* {now}\n"
        f"💎 *Manage risk — not financial advice.*"
    )

def detect_side(chg24: float, chg1h: float, min24: float, min1h: float):
    if chg24 >= min24 and chg1h >= min1h:
        return "BUY"
    if chg24 <= -min24 and chg1h <= -min1h:
        return "SELL"
    return None

def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("BOT_TOKEN or CHAT_ID is missing")

    send_message("🔥✅ *Meme Mode Enabled!* Now scanning MAJORS + MEMES for BUY/SELL + TP1–TP3 + SL 🚀")

    while True:
        try:
            now = datetime.now(timezone.utc).strftime("%H:%M UTC")

            majors = fetch_markets_top(MAJORS_TOP_N)
            memes = fetch_markets_ids(MEME_IDS)

            alerts = []

            # Majors
            for c in majors:
                sym = c["symbol"].upper()
                coin_id = c["id"]
                entry = float(c["current_price"])
                chg1h = c.get("price_change_percentage_1h_in_currency")
                chg24 = c.get("price_change_percentage_24h")
                if chg1h is None or chg24 is None:
                    continue

                side = detect_side(float(chg24), float(chg1h), MAJOR_MIN_24H, MAJOR_MIN_1H)
                if not side:
                    continue
                if not should_alert(sym, side, MAJOR_COOLDOWN):
                    continue

                levels = build_trade_levels(coin_id, entry, side)
                if not levels:
                    continue

                conf = score_signal(float(chg24), float(chg1h), side)
                msg = format_signal(sym, side, entry, levels, float(chg1h), float(chg24), conf, now, "MAJOR")
                alerts.append((conf, msg))

            # Memes
            for c in memes:
                sym = c["symbol"].upper()
                coin_id = c["id"]
                entry = float(c["current_price"])
                chg1h = c.get("price_change_percentage_1h_in_currency")
                chg24 = c.get("price_change_percentage_24h")
                if chg1h is None or chg24 is None:
                    continue

                side = detect_side(float(chg24), float(chg1h), MEME_MIN_24H, MEME_MIN_1H)
                if not side:
                    continue
                if not should_alert(sym, side, MEME_COOLDOWN):
                    continue

                levels = build_trade_levels(coin_id, entry, side)
                if not levels:
                    continue

                conf = score_signal(float(chg24), float(chg1h), side)
                msg = format_signal(sym, side, entry, levels, float(chg1h), float(chg24), conf, now, "MEME")
                alerts.append((conf, msg))

            if alerts:
                alerts.sort(key=lambda x: x[0], reverse=True)
                top = alerts[:MAX_ALERTS_PER_LOOP]
                send_message("🚨🔥 *NEW SETUPS INCOMING!* 🔥🚨\n\n" + "\n\n".join(m for _, m in top))
            else:
                print("No setups", now)

        except Exception as e:
            print("Error:", repr(e))

        time.sleep(CHECK_EVERY_SECONDS)

if __name__ == "__main__":
    main()
