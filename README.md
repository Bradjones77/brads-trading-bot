Advanced Coinbase Signal Bot v2 — Production-ready (Railway)
===========================================================

This build adds:
- More technical features: MACD, ATR, volume features.
- Improved labeling logic for short/long targets.
- Persistence: SQLite DB for subscribers, logs, and events.
- Logging to file and console.
- Railway deployment files: Procfile, .env.example.
- Model saving with joblib (models/ folder).
- Better background loops and graceful shutdown hints.

Requirements & Python Version
-----------------------------
Use Python 3.10+ (3.12 recommended). Install dependencies:
```
pip install -r requirements.txt
```

Environment variables (preferred) — set these on Railway:
- TELEGRAM_TOKEN (or leave in config.py)
- PUSH_INTERVAL_MINUTES (default 60)
- COINBASE_API_BASE (optional)

Files of interest
-----------------
- config.py
- coinbase_fetcher.py
- features.py (MACD, ATR, volume features)
- learner.py (online learner unchanged)
- signal_generator.py (improved labeling & thresholds)
- telegram_bot.py (SQLite persistence, logging, subscribe/unsubscribe)
- db.py (sqlite helpers)
- main.py (starts app and background tasks)
- Procfile (for Railway)

Deploy on Railway
-----------------
1. Upload project or link repo.
2. Set env vars (TELEGRAM_TOKEN especially).
3. Railway automatically detects Procfile and starts `web: python main.py`.
4. Check logs on Railway, run /subscribe in Telegram to get pushes.
