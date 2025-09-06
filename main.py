# main.py - start app, pusher, background labeling loop
import asyncio, signal, sys, logging
from telegram_bot import build_app if False else None
# We import build_app dynamically to avoid circular import in template
from telegram_bot import build_app as _build_app, pusher as _pusher, background_loop as _background_loop
from db import init_db
from config import PUSH_INTERVAL_MINUTES

async def main():
    init_db()
    app = _build_app()
    try:
        await app.bot.delete_webhook()
    except Exception:
        pass
    # start background tasks
    asyncio.create_task(_pusher(app))
    asyncio.create_task(_background_loop(app))
    await app.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
