# main.py
from signal_generator import run_signals

# Example list of coins you want to track
coins = [
    "BTC", "ETH", "USDT", "BNB", "DOGE", "ADA", "SOL", "MATIC", "XRP"
]

if __name__ == "__main__":
    run_signals(coins)
