import os

# Telegram bot token
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# CoinMarketCap API key
CMC_API_KEY = os.getenv("CMC_API_KEY")

# OpenAI API Key (optional if using AI features)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# List of coins to track
COINS = [
    "BTC", "ETH", "USDT", "BNB", "XRP", "ADA", "DOGE", "MATIC", "SOL", "DOT",
    "SHIB", "LTC", "TRX", "AVAX", "UNI", "CRO", "NEAR", "FTM", "ATOM", "ALGO",
    "LINK", "XLM", "BCH", "VET", "ICP", "FIL", "EGLD", "APE", "EOS", "THETA"
    # add up to 400 coins as needed
]
