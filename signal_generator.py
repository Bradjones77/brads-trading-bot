# signal_generator.py
from data_fetcher import fetch_cmc_data
from telegram_bot import send_signal
import random

# Example logic for long/short signals
def generate_signal(coin):
    # Randomly pick Long or Short for demonstration
    signal_type = random.choice(["📈 Long-term", "📉 Short-term"])
    price = coin.get("quote", {}).get("USD", {}).get("price", 0)
    symbol = coin.get("symbol", "N/A")
    return f"{symbol} {signal_type} 💰 Price: ${price:.2f}"

def run_signals():
    coins = fetch_cmc_data()
    if not coins:
        send_signal("❌ Could not fetch any coin data.")
        return

    messages = []
    for coin in coins[:50]:  # limit to top 50 coins for Telegram messages
        msg = generate_signal(coin)
        messages.append(msg)

    # Send messages to Telegram one by one
    for message in messages:
        send_signal(message)
