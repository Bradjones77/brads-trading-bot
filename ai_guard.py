import os
import json
import requests
from typing import Dict, Any, Tuple

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TIMEOUT = float(os.getenv("OPENAI_TIMEOUT", "10"))

# ✅ Default: AI is NOT allowed to override SL/TP unless request_ai_levels=True
AI_LEVELS_ONLY_WHEN_REQUESTED = (os.getenv("AI_LEVELS_ONLY_WHEN_REQUESTED", "1").strip() == "1")

def ai_enabled() -> bool:
    return bool(OPENAI_API_KEY)

def _openai_chat(prompt: str) -> str:
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
    r = requests.post(url, headers=headers, json=payload, timeout=OPENAI_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]

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
