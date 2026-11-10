import sys
import os

# Ensure engine path is in sys.path
sys.path.insert(0, os.path.dirname(__file__))

import engine.silence_warnings
import argparse
import pandas as pd
from tabulate import tabulate

from engine.config_manager import ConfigManager
from engine.position_manager import PositionManager
from engine.data_fetcher import FreeDataFetcher
from engine.strategy import SuperTrendMACDStrategy
from engine.dhan_client import DhanBrokerClient
from engine.dhan_resolver import DhanResolver
from engine.dhan_auth import DhanAuthManager
from engine.market_schedule import MarketScheduleManager

def cmd_status(args):
    """Prints comprehensive system status: configured stocks, capital, active positions, and market status."""
    config_mgr = ConfigManager()
    pos_mgr = PositionManager()
    data_fetcher = FreeDataFetcher()
    resolver = DhanResolver()
    
    broker_cfg = config_mgr.broker_config
    stocks = config_mgr.stocks
    open_positions = {p['symbol']: p for p in pos_mgr.get_all_open_positions()}
    
    auth_mgr = DhanAuthManager(config_mgr)
    auth_cfg = auth_mgr.auth_config
    auto_login_str = "🟢 ENABLED (09:00 AM)" if auth_cfg.get('auto_login_enabled') else "⚪ DISABLED"
    last_login_str = f"{auth_cfg.get('last_login_time', 'Never')} ({auth_cfg.get('last_login_status', 'N/A')})"
    mkt = MarketScheduleManager.get_market_status()
    
    print("\n" + "=" * 95)
    print("📈 ALPHA TRADING SYSTEM - STATUS")
    print("=" * 95)
    print(f"Market Status:      {mkt['message']} ({mkt['status']})")
    print(f"Broker:             {broker_cfg.get('name', 'DHAN')}")
    print(f"Trading Mode:       {broker_cfg.get('trading_mode', 'PAPER')} (Change in config/trading_config.json)")
    print(f"Product Type:       {broker_cfg.get('product_type', 'CNC')}")
    print(f"Auto-Login (09:00): {auto_login_str} | Last Login: {last_login_str}")
    print(f"Total Stocks Setup: {len(stocks)}")
    print(f"Open Positions:     {len(open_positions)}")
    print("-" * 95)
    
    table_data = []
    total_allocated = 0.0
    total_invested = 0.0
    total_pnl = 0.0
    
    for sym, cfg in stocks.items():
        is_en = cfg.get('enabled', True)
        enabled = "🟢 ACTIVE" if is_en else "⚪ PAUSED"
        alloc_cap = float(cfg.get('allocated_capital', 100000.0))
        if is_en:
            total_allocated += alloc_cap
        sl_mode = cfg.get('sl_mode', 'CANDLE_LOW')
        sec_id = resolver.get_security_id(sym) or "NOT_FOUND"
        
        pos = open_positions.get(sym)
        if pos:
            status = "🟢 OPEN"
            shares = pos['shares']
            entry_p = pos['entry_price']
            sl_p = pos['stop_loss_price']
            ltp = data_fetcher.get_latest_price(sym) or entry_p
            pnl = (ltp - entry_p) * shares
            pnl_pct = (ltp / entry_p - 1.0) * 100.0
            total_invested += shares * entry_p
            total_pnl += pnl
            pnl_str = f"Rs. {pnl:+,.2f} ({pnl_pct:+.2f}%)"
            pos_details = f"{shares} @ {entry_p:.2f} (SL: {sl_p:.2f})"
        else:
            status = "⚪ FLAT"
            pos_details = "-"
            pnl_str = "-"
            
        table_data.append([
            sym,
            sec_id,
            enabled,
            f"Rs. {alloc_cap:,.0f}",
            sl_mode,
            status,
            pos_details,
            pnl_str
        ])
        
    headers = ["Stock", "Dhan SecID", "Active", "Alloc Capital", "SL Mode", "Position", "Entry/SL", "Unrealized PnL"]
    print(tabulate(table_data, headers=headers, tablefmt="github"))
    print("-" * 95)
    print(f"Total Capital Allocated: Rs. {total_allocated:,.2f} | Currently Invested: Rs. {total_invested:,.2f} | Open PnL: Rs. {total_pnl:+,.2f}\n")

def cmd_set_capital(args):
    """Sets or updates the manual capital allocation for a stock."""
    config_mgr = ConfigManager()
    sym = args.symbol.upper()
    capital = float(args.capital)
    
    if config_mgr.update_stock_capital(sym, capital):
        print(f"✅ Successfully updated capital for {sym} to Rs. {capital:,.2f}")
    else:
        print(f"⚠️ Stock {sym} not found in configuration. Adding it now...")
        config_mgr.add_or_update_stock(sym, capital)
        print(f"✅ Added {sym} with allocated capital Rs. {capital:,.2f}")

