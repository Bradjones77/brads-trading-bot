import os
import time
import math
import requests
import psycopg2
from datetime import datetime, timezone, timedelta

# ======================
# OPTIONAL AI (FAIL-SAFE)
# ======================
try:
    from ai_guard import ai_enabled, judge_trade
except Exception:
    def ai_enabled() -> bool:
        return False
    def judge_trade(trade_context):
        raise RuntimeError("AI not available")

# ======================
# ENVIRONMENT VARIABLES
# ======================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

# ✅ OPTIONAL: set to "1" ONE TIME if you want to wipe trade history + reset win-rate stats to 0
RESET_STATS_ON_START = os.getenv("RESET_STATS_ON_START", "0") == "1"

if not BOT_TOKEN or not CHAT_ID or not DATABASE_URL:
    raise RuntimeError("BOT_TOKEN, CHAT_ID, or DATABASE_URL missing")

# ======================
# SETTINGS
# ======================
# ✅ UPDATED (Option B): scan every 14 minutes by default (override in Railway env var if you want)
SCAN_EVERY_SECONDS = int(os.getenv("SCAN_EVERY_SECONDS", "840"))  # 14 mins

CONFIDENCE_MIN = 65

# Safety spam guard (rolling hour)
MAX_SIGNALS_PER_HOUR = 10

# ✅ send signals ONLY on quarter-hour batches (00,15,30,45)
SEND_BATCH_EVERY_MINUTES = int(os.getenv("SEND_BATCH_EVERY_MINUTES", "15"))  # do not change unless you want different schedule

# Momentum thresholds
MIN_24H = 1.5
MIN_1H = 0.4

# Cooldown per symbol+side (persistent via DB)
ALERT_COOLDOWN_SECONDS = 60 * 60  # 1 hour

# In-memory fallback cooldown
last_alert_time = {}

# ✅ RAM spam guard store
_signal_times = []

# ✅ Pending queue (signals found during scans)
pending_signals = []
pending_keys = set()  # (SYM, SIDE) to avoid duplicates within the same batch window

# Memory rules
MEM_STRICT_MIN_TRADES = 6
MEM_STRICT_BLOCK_BELOW_WINRATE = 0.30
MEM_SOFT_MIN_TRADES = 4
MEM_SOFT_PENALIZE_BELOW_WINRATE = 0.45
MEM_SOFT_PENALTY = -10
MEM_LOOKBACK_DAYS = 14

# CoinGecko rate protection
COINGECKO_TIMEOUT = 30
COINGECKO_MAX_RETRIES = 6

# Cache markets briefly (keeps entry fresh, reduces API calls)
MARKETS_CACHE_TTL_SECONDS = int(os.getenv("MARKETS_CACHE_TTL_SECONDS", "60"))
_last_markets = None
_last_markets_ts = 0

OPEN_TRADES_CHECK_EVERY_SECONDS = 30 * 60
_last_open_check_ts = 0

# Telegram limits (important!)
TELEGRAM_MAX_CHARS = 3900  # keep under 4096 to be safe
TELEGRAM_SEND_RETRIES = 4

# Requests session
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "brads-trading-bot/2.5 (scan-14min; batch-15min; hourly-summary; whitelist; rate-safe)"
})

