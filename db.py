# db.py - simple SQLite persistence using sqlite3
import sqlite3, threading, time
from config import DB_PATH

_lock = threading.Lock()

def init_db():
    with _lock, sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS subscribers (
            chat_id INTEGER PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            level TEXT,
            msg TEXT
        )''')
        conn.commit()

def add_subscriber(chat_id):
    with _lock, sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute('INSERT OR IGNORE INTO subscribers(chat_id) VALUES (?)', (chat_id,))
        conn.commit()

def remove_subscriber(chat_id):
    with _lock, sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute('DELETE FROM subscribers WHERE chat_id=?', (chat_id,))
        conn.commit()

def list_subscribers():
    with _lock, sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute('SELECT chat_id FROM subscribers')
        return [r[0] for r in cur.fetchall()]

def log(level, msg):
    with _lock, sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute('INSERT INTO logs(level,msg) VALUES (?,?)', (level, msg))
        conn.commit()
