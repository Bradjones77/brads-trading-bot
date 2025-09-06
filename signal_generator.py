# signal_generator.py
import requests
from config import COINS, CMC_API_KEY, EMOJIS

CMC_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"

HEADERS = {
    "Accepts": "application/json",
    "X-CMC_PRO_API_KEY": CMC_API_KEY
}

def fetch_coin_price(coin):
    try:
        response = requests.get(CMC_URL, headers=HEADERS, params={"symbol": coin})
        data = response.json()
        price = data["data"][coin]["quote"]["USD"]["price"]
        return price
    except Exception as e:
        return None

# Basic signal logic
def analyze_signal(price):
    # Placeholder logic (you can expand with real indicators)
    if price is None:
        return "❌ Error fetching signal"
    elif price % 2 == 0:  # Simple demo logic
        return f"{EMOJIS['BUY']} Buy"
    else:
        return f"{EMOJIS['SELL']} Sell"

# Get signal for a single coin
def get_coin_signal(coin):
    price = fetch_coin_price(coin)
    signal = analyze_signal(price)
    return f"{coin}: {signal} — ${price:.2f}" if price else f"{coin}: ❌ Error fetching data"

# Get signals for all coins
def get_all_signals():
    results = []
    for coin in COINS:
        results.append(get_coin_signal(coin))
    return results