# ==============================
# COINGECKO WHITELIST (YOUR LIST)
# ==============================
COINGECKO_COIN_IDS = {
    # Majors / L1
    "bitcoin",
    "ethereum",
    "binancecoin",
    "ripple",
    "solana",
    "cardano",
    "dogecoin",
    "tron",
    "bitcoin-cash",
    "litecoin",
    "polkadot",
    "avalanche-2",
    "cosmos",
    "stellar",
    "ethereum-classic",
    "internet-computer",
    "near",
    "algorand",
    "aptos",
    "filecoin",
    "vechain",
    "hedera-hashgraph",
    "zcash",
    "monero",

    # DeFi / Core Infra
    "chainlink",
    "uniswap",
    "aave",
    "pancakeswap-token",
    "curve-dao-token",
    "synthetix-network-token",
    "compound-governance-token",
    "injective-protocol",
    "lido-dao",
    "morpho",
    "dydx",
    "the-graph",
    "reserve-rights-token",
    "qtum",
    "kyber-network-crystal",
    "loopring",
    "bancor",
    "0x",
    "gnosis",
    "band-protocol",

    # Layer 2 / Scaling / Modular
    "arbitrum",
    "optimism",
    "stacks",
    "starknet",
    "layerzero",
    "celestia",
    "skale",
    "osmosis",

    # AI / Compute / Data
    "bittensor",
    "render-token",
    "fetch-ai",
    "io-net",
    "numeraire",

    # Memes
    "shiba-inu",
    "pepe",
    "bonk",
    "floki",
    "dogwifcoin",
    "cheems-token",
    "book-of-meme",
    "peanut-the-squirrel",
    "official-trump",

    # Exchange / Wallet Tokens
    "trust-wallet-token",
    "nexo",
    "kucoin-shares",
    "okb",
    "gatechain-token",
    "htx",
    "mx-token",
    "bitget-token",

    # Payments / Utility / Legacy
    "xdc-network",
    "iota",
    "dash",
    "horizen",
    "siacoin",
    "holo",
    "ravencoin",
    "verge",
    "zilliqa",
    "theta-fuel",
    "theta-token",
    "basic-attention-token",

    # Gaming / NFT / Metaverse
    "axie-infinity",
    "apecoin",
    "the-sandbox",
    "gala",
    "immutable-x",
    "yield-guild-games",
    "open-campus",

    # Stables (shared + liquid)
    "tether",
    "usd-coin",
    "dai",
    "true-usd",
    "first-digital-usd",
    "ethena-usde",
    "ripple-usd",
    "frax",
    "paypal-usd",

    # Other Valid Overlaps
    "jasmycoin",
    "kite",
    "walrus",
    "sonic",
    "safepal",
    "space-id",
    "magic-eden",
    "power-ledger",
    "audius",
    "flux",
    "ontology-gas",
    "saga",
    "origin-protocol",
    "civic",
    "everipedia",
    "stratis",
    "wax",
    "cyberconnect",
    "amp-token",
    "oasis-network",
    "livepeer",
    "gas",
    "wormhole",
    "elrond-erd-2",
    "eigenlayer"
}

# ======================
# BINANCE CANDLE DATA (throttled)
# ======================
BINANCE_TIMEOUT = 10
BINANCE_MAX_RETRIES = 3
BINANCE_KLINES_CACHE_TTL_SECONDS = 60 * 60  # cache 1h per symbol
_binance_cache = {}  # key -> (ts, highs, lows, closes)

def fetch_binance_klines_usdt(symbol_upper: str, interval="1h", limit=120):
    """
    Returns highs, lows, closes arrays from Binance klines using SYMBOLUSDT.
    Throttled with cache. Fail-safe returns (None, None, None).
    """
    now = time.time()
    cache_key = (symbol_upper, interval, limit)
    cached = _binance_cache.get(cache_key)
    if cached and (now - cached[0]) < BINANCE_KLINES_CACHE_TTL_SECONDS:
        return cached[1], cached[2], cached[3]

    pair = f"{symbol_upper}USDT"
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": pair, "interval": interval, "limit": limit}

    delay = 2
    for _ in range(BINANCE_MAX_RETRIES):
        try:
            r = SESSION.get(url, params=params, timeout=BINANCE_TIMEOUT)
            if r.status_code in (418, 429):
                time.sleep(delay)
                delay = min(delay * 2, 20)
                continue
            if r.status_code == 400:
                return None, None, None
            r.raise_for_status()
            rows = r.json()
            highs = [float(x[2]) for x in rows]
            lows = [float(x[3]) for x in rows]
            closes = [float(x[4]) for x in rows]
            if len(closes) < 20:
                return None, None, None

            _binance_cache[cache_key] = (now, highs, lows, closes)
            return highs, lows, closes
        except Exception:
            time.sleep(delay)
            delay = min(delay * 2, 20)

    return None, None, None

# ======================
# ATR + CONSERVATIVE LEVELS
# ======================
def _atr(highs, lows, closes, period=14):
    try:
        if not highs or not lows or not closes:
            return None
        if len(closes) < period + 2:
            return None

        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1]),
            )
            trs.append(tr)

        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period
    except Exception:
        return None

