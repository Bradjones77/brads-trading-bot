import os
import json
import time
import random
import requests
from typing import Dict, Any, Tuple

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TIMEOUT = float(os.getenv("OPENAI_TIMEOUT", "10"))

# ✅ Default: AI is NOT allowed to override SL/TP unless request_ai_levels=True
AI_LEVELS_ONLY_WHEN_REQUESTED = (os.getenv("AI_LEVELS_ONLY_WHEN_REQUESTED", "1").strip() == "1")

# ✅ Retry / backoff controls (safe defaults)
OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "5"))
OPENAI_RETRY_BASE_DELAY = float(os.getenv("OPENAI_RETRY_BASE_DELAY", "2.0"))  # seconds
OPENAI_RETRY_MAX_DELAY = float(os.getenv("OPENAI_RETRY_MAX_DELAY", "60.0"))   # seconds

# Optional: cap max total time spent retrying (0 = no cap)
OPENAI_MAX_TOTAL_RETRY_SECONDS = float(os.getenv("OPENAI_MAX_TOTAL_RETRY_SECONDS", "0"))

SESSION = requests.Session()
SESSION.headers.update({
    "Content-Type": "application/json",
    "User-Agent": "brads-trading-bot/ai_guard (rate-safe; retries)",
})

def ai_enabled() -> bool:
    return bool(OPENAI_API_KEY)

def _sleep_backoff(delay: float):
    # small jitter to avoid thundering herd
    jitter = random.uniform(0.0, 0.35 * max(0.0, delay))
    time.sleep(max(0.0, delay) + jitter)

def _openai_chat(prompt: str) -> str:
    """
    Safe OpenAI call with:
    - Retry-After handling for 429
    - Exponential backoff for transient errors (429, 5xx, timeouts, connection errors)
    - Raises RuntimeError with explicit 429 text when rate limited beyond retries
    """
    if not OPENAI_API_KEY:
        raise RuntimeError("AI disabled: OPENAI_API_KEY not set")

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
    }

    payload = {
        "model": OPENAI_MODEL,
        "temperature": 0.1,
        "max_tokens": 260,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a conservative trading risk filter. "
                    "Output ONLY valid JSON. No markdown."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }

    delay = max(0.5, OPENAI_RETRY_BASE_DELAY)
    start_ts = time.time()
    last_err = None

    for attempt in range(1, OPENAI_MAX_RETRIES + 1):
        try:
            r = SESSION.post(url, headers=headers, json=payload, timeout=OPENAI_TIMEOUT)

            # ---- handle rate limit explicitly
            if r.status_code == 429:
                ra_hdr = r.headers.get("Retry-After")
                retry_after = None
                if ra_hdr:
                    try:
                        retry_after = float(ra_hdr)
                    except Exception:
                        retry_after = None

                body_snip = (r.text[:220] if getattr(r, "text", None) else "")
                last_err = f"HTTP 429 Too Many Requests retry_after={retry_after} body={body_snip}"

                # Respect Retry-After if present, else exponential backoff
                sleep_for = retry_after if (retry_after is not None and retry_after > 0) else delay
                sleep_for = min(sleep_for, OPENAI_RETRY_MAX_DELAY)

                # Optional total retry cap
                if OPENAI_MAX_TOTAL_RETRY_SECONDS and (time.time() - start_ts + sleep_for) > OPENAI_MAX_TOTAL_RETRY_SECONDS:
                    raise RuntimeError(f"AI error: 429 (rate limited, total retry cap hit): {last_err}")

                _sleep_backoff(sleep_for)
                delay = min(delay * 2.0, OPENAI_RETRY_MAX_DELAY)
                continue

            # ---- transient server errors: retry
            if 500 <= r.status_code <= 599:
                body_snip = (r.text[:220] if getattr(r, "text", None) else "")
                last_err = f"HTTP {r.status_code} server error body={body_snip}"

                if OPENAI_MAX_TOTAL_RETRY_SECONDS and (time.time() - start_ts + delay) > OPENAI_MAX_TOTAL_RETRY_SECONDS:
                    raise RuntimeError(f"AI error: server {r.status_code} (total retry cap hit): {last_err}")

                _sleep_backoff(delay)
                delay = min(delay * 2.0, OPENAI_RETRY_MAX_DELAY)
                continue

            # ---- other errors: don't spam retries unless it's a known transient class
            if r.status_code >= 400:
                body_snip = (r.text[:220] if getattr(r, "text", None) else "")
                r.raise_for_status()  # will raise HTTPError

            data = r.json()
            return data["choices"][0]["message"]["content"]

        except requests.exceptions.Timeout as e:
            last_err = f"Timeout: {repr(e)}"
        except requests.exceptions.ConnectionError as e:
            last_err = f"ConnectionError: {repr(e)}"
        except requests.exceptions.HTTPError as e:
            # If it’s not 429/5xx (handled above), no point retrying hard
            last_err = f"HTTPError: {repr(e)}"
            raise RuntimeError(f"AI error: {last_err}")
        except Exception as e:
            last_err = repr(e)

        # retry transient exceptions (timeout/connection/general)
        if attempt < OPENAI_MAX_RETRIES:
            if OPENAI_MAX_TOTAL_RETRY_SECONDS and (time.time() - start_ts + delay) > OPENAI_MAX_TOTAL_RETRY_SECONDS:
                raise RuntimeError(f"AI error: transient (total retry cap hit): {last_err}")
            _sleep_backoff(delay)
            delay = min(delay * 2.0, OPENAI_RETRY_MAX_DELAY)
            continue

    # If we exhausted retries, ensure 429 is visible to caller for cooldown logic
    if last_err and "429" in last_err:
        raise RuntimeError(f"AI error: 429 Too Many Requests (exhausted retries): {last_err}")

    raise RuntimeError(f"AI error: failed after retries: {last_err or 'unknown'}")

