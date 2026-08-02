import sqlite3
import os

DB_PATH = 'database/users.db'

def init_db():
    os.makedirs('database', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, platform TEXT, last_download TEXT, total_downloads INTEGER)''')
    conn.commit()
    conn.close()

def log_download(user_id, platform):
    init_db()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, platform, total_downloads) VALUES (?, ?, 0)", (user_id, platform))
    c.execute("UPDATE users SET total_downloads = total_downloads + 1, last_download = datetime('now') WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