def build_levels_from_candles(entry, side, highs, lows, closes):
    """
    Conservative targets based on ATR and recent swing structure,
    with HARD caps:
      TP1 max = +2%  (or -2% for short)
      TP2 max = +3.5%
      TP3 max = +5%
    """
    if entry is None or highs is None or lows is None or closes is None:
        return None

    atr = _atr(highs, lows, closes, period=14)
    if atr is None or atr <= 0:
        return None

    lookback = min(24, len(highs))
    recent_high = max(highs[-lookback:])
    recent_low = min(lows[-lookback:])

    TP1_CAP = 0.02
    TP2_CAP = 0.035
    TP3_CAP = 0.05

    if side == "LONG":
        sl = min(entry - 1.10 * atr, recent_low - 0.20 * atr)

        tp1_atr = entry + 0.60 * atr
        tp2_atr = entry + 1.00 * atr
        tp3_atr = entry + 1.40 * atr

        tp1_cap = entry * (1.0 + TP1_CAP)
        tp2_cap = entry * (1.0 + TP2_CAP)
        tp3_cap = entry * (1.0 + TP3_CAP)

        tp1 = min(tp1_atr, tp1_cap)
        tp2 = min(tp2_atr, tp2_cap)
        tp3 = min(tp3_atr, tp3_cap)

        tp1 = min(tp1, recent_high * 0.995)
        tp2 = min(tp2, recent_high * 1.000)
        tp3 = min(tp3, recent_high * 1.005)

        if not (sl < entry < tp1 < tp2 < tp3):
            return None
        return sl, tp1, tp2, tp3

    else:  # SHORT
        sl = max(entry + 1.10 * atr, recent_high + 0.20 * atr)

        tp1_atr = entry - 0.55 * atr
        tp2_atr = entry - 0.90 * atr
        tp3_atr = entry - 1.25 * atr

        tp1_cap = entry * (1.0 - TP1_CAP)
        tp2_cap = entry * (1.0 - TP2_CAP)
        tp3_cap = entry * (1.0 - TP3_CAP)

        tp1 = max(tp1_atr, tp1_cap)
        tp2 = max(tp2_atr, tp2_cap)
        tp3 = max(tp3_atr, tp3_cap)

        tp1 = max(tp1, recent_low * 1.005)
        tp2 = max(tp2, recent_low * 1.000)
        tp3 = max(tp3, recent_low * 0.995)

        if not (tp3 < tp2 < tp1 < entry < sl):
            return None
        return sl, tp1, tp2, tp3

def validate_ai_levels(side, entry, atr_value, fallback_levels, ai_levels):
    if not ai_levels:
        return fallback_levels

    try:
        sl = float(ai_levels["stop_loss"])
        tp1 = float(ai_levels["tp1"])
        tp2 = float(ai_levels["tp2"])
        tp3 = float(ai_levels["tp3"])
    except Exception:
        return fallback_levels

    if atr_value is None or atr_value <= 0:
        return fallback_levels

    if side == "LONG":
        if not (sl < entry < tp1 < tp2 < tp3):
            return fallback_levels
        if (tp1 - entry) > 1.2 * atr_value:
            return fallback_levels
        if (tp3 - entry) > 2.5 * atr_value:
            return fallback_levels
        if (entry - sl) > 1.3 * atr_value:
            return fallback_levels
    else:
        if not (tp3 < tp2 < tp1 < entry < sl):
            return fallback_levels
        if (entry - tp1) > 1.2 * atr_value:
            return fallback_levels
        if (entry - tp3) > 2.5 * atr_value:
            return fallback_levels
        if (sl - entry) > 1.3 * atr_value:
            return fallback_levels

    return (sl, tp1, tp2, tp3)