def cmd_add_stock(args):
    """Adds a new stock to be monitored and traded."""
    config_mgr = ConfigManager()
    resolver = DhanResolver()
    sym = args.symbol.upper()
    capital = float(args.capital)
    sl_mode = args.sl_mode.upper()
    
    sec_id = resolver.get_security_id(sym)
    if not sec_id:
        print(f"⚠️ Warning: Symbol '{sym}' was not found in Dhan NSE Equity scrip master. Please verify symbol name.")
    else:
        print(f"🔍 Resolved '{sym}' to Dhan Security ID: {sec_id}")
        
    config_mgr.add_or_update_stock(sym, capital, sl_mode=sl_mode, notes=args.notes)
    print(f"✅ Successfully added {sym} with Rs. {capital:,.2f} capital and {sl_mode} stop-loss!")

def cmd_remove_stock(args):
    """Removes a stock from trading configuration."""
    config_mgr = ConfigManager()
    sym = args.symbol.upper()
    if config_mgr.remove_stock(sym):
        print(f"✅ Removed {sym} from trading configuration.")
    else:
        print(f"⚠️ Stock {sym} was not in configuration.")

def cmd_toggle_stock(args):
    """Toggles trading enabled/disabled for a specific stock without removing it."""
    config_mgr = ConfigManager()
    sym = args.symbol.upper()
    if sym not in config_mgr.stocks:
        print(f"⚠️ Stock {sym} is not in configuration.")
        return
    new_state = config_mgr.toggle_stock_enabled(sym)
    status_str = "🟢 ACTIVE (Trading Enabled)" if new_state else "⚪ PAUSED (Trading Disabled)"
    print(f"Stock {sym} is now: {status_str}")

def cmd_scan(args):
    """Performs an immediate real-time strategy scan across all configured stocks."""
    mkt = MarketScheduleManager.get_market_status()
    if not mkt['is_trading_day'] and not getattr(args, 'force', False):
        print("\n" + "=" * 80)
        print(f"⚠️ MARKET CLOSED: {mkt['message']}")
        print("Signal scanning is skipped during weekends and market holidays.")
        print("Tip: Run 'python manage.py scan --force' if you wish to run an off-market test scan.")
        print("=" * 80 + "\n")
        return

    config_mgr = ConfigManager()
    data_fetcher = FreeDataFetcher()
    strat_cfg = config_mgr.strategy_config
    strategy = SuperTrendMACDStrategy(
        st_period=strat_cfg.get('supertrend_period', 8),
        st_multiplier=strat_cfg.get('supertrend_multiplier', 3.2),
        macd_fast=strat_cfg.get('macd_fast', 9),
        macd_slow=strat_cfg.get('macd_slow', 23),
        macd_signal=strat_cfg.get('macd_signal', 7)
    )
    
    active_stocks = config_mgr.get_active_stocks()
    print("\n" + "=" * 95)
    print("🔍 SCANNING REAL-TIME ALPHA SIGNALS")
    print("=" * 95)
    
    scan_rows = []
    for sym, cfg in active_stocks.items():
        sl_mode = cfg.get('sl_mode', 'CANDLE_LOW')
        df_1h = data_fetcher.fetch_1h_data(sym, period='15d')
        if df_1h is None or len(df_1h) < 10:
            scan_rows.append([sym, "ERR", "-", "-", "-", "-", "Could not fetch data"])
            continue
            
        res = strategy.evaluate_signals(df_1h, sl_mode=sl_mode)
        st_badge = "🟢 BULLISH" if res['is_st_bullish'] else "🔴 BEARISH"
        macd_badge = "🟢 BULL" if res['is_macd_bullish'] else "🔴 BEAR"
        
        signal_badge = res['action']
        if signal_badge == 'BUY':
            signal_str = "🟢 BUY SIGNAL"
        elif signal_badge == 'SELL':
            signal_str = "🔴 SELL SIGNAL"
        else:
            signal_str = "⚪ HOLD / FLAT"
            
        scan_rows.append([
            sym,
            f"Rs. {res['current_price']:.2f}",
            st_badge,
            f"{res['macd']:.2f}",
            f"{res['signal']:.2f}",
            macd_badge,
            signal_str,
            res['reason']
        ])
        
    headers = ["Stock", "LTP", "Trend", "Momentum", "Signal Baseline", "Momentum Trend", "Strategy Action", "Details"]
    print(tabulate(scan_rows, headers=headers, tablefmt="github"))
    print("=" * 95 + "\n")

