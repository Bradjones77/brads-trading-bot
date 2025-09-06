# main.py

# Standard imports
import asyncio

# Safe optional import
try:
    from telegram_bot import build_app
except ImportError:
    build_app = None

# Import your signal generator
from signal_generator import run_signals

# Your list of coins (example, replace with your actual coins)
coins = [
    "BTC", "ETH", "USDT", "BNB", "XRP", "ADA", "DOGE", "MATIC", "SOL", "DOT",
    "SHIB", "LTC", "TRX", "AVAX", "UNI", "CRO", "NEAR", "FTM", "ATOM", "ALGO",
    # Add the rest of your coins here
]

# Main async function to run signals
async def main():
    # Run your signals
    await run_signals(coins)

# Run the bot
if __name__ == "__main__":
    asyncio.run(main())