# ======================
# DATABASE
# ======================
def db_connect():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id SERIAL PRIMARY KEY,
            ts_utc TEXT NOT NULL,
            symbol TEXT NOT NULL,
            coin_id TEXT NOT NULL,
            coin_name TEXT NOT NULL,
            side TEXT NOT NULL,
            entry DOUBLE PRECISION NOT NULL,
            stop_loss DOUBLE PRECISION NOT NULL,
            tp1 DOUBLE PRECISION NOT NULL,
            tp2 DOUBLE PRECISION NOT NULL,
            tp3 DOUBLE PRECISION NOT NULL,
            confidence INTEGER NOT NULL,
            chg1h DOUBLE PRECISION,
            chg24 DOUBLE PRECISION,
            status TEXT NOT NULL DEFAULT 'OPEN',
            result TEXT,
            closed_ts_utc TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS cooldowns (
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            last_sent_ts TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (symbol, side)
        )
    """)
    conn.commit()
    return conn

def reset_stats(conn):
    """
    ✅ Resets win-rate stats to 0 by wiping trade history + cooldowns.
    Use RESET_STATS_ON_START=1 ONE TIME, then set it back to 0.
    """
    cur = conn.cursor()
    cur.execute("TRUNCATE TABLE trades RESTART IDENTITY")
    cur.execute("TRUNCATE TABLE cooldowns")
    conn.commit()

def insert_trade(conn, ts_utc, symbol, coin_id, coin_name, side, entry, sl, tp1, tp2, tp3, conf, chg1h, chg24):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO trades (ts_utc, symbol, coin_id, coin_name, side, entry, stop_loss, tp1, tp2, tp3, confidence, chg1h, chg24)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (ts_utc, symbol, coin_id, coin_name, side, entry, sl, tp1, tp2, tp3, conf, chg1h, chg24))
    conn.commit()

def close_trade(conn, trade_id, result):
    cur = conn.cursor()
    now_iso = datetime.now(timezone.utc).isoformat()
    cur.execute("""
        UPDATE trades
        SET status='CLOSED',
            result=%s,
            closed_ts_utc=%s
        WHERE id=%s
    """, (result, now_iso, trade_id))
    conn.commit()

def get_win_stats(conn):
    cur = conn.cursor()

    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE status='CLOSED') AS total,
            COUNT(*) FILTER (WHERE status='CLOSED' AND result='WIN') AS wins
        FROM trades
    """)
    total, wins = cur.fetchone()
    total = total or 0
    wins = wins or 0
    all_time_win_pct = (wins / total * 100.0) if total > 0 else 0.0

    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    cur.execute("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE result='WIN') AS wins
        FROM trades
        WHERE status='CLOSED'
          AND closed_ts_utc IS NOT NULL
          AND closed_ts_utc >= %s
    """, (seven_days_ago.isoformat(),))
    total7, wins7 = cur.fetchone()
    total7 = total7 or 0
    wins7 = wins7 or 0
    last7_win_pct = (wins7 / total7 * 100.0) if total7 > 0 else 0.0

    return all_time_win_pct, total, last7_win_pct, total7

def count_open_trades(conn):
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'")
        n = cur.fetchone()[0]
        return int(n or 0)
    except Exception:
        return 0

# ======================
# COOLDOWNS + SPAM GUARD
# ======================
def load_cooldowns(conn):
    try:
        cur = conn.cursor()
        cur.execute("SELECT symbol, side, last_sent_ts FROM cooldowns")
        rows = cur.fetchall()
        return {(sym, side): ts for sym, side, ts in rows}
    except Exception:
        return {}

def cooldown_ok(symbol, side, cooldown_cache):
    last_ts = cooldown_cache.get((symbol, side))
    if not last_ts:
        return True
    now = datetime.now(timezone.utc)
    return (now - last_ts).total_seconds() >= ALERT_COOLDOWN_SECONDS

def set_cooldown(conn, symbol, side, cooldown_cache):
    now = datetime.now(timezone.utc)
    cooldown_cache[(symbol, side)] = now
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO cooldowns (symbol, side, last_sent_ts)
            VALUES (%s,%s,%s)
            ON CONFLICT (symbol, side)
            DO UPDATE SET last_sent_ts = EXCLUDED.last_sent_ts
        """, (symbol, side, now))
        conn.commit()
    except Exception:
        key = f"{symbol}:{side}"
        last_alert_time[key] = int(time.time())

def should_alert_fallback_ram(symbol, side):
    now = int(time.time())
    key = f"{symbol}:{side}"
    if now - last_alert_time.get(key, 0) < ALERT_COOLDOWN_SECONDS:
        return False
    last_alert_time[key] = now
    return True

def spam_guard_ok(slots=1):
    """
    ✅ Rolling-hour spam guard.
    slots = how many signals you're about to add/send.
    """
    global _signal_times
    now = time.time()
    _signal_times = [t for t in _signal_times if (now - t) < 3600]
    if len(_signal_times) + int(slots) > MAX_SIGNALS_PER_HOUR:
        return False
    for _ in range(int(slots)):
        _signal_times.append(now)
    return True

# ======================
# TELEGRAM (SAFE SEND + CHUNKING)
# ======================
def _telegram_post(text, parse_mode="Markdown"):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": parse_mode}
    return SESSION.post(url, json=payload, timeout=20)

def send_message(text):
    delay = 2
    for attempt in range(TELEGRAM_SEND_RETRIES):
        try:
            r = _telegram_post(text, parse_mode="Markdown")
            if r.status_code >= 400:
                if attempt == 0:
                    r2 = _telegram_post(text, parse_mode=None)
                    if r2.ok:
                        return
                r.raise_for_status()
            return
        except Exception:
            time.sleep(delay)
            delay = min(delay * 2, 20)

def send_long_message(text):
    if not text:
        return
    if len(text) <= TELEGRAM_MAX_CHARS:
        send_message(text)
        return

    parts = []
    buf = ""
    for block in text.split("\n\n"):
        candidate = (buf + ("\n\n" if buf else "") + block)
        if len(candidate) <= TELEGRAM_MAX_CHARS:
            buf = candidate
        else:
            if buf:
                parts.append(buf)
            while len(block) > TELEGRAM_MAX_CHARS:
                parts.append(block[:TELEGRAM_MAX_CHARS])
                block = block[TELEGRAM_MAX_CHARS:]
            buf = block
    if buf:
        parts.append(buf)

    for p in parts:
        send_message(p)
        time.sleep(1.2)