def cmd_holidays(args):
    """Displays all configured market holidays and today's market status."""
    mkt = MarketScheduleManager.get_market_status()
    holidays = MarketScheduleManager.get_all_holidays()
    print("\n" + "=" * 70)
    print("📅 NSE / BSE MARKET HOLIDAYS CALENDAR")
    print("=" * 70)
    print(f"Today's Market Status: {mkt['message']} ({mkt['status']})")
    print("-" * 70)
    rows = []
    for h in holidays:
        is_today = "⭐ TODAY" if h['holiday_date'] == mkt['current_date'] else ""
        rows.append([h['id'], h['holiday_date'], h['name'], is_today])
    print(tabulate(rows, headers=["ID", "Date", "Holiday Name", "Status"], tablefmt="github"))
    print("=" * 70 + "\n")

def cmd_add_holiday(args):
    """Adds a market holiday (YYYY-MM-DD and name)."""
    try:
        res = MarketScheduleManager.add_holiday(args.date, args.name)
        print(f"✅ Successfully added market holiday: {res['holiday_date']} - {res['name']}")
    except Exception as e:
        print(f"❌ Error adding holiday: {e}")

def cmd_delete_holiday(args):
    """Deletes a market holiday by ID."""
    if MarketScheduleManager.delete_holiday(args.id):
        print(f"✅ Successfully removed holiday with ID {args.id}.")
    else:
        print(f"⚠️ Holiday ID {args.id} not found.")

def cmd_test_dhan(args):
    """Verifies Dhan credentials and displays account funds."""
    config_mgr = ConfigManager()
    b_cfg = config_mgr.broker_config
    client = DhanBrokerClient(
        client_id=b_cfg.get('client_id', ''),
        access_token=b_cfg.get('access_token', ''),
        mode=b_cfg.get('trading_mode', 'PAPER')
    )
    print("\nTesting Dhan connection...")
    resp = client.test_connection()
    print("Response:", resp)

def cmd_close_position(args):
    """Manually closes an open position in the system."""
    pos_mgr = PositionManager()
    data_fetcher = FreeDataFetcher()
    sym = args.symbol.upper()
    if not pos_mgr.is_in_position(sym):
        print(f"⚠️ No active open position found for {sym}.")
        return
    ltp = data_fetcher.get_latest_price(sym) or pos_mgr.get_position(sym)['entry_price']
    trade = pos_mgr.close_position(sym, exit_price=ltp, exit_reason="MANUAL_CLOSE")
    print(f"✅ Closed {sym} position @ Rs. {ltp:.2f}. Realized PnL: Rs. {trade['PnL']:+,.2f} ({trade['PnL_%']:+.2f}%)")

def cmd_reset_paper(args):
    """Clears all open paper positions."""
    pos_mgr = PositionManager()
    pos_mgr.positions = {}
    pos_mgr._save_positions()
    print("✅ Reset all paper trading positions. System is now 100% FLAT.")

def cmd_dhan_login(args):
    """Executes immediate automated login using PIN + TOTP."""
    auth_mgr = DhanAuthManager()
    print("Initiating Dhan automated login...")
    res = auth_mgr.execute_login()
    print("Result:", res)

def cmd_setup_auth(args):
    """Configures Dhan auto-login credentials."""
    auth_mgr = DhanAuthManager()
    updates = {}
    if args.client_id:
        updates['client_id'] = args.client_id
    if args.pin:
        updates['pin'] = args.pin
    if args.totp_secret:
        updates['totp_secret'] = args.totp_secret
    if args.api_key:
        updates['api_key'] = args.api_key
    if args.api_secret:
        updates['api_secret'] = args.api_secret
    if args.enable is not None:
        updates['auto_login_enabled'] = args.enable
    if args.time:
        updates['auto_login_time'] = args.time

    updated = auth_mgr.update_auth_config(**updates)
    print("✅ Auto-Login configuration updated successfully!")
    print(f"Auto-Login Enabled: {updated.get('auto_login_enabled')} | Scheduled Time: {updated.get('auto_login_time')}")
    print(f"Client ID: {updated.get('client_id')} | PIN set: {'YES' if updated.get('pin') else 'NO'} | TOTP Secret set: {'YES' if updated.get('totp_secret') else 'NO'}")

from engine.user_manager import UserManager

