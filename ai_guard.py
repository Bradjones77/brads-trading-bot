import os
import json
import requests
from typing import Dict, Any, Tuple

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TIMEOUT = float(os.getenv("OPENAI_TIMEOUT", "8"))

def ai_enabled() -> bool:
    return bool(OPENAI_API_KEY)

def _openai_chat(prompt: str) -> str:
    """
    Minimal OpenAI Chat Completions call via HTTPS.
    response_format forces a JSON object.
    """
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": OPENAI_MODEL,
        "temperature": 0.1,
        "max_tokens": 220,  # keep tight
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
    r = requests.post(url, headers=headers, json=payload, timeout=OPENAI_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]

def judge_trade(trade: Dict[str, Any]) -> Tuple[bool, int, str, Dict[str, float]]:
    """
    Returns: (approved, confidence_adjust, reason, levels)

    levels may include stop_loss,tp1,tp2,tp3 (all required if provided).
    Caller still validates levels (ordering + ATR clamps).
    """
    if not ai_enabled():
        raise RuntimeError("AI disabled: OPENAI_API_KEY not set")

    # Pull ATR if present (your main.py includes atr_1h in ctx)
    atr = trade.get("atr_1h", None)
    try:
        atr = float(atr) if atr is not None else None
    except Exception:
        atr = None

    # Hard conservative guidance (AI must follow)
    # If ATR exists: TPs must be small-to-moderate ATR multiples.
    # If ATR missing: AI should normally NOT override levels.
    guardrails = {
        "if_atr_present": {
            "long": {
                "max_sl_distance_atr": 1.3,
                "tp1_atr": 0.6,
                "tp2_atr": 1.0,
                "tp3_atr": 1.4,
                "max_tp1_atr": 1.2,
                "max_tp3_atr": 2.5
            },
            "short": {
                "max_sl_distance_atr": 1.3,
                "tp1_atr": 0.55,
                "tp2_atr": 0.90,
                "tp3_atr": 1.25,
                "max_tp1_atr": 1.2,
                "max_tp3_atr": 2.5
            }
        }
    }

    # Keep prompt compact to reduce failures/cost
    prompt_obj = {
        "task": "Approve/reject the trade. Optionally suggest conservative TP/SL.",
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
        "rules": [
            "Return ONLY a JSON object with keys: approved, confidence_adjust, reason, levels.",
            "confidence_adjust must be an integer between -20 and +20.",
            "levels must be {} OR must include ALL FOUR keys: stop_loss,tp1,tp2,tp3.",
            "Be conservative. Prefer NOT to override levels unless you are confident.",
            "Do NOT invent data. Use only fields provided in trade."
        ],
        "guardrails": guardrails,
        "trade": trade
    }

    raw = _openai_chat(json.dumps(prompt_obj, separators=(",", ":"))).strip()

    # Ultra-safe parse
    try:
        out = json.loads(raw)
    except Exception:
        # Fail-safe: don't crash caller; just "no AI help"
        return True, 0, "AI parse error (fallback)", {}

    approved = bool(out.get("approved", False))

    # Confidence adjust
    adj = out.get("confidence_adjust", 0)
    try:
        adj = int(adj)
    except Exception:
        adj = 0
    adj = max(-20, min(20, adj))

    reason = str(out.get("reason", "")).strip()[:120]

    # Levels (optional)
    levels_in = out.get("levels", {}) or {}
    levels: Dict[str, float] = {}
    for k in ("stop_loss", "tp1", "tp2", "tp3"):
        if k in levels_in:
            try:
                levels[k] = float(levels_in[k])
            except Exception:
                pass

    # Must include all 4 to be accepted; otherwise ignore
    if not all(k in levels for k in ("stop_loss", "tp1", "tp2", "tp3")):
        levels = {}

    # If ATR missing, strongly discourage overrides (extra safety)
    if atr is None and levels:
        # if ATR isn't available, don't let AI override targets
        levels = {}

    return approved, adj, reason, levels