# ======================
# COINGECKO SAFE HTTP
# ======================
# ✅ FIXED: always captures a real error message (not None)
def _get_json_with_backoff(url, params):
    delay = 5
    last_err = "unknown"

    for _ in range(COINGECKO_MAX_RETRIES):
        try:
            r = SESSION.get(url, params=params, timeout=COINGECKO_TIMEOUT)

            if r.status_code == 429:
                last_err = "HTTP 429 rate limit"
                retry_after = r.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = max(delay, int(retry_after))
                    except Exception:
                        pass
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue

            if r.status_code >= 400:
                last_err = f"HTTP {r.status_code}"
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue

            return r.json()

        except Exception as e:
            last_err = str(e)
            time.sleep(delay)
            delay = min(delay * 2, 120)

    raise RuntimeError(f"CoinGecko request failed after retries: {last_err}")

def _chunk_list(items, chunk_size):
    items = list(items)
    for i in range(0, len(items), chunk_size):
        yield items[i:i + chunk_size]

# ✅ FIXED: if CoinGecko fails, fall back to cached markets (bot keeps running)
def fetch_whitelist_markets():
    """
    Fetch ONLY the coins in COINGECKO_COIN_IDS.
    Uses CoinGecko /coins/markets with ids=... (chunked for safety).
    Cached to reduce API calls.
    Falls back to cached data if CoinGecko is rate-limited/down.
    """
    global _last_markets, _last_markets_ts

    now = time.time()
    if _last_markets and (now - _last_markets_ts) < MARKETS_CACHE_TTL_SECONDS:
        return _last_markets

    url = "https://api.coingecko.com/api/v3/coins/markets"
    all_rows = []

    try:
        for chunk in _chunk_list(sorted(COINGECKO_COIN_IDS), 200):
            params = {
                "vs_currency": "usd",
                "ids": ",".join(chunk),
                "order": "market_cap_desc",
                "sparkline": "false",
                "price_change_percentage": "1h,24h",
                "per_page": len(chunk),
                "page": 1,
            }
            data = _get_json_with_backoff(url, params)
            if isinstance(data, list):
                all_rows.extend(data)

        _last_markets = all_rows
        _last_markets_ts = now
        return all_rows

    except Exception as e:
        print("CoinGecko fallback used:", e)
        if _last_markets:
            return _last_markets
        raise

def fetch_simple_price_usd(coin_ids):
    if not coin_ids:
        return {}
    coin_ids = list(dict.fromkeys(coin_ids))[:200]
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": ",".join(coin_ids), "vs_currencies": "usd"}
    data = _get_json_with_backoff(url, params)
    return {k: v.get("usd") for k, v in data.items()}

# ======================
# CORE LOGIC
# ======================
def score(chg24, chg1h):
    return max(0, min(100, int(abs(chg24) * 6 + abs(chg1h) * 30)))

# ======================
# DECISION MEMORY
# ======================
def get_recent_side_performance(conn, symbol, side):
    try:
        cur = conn.cursor()
        since = datetime.now(timezone.utc) - timedelta(days=MEM_LOOKBACK_DAYS)
        cur.execute("""
            SELECT result
            FROM trades
            WHERE status='CLOSED'
              AND symbol=%s
              AND side=%s
              AND closed_ts_utc IS NOT NULL
              AND closed_ts_utc >= %s
            ORDER BY closed_ts_utc DESC
            LIMIT 50
        """, (symbol, side, since.isoformat()))
        rows = cur.fetchall()
        if not rows:
            return 0, None
        results = [r[0] for r in rows if r and r[0] in ("WIN", "LOSS")]
        if not results:
            return 0, None
        total = len(results)
        wins = sum(1 for x in results if x == "WIN")
        return total, (wins / total) if total > 0 else None
    except Exception:
        return 0, None

def apply_memory_rules(conn, symbol, side):
    total, winrate = get_recent_side_performance(conn, symbol, side)
    if winrate is None:
        return False, 0, None

    if total >= MEM_STRICT_MIN_TRADES and winrate < MEM_STRICT_BLOCK_BELOW_WINRATE:
        note = f"Blocked by memory: {total} trades, {winrate*100:.0f}% win (last {MEM_LOOKBACK_DAYS}d)"
        return True, 0, note

    if total >= MEM_SOFT_MIN_TRADES and winrate < MEM_SOFT_PENALIZE_BELOW_WINRATE:
        note = f"Memory penalty: {total} trades, {winrate*100:.0f}% win (last {MEM_LOOKBACK_DAYS}d)"
        return False, MEM_SOFT_PENALTY, note

    return False, 0, None

