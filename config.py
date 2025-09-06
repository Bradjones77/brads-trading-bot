# config.py

# Telegram Bot Token
TELEGRAM_TOKEN = "8493488991:AAHHMGBPWvLz-_vKJ-LM0Gae2AZm6cbR3jE"

# CoinMarketCap API Key
CMC_API_KEY = "2cc7c356-79e3-472d-853f-3db2675df271"

# List of coins to track (400+ coins, expandable)
COINS = [
    "BTC","ETH","USDT","BNB","XRP","ADA","DOGE","MATIC","SOL","DOT","SHIB","LTC",
    "TRX","AVAX","UNI","CRO","NEAR","FTM","ATOM","ALGO","LINK","XLM","BCH","VET",
    # Add remaining coins up to 400+
    "APE","PEPE","WBTC","LDO","ARB","DYDX","OP","APT","SUI","TON","LUNA2","XDC","RVN",
    "SC","FET","KLAY","XMR","VET","HIVE","DCR","NANO","ICX","CVC","NKN","DENT",
    "STMX","AKRO","LSK","CKB","PUNDIX","FRONT","FXS","MTL","RLC","MASK","API3",
    "SPELL","PERP","KEEP","DODO","TORN","ALPHA","CAKE","XVS","WOO","TRIBE","RAY",
    "TWT","AUDIO","LPT","MKR","OKB","HNT","CELO","KAVA","RUNE","FTT","IOST",
]

# Emoji mapping
EMOJIS = {
    "BUY": "🟢✅💎🚀",
    "SELL": "🔴❌📉💀",
    "HOLD": "🟡⚠️⏸️",
    "LONG": "📈🏦💰",
    "SHORT": "📉🔥⚡",
    "NEW": "🆕✨🎉",
    "HOT": "🔥🚀🌕",
    "COLD": "❄️🛑"
}

# Signal thresholds
SIGNAL_CONFIDENCE_THRESHOLD = 0.7
NEW_COIN_DETECTION = True

# Scan intervals
SCAN_INTERVAL_SECONDS = 60
PUSH_INTERVAL_MINUTES = 15
