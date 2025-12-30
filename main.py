import os
import time
import requests
from datetime import datetime, timezone

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# SETTINGS (we can tune these)
CHECK_EVERY_SECONDS = 300          # 5 minutes
TOP_N = 50                         # top 50 by market cap
ALERT_UP_PCT = 3.0                 # alert if 24h change >= +3%
ALERT_DOWN_PCT = -3.0              # alert if 24h change <= -3%

# prevents repeat alerts every loop
last_alert_bucket = {}

def send_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    r = requests.post(url, json=payload, timeout=20)
    print("Telegram:", r.status_code, r.text)

def fetch_top_markets(top_n: int):
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": top_n,
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "24h",
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def fmt(item: dict) -> str:
    sym = (item.get("symbol") or "").upper()
    price = item.get("current_price")
    chg = item.get("price_change_percentage_24h")
    price_str = f"${price:,.6f}" if price and price < 1 else f"${price:,.2f}"
    return f"{sym}  {price_str}  {chg:+.2f}%"

def should_alert(item: dict):
    chg = item.get("price_change_percentage_24h")
    if chg is None:
        return False, None

    if chg >= ALERT_UP_PCT:
        return True, "UP"
    if chg <= ALERT_DOWN_PCT:
        return True, "DOWN"
    return False, None

def alert_bucket(chg: float) -> int:
    # group alerts so we don't spam on tiny changes
    # e.g. 3.0–3.9 -> 3, 4.0–4.9 -> 4, etc.
    return int(abs(chg))

def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("BOT_TOKEN or CHAT_ID is missing")

    send_message("🟢 Movers bot started (CoinGecko). I'll alert on strong 24h movers.")

    while True:
        try:
            data = fetch_top_markets(TOP_N)
            now = datetime.now(timezone.utc).strftime("%H:%M UTC")

            alerts = []
            for item in data:
                ok, direction = should_alert(item)
                if not ok:
                    continue

                sym = (item.get("symbol") or "").upper()
                chg = float(item.get("price_change_percentage_24h"))
                bucket = alert_bucket(chg)

                # prevent repeated alerts within same "bucket"
                key = f"{sym}:{direction}"
                if last_alert_bucket.get(key) == bucket:
                    continue
                last_alert_bucket[key] = bucket

                alerts.append((direction, chg, item))

            if alerts:
                # sort: biggest movers first
                alerts.sort(key=lambda x: abs(x[1]), reverse=True)

                lines = []
                for direction, chg, item in alerts[:15]:  # cap message length
                    arrow = "📈" if direction == "UP" else "📉"
                    lines.append(f"{arrow} {fmt(item)}")

                msg = f"🚨 Strong Movers (Top {TOP_N}) — {now}\n\n" + "\n".join(lines)
                send_message(msg)
            else:
                print("No alerts", now)

        except Exception as e:
            # keep running even if API hiccups
            print("Error:", repr(e))

        time.sleep(CHECK_EVERY_SECONDS)

if __name__ == "__main__":
    main()