# ======================
# AI CONTEXT
# ======================
def build_ai_context(coin_name, sym, side, entry, sl, tp1, tp2, tp3, base_conf, chg1h, chg24, atr_value, mem_total=None, mem_winrate=None):
    action = "BUY" if side == "LONG" else "SELL"
    rr_tp1 = None
    try:
        if side == "LONG":
            rr_tp1 = (tp1 - entry) / max(1e-12, (entry - sl))
        else:
            rr_tp1 = (entry - tp1) / max(1e-12, (sl - entry))
    except Exception:
        rr_tp1 = None

    return {
        "coin": coin_name,
        "symbol": sym,
        "direction": side,
        "action": action,
        "entry": entry,
        "stop_loss": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "atr_1h": atr_value,
        "base_confidence": base_conf,
        "chg_1h_pct": chg1h,
        "chg_24h_pct": chg24,
        "rr_to_tp1": rr_tp1,
        "recent_performance": {
            "lookback_days": MEM_LOOKBACK_DAYS,
            "closed_trades": mem_total,
            "win_rate": mem_winrate
        }
    }

# ======================
# MESSAGE FORMAT
# ======================
def fmt_price(p):
    return f"${p:,.6f}" if p < 1 else f"${p:,.2f}"

def format_signal_msg(coin_name, sym, side, entry, sl, tp1, tp2, tp3, conf, chg1h, chg24, time_str, notes=None):
    direction = "🟢 *LONG (BUY)*" if side == "LONG" else "🔴 *SHORT (SELL)*"
    extra = ""
    if notes:
        joined = " | ".join([n for n in notes if n])[:250]
        if joined:
            extra = f"\n🧠 *Notes:* `{joined}`"

    return (
        f"🚨 *TRADE SIGNAL* 🚨\n"
        f"*{coin_name}* `({sym})`\n"
        f"{direction} • *Confidence:* {conf}/100\n"
        f"⏰ *Time:* {time_str}\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎯 *Entry:* `{fmt_price(entry)}`\n"
        f"🛑 *Stop Loss:* `{fmt_price(sl)}`\n"
        f"✅ *TP1:* `{fmt_price(tp1)}`\n"
        f"✅ *TP2:* `{fmt_price(tp2)}`\n"
        f"✅ *TP3:* `{fmt_price(tp3)}`\n"
        f"━━━━━━━━━━━━━━\n"
        f"📈 *Momentum:* 1h `{chg1h:+.2f}%` | 24h `{chg24:+.2f}%`\n"
        f"{extra}\n"
        f"_Not financial advice_"
    )

def format_quarter_header(conn, time_str):
    all_win, all_total, win7, total7 = get_win_stats(conn)
    open_n = count_open_trades(conn)
    return (
        f"⏱ *15-Min Signal Batch* ({time_str})\n"
        f"📊 *Win Rate:* All-time `{all_win:.1f}%` ({all_total} trades) | Last 7D `{win7:.1f}%` ({total7} trades)\n"
        f"🟦 *Open Trades:* `{open_n}`\n"
        f"━━━━━━━━━━━━━━"
    )

def format_hourly_summary(conn, time_str):
    all_win, all_total, win7, total7 = get_win_stats(conn)
    open_n = count_open_trades(conn)
    queued = len(pending_signals)
    return (
        f"🧠 *Hourly Market Scan Summary* ({time_str})\n"
        f"📊 *Win Rate:* All-time `{all_win:.1f}%` ({all_total} trades) | Last 7D `{win7:.1f}%` ({total7} trades)\n"
        f"🟦 *Open Trades:* `{open_n}`\n"
        f"📥 *Queued Signals (next batch):* `{queued}`\n"
        f"━━━━━━━━━━━━━━\n"
        f"_Not financial advice_"
    )

# ======================
# TRADE OUTCOME TRACKING
# ======================
def update_open_trades(conn):
    global _last_open_check_ts
    now = time.time()
    if (now - _last_open_check_ts) < OPEN_TRADES_CHECK_EVERY_SECONDS:
        return
    _last_open_check_ts = now

    cur = conn.cursor()
    cur.execute("""
        SELECT id, coin_id, side, stop_loss, tp1
        FROM trades
        WHERE status='OPEN'
        ORDER BY id ASC
        LIMIT 200
    """)
    rows = cur.fetchall()
    if not rows:
        return

    coin_ids = list({r[1] for r in rows})
    prices = fetch_simple_price_usd(coin_ids)

    for trade_id, coin_id, side, sl, tp1 in rows:
        px = prices.get(coin_id)
        if px is None:
            continue

        if side == "LONG":
            if px <= sl:
                close_trade(conn, trade_id, "LOSS")
            elif px >= tp1:
                close_trade(conn, trade_id, "WIN")
        else:
            if px >= sl:
                close_trade(conn, trade_id, "LOSS")
            elif px <= tp1:
                close_trade(conn, trade_id, "WIN")

