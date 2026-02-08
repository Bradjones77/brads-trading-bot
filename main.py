import os
import time
import traceback
import requests
import psycopg2
from psycopg2 import OperationalError, InterfaceError
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
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("CHAT_ID") or "").strip()
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

COINGECKO_API_KEY = (os.getenv("COINGECKO_API_KEY") or "").strip()

COINGECKO_BASE_URL = (os.getenv(
    "COINGECKO_BASE_URL",
    "https://pro-api.coingecko.com/api/v3"
) or "").strip().rstrip("/")

if not BOT_TOKEN or not CHAT_ID or not DATABASE_URL:
    raise RuntimeError("BOT_TOKEN, CHAT_ID, or DATABASE_URL missing")

if not COINGECKO_API_KEY:
    raise RuntimeError("COINGECKO_API_KEY missing (set Railway Variable COINGECKO_API_KEY)")

if not COINGECKO_BASE_URL.startswith("http"):
    raise RuntimeError(f"COINGECKO_BASE_URL looks wrong: {COINGECKO_BASE_URL!r}")

# ======================
# SETTINGS
# ======================
SCAN_EVERY_SECONDS = int(os.getenv("SCAN_EVERY_SECONDS", "600"))  # scan every 10 minutes
CONFIDENCE_MIN = int(os.getenv("CONFIDENCE_MIN", "65"))
MAX_SIGNALS_PER_HOUR = int(os.getenv("MAX_SIGNALS_PER_HOUR", "10"))

MIN_24H = float(os.getenv("MIN_24H", "1.5"))
MIN_1H = float(os.getenv("MIN_1H", "0.4"))

ALERT_COOLDOWN_SECONDS = int(os.getenv("ALERT_COOLDOWN_SECONDS", str(60 * 60)))

last_alert_time = {}
pending_signals = []
pending_keys = set()

MEM_STRICT_MIN_TRADES = int(os.getenv("MEM_STRICT_MIN_TRADES", "6"))
MEM_STRICT_BLOCK_BELOW_WINRATE = float(os.getenv("MEM_STRICT_BLOCK_BELOW_WINRATE", "0.30"))
MEM_SOFT_MIN_TRADES = int(os.getenv("MEM_SOFT_MIN_TRADES", "4"))
MEM_SOFT_PENALIZE_BELOW_WINRATE = float(os.getenv("MEM_SOFT_PENALIZE_BELOW_WINRATE", "0.45"))
MEM_SOFT_PENALTY = int(os.getenv("MEM_SOFT_PENALTY", "-10"))
MEM_LOOKBACK_DAYS = int(os.getenv("MEM_LOOKBACK_DAYS", "14"))

COINGECKO_TIMEOUT = int(os.getenv("COINGECKO_TIMEOUT", "30"))
COINGECKO_MAX_RETRIES = int(os.getenv("COINGECKO_MAX_RETRIES", "6"))

MARKETS_CACHE_TTL_SECONDS = int(os.getenv("MARKETS_CACHE_TTL_SECONDS", str(20 * 60)))
_last_markets = None
_last_markets_ts = 0

OPEN_TRADES_CHECK_EVERY_SECONDS = int(os.getenv("OPEN_TRADES_CHECK_EVERY_SECONDS", str(30 * 60)))
_last_open_check_ts = 0

TELEGRAM_MAX_CHARS = int(os.getenv("TELEGRAM_MAX_CHARS", "3900"))
TELEGRAM_SEND_RETRIES = int(os.getenv("TELEGRAM_SEND_RETRIES", "4"))

# ======================
# TAKE PROFIT FALLBACK CAPS (ONLY used when bot can't decide)
# ======================
TP1_CAP_FALLBACK = float(os.getenv("TP1_CAP_FALLBACK", "0.035"))       # 3.5%
TP2_CAP_FALLBACK = float(os.getenv("TP2_CAP_FALLBACK", "0.055"))       # 5.5%
TP3_CAP_MAX_FALLBACK = float(os.getenv("TP3_CAP_MAX_FALLBACK", "0.12"))  # 12% safety ceiling