def judge_trade(trade: Dict[str, Any]) -> Tuple[bool, int, str, Dict[str, float]]:
    if not ai_enabled():
        raise RuntimeError("AI disabled: OPENAI_API_KEY not set")

    request_ai_levels = bool(trade.get("request_ai_levels", False))

    atr = trade.get("atr_1h", None)
    try:
        atr = float(atr) if atr is not None else None
    except Exception:
        atr = None

    guardrails = {
        "if_atr_present": {
            "long": {
                "max_sl_distance_atr": 1.35,
                "max_tp1_atr": 1.2,
                "max_tp3_atr": 2.8
            },
            "short": {
                "max_sl_distance_atr": 1.35,
                "max_tp1_atr": 1.2,
                "max_tp3_atr": 2.8
            }
        }
    }

    rules = [
        "Return ONLY a JSON object with keys: approved, confidence_adjust, reason, levels.",
        "confidence_adjust must be an integer between -20 and +20.",
        "reason must be a short string <= 120 chars.",
        "levels must be {} OR must include ALL FOUR keys: stop_loss,tp1,tp2,tp3.",
        "Be conservative.",
        "Do NOT invent data. Use only fields provided in trade.",
        "NEVER change entry. Entry is bot-controlled and immutable."
    ]

    if AI_LEVELS_ONLY_WHEN_REQUESTED:
        rules.append("If request_ai_levels is false, set levels to {}.")

    if atr is None:
        rules.append("If atr_1h is missing or invalid, set levels to {}.")

    prompt_obj = {
        "task": "Approve/reject the trade. Optionally suggest conservative SL/TP if allowed.",
        "output_schema": {
            "approved": "boolean",
            "confidence_adjust": "integer -20..+20",
            "reason": "short string <= 120 chars",
            "levels": {
                "stop_loss": "number",
                "tp1": "number",
                "tp2": "number",
                "tp3": "number"
            }
        },
        "rules": rules,
        "guardrails": guardrails,
        "trade": trade
    }

    raw = _openai_chat(json.dumps(prompt_obj, separators=(",", ":"))).strip()

    try:
        out = json.loads(raw)
    except Exception:
        return True, 0, "AI parse error (fallback)", {}

    approved = bool(out.get("approved", False))

    adj = out.get("confidence_adjust", 0)
    try:
        adj = int(adj)
    except Exception:
        adj = 0
    adj = max(-20, min(20, adj))

    reason = str(out.get("reason", "")).strip()[:120]

    levels_in = out.get("levels", {}) or {}
    levels: Dict[str, float] = {}
    for k in ("stop_loss", "tp1", "tp2", "tp3"):
        if k in levels_in:
            try:
                levels[k] = float(levels_in[k])
            except Exception:
                pass

    if not all(k in levels for k in ("stop_loss", "tp1", "tp2", "tp3")):
        levels = {}

    if AI_LEVELS_ONLY_WHEN_REQUESTED and not request_ai_levels:
        levels = {}

    if atr is None and levels:
        levels = {}

    return approved, adj, reason, levels
