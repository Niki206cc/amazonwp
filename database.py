import sqlite3
from datetime import datetime
from config import DB_PATH


def now():
    return datetime.now().isoformat(timespec="seconds")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asin TEXT,
            title TEXT NOT NULL,
            amazon_url TEXT,
            image_url TEXT,
            local_image TEXT,
            price TEXT,
            category TEXT,
            features TEXT,
            notes TEXT,
            source TEXT NOT NULL DEFAULT 'manual',
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            alt_titles TEXT NOT NULL DEFAULT '',
            meta_description TEXT NOT NULL DEFAULT '',
            excerpt TEXT NOT NULL DEFAULT '',
            html TEXT NOT NULL,
            ai_engine TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            published_at TEXT,
            error TEXT,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER NOT NULL UNIQUE,
            position INTEGER NOT NULL,
            approved_at TEXT NOT NULL,
            FOREIGN KEY(article_id) REFERENCES articles(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS dismissed_products (
            asin TEXT PRIMARY KEY,
            title TEXT,
            dismissed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """)

        defaults = {
            "ai_engine": "openai",
            "openai_api_key": "",
            "openai_model": "gpt-5-mini",
            "gemini_api_key": "",
            "gemini_model": "gemini-2.5-flash",
            "smtp_host": "",
            "smtp_port": "587",
            "smtp_user": "",
            "smtp_password": "",
            "smtp_from": "",
            "smtp_to": "",
            "smtp_security": "starttls",
            "amazon_credential_id": "",
            "amazon_secret": "",
            "amazon_partner_tag": "",
            "amazon_marketplace": "www.amazon.it",
            "amazon_token_url": "https://api.amazon.co.uk/auth/o2/token",
            "amazon_api_base": "https://creatorsapi.amazon",
            "publish_days": "0,1,2,3,4,5,6",
            "publish_time": "09:00",
            "scheduler_enabled": "0",
            "email_subject_prefix": "",
        }
        for k, v in defaults.items():
            db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))


def get_settings():
    with get_db() as db:
        return {r["key"]: r["value"] for r in db.execute("SELECT key,value FROM settings")}


def set_settings(values):
    with get_db() as db:
        for key, value in values.items():
            db.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value or "")),
            )


def log(message, level="INFO"):
    with get_db() as db:
        db.execute("INSERT INTO logs(level,message,created_at) VALUES(?,?,?)", (level, message, now()))
