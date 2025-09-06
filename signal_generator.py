# signal_generator.py
from data_fetcher import fetch_cmc_data
from telegram_bot import send_signal

def run_signals(coins):
    """
    Fetch signals for a list of coins and send them via Telegram.
    coins: list of coin symbols, e.g., ["BTC", "ETH", "DOGE"]
    """
    results = {}
    for coin in coins:
        try:
            data = fetch_cmc_data(coin)
            # Example logic: long or short based on market trend
            signal = "📈 Long-term" if data.get("trend") == "up" else "📉 Short-term"
            results[coin] = signal
        except Exception as e:
            results[coin] = f"❌ Error fetching signal ({e})"

    send_signal(results)
