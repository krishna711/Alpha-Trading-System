import os
import sqlite3
from datetime import datetime, time
from typing import List, Dict, Any, Optional, Tuple
from .db import get_connection

DEFAULT_HOLIDAYS_2026 = [
    ("2026-01-26", "Republic Day"),
    ("2026-02-17", "Mahashivratri"),
    ("2026-03-04", "Holi"),
    ("2026-03-20", "Id-Ul-Fitr (Ramzan Id)"),
    ("2026-03-27", "Ram Navami"),
    ("2026-04-03", "Good Friday"),
    ("2026-04-14", "Dr. B.R. Ambedkar Jayanti"),
    ("2026-05-01", "Maharashtra Day"),
    ("2026-05-27", "Bakri Id / Eid ul-Adha"),
    ("2026-06-26", "Muharram"),
    ("2026-08-15", "Independence Day"),
    ("2026-10-02", "Mahatma Gandhi Jayanti"),
    ("2026-10-20", "Dussehra"),
    ("2026-11-10", "Market Holiday"),
    ("2026-11-11", "Diwali Balipratipada"),
    ("2026-11-24", "Guru Nanak Jayanti"),
    ("2026-12-25", "Christmas"),
]

def init_market_holidays():
    """Initializes the market_holidays table and seeds default 2026 holidays."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS market_holidays (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    holiday_date TEXT UNIQUE NOT NULL, -- Format: YYYY-MM-DD
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM market_holidays;")
            if cur.fetchone()[0] == 0:
                now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                for dt_str, name in DEFAULT_HOLIDAYS_2026:
                    cur.execute("""
                        INSERT OR IGNORE INTO market_holidays (holiday_date, name, created_at)
                        VALUES (?, ?, ?);
                    """, (dt_str, name, now_str))
    finally:
        conn.close()

class MarketScheduleManager:
    """Manages Market Holidays and Market Hours (Mon-Fri 09:15 to 15:30 IST)."""

    @staticmethod
    def get_all_holidays() -> List[Dict[str, Any]]:
        init_market_holidays()
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, holiday_date, name, created_at FROM market_holidays ORDER BY holiday_date ASC;")
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def add_holiday(holiday_date: str, name: str) -> Dict[str, Any]:
        """Adds or updates a market holiday. holiday_date must be 'YYYY-MM-DD'."""
        init_market_holidays()
        date_clean = holiday_date.strip()
        name_clean = name.strip()
        # Validate date format
        try:
            datetime.strptime(date_clean, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Invalid date format. Expected YYYY-MM-DD (e.g. 2026-11-10).")

        if not name_clean:
            raise ValueError("Holiday name is required.")

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = get_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO market_holidays (holiday_date, name, created_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(holiday_date) DO UPDATE SET
                        name = excluded.name;
                """, (date_clean, name_clean, now_str))
                h_id = cur.lastrowid
                return {'id': h_id, 'holiday_date': date_clean, 'name': name_clean}
        finally:
            conn.close()

    @staticmethod
    def delete_holiday(holiday_id: int) -> bool:
        """Deletes a holiday by its ID."""
        conn = get_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM market_holidays WHERE id = ?;", (holiday_id,))
                return cur.rowcount > 0
        finally:
            conn.close()

    @staticmethod
    def is_weekend(dt: Optional[datetime] = None) -> bool:
        """Returns True if given datetime (default now) is Saturday or Sunday."""
        if dt is None:
            dt = datetime.now()
        return dt.weekday() in (5, 6) # 5 = Saturday, 6 = Sunday

    @staticmethod
    def is_holiday(dt: Optional[datetime] = None) -> Tuple[bool, Optional[str]]:
        """Returns (True, holiday_name) if dt is an official holiday, else (False, None)."""
        init_market_holidays()
        if dt is None:
            dt = datetime.now()
        date_str = dt.strftime('%Y-%m-%d')
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM market_holidays WHERE holiday_date = ?;", (date_str,))
            row = cur.fetchone()
            if row:
                return True, row['name']
            return False, None
        finally:
            conn.close()

    @staticmethod
    def is_trading_day(dt: Optional[datetime] = None) -> Tuple[bool, str]:
        """
        Returns (True, 'Market Day') if today is a weekday and not a holiday.
        Otherwise returns (False, Reason).
        """
        if dt is None:
            dt = datetime.now()

        if MarketScheduleManager.is_weekend(dt):
            day_name = dt.strftime('%A')
            return False, f"Weekend ({day_name})"

        is_hol, hol_name = MarketScheduleManager.is_holiday(dt)
        if is_hol:
            return False, f"Market Holiday: {hol_name}"

        return True, "Trading Day"

    @staticmethod
    def get_market_status(dt: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Comprehensive market status check.
        Returns:
        - is_open: True if currently inside 09:15 - 15:30 on a trading day.
        - is_trading_day: True if Mon-Fri and not a holiday.
        - status: 'OPEN', 'HOLIDAY', 'WEEKEND', 'CLOSED_HOURS'
        - message: User-friendly string
        - holiday_name: name if today is a holiday
        """
        if dt is None:
            dt = datetime.now()

        # Check weekend
        if MarketScheduleManager.is_weekend(dt):
            day_name = dt.strftime('%A')
            return {
                'is_open': False,
                'is_trading_day': False,
                'status': 'WEEKEND',
                'message': f"Market Closed: Weekend ({day_name})",
                'holiday_name': None,
                'current_time': dt.strftime('%H:%M:%S'),
                'current_date': dt.strftime('%Y-%m-%d')
            }

        # Check holiday
        is_hol, hol_name = MarketScheduleManager.is_holiday(dt)
        if is_hol:
            return {
                'is_open': False,
                'is_trading_day': False,
                'status': 'HOLIDAY',
                'message': f"Market Closed: Holiday ({hol_name})",
                'holiday_name': hol_name,
                'current_time': dt.strftime('%H:%M:%S'),
                'current_date': dt.strftime('%Y-%m-%d')
            }

        # Trading day: check market hours (09:15 to 15:30 IST)
        market_open_time = time(9, 15)
        market_close_time = time(15, 30)
        curr_time = dt.time()

        if market_open_time <= curr_time <= market_close_time:
            return {
                'is_open': True,
                'is_trading_day': True,
                'status': 'OPEN',
                'message': "Market Open (09:15 - 15:30 IST)",
                'holiday_name': None,
                'current_time': dt.strftime('%H:%M:%S'),
                'current_date': dt.strftime('%Y-%m-%d')
            }
        elif curr_time < market_open_time:
            return {
                'is_open': False,
                'is_trading_day': True,
                'status': 'PRE_MARKET',
                'message': "Market Closed (Opens at 09:15 AM IST)",
                'holiday_name': None,
                'current_time': dt.strftime('%H:%M:%S'),
                'current_date': dt.strftime('%Y-%m-%d')
            }
        else:
            return {
                'is_open': False,
                'is_trading_day': True,
                'status': 'POST_MARKET',
                'message': "Market Closed (Closed at 03:30 PM IST)",
                'holiday_name': None,
                'current_time': dt.strftime('%H:%M:%S'),
                'current_date': dt.strftime('%Y-%m-%d')
            }

# Auto-initialize table on module import
init_market_holidays()

