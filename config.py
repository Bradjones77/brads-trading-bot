# config.py - Bot configuration

# Telegram Bot Token
TELEGRAM_TOKEN = "8493488991:AAHHMGBPWvLz-_vKJ-LM0Gae2AZm6cbR3jE"

# CoinMarketCap API Key
CMC_API_KEY = "2cc7c356-79e3-472d-853f-3db2675df271"

# List of 400+ coins/tokens to track (expandable)
COINS = [
    "BTC","ETH","USDT","BNB","XRP","ADA","DOGE","MATIC","SOL","DOT","SHIB","LTC",
    "TRX","AVAX","UNI","CRO","NEAR","FTM","ATOM","ALGO","LINK","XLM","BCH","VET",
    "ICP","FIL","EGLD","APE","EOS","THETA","HBAR","SAND","GRT","CHZ","KSM","STX",
    "QNT","CFX","ZIL","ENJ","BAT","DCR","NEO","1INCH","FLOW","LRC","ZRX","RUNE",
    "CELO","AR","KAVA","MANA","UMA","REV","KNC","HNT","OKB","CRV","MINA","AUDIO",
    "OCEAN","LPT","ANKR","GLM","CVX","BAL","SRM","IOST","SKL","SXP","XTZ","IOTA",
    "XEM","QTUM","FTT","WAXP","MKR","DGB","HIVE","OGN","STORJ","LUNA","RSR","AMP",
    "XCH","NANO","GNO","ZEN","ARDR","OXT","REQ","REN","ICX","COTI","NKN","DENT",
    "STMX","AKRO","LSK","CKB","PUNDIX","CVC","ONT","LOOM","FET","POLY","TWT","RAY",
    "MASK","API3","FXS","SPELL","MTL","KEEP","DODO","PERP","SUSHI","BTRST","KP3R",
    "TRIBE","WOO","XVS","CAKE","ALPHA","TORN","AAVE","COMP","SNX","YFI",
    "PEPE","WBTC","LDO","ARB","DYDX","OP","APT","SUI","TON","LUNA2","XDC","RVN",
    "SC","FET","KLAY","XMR","VET","HIVE","DCR","NANO","ICX","CVC","NKN","DENT",
    "STMX","AKRO","LSK","CKB","PUNDIX","FRONT","FXS","MTL","RLC","MASK","API3",
    "SPELL","PERP","KEEP","DODO","TORN","ALPHA","CAKE","XVS","WOO","TRIBE","RAY",
]

# Push interval in minutes (how often the bot sends top signals)
PUSH_INTERVAL_MINUTES = 15

# Scan interval in seconds (how often bot checks coin data)
SCAN_INTERVAL_SECONDS = 60

# Emoji mapping for signal outputs
EMOJIS = {
    "BUY": "🟢✅💎🚀",         # Buy opportunities
    "SELL": "🔴❌📉💀",        # Sell signals
    "HOLD": "🟡⚠️⏸️",          # Hold/Wait
    "LONG": "📈🏦💰",           # Long-term trend
    "SHORT": "📉🔥⚡",          # Short-term trend
    "NEW": "🆕✨🎉",             # Newly listed coin
    "HIGH_VOL": "🌪️💥",        # High volatility
    "LOW_VOL": "🛡️⛱️",         # Low volatility
    "HOT": "🔥🚀🌕",             # Hot trending coin
    "COLD": "❄️🛑",             # Cold/weak coin
}

# Signal thresholds
SIGNAL_CONFIDENCE_THRESHOLD = 0.7  # 70% confidence for sending signal
NEW_COIN_DETECTION = True          # Track new coins listed on market
