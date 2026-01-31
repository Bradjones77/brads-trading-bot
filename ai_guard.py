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
    Fail-safe: caller will catch exceptions and fallback.
    """
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": OPENAI_MODEL,
        "temperature": 0.1,
        "max_tokens": 180,
        # Forces valid JSON object output (reduces parse failures massively)
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "You are a strict trading risk filter. Output ONLY valid JSON."},
            {"role": "user", "content": prompt},
        ],
    }
    r = requests.post(url, headers=headers, json=payload, timeout=OPENAI_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]

def judge_trade(trade: Dict[str, Any]) -> Tuple[bool, int, str]:
    """
    Returns: (approved, confidence_adjust, reason)
    confidence_adjust is clamped to [-20, +20]
    """
    if not ai_enabled():
        raise RuntimeError("AI disabled: OPENAI_API_KEY not set")

    prompt_obj = {
        "task": "Decide if this trade is worth sending as a signal. Also adjust confidence.",
        "rules": [
            "Return JSON only with keys: approved (boolean), confidence_adjust (integer between -20 and +20), reason (short string).",
            "Reject if entry looks late, risk/reward is poor, momentum is fading, or context seems unfavorable.",
            "Approve if trend/momentum align, risk/reward is reasonable, and the move is not exhausted.",
            "Do not invent prices. Use provided fields only."
        ],
        "trade": trade
    }

    raw = _openai_chat(json.dumps(prompt_obj, separators=(",", ":"))).strip()

    # Parse JSON robustly (response_format should ensure valid JSON, but keep it safe)
    try:
        out = json.loads(raw)
    except Exception:
        # ultra-safe fallback: reject rather than crash
        return False, 0, "AI parse error"

    approved = bool(out.get("approved", False))
    adj = out.get("confidence_adjust", 0)
    try:
        adj = int(adj)
    except Exception:
        adj = 0

    reason = str(out.get("reason", "")).strip()[:120]

    if adj < -20:
        adj = -20
    if adj > 20:
        adj = 20

    return approved, adj, reason
