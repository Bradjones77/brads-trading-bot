import os
import requests
from datetime import datetime, timezone

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

COINS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "TRXUSDT", "AVAXUSDT", "LINKUSDT",
    "MATICUSDT", "DOTUSDT", "TONUSDT", "SHIBUSDT", "PEPEUSDT",
    "BCHUSDT", "LTCUSDT", "UNIUSDT", "NEARUSDT", "APTUSDT",
]

def send_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    r = requests.post(url, json=payload, timeout=20)
    print("Telegram:", r.status_code, r.text)

def fetch_binance_24h() -> dict:
    url = "https://api.binance.com/api/v3/ticker/24hr"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    return {item["symbol"]: item for item in data}

def fmt_row(symbol: str, item: dict) -> str:
    last = float(item["lastPrice"])
    chg = float(item["priceChangePercent"])
    vol = float(item["quoteVolume"])  # USDT volume
    return f"{symbol.replace('USDT',''):>5}  ${last:,.4f}  {chg:+.2f}%  vol ${vol/1e6:.1f}M"

def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("BOT_TOKEN or CHAT_ID is missing")

    all_24h = fetch_binance_24h()

    lines = []
    for sym in COINS:
        if sym in all_24h:
            lines.append(fmt_row(sym, all_24h[sym]))
        else:
            lines.append(f"{sym}: not found")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg = "📊 Crypto Snapshot (24h)\n" + now + "\n\n" + "\n".join(lines)

    # Telegram has a message length limit; this stays safe
    send_message(msg)

if __name__ == "__main__":
    main()