# ======================
# SCAN + QUEUE (WHITELIST ONLY)
# ======================
def scan_and_queue(conn):
    global pending_signals, pending_keys

    markets = fetch_whitelist_markets()
    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    ts_iso = datetime.now(timezone.utc).isoformat()
    cooldown_cache = load_cooldowns(conn)

    for c in markets:
        coin_id = c.get("id")
        if not coin_id or coin_id not in COINGECKO_COIN_IDS:
            continue

        chg1h = c.get("price_change_percentage_1h_in_currency")
        chg24 = c.get("price_change_percentage_24h")
        entry = c.get("current_price")
        if chg1h is None or chg24 is None or entry is None:
            continue

        side = None
        if chg24 > MIN_24H and chg1h > MIN_1H:
            side = "LONG"
        elif chg24 < -MIN_24H and chg1h < -MIN_1H:
            side = "SHORT"
        if not side:
            continue

        sym = (c.get("symbol") or "").upper()
        coin_name = c.get("name") or sym
        if not sym:
            continue

        # cooldown (DB; RAM fallback)
        try:
            if not cooldown_ok(sym, side, cooldown_cache):
                continue
        except Exception:
            if not should_alert_fallback_ram(sym, side):
                continue

        key = (sym, side)
        if key in pending_keys:
            continue

        # base confidence + memory BEFORE Binance
        conf = score(chg24, chg1h)
        blocked, mem_delta, mem_note = apply_memory_rules(conn, sym, side)
        if blocked:
            continue

        conf_after_mem = max(0, min(100, conf + mem_delta))
        if conf_after_mem < CONFIDENCE_MIN:
            continue

        notes = []
        if mem_note:
            notes.append(mem_note)

        # Levels (Binance first)
        highs, lows, closes = fetch_binance_klines_usdt(sym, interval="1h", limit=120)
        levels = build_levels_from_candles(entry, side, highs, lows, closes)
        atr_val = _atr(highs, lows, closes, period=14) if levels else None

        # CoinGecko fallback if Binance not available
        if not levels:
            high_24h = c.get("high_24h")
            low_24h = c.get("low_24h")
            if high_24h is None or low_24h is None or high_24h <= 0 or low_24h <= 0:
                continue

            TP1_CAP = 0.02
            TP2_CAP = 0.035
            TP3_CAP = 0.05

            if side == "LONG":
                sl = low_24h * 0.997
                if sl >= entry:
                    continue

                tp1 = min(entry * (1.0 + TP1_CAP), high_24h * 0.995)
                tp2 = min(entry * (1.0 + TP2_CAP), high_24h * 1.000)
                tp3 = min(entry * (1.0 + TP3_CAP), high_24h * 1.005)

                if not (sl < entry < tp1 < tp2 < tp3):
                    continue
            else:
                sl = high_24h * 1.003
                if sl <= entry:
                    continue

                tp1 = max(entry * (1.0 - TP1_CAP), low_24h * 1.005)
                tp2 = max(entry * (1.0 - TP2_CAP), low_24h * 1.000)
                tp3 = max(entry * (1.0 - TP3_CAP), low_24h * 0.995)

                if not (tp3 < tp2 < tp1 < entry < sl):
                    continue

            levels = (sl, tp1, tp2, tp3)

        sl, tp1, tp2, tp3 = levels

        # AI layer (fail-safe)
        final_conf = conf_after_mem
        if ai_enabled():
            try:
                mem_total, mem_wr = get_recent_side_performance(conn, sym, side)
                ctx = build_ai_context(
                    coin_name=coin_name,
                    sym=sym,
                    side=side,
                    entry=entry,
                    sl=sl,
                    tp1=tp1,
                    tp2=tp2,
                    tp3=tp3,
                    base_conf=conf_after_mem,
                    chg1h=chg1h,
                    chg24=chg24,
                    atr_value=atr_val,
                    mem_total=mem_total,
                    mem_winrate=mem_wr
                )

                approved, adj, reason, ai_levels = judge_trade(ctx)
                if not approved:
                    continue

                final_conf = max(0, min(100, conf_after_mem + int(adj)))

                if atr_val is not None:
                    sl, tp1, tp2, tp3 = validate_ai_levels(
                        side=side,
                        entry=entry,
                        atr_value=atr_val,
                        fallback_levels=(sl, tp1, tp2, tp3),
                        ai_levels=ai_levels
                    )

                if reason:
                    notes.append(f"AI: {reason} ({int(adj):+d})")
            except Exception:
                final_conf = conf_after_mem

        if final_conf < CONFIDENCE_MIN:
            continue

        # queue cap (also protects memory + telegram)
        if len(pending_signals) >= MAX_SIGNALS_PER_HOUR:
            break

        # reserve spam slots NOW (so we don't queue 50 then fail to send)
        if not spam_guard_ok(slots=1):
            continue

        # persist cooldown BEFORE queue (prevents duplicates on restart)
        set_cooldown(conn, sym, side, cooldown_cache)

        # save trade immediately (keeps DB consistent even if send happens later)
        insert_trade(conn, ts_iso, sym, coin_id, coin_name, side, entry, sl, tp1, tp2, tp3, final_conf, chg1h, chg24)

        pending_keys.add(key)
        pending_signals.append(
            format_signal_msg(coin_name, sym, side, entry, sl, tp1, tp2, tp3, final_conf, chg1h, chg24, now_str, notes=notes)
        )

