# coinbase_fetcher.py - fetch products & candles from Coinbase Exchange
import requests, time
from config import COINBASE_API_BASE
HEADERS = {'User-Agent':'AdvancedCoinbaseBot/2.0'}

def list_usd_products(limit=500):
    url = COINBASE_API_BASE + '/products'
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    prods = r.json()
    out = [p['id'] for p in prods if p.get('quote_currency')=='USD' and not p.get('trading_disabled',False)]
    return out[:limit]

def fetch_candles(product_id, granularity=300, limit=300):
    url = COINBASE_API_BASE + f'/products/{product_id}/candles'
    params={'granularity':granularity}
    r = requests.get(url, headers=HEADERS, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    data.sort(key=lambda x: x[0])
    return data[-limit:]
