# signal_generator.py
from data_fetcher import fetch_cmc_data
from config import COINS

def run_signals():
    data = fetch_cmc_data()
    signals = {}
    for coin in COINS:
        coin_data = next((x for x in data if x["symbol"] == coin), None)
        if coin_data:
            # Simple example: bullish if 24h % change > 2%
            signals[coin] = "📈 Buy" if coin_data["quote"]["USD"]["percent_change_24h"] > 2 else "📉 Hold/Sell"
        else:
            signals[coin] = "❌ No data"
    return signals
