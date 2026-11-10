import os
import sqlite3
import hashlib
import secrets
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'state', 'trading_system.db')

def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def hash_password(password: str, salt: Optional[str] = None) -> str:
    """Hashes password using PBKDF2-HMAC-SHA256 with salt."""
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100000).hex()
    return f"{salt}:{hashed}"

def verify_password(password: str, stored_hash: str) -> bool:
    """Verifies a plain password against stored salt:hash string."""
    try:
        salt, hashed = stored_hash.split(':')
        check = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100000).hex()
        return secrets.compare_digest(hashed, check)
    except Exception:
        return False

def init_db():
    """Initializes database tables and creates default admin user if not present."""
    conn = get_connection()
    with conn:
        # 1. Users Table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user', -- 'admin' or 'user'
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
        """)

        # 2. User Sessions Table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)

        # 3. User Broker & Auto-Login Configuration
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_broker_config (
                user_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL DEFAULT 'DHAN',
                dhan_client_id TEXT DEFAULT '',
                access_token TEXT DEFAULT '',
                trading_mode TEXT NOT NULL DEFAULT 'PAPER', -- 'PAPER' or 'LIVE'
                product_type TEXT NOT NULL DEFAULT 'CNC',
                auto_login_enabled INTEGER NOT NULL DEFAULT 0,
                auto_login_time TEXT NOT NULL DEFAULT '09:00',
                dhan_pin TEXT DEFAULT '',
                totp_secret TEXT DEFAULT '',
                api_key TEXT DEFAULT '',
                api_secret TEXT DEFAULT '',
                last_login_time TEXT DEFAULT '',
                last_login_status TEXT DEFAULT 'NOT_CONFIGURED',
                last_login_message TEXT DEFAULT '',
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)

        # 4. User Stocks Universe
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_stocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                allocated_capital REAL NOT NULL DEFAULT 100000.0,
                sl_mode TEXT NOT NULL DEFAULT 'CANDLE_LOW',
                enabled INTEGER NOT NULL DEFAULT 1,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(user_id, symbol),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)

        # 5. User Active Positions
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN', -- 'OPEN' or 'CLOSED'
                entry_price REAL NOT NULL,
                entry_time TEXT NOT NULL,
                entry_bar_time TEXT DEFAULT '',
                shares INTEGER NOT NULL,
                allocated_capital REAL NOT NULL,
                invested_amount REAL NOT NULL,
                stop_loss_price REAL NOT NULL,
                sl_mode TEXT NOT NULL,
                order_id TEXT DEFAULT '',
                highest_price_seen REAL NOT NULL,
                UNIQUE(user_id, symbol, status),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)

        # 6. User Closed Trades History
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                entry_time TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_time TEXT NOT NULL,
                exit_price REAL NOT NULL,
                shares INTEGER NOT NULL,
                invested_amount REAL NOT NULL,
                pnl REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                exit_reason TEXT NOT NULL,
                entry_order_id TEXT DEFAULT '',
                exit_order_id TEXT DEFAULT '',
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)

        # 7. User Cumulative PnL Snapshots (for PnL Graph)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_pnl_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                cumulative_pnl REAL NOT NULL DEFAULT 0.0,
                unrealized_pnl REAL NOT NULL DEFAULT 0.0,
                total_pnl REAL NOT NULL DEFAULT 0.0,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)

        # Create Default Admin User if no users exist
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users;")
        count = cur.fetchone()[0]
        if count == 0:
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            pwd_hash = hash_password("admin123")
            cur.execute("""
                INSERT INTO users (username, password_hash, full_name, role, is_active, created_at)
                VALUES (?, ?, ?, 'admin', 1, ?);
            """, ("admin", pwd_hash, "Administrator", now_str))
            admin_id = cur.lastrowid

            # Create default config for admin
            cur.execute("""
                INSERT INTO user_broker_config (user_id, trading_mode, auto_login_enabled)
                VALUES (?, 'PAPER', 0);
            """, (admin_id,))

            # Seed default high-beta stocks for admin
            default_stocks = [
                ("BSE", 250000.0, "CANDLE_LOW", 1, "Market Infrastructure Top Runner"),
                ("MCX", 100000.0, "CANDLE_LOW", 1, "Commodity Exchange Runner"),
                ("CDSL", 100000.0, "CANDLE_LOW", 1, "Depository Compounder"),
                ("DIXON", 100000.0, "CANDLE_LOW", 1, "High-Beta Electronics"),
                ("SHRIRAMFIN", 100000.0, "CANDLE_LOW", 1, "NBFC Breakout Runner"),
                ("TRENT", 150000.0, "CANDLE_LOW", 1, "Retail Momentum Leader")
            ]
            for sym, cap, sl, en, note in default_stocks:
                cur.execute("""
                    INSERT INTO user_stocks (user_id, symbol, allocated_capital, sl_mode, enabled, notes, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                """, (admin_id, sym, cap, sl, en, note, now_str))

            # Initial 0 snapshot
            cur.execute("""
                INSERT INTO user_pnl_snapshots (user_id, timestamp, cumulative_pnl, unrealized_pnl, total_pnl)
                VALUES (?, ?, 0.0, 0.0, 0.0);
            """, (admin_id, now_str))
            print(f"[DB] Initialized database with default admin user (username: 'admin', password: 'admin123').")

    conn.close()

# Auto-initialize DB on import
init_db()