# ======================
# COINGECKO OHLC SETTINGS
# ======================
COINGECKO_OHLC_DAYS = int(os.getenv("COINGECKO_OHLC_DAYS", "7"))
COINGECKO_OHLC_CACHE_TTL_SECONDS = int(os.getenv("COINGECKO_OHLC_CACHE_TTL_SECONDS", str(10 * 60)))
_ohlc_cache = {}  # coin_id -> (ts, highs, lows, closes)

# ======================
# AI SAFETY MODES
# ======================
AI_REQUEST_LEVELS_ONLY_ON_FALLBACK = (os.getenv("AI_REQUEST_LEVELS_ONLY_ON_FALLBACK", "1").strip() == "1")

AI_FILTER_MODE = (os.getenv("AI_FILTER_MODE", "levels_only") or "").strip().lower()
# modes:
# - "off"               = never call OpenAI
# - "levels_only"       = call OpenAI ONLY when ai_requested=True (recommended)
# - "filter_and_levels" = call OpenAI for approve/reject always (more expensive)

AI_MIN_CALL_INTERVAL_SECONDS = int(os.getenv("AI_MIN_CALL_INTERVAL_SECONDS", "2"))
AI_COOLDOWN_ON_429_SECONDS = int(os.getenv("AI_COOLDOWN_ON_429_SECONDS", str(15 * 60)))

_last_ai_call_ts = 0.0
_ai_cooldown_until = 0.0

def can_call_ai_now() -> bool:
    global _last_ai_call_ts, _ai_cooldown_until
    now = time.time()
    if now < _ai_cooldown_until:
        return False
    if (now - _last_ai_call_ts) < AI_MIN_CALL_INTERVAL_SECONDS:
        return False
    _last_ai_call_ts = now
    return True

def mark_ai_cooldown():
    global _ai_cooldown_until
    _ai_cooldown_until = time.time() + AI_COOLDOWN_ON_429_SECONDS

# ======================
# Requests session
# ======================
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "brads-trading-bot/2.8 (db-reconnect; loop-safe; ai-rate-safe; coingecko-ohlc)",
    "accept": "application/json",
    "x-cg-pro-api-key": COINGECKO_API_KEY
})

def coingecko_self_test():
    url = f"{COINGECKO_BASE_URL}/ping"
    r = SESSION.get(url, timeout=15)
    r.raise_for_status()
    print("✅ CoinGecko OK:", r.json())

# ==============================
# COINGECKO WHITELIST
# ==============================
COINGECKO_COIN_IDS = {
    "bitcoin", "ethereum", "binancecoin", "ripple", "solana", "cardano", "dogecoin",
    "tron", "bitcoin-cash", "litecoin", "polkadot", "avalanche-2", "cosmos",
    "stellar", "ethereum-classic", "internet-computer", "near", "algorand", "aptos",
    "filecoin", "vechain", "hedera-hashgraph", "zcash", "monero",

    "chainlink", "uniswap", "aave", "pancakeswap-token", "curve-dao-token",
    "synthetix-network-token", "compound-governance-token", "injective-protocol",
    "lido-dao", "morpho", "dydx", "the-graph", "reserve-rights-token", "qtum",
    "kyber-network-crystal", "loopring", "bancor", "0x", "gnosis", "band-protocol",

    "arbitrum", "optimism", "stacks", "starknet", "layerzero", "celestia", "skale", "osmosis",

    "bittensor", "render-token", "fetch-ai", "io-net", "numeraire",

    "shiba-inu", "pepe", "bonk", "floki", "dogwifcoin", "cheems-token",
    "book-of-meme", "peanut-the-squirrel", "official-trump",

    "trust-wallet-token", "nexo", "kucoin-shares", "okb", "gatechain-token", "htx",
    "mx-token", "bitget-token",

    "xdc-network", "iota", "dash", "horizen", "siacoin", "holo", "ravencoin",
    "verge", "zilliqa", "theta-fuel", "theta-token", "basic-attention-token",

    "axie-infinity", "apecoin", "the-sandbox", "gala", "immutable-x",
    "yield-guild-games", "open-campus",

    "tether", "usd-coin", "dai", "true-usd", "first-digital-usd", "ethena-usde",
    "ripple-usd", "frax", "paypal-usd",

    "jasmycoin", "kite", "walrus", "sonic", "safepal", "space-id", "magic-eden",
    "power-ledger", "audius", "flux", "ontology-gas", "saga", "origin-protocol",
    "civic", "everipedia", "stratis", "wax", "cyberconnect", "amp-token",
    "oasis-network", "livepeer", "gas", "wormhole", "elrond-erd-2", "eigenlayer"
}

