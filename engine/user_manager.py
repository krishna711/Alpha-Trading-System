import os
import sqlite3
import secrets
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from .db import get_connection, hash_password, verify_password

class UserManager:
    """
    Handles Multi-User Authentication, User Profiles, Broker Configs,
    Stocks Universe, Positions, Trades, and PnL Snapshots.
    """

    @staticmethod
    def create_user(username: str, password: str, full_name: str, role: str = "user") -> Dict[str, Any]:
        username = username.strip().lower()
        if not username or not password:
            raise ValueError("Username and password are required.")
        if len(password) < 6:
            raise ValueError("Password must be at least 6 characters.")

        pwd_hash = hash_password(password)
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        conn = get_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO users (username, password_hash, full_name, role, is_active, created_at)
                    VALUES (?, ?, ?, ?, 1, ?);
                """, (username, pwd_hash, full_name.strip(), role, now_str))
                user_id = cur.lastrowid

                # Create default broker config for new user
                cur.execute("""
                    INSERT INTO user_broker_config (user_id, trading_mode, auto_login_enabled)
                    VALUES (?, 'PAPER', 0);
                """, (user_id,))

                # Seed initial 0 PnL snapshot
                cur.execute("""
                    INSERT INTO user_pnl_snapshots (user_id, timestamp, cumulative_pnl, unrealized_pnl, total_pnl)
                    VALUES (?, ?, 0.0, 0.0, 0.0);
                """, (user_id, now_str))

                return {
                    'id': user_id,
                    'username': username,
                    'full_name': full_name,
                    'role': role,
                    'created_at': now_str
                }
        except sqlite3.IntegrityError:
            raise ValueError(f"Username '{username}' already exists.")
        finally:
            conn.close()

    @staticmethod
    def authenticate_user(username: str, password: str) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, username, password_hash, full_name, role, is_active FROM users WHERE username = ?;", (username.strip().lower(),))
        row = cur.fetchone()
        conn.close()

        if row and row['is_active'] and verify_password(password, row['password_hash']):
            return {
                'id': row['id'],
                'username': row['username'],
                'full_name': row['full_name'],
                'role': row['role']
            }
        return None

    @staticmethod
    def create_session(user_id: int, days_valid: int = 7) -> str:
        token = secrets.token_hex(32)
        now = datetime.now()
        expires = (now + timedelta(days=days_valid)).strftime('%Y-%m-%d %H:%M:%S')

        conn = get_connection()
        with conn:
            conn.execute("""
                INSERT INTO user_sessions (token, user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?);
            """, (token, user_id, now.strftime('%Y-%m-%d %H:%M:%S'), expires))
        conn.close()
        return token

    @staticmethod
    def get_user_by_session(token: str) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT u.id, u.username, u.full_name, u.role, u.is_active
            FROM user_sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.token = ? AND s.expires_at > ? AND u.is_active = 1;
        """, (token, now_str))
        row = cur.fetchone()
        conn.close()
        if row:
            return dict(row)
        return None

    @staticmethod
    def delete_session(token: str):
        conn = get_connection()
        with conn:
            conn.execute("DELETE FROM user_sessions WHERE token = ?;", (token,))
        conn.close()

    @staticmethod
    def get_all_users() -> List[Dict[str, Any]]:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, username, full_name, role, is_active, created_at FROM users ORDER BY id ASC;")
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, username, full_name, role, is_active, created_at FROM users WHERE id = ?;", (user_id,))
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    @staticmethod
    def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, username, full_name, role, is_active, created_at FROM users WHERE username = ?;", (username.strip().lower(),))
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    @staticmethod
    def change_password(user_id: int, new_password: str, clear_sessions: bool = False) -> bool:
        """Admin or system method to set a new password for any user."""
        if not new_password or len(new_password) < 6:
            raise ValueError("New password must be at least 6 characters.")
        pwd_hash = hash_password(new_password)
        conn = get_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute("UPDATE users SET password_hash = ? WHERE id = ?;", (pwd_hash, user_id))
                updated = cur.rowcount > 0
                if updated and clear_sessions:
                    cur.execute("DELETE FROM user_sessions WHERE user_id = ?;", (user_id,))
                return updated
        finally:
            conn.close()

    @staticmethod
    def verify_and_change_password(user_id: int, current_password: str, new_password: str) -> bool:
        """Allows a user to change their own password after verifying the current password."""
        if not new_password or len(new_password) < 6:
            raise ValueError("New password must be at least 6 characters.")
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT password_hash FROM users WHERE id = ?;", (user_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError("User not found.")
            if not verify_password(current_password, row['password_hash']):
                raise ValueError("Current password is incorrect.")
        finally:
            conn.close()

        return UserManager.change_password(user_id, new_password, clear_sessions=False)

    @staticmethod
    def delete_user(user_id: int) -> bool:
        """Deletes a user and cascades deletion to all their configurations, stocks, positions, and trades."""
        conn = get_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute("SELECT id, role FROM users WHERE id = ?;", (user_id,))
                user = cur.fetchone()
                if not user:
                    return False
                if user['role'] == 'admin':
                    cur.execute("SELECT COUNT(*) FROM users WHERE role = 'admin';")
                    admin_count = cur.fetchone()[0]
                    if admin_count <= 1:
                        raise ValueError("Cannot delete the last administrator account.")
                cur.execute("DELETE FROM users WHERE id = ?;", (user_id,))
                return cur.rowcount > 0
        finally:
            conn.close()

    # ---------------- USER BROKER & AUTH CONFIG ----------------

    @staticmethod
    def get_user_broker_config(user_id: int) -> Dict[str, Any]:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM user_broker_config WHERE user_id = ?;", (user_id,))
        row = cur.fetchone()
        conn.close()
        if row:
            return dict(row)
        return {}

    @staticmethod
    def update_user_broker_config(user_id: int, **kwargs) -> Dict[str, Any]:
        valid_cols = [
            'name', 'dhan_client_id', 'access_token', 'trading_mode', 'product_type',
            'auto_login_enabled', 'auto_login_time', 'dhan_pin', 'totp_secret',
            'api_key', 'api_secret', 'last_login_time', 'last_login_status', 'last_login_message'
        ]
        updates = {k: v for k, v in kwargs.items() if k in valid_cols}
        if not updates:
            return UserManager.get_user_broker_config(user_id)

        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [user_id]

        conn = get_connection()
        with conn:
            conn.execute(f"UPDATE user_broker_config SET {set_clause} WHERE user_id = ?;", values)
        conn.close()
        return UserManager.get_user_broker_config(user_id)

    # ---------------- USER STOCKS UNIVERSE ----------------

    @staticmethod
    def get_user_stocks(user_id: int, only_enabled: bool = False) -> List[Dict[str, Any]]:
        conn = get_connection()
        cur = conn.cursor()
        query = "SELECT * FROM user_stocks WHERE user_id = ?"
        params = [user_id]
        if only_enabled:
            query += " AND enabled = 1"
        query += " ORDER BY symbol ASC;"
        cur.execute(query, params)
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def add_or_update_user_stock(user_id: int, symbol: str, allocated_capital: float,
                                 sl_mode: str = "CANDLE_LOW", enabled: bool = True, notes: str = "") -> Dict[str, Any]:
        sym = symbol.upper().strip()
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = get_connection()
        with conn:
            conn.execute("""
                INSERT INTO user_stocks (user_id, symbol, allocated_capital, sl_mode, enabled, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, symbol) DO UPDATE SET
                    allocated_capital = excluded.allocated_capital,
                    sl_mode = excluded.sl_mode,
                    enabled = excluded.enabled,
                    notes = excluded.notes;
            """, (user_id, sym, float(allocated_capital), sl_mode, 1 if enabled else 0, notes, now_str))
        conn.close()
        return {'symbol': sym, 'allocated_capital': allocated_capital, 'sl_mode': sl_mode, 'enabled': enabled}

    @staticmethod
    def toggle_user_stock_enabled(user_id: int, symbol: str) -> bool:
        sym = symbol.upper().strip()
        conn = get_connection()
        new_state = False
        try:
            with conn:
                cur = conn.cursor()
                cur.execute("SELECT enabled FROM user_stocks WHERE user_id = ? AND symbol = ?;", (user_id, sym))
                row = cur.fetchone()
                if row:
                    new_state = not bool(row['enabled'])
                    cur.execute("UPDATE user_stocks SET enabled = ? WHERE user_id = ? AND symbol = ?;", (1 if new_state else 0, user_id, sym))
        finally:
            conn.close()
        return new_state

    @staticmethod
    def remove_user_stock(user_id: int, symbol: str) -> bool:
        sym = symbol.upper().strip()
        conn = get_connection()
        deleted = False
        try:
            with conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM user_stocks WHERE user_id = ? AND symbol = ?;", (user_id, sym))
                deleted = cur.rowcount > 0
        finally:
            conn.close()
        return deleted

    # ---------------- USER ACTIVE POSITIONS ----------------

    @staticmethod
    def get_user_open_positions(user_id: int) -> List[Dict[str, Any]]:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM user_positions WHERE user_id = ? AND status = 'OPEN' ORDER BY id DESC;", (user_id,))
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def open_user_position(user_id: int, symbol: str, entry_price: float, shares: int,
                           allocated_capital: float, stop_loss_price: float,
                           sl_mode: str, entry_bar_time: str = "", order_id: str = "") -> Dict[str, Any]:
        sym = symbol.upper().strip()
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        invested = round(shares * entry_price, 2)
        conn = get_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO user_positions (user_id, symbol, status, entry_price, entry_time, entry_bar_time,
                                               shares, allocated_capital, invested_amount, stop_loss_price,
                                               sl_mode, order_id, highest_price_seen)
                    VALUES (?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, symbol, status) DO UPDATE SET
                        entry_price = excluded.entry_price,
                        shares = excluded.shares,
                        invested_amount = excluded.invested_amount,
                        stop_loss_price = excluded.stop_loss_price;
                """, (user_id, sym, entry_price, now_str, str(entry_bar_time), shares,
                      allocated_capital, invested, stop_loss_price, sl_mode, order_id, entry_price))
        finally:
            conn.close()
        return {
            'symbol': sym,
            'shares': shares,
            'entry_price': entry_price,
            'stop_loss_price': stop_loss_price,
            'status': 'OPEN'
        }

    @staticmethod
    def close_user_position(user_id: int, symbol: str, exit_price: float,
                            exit_reason: str, exit_order_id: str = "") -> Optional[Dict[str, Any]]:
        sym = symbol.upper().strip()
        conn = get_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM user_positions WHERE user_id = ? AND symbol = ? AND status = 'OPEN';", (user_id, sym))
                pos = cur.fetchone()
                if not pos:
                    return None

                entry_price = pos['entry_price']
                shares = pos['shares']
                pnl = (exit_price - entry_price) * shares
                pnl_pct = (exit_price / entry_price - 1.0) * 100.0
                now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                # Delete open position
                cur.execute("DELETE FROM user_positions WHERE id = ?;", (pos['id'],))

                # Insert trade record
                cur.execute("""
                    INSERT INTO user_trades (user_id, symbol, entry_time, entry_price, exit_time, exit_price,
                                            shares, invested_amount, pnl, pnl_pct, exit_reason, entry_order_id, exit_order_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (user_id, sym, pos['entry_time'], entry_price, now_str, exit_price,
                      shares, pos['invested_amount'], round(pnl, 2), round(pnl_pct, 2),
                      exit_reason, pos['order_id'], exit_order_id))

                # Record cumulative PnL snapshot
                cur.execute("SELECT COALESCE(SUM(pnl), 0) FROM user_trades WHERE user_id = ?;", (user_id,))
                total_realized = cur.fetchone()[0]
                cur.execute("""
                    INSERT INTO user_pnl_snapshots (user_id, timestamp, cumulative_pnl, unrealized_pnl, total_pnl)
                    VALUES (?, ?, ?, 0.0, ?);
                """, (user_id, now_str, round(total_realized, 2), round(total_realized, 2)))

                return {
                    'symbol': sym,
                    'shares': shares,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'pnl': round(pnl, 2),
                    'pnl_pct': round(pnl_pct, 2),
                    'exit_reason': exit_reason
                }
        finally:
            conn.close()

    # ---------------- TRADES & PNL SNAPSHOTS ----------------

    @staticmethod
    def get_user_trades(user_id: int) -> List[Dict[str, Any]]:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM user_trades WHERE user_id = ? ORDER BY id DESC LIMIT 100;", (user_id,))
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def get_user_pnl_history(user_id: int) -> List[Dict[str, Any]]:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT timestamp, cumulative_pnl, unrealized_pnl, total_pnl
            FROM user_pnl_snapshots
            WHERE user_id = ?
            ORDER BY id ASC;
        """, (user_id,))
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def record_pnl_snapshot(user_id: int, unrealized_pnl: float = 0.0):
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = get_connection()
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT COALESCE(SUM(pnl), 0) FROM user_trades WHERE user_id = ?;", (user_id,))
            total_realized = cur.fetchone()[0]
            total_pnl = total_realized + unrealized_pnl
            cur.execute("""
                INSERT INTO user_pnl_snapshots (user_id, timestamp, cumulative_pnl, unrealized_pnl, total_pnl)
                VALUES (?, ?, ?, ?, ?);
            """, (user_id, now_str, round(total_realized, 2), round(unrealized_pnl, 2), round(total_pnl, 2)))
        conn.close()

    # ---------------- ADMIN PERFORMANCE OVERVIEW ----------------

    @staticmethod
    def get_all_users_performance() -> List[Dict[str, Any]]:
        """Calculates performance summary for each user to show in Admin Overview."""
        users = UserManager.get_all_users()
        overview = []
        for u in users:
            uid = u['id']
            b_cfg = UserManager.get_user_broker_config(uid)
            stocks = UserManager.get_user_stocks(uid)
            open_pos = UserManager.get_user_open_positions(uid)
            trades = UserManager.get_user_trades(uid)

            total_allocated = sum(s['allocated_capital'] for s in stocks if s['enabled'])
            total_invested = sum(p['invested_amount'] for p in open_pos)
            realized_pnl = sum(t['pnl'] for t in trades)
            win_trades = [t for t in trades if t['pnl'] > 0]
            win_rate = (len(win_trades) / len(trades) * 100.0) if trades else 0.0

            today_str = datetime.now().strftime('%Y-%m-%d')
            last_time = b_cfg.get('last_login_time') or ''
            last_status = b_cfg.get('last_login_status') or 'N/A'
            has_token = bool((b_cfg.get('access_token') or '').strip())
            logged_in_today = bool(last_time.startswith(today_str) and last_status == 'SUCCESS')

            overview.append({
                'user_id': uid,
                'username': u['username'],
                'full_name': u['full_name'],
                'role': u['role'],
                'trading_mode': b_cfg.get('trading_mode', 'PAPER'),
                'auto_login': bool(b_cfg.get('auto_login_enabled')),
                'last_login_status': last_status,
                'last_login_time': last_time,
                'has_token': has_token,
                'logged_in_today': logged_in_today,
                'total_stocks': len(stocks),
                'active_stocks': len([s for s in stocks if s['enabled']]),
                'open_positions': len(open_pos),
                'total_allocated': round(total_allocated, 2),
                'total_invested': round(total_invested, 2),
                'realized_pnl': round(realized_pnl, 2),
                'total_trades': len(trades),
                'win_rate_pct': round(win_rate, 2),
                'win_rate_%': round(win_rate, 2)
            })
        return overview
