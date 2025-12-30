import os
import time
import requests
from datetime import datetime, timezone

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

CHECK_EVERY_SECONDS = 300
TOP_N = 30

def send_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    requests.post(url, json=payload, timeout=20)

def fetch_markets():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": TOP_N,
        "page": 1,
        "price_change_percentage": "1h,24h",
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def signal_from_coin(c):
    price = c["current_price"]
    chg_1h = c.get("price_change_percentage_1h_in_currency")
    chg_24h = c.get("price_change_percentage_24h")

    if chg_1h is None or chg_24h is None:
        return None

    # Simple logic (we refine later)
    if chg_24h > 2 and chg_1h > 0.5:
        return "BULLISH"
    if chg_24h < -2 and chg_1h < -0.5:
        return "BEARISH"

    return None

def main():
    send_message("🟢 Signal engine started")

    while True:
        try:
            data = fetch_markets()
            now = datetime.now(timezone.utc).strftime("%H:%M UTC")

            alerts = []
            for c in data:
                signal = signal_from_coin(c)
                if not signal:
                    continue

                sym = c["symbol"].upper()
                price = c["current_price"]
                chg1h = c["price_change_percentage_1h_in_currency"]
                chg24h = c["price_change_percentage_24h"]

                alerts.append(
                    f"{'📈' if signal=='BULLISH' else '📉'} {sym}\n"
                    f"Bias: {signal}\n"
                    f"Price: ${price:,.4f}\n"
                    f"1h: {chg1h:+.2f}% | 24h: {chg24h:+.2f}%\n"
                )

            if alerts:
                msg = f"🚨 Trade Bias Signals — {now}\n\n" + "\n".join(alerts[:10])
                send_message(msg)

        except Exception as e:
            print("Error:", e)

        time.sleep(CHECK_EVERY_SECONDS)

if __name__ == "__main__":
    main()