def cmd_list_users(args):
    """Lists all registered users, roles, and real-time performance summary."""
    overview = UserManager.get_all_users_performance()
    print("\n" + "=" * 105)
    print("👥 ALPHA TRADING SYSTEM - MULTI-USER OVERVIEW")
    print("=" * 105)
    table_data = []
    for u in overview:
        auto_str = "🟢 ON" if u['auto_login'] else "⚪ OFF"
        table_data.append([
            u['user_id'],
            u['username'],
            u['full_name'],
            u['role'].upper(),
            u['trading_mode'],
            auto_str,
            u['total_stocks'],
            u['open_positions'],
            f"Rs. {u['total_allocated']:,.0f}",
            f"Rs. {u['realized_pnl']:+,.2f}",
            f"{u['win_rate_%']:.1f}% ({u['total_trades']})"
        ])
    headers = ["ID", "Username", "Full Name", "Role", "Mode", "Auto-Login", "Stocks", "Open Pos", "Alloc Capital", "Realized PnL", "Win Rate"]
    print(tabulate(table_data, headers=headers, tablefmt="github"))
    print("=" * 105 + "\n")

def cmd_create_user(args):
    """Creates a new user profile with independent broker credentials and stock list."""
    try:
        user = UserManager.create_user(
            username=args.username,
            password=args.password,
            full_name=args.name,
            role=args.role
        )
        print(f"✅ Successfully created user '{user['username']}' (ID: {user['id']}, Role: {user['role'].upper()})!")
        print(f"They can now log into the Web UI or configure independent Dhan credentials & stocks.")
    except Exception as e:
        print(f"❌ Error creating user: {e}")

def cmd_change_password(args):
    """Changes password for any registered user."""
    u = UserManager.get_user_by_username(args.username)
    if not u:
        print(f"❌ User '{args.username}' not found.")
        return
    try:
        UserManager.change_password(u['id'], args.password, clear_sessions=True)
        print(f"✅ Password successfully changed for user '{u['username']}'.")
    except Exception as e:
        print(f"❌ Error changing password: {e}")

def cmd_delete_user(args):
    """Deletes a user account and all associated stocks, positions, and trades."""
    u = UserManager.get_user_by_username(args.username)
    if not u:
        print(f"❌ User '{args.username}' not found.")
        return
    try:
        deleted = UserManager.delete_user(u['id'])
        if deleted:
            print(f"✅ User '{u['username']}' has been permanently deleted.")
        else:
            print(f"⚠️ Failed to delete user '{args.username}'.")
    except Exception as e:
        print(f"❌ Error deleting user: {e}")

def cmd_toggle_auto_login(args):
    """Toggles auto-login between Enabled and Disabled."""
    auth_mgr = DhanAuthManager()
    curr = auth_mgr.auth_config.get('auto_login_enabled', False)
    new_state = not curr
    auth_mgr.update_auth_config(auto_login_enabled=new_state)
    status_str = "🟢 ENABLED (09:00 AM daily)" if new_state else "⚪ DISABLED"
    print(f"Dhan Auto-Login is now: {status_str}")

def cmd_web(args):
    """Starts the interactive Web UI dashboard."""
    from web.server import start_web_server
    start_web_server(host=args.host, port=args.port)

