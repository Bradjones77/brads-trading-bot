import os
import requests
from config import CMC_API_KEY

BASE_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"

def fetch_cmc_data(coin):
    headers = {
        "X-CMC_PRO_API_KEY": CMC_API_KEY
    }
    params = {
        "symbol": coin,
        "convert": "USD"
    }
    response = requests.get(BASE_URL, headers=headers, params=params)
    data = response.json()
    try:
        price = data["data"][coin]["quote"]["USD"]["price"]
        # Simple trend example (replace with your strategy)
        trend = "🔼" if price % 2 == 0 else "🔽"
        return {"price": price, "trend": trend}
    except Exception:
        return {"price": None, "trend": "❌ Error fetching"}