# ======================
# COINGECKO SAFE HTTP
# ======================
def _get_json_with_backoff(url, params):
    delay = 5
    last_err = "unknown"
    for attempt in range(1, COINGECKO_MAX_RETRIES + 1):
        try:
            r = SESSION.get(url, params=params, timeout=COINGECKO_TIMEOUT)

            if r.status_code == 401:
                last_err = "HTTP 401 Unauthorized (check COINGECKO_API_KEY / plan / base URL)"
                raise RuntimeError(last_err)

            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                ra = None
                if retry_after:
                    try:
                        ra = int(retry_after)
                    except Exception:
                        ra = None
                body_snip = (r.text[:200] if getattr(r, "text", None) else "")
                last_err = f"HTTP 429 rate limited retry_after={ra} body={body_snip}"
                sleep_for = max(delay, ra or 0)
                sleep_for = min(sleep_for, 120)
                time.sleep(sleep_for)
                delay = min(delay * 2, 120)
                continue

            if r.status_code >= 400:
                body_snip = (r.text[:200] if getattr(r, "text", None) else "")
                last_err = f"HTTP {r.status_code} body={body_snip}"
                r.raise_for_status()

            return r.json()

        except Exception as e:
            last_err = repr(e)
            time.sleep(delay)
            delay = min(delay * 2, 120)

    raise RuntimeError(f"CoinGecko request failed after retries: {last_err}")

def _chunk_list(items, chunk_size):
    items = list(items)
    for i in range(0, len(items), chunk_size):
        yield items[i:i + chunk_size]

def fetch_whitelist_markets():
    global _last_markets, _last_markets_ts
    now = time.time()
    if _last_markets and (now - _last_markets_ts) < MARKETS_CACHE_TTL_SECONDS:
        return _last_markets

    url = f"{COINGECKO_BASE_URL}/coins/markets"
    all_rows = []
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

def fetch_simple_price_usd(coin_ids):
    if not coin_ids:
        return {}
    coin_ids = list(dict.fromkeys(coin_ids))[:200]
    url = f"{COINGECKO_BASE_URL}/simple/price"
    params = {"ids": ",".join(coin_ids), "vs_currencies": "usd"}
    data = _get_json_with_backoff(url, params)
    return {k: v.get("usd") for k, v in data.items()}

# ======================
# COINGECKO OHLC
# ======================
def fetch_coingecko_ohlc_usd(coin_id: str, days: int = COINGECKO_OHLC_DAYS):
    if not coin_id:
        return None, None, None

    now = time.time()
    cached = _ohlc_cache.get(coin_id)
    if cached and (now - cached[0]) < COINGECKO_OHLC_CACHE_TTL_SECONDS:
        return cached[1], cached[2], cached[3]

    url = f"{COINGECKO_BASE_URL}/coins/{coin_id}/ohlc"
    params = {"vs_currency": "usd", "days": int(days)}

    try:
        rows = _get_json_with_backoff(url, params)
        if not isinstance(rows, list) or len(rows) < 20:
            return None, None, None

        highs, lows, closes = [], [], []
        for r in rows:
            if not isinstance(r, (list, tuple)) or len(r) < 5:
                continue
            try:
                highs.append(float(r[2]))
                lows.append(float(r[3]))
                closes.append(float(r[4]))
            except Exception:
                continue

        if len(closes) < 20:
            return None, None, None

        _ohlc_cache[coin_id] = (now, highs, lows, closes)
        return highs, lows, closes
    except Exception:
        return None, None, None

# ======================
# ATR + LEVELS
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

