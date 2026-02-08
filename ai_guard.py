import os
import json
import time
import requests
from typing import Dict, Any, Tuple

OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OPENAI_MODEL = (os.getenv("OPENAI_MODEL", "gpt-4o-mini") or "").strip()
OPENAI_TIMEOUT = float(os.getenv("OPENAI_TIMEOUT", "15"))

# ✅ Default: AI is NOT allowed to override SL/TP unless request_ai_levels=True
AI_LEVELS_ONLY_WHEN_REQUESTED = (os.getenv("AI_LEVELS_ONLY_WHEN_REQUESTED", "1").strip() == "1")

# Retry / backoff
OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "5"))
OPENAI_BASE_DELAY_SECONDS = float(os.getenv("OPENAI_BASE_DELAY_SECONDS", "2"))
OPENAI_MAX_DELAY_SECONDS = float(os.getenv("OPENAI_MAX_DELAY_SECONDS", "30"))
OPENAI_COOLDOWN_ON_429_SECONDS = int(os.getenv("OPENAI_COOLDOWN_ON_429_SECONDS", str(15 * 60)))

_ai_cooldown_until = 0.0

def ai_enabled() -> bool:
    return bool(OPENAI_API_KEY)

def _in_cooldown() -> bool:
    return time.time() < _ai_cooldown_until

def _set_cooldown():
    global _ai_cooldown_until
    _ai_cooldown_until = time.time() + OPENAI_COOLDOWN_ON_429_SECONDS

def _openai_chat(prompt: str) -> str:
    """
    Calls OpenAI with retry/backoff.
    Never throws 429 repeatedly: if rate-limited, enters cooldown.
    """
    if not ai_enabled():
        raise RuntimeError("AI disabled: OPENAI_API_KEY not set")

    if _in_cooldown():
        raise RuntimeError("AI cooldown active (rate limited)")

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
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

    delay = OPENAI_BASE_DELAY_SECONDS

    last_err = None
    for attempt in range(1, OPENAI_MAX_RETRIES + 1):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=OPENAI_TIMEOUT)

            # Handle rate limiting
            if r.status_code == 429:
                _set_cooldown()
                raise RuntimeError("HTTP 429 Too Many Requests (cooldown set)")

            # Retryable server errors
            if r.status_code in (500, 502, 503, 504):
                raise RuntimeError(f"HTTP {r.status_code} server error")

            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]

        except Exception as e:
            last_err = e

            # If cooldown got set, stop retrying now
            if "429" in str(e):
                break

            if attempt < OPENAI_MAX_RETRIES:
                time.sleep(delay)
                delay = min(delay * 2, OPENAI_MAX_DELAY_SECONDS)

    raise RuntimeError(f"OpenAI request failed: {repr(last_err)}")

def judge_trade(trade: Dict[str, Any]) -> Tuple[bool, int, str, Dict[str, float]]:
    """
    Always returns a safe result:
    - If AI fails: approved=True, adj=0, reason="AI unavailable (fallback)", levels={}
    """
    if not ai_enabled():
        return True, 0, "AI disabled (fallback)", {}

    request_ai_levels = bool(trade.get("request_ai_levels", False))

    atr = trade.get("atr_1h", None)
    try:
        atr = float(atr) if atr is not None else None
    except Exception:
        atr = None

    guardrails = {
        "if_atr_present": {
            "long": {"max_sl_distance_atr": 1.35, "max_tp1_atr": 1.2, "max_tp3_atr": 2.8},
            "short": {"max_sl_distance_atr": 1.35, "max_tp1_atr": 1.2, "max_tp3_atr": 2.8}
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
            "levels": {"stop_loss": "number", "tp1": "number", "tp2": "number", "tp3": "number"}
        },
        "rules": rules,
        "guardrails": guardrails,
        "trade": trade
    }

    try:
        raw = _openai_chat(json.dumps(prompt_obj, separators=(",", ":"))).strip()
    except Exception as e:
        # SAFE fallback (never crash bot)
        msg = str(e)
        if "cooldown" in msg.lower():
            return True, 0, "AI cooldown (fallback)", {}
        return True, 0, "AI unavailable (fallback)", {}

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
