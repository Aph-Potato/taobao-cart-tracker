"""数据库操作模块 - SQLite"""
import sqlite3
import os
from datetime import datetime

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DB_DIR, "tracker.db")
os.makedirs(DB_DIR, exist_ok=True)


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            taobao_id TEXT UNIQUE NOT NULL,
            item_id TEXT DEFAULT '',
            name TEXT NOT NULL,
            image_url TEXT,
            shop_name TEXT,
            url TEXT,
            is_active INTEGER DEFAULT 1,
            status TEXT DEFAULT 'available',
            unavailable_reason TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            price REAL NOT NULL,
            original_price REAL,
            source TEXT DEFAULT 'playwright',
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_price_product_time ON price_history(product_id, recorded_at DESC);
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            alert_type TEXT NOT NULL DEFAULT 'lowest_price',
            threshold_value REAL,
            is_triggered INTEGER DEFAULT 0,
            triggered_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scrape_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            success INTEGER NOT NULL DEFAULT 0,
            items_found INTEGER DEFAULT 0,
            message TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # 迁移
    for col, typ in [("status", "TEXT DEFAULT 'available'"), ("unavailable_reason", "TEXT DEFAULT ''"),
                     ("item_id", "TEXT DEFAULT ''")]:
        try: cursor.execute(f"ALTER TABLE products ADD COLUMN {col} {typ}")
        except: pass
    for key, val in {"scrape_interval_hours": "6", "price_drop_pct": "0"}.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))
    conn.commit()
    conn.close()


# ========== 商品 ==========

def get_all_products(active_only=True, status=None):
    conn = get_connection()
    conds = []; params = []
    if active_only: conds.append("is_active = 1")
    if status:
        conds.append("status != 'available'" if status == '__non_available' else "status = ?")
        if status != '__non_available': params.append(status)
    query = "SELECT * FROM products" + (" WHERE " + " AND ".join(conds) if conds else "") + " ORDER BY created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_products_grouped(active_only=True):
    return get_all_products(active_only, 'available'), get_all_products(active_only, '__non_available')


def get_product_by_id(pid):
    conn = get_connection()
    row = conn.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def upsert_product(taobao_id, name, image_url=None, shop_name=None, url=None,
                   status='available', unavailable_reason='', item_id=''):
    conn = get_connection()
    existing = conn.execute("SELECT id FROM products WHERE taobao_id = ?", (taobao_id,)).fetchone()
    if existing:
        conn.execute(
            "UPDATE products SET name=?,image_url=?,shop_name=?,url=?,is_active=1,status=?,unavailable_reason=?,item_id=? WHERE taobao_id=?",
            (name, image_url, shop_name, url, status, unavailable_reason, item_id, taobao_id))
        pid = existing["id"]
    else:
        cur = conn.execute(
            "INSERT INTO products (taobao_id,name,image_url,shop_name,url,status,unavailable_reason,item_id) VALUES (?,?,?,?,?,?,?,?)",
            (taobao_id, name, image_url, shop_name, url, status, unavailable_reason, item_id))
        pid = cur.lastrowid
    conn.commit(); conn.close()
    return pid


def set_product_inactive(taobao_id):
    conn = get_connection()
    conn.execute("UPDATE products SET is_active = 0 WHERE taobao_id = ?", (taobao_id,))
    conn.commit(); conn.close()


# ========== 价格 ==========

def add_price_record(product_id, price, original_price=None, source="playwright"):
    conn = get_connection()
    conn.execute("INSERT INTO price_history (product_id,price,original_price,source) VALUES (?,?,?,?)",
                 (product_id, price, original_price, source))
    conn.commit(); conn.close()


def get_price_history(product_id, limit=90):
    conn = get_connection()
    rows = conn.execute("SELECT * FROM price_history WHERE product_id=? ORDER BY recorded_at DESC LIMIT ?",
                        (product_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_price(product_id):
    conn = get_connection()
    row = conn.execute("SELECT price,original_price,recorded_at FROM price_history WHERE product_id=? ORDER BY recorded_at DESC LIMIT 1",
                       (product_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_lowest_price(product_id):
    conn = get_connection()
    row = conn.execute("SELECT MIN(price) as lowest_price, recorded_at FROM price_history WHERE product_id=?",
                       (product_id,)).fetchone()
    conn.close()
    return dict(row) if row and row["lowest_price"] is not None else None


def get_price_stats(product_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) as total_records, MIN(price) as min_price, MAX(price) as max_price, AVG(price) as avg_price,"
        "(SELECT price FROM price_history WHERE product_id=? ORDER BY recorded_at DESC LIMIT 1) as current_price,"
        "(SELECT original_price FROM price_history WHERE product_id=? ORDER BY recorded_at DESC LIMIT 1) as current_original_price "
        "FROM price_history WHERE product_id=?", (product_id, product_id, product_id)).fetchone()
    conn.close()
    return dict(row) if row else None


# ========== 提醒 ==========

def check_and_trigger_alerts(product_id, new_price):
    conn = get_connection()
    pending = conn.execute("SELECT * FROM alerts WHERE product_id=? AND is_triggered=0", (product_id,)).fetchall()
    triggered = []
    for a in pending:
        if a["alert_type"] == "lowest_price":
            lowest = conn.execute("SELECT MIN(price) as lowest FROM price_history WHERE product_id=?",
                                  (product_id,)).fetchone()
            if lowest and new_price <= lowest["lowest"]:
                conn.execute("UPDATE alerts SET is_triggered=1,triggered_at=? WHERE id=?",
                             (datetime.now().isoformat(), a["id"]))
                triggered.append(dict(a))
    conn.commit(); conn.close()
    return triggered


def add_scrape_log(success: bool, items_found: int, message: str):
    conn = get_connection()
    conn.execute("INSERT INTO scrape_log (success, items_found, message) VALUES (?, ?, ?)",
                 (1 if success else 0, items_found, message))
    conn.commit()
    # 只保留最近 100 条
    conn.execute("DELETE FROM scrape_log WHERE id NOT IN (SELECT id FROM scrape_log ORDER BY id DESC LIMIT 100)")
    conn.commit()
    conn.close()


def get_last_scrape() -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM scrape_log ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else None


def get_scrape_logs(limit=10):
    conn = get_connection()
    rows = conn.execute("SELECT * FROM scrape_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def cleanup_old_records(days=365):
    conn = get_connection()
    conn.execute("DELETE FROM price_history WHERE recorded_at < datetime('now', ?)", (f"-{days} days",))
    deleted = conn.total_changes
    conn.commit(); conn.close()
    return deleted


# ========== 设置 ==========

def get_setting(key, default=None):
    conn = get_connection()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_connection()
    conn.execute("INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=?",
                 (key, value, value))
    conn.commit(); conn.close()


def get_all_settings():
    conn = get_connection()
    rows = conn.execute("SELECT key,value FROM settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}