def build_levels_from_candles(entry, side, highs, lows, closes, use_caps: bool = False):
    if entry is None or highs is None or lows is None or closes is None:
        return None

    atr = _atr(highs, lows, closes, period=14)
    if atr is None or atr <= 0:
        return None

    lookback = min(24, len(highs))
    recent_high = max(highs[-lookback:])
    recent_low = min(lows[-lookback:])

    if side == "LONG":
        sl = min(entry - 1.10 * atr, recent_low - 0.20 * atr)
        tp1 = entry + 0.60 * atr
        tp2 = entry + 1.00 * atr
        tp3 = entry + 1.60 * atr

        tp1 = min(tp1, recent_high * 0.995)
        tp2 = min(tp2, recent_high * 1.000)
        tp3 = min(tp3, recent_high * 1.010)

        if use_caps:
            tp1 = min(tp1, entry * (1.0 + TP1_CAP_FALLBACK))
            tp2 = min(tp2, entry * (1.0 + TP2_CAP_FALLBACK))
            tp3_pct = min(
                TP3_CAP_MAX_FALLBACK,
                max(TP2_CAP_FALLBACK + 0.01, (1.8 * atr) / max(1e-12, entry))
            )
            tp3 = min(tp3, entry * (1.0 + tp3_pct))

        if not (sl < entry < tp1 < tp2 < tp3):
            return None
        return sl, tp1, tp2, tp3

    else:  # SHORT
        sl = max(entry + 1.10 * atr, recent_high + 0.20 * atr)
        tp1 = entry - 0.55 * atr
        tp2 = entry - 0.90 * atr
        tp3 = entry - 1.45 * atr

        tp1 = max(tp1, recent_low * 1.005)
        tp2 = max(tp2, recent_low * 1.000)
        tp3 = max(tp3, recent_low * 0.990)

        if use_caps:
            tp1 = max(tp1, entry * (1.0 - TP1_CAP_FALLBACK))
            tp2 = max(tp2, entry * (1.0 - TP2_CAP_FALLBACK))
            tp3_pct = min(
                TP3_CAP_MAX_FALLBACK,
                max(TP2_CAP_FALLBACK + 0.01, (1.8 * atr) / max(1e-12, entry))
            )
            tp3 = max(tp3, entry * (1.0 - tp3_pct))

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
        if (tp3 - entry) > 2.8 * atr_value:
            return fallback_levels
        if (entry - sl) > 1.35 * atr_value:
            return fallback_levels
    else:
        if not (tp3 < tp2 < tp1 < entry < sl):
            return fallback_levels
        if (entry - tp1) > 1.2 * atr_value:
            return fallback_levels
        if (entry - tp3) > 2.8 * atr_value:
            return fallback_levels
        if (sl - entry) > 1.35 * atr_value:
            return fallback_levels

    return (sl, tp1, tp2, tp3)

def _rr_to_tp1(side, entry, sl, tp1):
    try:
        if side == "LONG":
            risk = max(1e-12, entry - sl)
            reward = max(1e-12, tp1 - entry)
        else:
            risk = max(1e-12, sl - entry)
            reward = max(1e-12, entry - tp1)
        return reward / risk
    except Exception:
        return None

def ai_levels_better(side, entry, fallback_levels, candidate_levels):
    try:
        f_sl, f_tp1, f_tp2, f_tp3 = fallback_levels
        a_sl, a_tp1, a_tp2, a_tp3 = candidate_levels
    except Exception:
        return False

    f_rr = _rr_to_tp1(side, entry, f_sl, f_tp1)
    a_rr = _rr_to_tp1(side, entry, a_sl, a_tp1)

    if f_rr is None or a_rr is None:
        return False
    if a_rr + 1e-9 < f_rr:
        return False

    if side == "LONG":
        f_risk = entry - f_sl
        a_risk = entry - a_sl
    else:
        f_risk = f_sl - entry
        a_risk = a_sl - entry

    if a_risk > f_risk * 1.15:
        return False

    return True

# ======================
# DATABASE (RECONNECT-SAFE)
# ======================
def _ensure_schema(conn):
    cur = conn.cursor()
    try:
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS levels_source TEXT")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS ai_requested BOOLEAN")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS ai_applied BOOLEAN")
        cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS ai_reason TEXT")
        conn.commit()
    except Exception:
        conn.rollback()

