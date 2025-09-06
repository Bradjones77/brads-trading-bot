# data_fetcher.py
import requests
from config import CMC_API_KEY

CMC_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"

def fetch_cmc_data():
    headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
    params = {"start": "1", "limit": "500", "convert": "USD"}
    response = requests.get(CMC_URL, headers=headers, params=params)
    data = response.json()
    return data.get("data", [])
