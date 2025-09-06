# config.py
import os

# Telegram bot token
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8493488991:AAHHMGBPWvLz-_vKJ-LM0Gae2AZm6cbR3jE")

# CoinMarketCap API key
CMC_API_KEY = os.getenv("CMC_API_KEY", "2cc7c356-79e3-472d-853f-3db2675df271")

# OpenAI API key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "organizations/8e2204fc-8ff0-4c72-88e6-fa1545b6c784/apiKeys/9250399e-6916-46a4-9f0b-650a26f56f5c")

# List of coins to trade (start with 400+ coins)
COINS = [
    "BTC","ETH","USDT","BNB","XRP","ADA","DOGE","MATIC","SOL","DOT",
    "SHIB","LTC","TRX","AVAX","UNI","CRO","NEAR","FTM","ATOM","ALGO",
    "LINK","XLM","BCH","VET","ICP","FIL","EGLD","APE","EOS","THETA",
    # Add more coins as needed or dynamically fetch
]
