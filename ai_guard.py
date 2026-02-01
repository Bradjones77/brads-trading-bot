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
    response_format forces JSON object output.
    Fail-safe: caller catches exceptions and falls back.
    """
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
            {"role": "system", "content": "You are a strict trading risk filter. Output ONLY valid JSON."},
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

    levels is optional and may include:
      - stop_loss
      - tp1
      - tp2
      - tp3

    Caller MUST validate levels (caps + ordering). If invalid, ignore.
    """
    if not ai_enabled():
        raise RuntimeError("AI disabled: OPENAI_API_KEY not set")

    prompt_obj = {
        "task": "Decide if this trade is worth sending as a signal. Optionally refine TP/SL realistically and conservatively.",
        "rules": [
            "Return JSON only with keys: approved (boolean), confidence_adjust (integer -20..+20), reason (short string), levels (object).",
            "levels can be {} if you do not want to change targets.",
            "If you provide levels, include all four keys: stop_loss,tp1,tp2,tp3 as numbers.",
            "For LONG: stop_loss < entry < tp1 < tp2 < tp3.",
            "For SHORT: tp3 < tp2 < tp1 < entry < stop_loss.",
            "Keep targets conservative and realistic. Avoid large jumps. Prefer small-to-moderate targets.",
            "Do not invent data. Use the provided fields only."
        ],
        "trade": trade
    }

    raw = _openai_chat(json.dumps(prompt_obj, separators=(",", ":"))).strip()

    try:
        out = json.loads(raw)
    except Exception:
        return False, 0, "AI parse error", {}

    approved = bool(out.get("approved", False))

    adj = out.get("confidence_adjust", 0)
    try:
        adj = int(adj)
    except Exception:
        adj = 0
    if adj < -20:
        adj = -20
    if adj > 20:
        adj = 20

    reason = str(out.get("reason", "")).strip()[:120]

    levels_in = out.get("levels", {}) or {}
    levels: Dict[str, float] = {}
    for k in ("stop_loss", "tp1", "tp2", "tp3"):
        if k in levels_in:
            try:
                levels[k] = float(levels_in[k])
            except Exception:
                pass

    # If AI didn't provide all 4, treat as "no change"
    if not all(k in levels for k in ("stop_loss", "tp1", "tp2", "tp3")):
        levels = {}

    return approved, adj, reason, levels
