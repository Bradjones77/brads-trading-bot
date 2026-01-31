import os
import json
import requests
from typing import Dict, Any, Optional, Tuple

# Fail-safe AI gatekeeper + confidence adjuster
# If anything fails, caller should fallback to base logic.

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # default small/fast/cheap
OPENAI_TIMEOUT = float(os.getenv("OPENAI_TIMEOUT", "8"))  # seconds (hard cap)

def ai_enabled() -> bool:
    return bool(OPENAI_API_KEY)

def _openai_chat(prompt: str) -> str:
    """
    Minimal OpenAI Chat Completions call via HTTPS.
    No SDK required. If this errors, caller will fallback safely.
    """
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": OPENAI_MODEL,
        "temperature": 0.1,
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
    Returns: (approved, adjusted_confidence, reason)
    - If AI fails, caller should ignore this and fallback to base behavior.
    """
    if not ai_enabled():
        raise RuntimeError("AI disabled: OPENAI_API_KEY not set")

    # Keep prompt short + structured to reduce cost and weirdness
    prompt_obj = {
        "task": "Decide if this trade is worth sending as a signal. Also adjust confidence.",
        "rules": [
            "Return JSON only with keys: approved (boolean), confidence_adjust (integer between -20 and +20), reason (short string).",
            "Reject if entry looks late, risk/reward is poor, momentum is fading, or market context seems unfavorable.",
            "Approve if trend/momentum align, risk/reward is reasonable, and the move is not exhausted.",
            "Do not invent prices. Use the provided fields only."
        ],
        "trade": trade
    }

    prompt = json.dumps(prompt_obj, separators=(",", ":"))

    raw = _openai_chat(prompt).strip()

    # Hard parse JSON
    out = json.loads(raw)

    approved = bool(out.get("approved", False))
    adj = int(out.get("confidence_adjust", 0))
    reason = str(out.get("reason", "")).strip()[:120]

    # Clamp adjustment for safety
    if adj < -20:
        adj = -20
    if adj > 20:
        adj = 20

    return approved, adj, reason