def db_connect():
    delay = 2
    while True:
        try:
            conn = psycopg2.connect(DATABASE_URL)
            conn.autocommit = False
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

            _ensure_schema(conn)
            print("✅ DB connected")
            return conn

        except Exception as e:
            print("❌ DB connect failed, retrying:", repr(e))
            time.sleep(delay)
            delay = min(delay * 2, 30)

def ensure_conn(conn):
    try:
        if conn is None or conn.closed != 0:
            return db_connect()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        conn.commit()
        return conn
    except (OperationalError, InterfaceError) as e:
        print("⚠️ DB connection lost, reconnecting:", repr(e))
        try:
            if conn:
                conn.close()
        except Exception:
            pass
        return db_connect()
    except Exception as e:
        print("⚠️ ensure_conn error:", repr(e))
        return conn

def insert_trade(
    conn,
    ts_utc, symbol, coin_id, coin_name, side,
    entry, sl, tp1, tp2, tp3,
    conf, chg1h, chg24,
    levels_source=None,
    ai_requested=None,
    ai_applied=None,
    ai_reason=None
):
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO trades (
                ts_utc, symbol, coin_id, coin_name, side,
                entry, stop_loss, tp1, tp2, tp3,
                confidence, chg1h, chg24,
                levels_source, ai_requested, ai_applied, ai_reason
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            ts_utc, symbol, coin_id, coin_name, side,
            entry, sl, tp1, tp2, tp3,
            conf, chg1h, chg24,
            levels_source, ai_requested, ai_applied, ai_reason
        ))
        conn.commit()
        return
    except Exception:
        conn.rollback()

    # old insert fallback (if columns not present)
    cur.execute("""
        INSERT INTO trades (
            ts_utc, symbol, coin_id, coin_name, side,
            entry, stop_loss, tp1, tp2, tp3,
            confidence, chg1h, chg24
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        ts_utc, symbol, coin_id, coin_name, side,
        entry, sl, tp1, tp2, tp3,
        conf, chg1h, chg24
    ))
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

# ======================
# COOLDOWNS
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

# ======================
# TELEGRAM
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
# CORE
# ======================
def score(chg24, chg1h):
    return max(0, min(100, int(abs(chg24) * 6 + abs(chg1h) * 30)))

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
        note = f"Blocked by memory: {total} trades, {winrate*100:.0f}% win ({MEM_LOOKBACK_DAYS}d)"
        return True, 0, note

    if total >= MEM_SOFT_MIN_TRADES and winrate < MEM_SOFT_PENALIZE_BELOW_WINRATE:
        note = f"Memory penalty: {total} trades, {winrate*100:.0f}% win ({MEM_LOOKBACK_DAYS}d)"
        return False, MEM_SOFT_PENALTY, note

    return False, 0, None

def build_ai_context(
    coin_name, sym, side, entry, sl, tp1, tp2, tp3,
    base_conf, chg1h, chg24, atr_value,
    mem_total=None, mem_winrate=None,
    request_ai_levels: bool = False
):
    action = "BUY" if side == "LONG" else "SELL"
    rr_tp1 = _rr_to_tp1(side, entry, sl, tp1)
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
        },
        "request_ai_levels": bool(request_ai_levels),
        "rule": "AI may suggest SL/TP only. Entry is bot-controlled."
    }

def fmt_price(p):
    return f"${p:,.6f}" if p < 1 else f"${p:,.2f}"

def format_signal_msg(coin_name, sym, side, entry, sl, tp1, tp2, tp3, conf, chg1h, chg24, time_str, notes=None):
    direction = "🟢 *LONG (BUY)*" if side == "LONG" else "🔴 *SHORT (SELL)*"
    extra = ""
    if notes:
        joined = " | ".join([n for n in notes if n])[:260]
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

def format_hourly_header(conn, time_str):
    all_win, all_total, win7, total7 = get_win_stats(conn)
    return (
        f"🧠 *Hourly Market Scan* ({time_str})\n"
        f"📊 *Win Rate:* All-time `{all_win:.1f}%` ({all_total} trades) | Last 7D `{win7:.1f}%` ({total7} trades)\n"
        f"━━━━━━━━━━━━━━"
    )

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

def scan_and_collect(conn):
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

        # cooldown
        try:
            if not cooldown_ok(sym, side, cooldown_cache):
                continue
        except Exception:
            if not should_alert_fallback_ram(sym, side):
                continue

        key = (sym, side)
        if key in pending_keys:
            continue

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

        highs, lows, closes = fetch_coingecko_ohlc_usd(coin_id, days=COINGECKO_OHLC_DAYS)
        atr_val = _atr(highs, lows, closes, period=14) if highs and lows and closes else None

        levels_source = "BOT_CANDLES"
        levels = build_levels_from_candles(entry, side, highs, lows, closes, use_caps=False)

        used_fallback_caps = False

        if not levels and highs and lows and closes:
            used_fallback_caps = True
            levels_source = "FALLBACK_CAPS_CANDLES"
            levels = build_levels_from_candles(entry, side, highs, lows, closes, use_caps=True)

        if not levels:
            used_fallback_caps = True
            levels_source = "FALLBACK_CAPS_24H"
            high_24h = c.get("high_24h")
            low_24h = c.get("low_24h")
            if high_24h is None or low_24h is None or high_24h <= 0 or low_24h <= 0:
                continue

            if side == "LONG":
                sl = low_24h * 0.997
                if sl >= entry:
                    continue
                tp1 = min(entry * (1.0 + TP1_CAP_FALLBACK), high_24h * 0.995)
                tp2 = min(entry * (1.0 + TP2_CAP_FALLBACK), high_24h * 1.000)
                tp3_cap_dynamic = entry * (1.0 + TP3_CAP_MAX_FALLBACK)
                tp3 = min(tp3_cap_dynamic, high_24h * 1.005)
                if not (sl < entry < tp1 < tp2 < tp3):
                    continue
            else:
                sl = high_24h * 1.003
                if sl <= entry:
                    continue
                tp1 = max(entry * (1.0 - TP1_CAP_FALLBACK), low_24h * 1.005)
                tp2 = max(entry * (1.0 - TP2_CAP_FALLBACK), low_24h * 1.000)
                tp3_cap_dynamic = entry * (1.0 - TP3_CAP_MAX_FALLBACK)
                tp3 = max(tp3_cap_dynamic, low_24h * 0.995)
                if not (tp3 < tp2 < tp1 < entry < sl):
                    continue

            levels = (sl, tp1, tp2, tp3)

        sl, tp1, tp2, tp3 = levels

        final_conf = conf_after_mem
        ai_applied = False
        ai_requested = False
        ai_reason = None

        if ai_enabled() and AI_FILTER_MODE != "off":
            ai_requested = bool(used_fallback_caps and AI_REQUEST_LEVELS_ONLY_ON_FALLBACK)

            want_ai_call = (
                (AI_FILTER_MODE == "filter_and_levels") or
                (AI_FILTER_MODE == "levels_only" and ai_requested)
            )

            if want_ai_call and can_call_ai_now():
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
                        base_confidence=conf_after_mem,
                        chg1h=chg1h,
                        chg24=chg24,
                        atr_value=atr_val,
                        mem_total=mem_total,
                        mem_winrate=mem_wr,
                        request_ai_levels=ai_requested
                    )

                    approved, adj, reason, ai_levels = judge_trade(ctx)

                    if AI_FILTER_MODE == "filter_and_levels" and not approved:
                        continue

                    final_conf = max(0, min(100, conf_after_mem + int(adj)))
                    if reason:
                        ai_reason = f"{reason} ({int(adj):+d})"

                    if ai_requested and atr_val is not None and ai_levels:
                        candidate = validate_ai_levels(
                            side=side,
                            entry=entry,
                            atr_value=atr_val,
                            fallback_levels=(sl, tp1, tp2, tp3),
                            ai_levels=ai_levels
                        )
                        if candidate != (sl, tp1, tp2, tp3) and ai_levels_better(side, entry, (sl, tp1, tp2, tp3), candidate):
                            sl, tp1, tp2, tp3 = candidate
                            ai_applied = True
                            levels_source = "AI_APPLIED"

                except Exception as e:
                    err = repr(e)
                    print("AI error:", err)
                    if "429" in err or "Too Many Requests" in err:
                        mark_ai_cooldown()
                        notes.append("AI rate-limited -> cooling down, kept bot levels")
                    else:
                        notes.append(f"AI error -> kept bot levels ({err[:120]})")
                    final_conf = conf_after_mem

        if final_conf < CONFIDENCE_MIN:
            continue

        notes.append(f"Levels: {levels_source}")
        if ai_enabled() and AI_FILTER_MODE != "off":
            if ai_requested:
                notes.append("AI requested: YES")
            if ai_applied:
                notes.append("AI applied: YES")
            if ai_reason:
                notes.append(f"AI: {ai_reason}")

        set_cooldown(conn, sym, side, cooldown_cache)
        insert_trade(
            conn,
            ts_iso, sym, coin_id, coin_name, side,
            entry, sl, tp1, tp2, tp3,
            final_conf, chg1h, chg24,
            levels_source=levels_source,
            ai_requested=ai_requested if (ai_enabled() and AI_FILTER_MODE != "off") else None,
            ai_applied=ai_applied if (ai_enabled() and AI_FILTER_MODE != "off") else None,
            ai_reason=ai_reason
        )

        pending_keys.add(key)
        pending_signals.append(
            format_signal_msg(
                coin_name, sym, side, entry, sl, tp1, tp2, tp3,
                final_conf, chg1h, chg24, now_str, notes=notes
            )
        )

        if len(pending_signals) >= MAX_SIGNALS_PER_HOUR:
            break

def should_send_now(last_sent_hour):
    now = datetime.now(timezone.utc)
    hour_key = now.strftime("%Y-%m-%d %H")
    return (hour_key != last_sent_hour), hour_key

def send_hourly_update(conn):
    global pending_signals, pending_keys

    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    header = format_hourly_header(conn, now_str)

    if pending_signals:
        body = "\n\n".join(pending_signals)
        msg = f"{header}\n\n{body}"
        send_long_message(msg)
    else:
        send_message(f"{header}\n\n❌ *No coins worth investing in.*\n\n_Not financial advice_")

    pending_signals = []
    pending_keys = set()

def main_loop():
    conn = db_connect()

    try:
        coingecko_self_test()
    except Exception as e:
        print("coingecko_self_test error:", repr(e))

    ai_status = "ON ✅" if ai_enabled() and AI_FILTER_MODE != "off" else "OFF (disabled) ⚠️"
    send_message(
        "✅ Bot online. Analysing 24/7.\n"
        "⏳ Signals are sent once per hour.\n"
        f"⏱ Scan interval: {SCAN_EVERY_SECONDS}s\n"
        f"🤖 AI Mode: {AI_FILTER_MODE} | {ai_status}\n"
        f"🧾 CoinGecko Whitelist: {len(COINGECKO_COIN_IDS)} coins ✅\n"
        f"🕯️ Candles: CoinGecko OHLC ({COINGECKO_OHLC_DAYS}d) ✅\n"
        "🛟 TP caps are FALLBACK-ONLY\n"
        "🧾 Proof: message notes + DB columns levels_source / ai_requested / ai_applied\n"
        "_Not financial advice_"
    )

    last_sent_hour = None

    while True:
        try:
            conn = ensure_conn(conn)
        except Exception:
            pass

        try:
            update_open_trades(conn)
        except Exception as e:
            print("update_open_trades error:", repr(e))

        try:
            scan_and_collect(conn)
        except Exception as e:
            print("scan_and_collect error:", repr(e))

        try:
            do_send, last_sent_hour = should_send_now(last_sent_hour)
            if do_send:
                send_hourly_update(conn)
        except Exception as e:
            print("hourly_send error:", repr(e))

        time.sleep(SCAN_EVERY_SECONDS)

if __name__ == "__main__":
    while True:
        try:
            main_loop()
        except Exception as e:
            print("🔥 FATAL loop error (auto-restarting):", repr(e))
            traceback.print_exc()
            time.sleep(10)
