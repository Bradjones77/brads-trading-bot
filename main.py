import os
import time
import requests
from datetime import datetime, timezone

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

CHECK_EVERY_SECONDS = 300          # 5 minutes
TOP_N = 30
MAX_ALERTS_PER_LOOP = 6

# Tune these later
MIN_24H_TREND = 2.0                # % 24h move considered trending
MIN_1H_MOMENTUM = 0.6              # % 1h move considered strong momentum

COOLDOWN_SECONDS = 60 * 60         # 1 hour cooldown per coin/direction
last_alert_time = {}

def send_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    r = requests.post(url, json=payload, timeout=20)
    print("Telegram:", r.status_code)

def fetch_markets():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": TOP_N,
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "1h,24h",
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def fetch_ohlc(coin_id: str, days: int = 1):
    # days=1 returns 5-minute candles
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"
    params = {"vs_currency": "usd", "days": days}
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def clamp(n, lo, hi):
    return max(lo, min(hi, n))

def score_signal(chg24: float, chg1h: float, side: str) -> int:
    trend_pts = clamp(int(abs(chg24) * 6), 0, 60)      # up to 60
    mom_pts   = clamp(int(abs(chg1h) * 30), 0, 30)     # up to 30
    aligned = (chg24 > 0 and chg1h > 0 and side == "BUY") or (chg24 < 0 and chg1h < 0 and side == "SELL")
    bonus = 10 if aligned else -10
    return clamp(trend_pts + mom_pts + bonus, 0, 100)

def pretty_price(x: float) -> str:
    if x < 1:
        return f"${x:,.6f}"
    return f"${x:,.2f}"

def should_alert(symbol: str, side: str) -> bool:
    now = int(time.time())
    key = f"{symbol}:{side}"
    last = last_alert_time.get(key, 0)
    if now - last < COOLDOWN_SECONDS:
        return False
    last_alert_time[key] = now
    return True

def build_trade_levels(coin_id: str, entry: float, side: str):
    """
    Build Entry/SL/TPs using last ~12h of 5m OHLC.
    BUY: SL near swing low, TPs at 1R/2R/3R
    SELL: SL near swing high, TPs at 1R/2R/3R
    """
    ohlc = fetch_ohlc(coin_id, days=1)
    if not ohlc or len(ohlc) < 30:
        return None

    recent = ohlc[-144:] if len(ohlc) >= 144 else ohlc  # ~12h
    highs = [c[2] for c in recent]
    lows  = [c[3] for c in recent]

    swing_high = max(highs)
    swing_low = min(lows)

    # small buffer so SL isn't exactly on the swing
    buffer_pct = 0.002  # 0.2%

    if side == "BUY":
        sl = swing_low * (1 - buffer_pct)
        risk = entry - sl
        if risk <= 0:
            return None
        tp1 = entry + 1 * risk
        tp2 = entry + 2 * risk
        tp3 = entry + 3 * risk
    else:  # SELL
        sl = swing_high * (1 + buffer_pct)
        risk = sl - entry
        if risk <= 0:
            return None
        tp1 = entry - 1 * risk
        tp2 = entry - 2 * risk
        tp3 = entry - 3 * risk

    return {
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk_pct": (risk / entry) * 100.0,
    }

def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("BOT_TOKEN or CHAT_ID is missing")

    send_message("🟢 Signal engine started (Professional BUY/SELL + TP1–TP3 + SL).")

    while True:
        try:
            data = fetch_markets()
            now = datetime.now(timezone.utc).strftime("%H:%M UTC")

            alerts = []

            for c in data:
                sym = c["symbol"].upper()
                coin_id = c["id"]
                entry = float(c["current_price"])

                chg1h = c.get("price_change_percentage_1h_in_currency")
                chg24 = c.get("price_change_percentage_24h")
                if chg1h is None or chg24 is None:
                    continue

                side = None
                if chg24 >= MIN_24H_TREND and chg1h >= MIN_1H_MOMENTUM:
                    side = "BUY"
                elif chg24 <= -MIN_24H_TREND and chg1h <= -MIN_1H_MOMENTUM:
                    side = "SELL"
                else:
                    continue

                if not should_alert(sym, side):
                    continue

                levels = build_trade_levels(coin_id, entry, side)
                if not levels:
                    continue

                conf = score_signal(float(chg24), float(chg1h), side)

                emoji = "🟩" if side == "BUY" else "🟥"
                action = "BUY" if side == "BUY" else "SELL"
                title_emoji = "🚀" if side == "BUY" else "⚠️"

                msg = (
                    f"{title_emoji} {emoji} {sym} SIGNAL ({action})\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🎯 Entry: {pretty_price(entry)}\n"
                    f"🛑 Stop Loss: {pretty_price(levels['sl'])}\n"
                    f"✅ TP1: {pretty_price(levels['tp1'])}\n"
                    f"✅ TP2: {pretty_price(levels['tp2'])}\n"
                    f"✅ TP3: {pretty_price(levels['tp3'])}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📊 Momentum: 1h {float(chg1h):+.2f}% | 24h {float(chg24):+.2f}%\n"
                    f"🧠 Confidence: {conf}/100\n"
                    f"⚖️ Risk (approx): {levels['risk_pct']:.2f}%\n"
                    f"⏰ Time: {now}"
                )

                alerts.append((conf, msg))

            if alerts:
                alerts.sort(key=lambda x: x[0], reverse=True)
                top = alerts[:MAX_ALERTS_PER_LOOP]
                send_message("📣 NEW TRADE SETUPS\n\n" + "\n\n".join(m for _, m in top))
            else:
                print("No signals", now)

        except Exception as e:
            print("Error:", repr(e))

        time.sleep(CHECK_EVERY_SECONDS)

if __name__ == "__main__":
    main()
