import os
import sys

# Ensure trading_system directory is on sys.path
sys.path.insert(0, os.path.dirname(__file__))

import engine.silence_warnings
import time
import argparse
from datetime import datetime, timedelta

from engine.executor import TradingExecutor
from engine.config_manager import ConfigManager
from engine.position_manager import PositionManager
from engine.dhan_auth import DhanAuthManager
from engine.user_manager import UserManager
from engine.market_schedule import MarketScheduleManager

LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'trading_bot.log')

def log_message(msg: str):
    """Logs message with timestamp to both console and file."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    full_msg = f"[{timestamp}] {msg}"
    print(full_msg)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(full_msg + '\n')
    except Exception:
        pass

def is_hourly_bar_close(now: datetime, window_seconds: int = 120) -> bool:
    """
    NSE hourly bars complete at:
    10:15, 11:15, 12:15, 13:15, 14:15, 15:15 IST
    Checks if current time is within window_seconds after bar completion.
    """
    bar_close_hours = [10, 11, 12, 13, 14, 15]
    for h in bar_close_hours:
        close_time = now.replace(hour=h, minute=15, second=0, microsecond=0)
        diff = (now - close_time).total_seconds()
        if 0 <= diff <= window_seconds:
            return True
    return False

def run_single_evaluation(executor: TradingExecutor):
    """Executes a single evaluation pass for all users and returns the results."""
    log_message("Starting Multi-User Strategy Evaluation Cycle...")
    results = executor.run_cycle_all_users()
    log_message(f"Evaluation Cycle Completed. Evaluated {results.get('users_evaluated', 0)} users.")
    return results

def main():
    parser = argparse.ArgumentParser(description="Multi-User Alpha Trading Bot for Dhan")
    parser.add_argument("--once", action="store_true", help="Run one evaluation cycle immediately and exit")
    parser.add_argument("--force", action="store_true", help="Force execution even outside normal market hours, weekends, or holidays")
    parser.add_argument("--interval", type=int, default=60, help="Loop interval in seconds (default: 60s)")
    args = parser.parse_args()

    executor = TradingExecutor()
    auth_mgr = DhanAuthManager()
    
    users = UserManager.get_all_users()
    active_users = [u for u in users if u['is_active']]

    log_message("=" * 80)
    log_message("🚀 ALPHA TRADING SYSTEM INITIALIZED (MULTI-USER ENGINE)")
    log_message(f"Total Users Registered: {len(users)} | Active Users: {len(active_users)}")
    for u in active_users:
        b_cfg = UserManager.get_user_broker_config(u['id'])
        stocks = UserManager.get_user_stocks(u['id'], only_enabled=True)
        log_message(f"  • User: {u['username']} | Mode: {b_cfg.get('trading_mode', 'PAPER')} | Stocks: {len(stocks)} active | Auto-Login: {'ON' if b_cfg.get('auto_login_enabled') else 'OFF'}")
    
    mkt_now = MarketScheduleManager.get_market_status()
    log_message(f"Current Market Status: {mkt_now['message']} ({mkt_now['status']})")
    log_message("=" * 80)

    # 1. Single-run mode
    if args.once:
        if not mkt_now['is_trading_day'] and not args.force:
            log_message(f"⚠️ Cannot run evaluation: {mkt_now['message']}. Use --force to override.")
            return
        run_single_evaluation(executor)
        return

    # 2. Continuous Daemon Mode
    last_candle_checked = None
    last_holiday_log_date = None
    log_message("Entering continuous daemon loop. Monitoring trading schedule, 09:00 AM auto-login, bar closes, and stop-losses...")

    while True:
        try:
            now = datetime.now()
            today_str = now.strftime('%Y-%m-%d')
            mkt_status = MarketScheduleManager.get_market_status(now)

            # Weekend and Holiday Check
            if not mkt_status['is_trading_day'] and not args.force:
                if last_holiday_log_date != today_str:
                    log_message(f"🏖️ Market is CLOSED today: {mkt_status['message']}. Skipping 09:00 AM auto-logins and all strategy scans.")
                    last_holiday_log_date = today_str
                # Sleep for 5 minutes during weekends/holidays to save resources
                time.sleep(300)
                continue

            # It's an active trading day! Check Auto-Login at 09:00 AM for all users
            auth_mgr.check_and_trigger_all_auto_logins(now)

            # Check if within trading hours (09:15 - 15:30 IST)
            if not mkt_status['is_open'] and not args.force:
                time.sleep(60)
                continue

            # Check if an hourly bar closed
            if is_hourly_bar_close(now) or args.force:
                current_candle_id = f"{now.strftime('%Y%m%d_%H')}_15"
                if last_candle_checked != current_candle_id:
                    log_message(f"Bar Close Detected ({now.strftime('%H:%M:%S')}). Executing strategy rules for all users...")
                    run_single_evaluation(executor)
                    last_candle_checked = current_candle_id
            
            # Intraday Stop Loss Monitoring (Checks open positions against LTP every minute across all users)
            current_users = UserManager.get_all_users()
            for u in current_users:
                if not u['is_active']:
                    continue
                uid = u['id']
                open_pos = UserManager.get_user_open_positions(uid)
                if not open_pos:
                    continue
                
                broker = executor.get_user_broker_client(uid)
                for p in open_pos:
                    sym = p['symbol']
                    sl = float(p['stop_loss_price'])
                    ltp = executor.data_fetcher.get_latest_price(sym)
                    if ltp and ltp <= sl:
                        log_message(f"🚨 INTRADAY STOP LOSS HIT for User {u['username']} on {sym}! LTP: Rs. {ltp:.2f} <= SL: Rs. {sl:.2f}")
                        shares = int(p['shares'])
                        order_resp = broker.place_order(sym, 'SELL', shares, price=ltp)
                        if order_resp.get('status') == 'SUCCESS':
                            trade = UserManager.close_user_position(
                                user_id=uid,
                                symbol=sym,
                                exit_price=ltp,
                                exit_reason=f"INTRADAY_SL_HIT_{p.get('sl_mode')}",
                                exit_order_id=order_resp.get('order_id', '')
                            )
                            if trade:
                                log_message(f"Closed {sym} position for User {u['username']}. PnL: Rs. {trade['pnl']:.2f} ({trade['pnl_pct']}%)")

            time.sleep(args.interval)

        except KeyboardInterrupt:
            log_message("Bot stopped by user (Ctrl+C). Exiting safely.")
            break
        except Exception as e:
            log_message(f"Unexpected error in daemon loop: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
