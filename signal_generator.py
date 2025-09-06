from data_fetcher import fetch_cmc_data
from config import COINS

def run_signals(coins=COINS):
    signals = {}
    for coin in coins:
        data = fetch_cmc_data(coin)
        signals[coin] = f"{data['trend']} ${data['price'] if data['price'] else 'N/A'}"
    return signals