def main():
    parser = argparse.ArgumentParser(description="Alpha Trading System Manager")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # status
    p_status = subparsers.add_parser("status", help="Show system status, allocated funds, and open positions")
    p_status.set_defaults(func=cmd_status)
    
    # set-capital
    p_cap = subparsers.add_parser("set-capital", help="Update allocated capital for a stock")
    p_cap.add_argument("symbol", help="Stock symbol (e.g. BSE, MCX)")
    p_cap.add_argument("capital", type=float, help="New allocated fund in INR (e.g. 150000)")
    p_cap.set_defaults(func=cmd_set_capital)
    
    # add-stock
    p_add = subparsers.add_parser("add-stock", help="Add a new stock with custom capital")
    p_add.add_argument("symbol", help="Stock symbol (e.g. TRENT, HAL)")
    p_add.add_argument("capital", type=float, help="Allocated fund in INR")
    p_add.add_argument("--sl-mode", default="CANDLE_LOW", choices=["CANDLE_LOW", "1_PCT"], help="Stop loss mode")
    p_add.add_argument("--notes", default="", help="Optional notes")
    p_add.set_defaults(func=cmd_add_stock)
    
    # remove-stock
    p_rem = subparsers.add_parser("remove-stock", help="Remove a stock from trading")
    p_rem.add_argument("symbol", help="Stock symbol to remove")
    p_rem.set_defaults(func=cmd_remove_stock)

    # toggle-stock
    p_tog_stk = subparsers.add_parser("toggle-stock", help="Enable or disable trading for a stock without removing it")
    p_tog_stk.add_argument("symbol", help="Stock symbol to enable/disable")
    p_tog_stk.set_defaults(func=cmd_toggle_stock)
    
    # scan
    p_scan = subparsers.add_parser("scan", help="Scan real-time signals across all stocks")
    p_scan.add_argument("--force", action="store_true", help="Force scan even on weekends or market holidays")
    p_scan.set_defaults(func=cmd_scan)
    
    # test-dhan
    p_dhan = subparsers.add_parser("test-dhan", help="Test Dhan API connection")
    p_dhan.set_defaults(func=cmd_test_dhan)

    # close-position
    p_close = subparsers.add_parser("close-position", help="Manually close an open position")
    p_close.add_argument("symbol", help="Stock symbol to close")
    p_close.set_defaults(func=cmd_close_position)

    # reset-paper
    p_reset = subparsers.add_parser("reset-paper", help="Reset all open paper positions to FLAT")
    p_reset.set_defaults(func=cmd_reset_paper)

    # dhan-login
    p_login = subparsers.add_parser("dhan-login", help="Execute immediate login using PIN + TOTP")
    p_login.set_defaults(func=cmd_dhan_login)

    # setup-auth
    p_sauth = subparsers.add_parser("setup-auth", help="Configure auto-login credentials (PIN, TOTP Secret, API Key)")
    p_sauth.add_argument("--client-id", help="Dhan Client ID")
    p_sauth.add_argument("--pin", help="Dhan 6-digit PIN")
    p_sauth.add_argument("--totp-secret", help="Google Authenticator TOTP Secret key")
    p_sauth.add_argument("--api-key", help="Dhan API Key / App ID")
    p_sauth.add_argument("--api-secret", help="Dhan API Secret / App Secret")
    p_sauth.add_argument("--enable", action="store_true", default=None, help="Enable auto-login")
    p_sauth.add_argument("--disable", dest="enable", action="store_false", help="Disable auto-login")
    p_sauth.add_argument("--time", default="09:00", help="Auto-login time in IST (default: 09:00)")
    p_sauth.set_defaults(func=cmd_setup_auth)

    # toggle-auto-login
    p_tog = subparsers.add_parser("toggle-auto-login", help="Toggle daily 09:00 AM auto-login ON/OFF")
    p_tog.set_defaults(func=cmd_toggle_auto_login)

    # list-users
    p_lusers = subparsers.add_parser("list-users", help="List all users and performance overview")
    p_lusers.set_defaults(func=cmd_list_users)

    # create-user
    p_cuser = subparsers.add_parser("create-user", help="Create a new trading user account")
    p_cuser.add_argument("username", help="Unique username")
    p_cuser.add_argument("password", help="Password (min 6 chars)")
    p_cuser.add_argument("--name", default="", help="Full Name")
    p_cuser.add_argument("--role", default="user", choices=["user", "admin"], help="Role (user or admin)")
    p_cuser.set_defaults(func=cmd_create_user)

    # change-password
    p_cpwd = subparsers.add_parser("change-password", help="Change password for a user account")
    p_cpwd.add_argument("username", help="Username of account")
    p_cpwd.add_argument("password", help="New password (min 6 chars)")
    p_cpwd.set_defaults(func=cmd_change_password)

    # delete-user
    p_duser = subparsers.add_parser("delete-user", help="Delete a user account and its data")
    p_duser.add_argument("username", help="Username to delete")
    p_duser.set_defaults(func=cmd_delete_user)

    # holidays
    p_hols = subparsers.add_parser("holidays", help="List all market holidays and current market status")
    p_hols.set_defaults(func=cmd_holidays)

    # add-holiday
    p_ahol = subparsers.add_parser("add-holiday", help="Add a market holiday (YYYY-MM-DD and Name)")
    p_ahol.add_argument("date", help="Holiday date in YYYY-MM-DD format (e.g. 2026-11-10)")
    p_ahol.add_argument("name", help="Holiday description (e.g. 'Diwali Holiday')")
    p_ahol.set_defaults(func=cmd_add_holiday)

    # delete-holiday
    p_dhol = subparsers.add_parser("delete-holiday", help="Remove a market holiday by ID")
    p_dhol.add_argument("id", type=int, help="Holiday ID to delete")
    p_dhol.set_defaults(func=cmd_delete_holiday)

    # web
    p_web = subparsers.add_parser("web", help="Launch interactive browser Web UI")
    p_web.add_argument("--host", default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    p_web.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    p_web.set_defaults(func=cmd_web)
    
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)
        
    args = parser.parse_args()
    if hasattr(args, 'func'):
        args.func(args)

if __name__ == "__main__":
    main()
