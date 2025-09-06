# data_fetcher.py
import requests
from config import CMC_API_KEY

CMC_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"

def fetch_cmc_data():
    headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
    params = {
        "start": "1",      # start from rank 1
        "limit": "500",    # get 500 coins
        "convert": "USD"
    }
    try:
        response = requests.get(CMC_URL, headers=headers, params=params)
        response.raise_for_status()  # raise exception for bad status
        data = response.json()
        coins = data.get("data", [])
        if not coins:
            print("⚠️ No coin data returned from CoinMarketCap.")
        else:
            print(f"✅ Fetched {len(coins)} coins from CoinMarketCap.")
        return coins
    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching data: {e}")
        return []
