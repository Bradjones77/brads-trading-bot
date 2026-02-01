import os
import time
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

if not BOT_TOKEN or not CHAT_ID or not DATABASE_URL:
    raise RuntimeError("BOT_TOKEN, CHAT_ID, or DATABASE_URL missing")

# ======================
# SETTINGS
# ======================
SCAN_EVERY_SECONDS = 600          # 10 minutes (analyse 24/7)
TOP_N_COINS = 150                 # Top 150 only (CoinGecko)
CONFIDENCE_MIN = 65               # only send signals >= 65 confidence
MAX_SIGNALS_PER_HOUR = 10         # prevent spam

# Momentum thresholds
MIN_24H = 1.5
MIN_1H = 0.4

# Cooldown per symbol+side to avoid repeats (PERSISTENT via DB)
ALERT_COOLDOWN_SECONDS = 60 * 60  # 1 hour

# In-memory fallback cooldowns (used only if DB check fails)
last_alert_time = {}

# Pending signals bucket (collect during hour, send hourly)
pending_signals = []
pending_keys = set()  # (symbol, side)

# ======================
# MEMORY RULES (A + B)
# ======================
MEM_STRICT_MIN_TRADES = 6
MEM_STRICT_BLOCK_BELOW_WINRATE = 0.30

MEM_SOFT_MIN_TRADES = 4
MEM_SOFT_PENALIZE_BELOW_WINRATE = 0.45
MEM_SOFT_PENALTY = -10

MEM_LOOKBACK_DAYS = 14

# ======================
# COINGECKO RATE LIMIT PROTECTION
# ======================
COINGECKO_TIMEOUT = 30
COINGECKO_MAX_RETRIES = 6

# Cache last good markets snapshot so bot can keep running during 429 bursts
MARKETS_CACHE_TTL_SECONDS = 20 * 60  # 20 minutes
_last_markets = None
_last_markets_ts = 0

# Reduce /simple/price frequency (open trade monitoring)
OPEN_TRADES_CHECK_EVERY_SECONDS = 30 * 60  # 30 minutes
_last_open_check_ts = 0

# One requests session (slightly nicer to CoinGecko)
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "brads-trading-bot/1.0 (signals-only; rate-safe)"
})

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
            side TEXT NOT NULL,             -- LONG / SHORT
            entry DOUBLE PRECISION NOT NULL,
            stop_loss DOUBLE PRECISION NOT NULL,
            tp1 DOUBLE PRECISION NOT NULL,
            tp2 DOUBLE PRECISION NOT NULL,
            tp3 DOUBLE PRECISION NOT NULL,
            confidence INTEGER NOT NULL,
            chg1h DOUBLE PRECISION,
            chg24 DOUBLE PRECISION,
            status TEXT NOT NULL DEFAULT 'OPEN',  -- OPEN / CLOSED
            result TEXT,                          -- WIN / LOSS
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

# ======================
# PERSISTENT COOLDOWNS (DB)
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
def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    SESSION.post(url, json=payload, timeout=20)

# ======================
# COINGECKO SAFE HTTP
# ======================
def _get_json_with_backoff(url, params):
    delay = 5
    last_err = None

    for attempt in range(COINGECKO_MAX_RETRIES):
        try:
            r = SESSION.get(url, params=params, timeout=COINGECKO_TIMEOUT)

            # Rate limit
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = max(delay, int(retry_after))
                    except Exception:
                        pass
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue

            r.raise_for_status()
            return r.json()

        except Exception as e:
            last_err = e
            time.sleep(delay)
            delay = min(delay * 2, 120)

    raise RuntimeError(f"CoinGecko request failed after retries: {last_err}")

# ======================
# MARKET DATA (CoinGecko)
# ======================
def fetch_top_markets(top_n=150):
    global _last_markets, _last_markets_ts

    # Serve cache if fresh (prevents hammering on restarts and during 429 bursts)
    now = time.time()
    if _last_markets and (now - _last_markets_ts) < MARKETS_CACHE_TTL_SECONDS:
        return _last_markets

    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "sparkline": "false",
        "price_change_percentage": "1h,24h",
        "per_page": top_n,
        "page": 1,
    }

    data = _get_json_with_backoff(url, params)
    _last_markets = data
    _last_markets_ts = now
    return data

def fetch_simple_price_usd(coin_ids):
    if not coin_ids:
        return {}

    # Basic safety: CoinGecko URLs can get big; keep it reasonable
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

def build_levels(entry, side, high_24h, low_24h):
    if entry is None or high_24h is None or low_24h is None:
        return None
    if low_24h <= 0 or high_24h <= 0:
        return None

    if side == "LONG":
        sl = low_24h * 0.997
        risk = entry - sl
        if risk <= 0:
            return None
        tp1 = entry + 1.0 * risk
        tp2 = entry + 2.0 * risk
        tp3 = entry + 3.0 * risk
        return sl, tp1, tp2, tp3
    else:  # SHORT
        sl = high_24h * 1.003
        risk = sl - entry
        if risk <= 0:
            return None
        tp1 = entry - 0.8 * risk
        tp2 = entry - 1.1 * risk
        tp3 = entry - 1.4 * risk
        return sl, tp1, tp2, tp3

