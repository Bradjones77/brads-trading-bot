# config.py - configuration & defaults
import os
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', "8249914971:AAHZsWf0mzExoMDe2VDLkP6xarmk1L9Vtjg")
COINBASE_API_BASE = os.getenv('COINBASE_API_BASE', "https://api.exchange.coinbase.com")
COINS = os.getenv('COINS', "BTC-USD,ETH-USD,DOGE-USD,SHIB-USD").split(',')  # CSV or edit here
PUSH_INTERVAL_MINUTES = int(os.getenv('PUSH_INTERVAL_MINUTES', "60"))
SCAN_INTERVAL_SECONDS = int(os.getenv('SCAN_INTERVAL_SECONDS', "300"))
MODEL_DIR = os.getenv('MODEL_DIR','models')
DATA_DIR = os.getenv('DATA_DIR','data')
DB_PATH = os.getenv('DB_PATH','bot_data.db')