# ======================
# SCHEDULED SENDERS
# ======================
_last_quarter_key = None
_last_hour_key = None

def _current_quarter_key(dt: datetime) -> str:
    minute_bucket = (dt.minute // SEND_BATCH_EVERY_MINUTES) * SEND_BATCH_EVERY_MINUTES
    return dt.strftime("%Y-%m-%d %H:") + f"{minute_bucket:02d}"

def should_send_quarter_batch():
    global _last_quarter_key
    now = datetime.now(timezone.utc)

    if SEND_BATCH_EVERY_MINUTES <= 0:
        return False

    aligned = (now.minute % SEND_BATCH_EVERY_MINUTES == 0)
    if not aligned:
        return False

    if now.second > 8:
        return False

    qkey = _current_quarter_key(now)
    if qkey == _last_quarter_key:
        return False

    _last_quarter_key = qkey
    return True

def send_quarter_batch(conn):
    global pending_signals, pending_keys

    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    header = format_quarter_header(conn, now_str)

    if pending_signals:
        body = "\n\n".join(pending_signals)
        msg = f"{header}\n\n{body}"
        send_long_message(msg)
    else:
        send_message(f"{header}\n\n❌ *No signals in the last 15 minutes.*\n\n_Not financial advice_")

    pending_signals = []
    pending_keys = set()

def should_send_hourly_summary():
    global _last_hour_key
    now = datetime.now(timezone.utc)

    if now.minute != 0:
        return False
    if now.second > 10:
        return False

    hour_key = now.strftime("%Y-%m-%d %H")
    if hour_key == _last_hour_key:
        return False

    _last_hour_key = hour_key
    return True

def send_hourly_summary(conn):
    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    msg = format_hourly_summary(conn, now_str)
    send_message(msg)

# ======================
# MAIN LOOP
# ======================
def main():
    conn = db_connect()

    if RESET_STATS_ON_START:
        try:
            reset_stats(conn)
        except Exception as e:
            print("reset_stats error:", e)

    ai_status = "ON ✅" if ai_enabled() else "OFF (no OPENAI_API_KEY) ⚠️"
    send_message(
        "✅ Bot online. Scanning 24/7.\n"
        "⏳ Signals are queued and sent every 15 minutes (00/15/30/45).\n"
        "🧠 Hourly market scan summary sent on the hour.\n"
        f"⏱ Scan interval: {SCAN_EVERY_SECONDS}s\n"
        f"🎯 Confidence Filter: {CONFIDENCE_MIN}+\n"
        f"🤖 AI Filter: {ai_status}\n"
        f"🧾 CoinGecko Whitelist: {len(COINGECKO_COIN_IDS)} coins ✅\n"
        "🧠 Decision Memory: ON ✅\n"
        "🕒 Persistent Cooldowns: ON ✅\n"
        f"🧯 Spam Guard: max {MAX_SIGNALS_PER_HOUR}/hour\n"
        "_Not financial advice_"
    )

    while True:
        try:
            update_open_trades(conn)
        except Exception as e:
            print("update_open_trades error:", e)

        try:
            scan_and_queue(conn)
        except Exception as e:
            print("scan_and_queue error:", e)

        try:
            if should_send_quarter_batch():
                send_quarter_batch(conn)
        except Exception as e:
            print("quarter_batch_send error:", e)

        try:
            if should_send_hourly_summary():
                send_hourly_summary(conn)
        except Exception as e:
            print("hourly_summary_send error:", e)

        time.sleep(SCAN_EVERY_SECONDS)

if __name__ == "__main__":
    main()