# ======================
# DECISION MEMORY (DB)
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
def build_ai_context(coin_name, sym, side, entry, sl, tp1, tp2, tp3, base_conf, chg1h, chg24, mem_total=None, mem_winrate=None):
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

def format_hourly_header(conn, time_str):
    all_win, all_total, win7, total7 = get_win_stats(conn)
    return (
        f"🧠 *Hourly Market Scan* ({time_str})\n"
        f"📊 *Win Rate:* All-time `{all_win:.1f}%` ({all_total} trades) | Last 7D `{win7:.1f}%` ({total7} trades)\n"
        f"━━━━━━━━━━━━━━"
    )

# ======================
# TRADE OUTCOME TRACKING
# ======================
def update_open_trades(conn):
    """
    Rate-safe: only do this every OPEN_TRADES_CHECK_EVERY_SECONDS.
    """
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
        else:  # SHORT
            if px >= sl:
                close_trade(conn, trade_id, "LOSS")
            elif px <= tp1:
                close_trade(conn, trade_id, "WIN")

# ======================
# SCAN + COLLECT
# ======================
def scan_and_collect(conn):
    global pending_signals, pending_keys

    markets = fetch_top_markets(TOP_N_COINS)
    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    ts_iso = datetime.now(timezone.utc).isoformat()

    cooldown_cache = load_cooldowns(conn)

    for c in markets:
        chg1h = c.get("price_change_percentage_1h_in_currency")
        chg24 = c.get("price_change_percentage_24h")
        entry = c.get("current_price")
        high_24h = c.get("high_24h")
        low_24h = c.get("low_24h")

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
        coin_id = c.get("id")
        coin_name = c.get("name") or sym
        if not sym or not coin_id:
            continue

        # Persistent cooldown (DB); fallback to RAM if needed
        try:
            if not cooldown_ok(sym, side, cooldown_cache):
                continue
        except Exception:
            if not should_alert_fallback_ram(sym, side):
                continue

        key = (sym, side)
        if key in pending_keys:
            continue

        levels = build_levels(entry, side, high_24h, low_24h)
        if not levels:
            continue
        sl, tp1, tp2, tp3 = levels

        conf = score(chg24, chg1h)
        notes = []

        blocked, mem_delta, mem_note = apply_memory_rules(conn, sym, side)
        if mem_note:
            notes.append(mem_note)
        if blocked:
            continue

        conf_after_mem = max(0, min(100, conf + mem_delta))

        final_conf = conf_after_mem
        if ai_enabled():
            try:
                mem_total, mem_wr = get_recent_side_performance(conn, sym, side)
                ctx = build_ai_context(
                    coin_name, sym, side, entry, sl, tp1, tp2, tp3,
                    conf_after_mem, chg1h, chg24,
                    mem_total=mem_total, mem_winrate=mem_wr
                )
                approved, adj, reason = judge_trade(ctx)
                if not approved:
                    continue
                final_conf = max(0, min(100, conf_after_mem + int(adj)))
                if reason:
                    notes.append(f"AI: {reason} ({int(adj):+d})")
            except Exception:
                final_conf = conf_after_mem

        if final_conf < CONFIDENCE_MIN:
            continue

        # Set cooldown BEFORE saving/sending so restarts don't resend
        set_cooldown(conn, sym, side, cooldown_cache)

        insert_trade(conn, ts_iso, sym, coin_id, coin_name, side, entry, sl, tp1, tp2, tp3, final_conf, chg1h, chg24)

        pending_keys.add(key)
        pending_signals.append(
            format_signal_msg(coin_name, sym, side, entry, sl, tp1, tp2, tp3, final_conf, chg1h, chg24, now_str, notes=notes)
        )

        if len(pending_signals) >= MAX_SIGNALS_PER_HOUR:
            break

# ======================
# HOURLY SEND CONTROL
# ======================
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
        send_message(f"{header}\n\n{body}")
    else:
        send_message(f"{header}\n\n❌ *No coins worth investing in.*\n\n_Not financial advice_")

    pending_signals = []
    pending_keys = set()

# ======================
# MAIN LOOP (RATE-SAFE)
# ======================
def main():
    conn = db_connect()

    ai_status = "ON ✅" if ai_enabled() else "OFF (no OPENAI_API_KEY) ⚠️"
    send_message(
        "✅ Bot online. Analysing 24/7.\n"
        "⏳ Signals are sent once per hour.\n"
        f"🤖 AI Filter: {ai_status}\n"
        "🧠 Decision Memory: ON ✅\n"
        "🕒 Persistent Cooldowns: ON ✅\n"
        "_Not financial advice_"
    )

    last_sent_hour = None

    while True:
        # 1) Open trade monitoring (rate-limited internally)
        try:
            update_open_trades(conn)
        except Exception as e:
            print("update_open_trades error:", e)

        # 2) Scan market (protected by backoff + cache)
        try:
            scan_and_collect(conn)
        except Exception as e:
            print("scan_and_collect error:", e)

        # 3) ALWAYS attempt hourly send, even if CoinGecko failed
        try:
            do_send, last_sent_hour = should_send_now(last_sent_hour)
            if do_send:
                send_hourly_update(conn)
        except Exception as e:
            print("hourly_send error:", e)

        time.sleep(SCAN_EVERY_SECONDS)

if __name__ == "__main__":
    main()
