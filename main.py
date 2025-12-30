import os
import requests
from datetime import datetime, timezone

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Top-ish mix. We can expand later to 50 + 50.
COINS = [
    "bitcoin", "ethereum", "solana", "binancecoin", "ripple",
    "dogecoin", "cardano", "tron", "avalanche-2", "chainlink",
    "polygon-ecosystem-token", "polkadot", "the-open-network", "shiba-inu", "pepe",
    "bitcoin-cash", "litecoin", "uniswap", "near", "aptos",
]

def send_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    r = requests.post(url, json=payload, timeout=20)
    print("Telegram:", r.status_code, r.text)

def fetch_coingecko_markets(ids):
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "ids": ",".join(ids),
        "order": "market_cap_desc",
        "per_page": len(ids),
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "24h",
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    return {item["id"]: item for item in data}

def fmt_row(item: dict) -> str:
    sym = (item.get("symbol") or "").upper()
    price = item.get("current_price")
    chg = item.get("price_change_percentage_24h")  # %
    mcap = item.get("market_cap") or 0

    price_str = f"${price:,.6f}" if price and price < 1 else f"${price:,.2f}"
    chg_str = f"{chg:+.2f}%" if chg is not None else "n/a"
    mcap_str = f"${mcap/1e9:.2f}B" if mcap else "n/a"

    return f"{sym:>6}  {price_str:>12}  {chg_str:>8}  mcap {mcap_str}"

def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("BOT_TOKEN or CHAT_ID is missing")

    markets = fetch_coingecko_markets(COINS)

    lines = []
    for cid in COINS:
        item = markets.get(cid)
        if not item:
            lines.append(f"{cid}: not found")
        else:
            lines.append(fmt_row(item))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg = "📊 Crypto Snapshot (CoinGecko, 24h)\n" + now + "\n\n" + "\n".join(lines)
    send_message(msg)

if __name__ == "__main__":
    main()
