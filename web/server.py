import os
import sys
import json
import uvicorn
import pandas as pd
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException, Response, Cookie, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.silence_warnings
from engine.config_manager import ConfigManager
from engine.position_manager import PositionManager
from engine.data_fetcher import FreeDataFetcher
from engine.strategy import SuperTrendMACDStrategy
from engine.dhan_resolver import DhanResolver
from engine.dhan_auth import DhanAuthManager
from engine.dhan_client import DhanBrokerClient
from engine.user_manager import UserManager
from engine.market_schedule import MarketScheduleManager

app = FastAPI(title="Alpha Trading System - Multi-User")

config_mgr = ConfigManager()
data_fetcher = FreeDataFetcher()
resolver = DhanResolver()
auth_mgr = DhanAuthManager()

# ----------------- PYDANTIC REQUEST MODELS -----------------

class LoginRequest(BaseModel):
    username: str
    password: str

class AddHolidayRequest(BaseModel):
    holiday_date: str
    name: str

class CreateUserRequest(BaseModel):
    username: str
    password: str
    full_name: str
    role: Optional[str] = "user"

class ChangeOwnPasswordRequest(BaseModel):
    current_password: str
    new_password: str

class AdminChangePasswordRequest(BaseModel):
    new_password: str

class CapitalUpdateRequest(BaseModel):
    symbol: str
    capital: float
    user_id: Optional[int] = None

class AddStockRequest(BaseModel):
    symbol: str
    capital: float
    sl_mode: Optional[str] = "CANDLE_LOW"
    notes: Optional[str] = ""
    user_id: Optional[int] = None

class ToggleStockRequest(BaseModel):
    symbol: str
    user_id: Optional[int] = None

class RemoveStockRequest(BaseModel):
    symbol: str
    user_id: Optional[int] = None

class ClosePositionRequest(BaseModel):
    symbol: str
    user_id: Optional[int] = None

class DhanAuthSettingsRequest(BaseModel):
    client_id: Optional[str] = None
    pin: Optional[str] = None
    totp_secret: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    auto_login_enabled: Optional[bool] = None
    auto_login_time: Optional[str] = "09:00"
    trading_mode: Optional[str] = None
    user_id: Optional[int] = None

# ----------------- AUTH HELPER DEPENDENCY -----------------

def get_current_user(request: Request) -> Dict[str, Any]:
    """Extracts session token from cookie or Authorization header and returns user dict."""
    token = request.cookies.get("alpha_session")
    if not token:
        auth_hdr = request.headers.get("Authorization")
        if auth_hdr and auth_hdr.startswith("Bearer "):
            token = auth_hdr.split(" ")[1]

    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")

    user = UserManager.get_user_by_session(token)
    if not user:
        raise HTTPException(status_code=401, detail="Session expired or invalid. Please log in.")
    return user

def resolve_target_user_id(current_user: Dict[str, Any], requested_user_id: Optional[int] = None) -> int:
    """If current_user is admin and requested_user_id is provided, returns requested_user_id; otherwise returns current_user['id']."""
    if current_user.get('role') == 'admin' and requested_user_id:
        return requested_user_id
    return current_user['id']

# ----------------- AUTHENTICATION API -----------------

@app.post("/api/auth/login")
def api_login(req: LoginRequest, response: Response):
    user = UserManager.authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=400, detail="Invalid username or password.")
    token = UserManager.create_session(user['id'])
    response.set_cookie(key="alpha_session", value=token, httponly=True, max_age=86400 * 7, samesite="lax")
    return {
        'status': 'SUCCESS',
        'token': token,
        'user': user
    }

@app.post("/api/auth/logout")
def api_logout(request: Request, response: Response):
    token = request.cookies.get("alpha_session")
    if token:
        UserManager.delete_session(token)
    response.delete_cookie(key="alpha_session")
    return {'status': 'SUCCESS', 'message': 'Logged out.'}

@app.get("/api/auth/me")
def api_me(current_user: Dict[str, Any] = Depends(get_current_user)):
    return {'user': current_user}

@app.post("/api/auth/change-password")
def api_change_own_password(req: ChangeOwnPasswordRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    try:
        UserManager.verify_and_change_password(
            user_id=current_user['id'],
            current_password=req.current_password,
            new_password=req.new_password
        )
        return {'status': 'SUCCESS', 'message': 'Password updated successfully.'}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ----------------- ADMIN API -----------------

@app.get("/api/admin/users")
def api_admin_get_users(current_user: Dict[str, Any] = Depends(get_current_user)):
    if current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin privileges required.")
    users_perf = UserManager.get_all_users_performance()
    return {'users': users_perf}

@app.post("/api/admin/users/create")
def api_admin_create_user(req: CreateUserRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    if current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin privileges required.")
    try:
        new_u = UserManager.create_user(
            username=req.username,
            password=req.password,
            full_name=req.full_name,
            role=req.role or "user"
        )
        return {'status': 'SUCCESS', 'user': new_u}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/admin/users/{user_id}/password")
def api_admin_change_user_password(user_id: int, req: AdminChangePasswordRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    if current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin privileges required.")
    try:
        updated = UserManager.change_password(
            user_id=user_id,
            new_password=req.new_password,
            clear_sessions=(user_id != current_user['id'])
        )
        if not updated:
            raise HTTPException(status_code=404, detail="User not found.")
        return {'status': 'SUCCESS', 'message': 'User password updated successfully.'}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/admin/users/{user_id}")
def api_admin_delete_user(user_id: int, current_user: Dict[str, Any] = Depends(get_current_user)):
    if current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin privileges required.")
    if user_id == current_user['id']:
        raise HTTPException(status_code=400, detail="You cannot delete your own active account.")
    try:
        deleted = UserManager.delete_user(user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="User not found.")
        return {'status': 'SUCCESS', 'message': 'User deleted successfully.'}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ----------------- USER DASHBOARD API -----------------

@app.get("/api/dashboard")
def get_user_dashboard(user_id: Optional[int] = None, current_user: Dict[str, Any] = Depends(get_current_user)):
    target_uid = resolve_target_user_id(current_user, user_id)
    target_user = UserManager.get_user_by_id(target_uid)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found.")

    b_cfg = UserManager.get_user_broker_config(target_uid)
    stocks = UserManager.get_user_stocks(target_uid)
    open_pos = UserManager.get_user_open_positions(target_uid)
    trades = UserManager.get_user_trades(target_uid)
    pnl_history = UserManager.get_user_pnl_history(target_uid)

    total_allocated = sum(s['allocated_capital'] for s in stocks if s['enabled'])
    total_invested = sum(p['invested_amount'] for p in open_pos)
    total_realized_pnl = sum(t['pnl'] for t in trades)
    
    # Calculate real-time unrealized PnL
    enhanced_positions = []
    total_unrealized_pnl = 0.0

    for p in open_pos:
        sym = p['symbol']
        ltp = data_fetcher.get_latest_price(sym) or p['entry_price']
        shares = p['shares']
        entry_price = p['entry_price']
        pnl = (ltp - entry_price) * shares
        pnl_pct = (ltp / entry_price - 1.0) * 100.0
        invested = p['invested_amount']

        total_unrealized_pnl += pnl

        enhanced_positions.append({
            'symbol': sym,
            'shares': shares,
            'entry_price': round(entry_price, 2),
            'ltp': round(ltp, 2),
            'invested': round(invested, 2),
            'pnl': round(pnl, 2),
            'pnl_pct': round(pnl_pct, 2),
            'stop_loss_price': round(p['stop_loss_price'], 2),
            'sl_mode': p.get('sl_mode', 'CANDLE_LOW'),
            'entry_time': p.get('entry_time', '')
        })

    total_pnl = total_realized_pnl + total_unrealized_pnl

    # Enhanced stock list with security IDs
    enhanced_stocks = []
    for s in stocks:
        sec_id = resolver.get_security_id(s['symbol']) or "UNKNOWN"
        enhanced_stocks.append({
            'id': s['id'],
            'symbol': s['symbol'],
            'security_id': sec_id,
            'allocated_capital': float(s['allocated_capital']),
            'sl_mode': s.get('sl_mode', 'CANDLE_LOW'),
            'enabled': bool(s['enabled']),
            'notes': s.get('notes', '')
        })

    # Prepare PnL Chart Data
    chart_labels = []
    chart_data = []
    for snapshot in pnl_history:
        chart_labels.append(snapshot['timestamp'])
        chart_data.append(round(snapshot['total_pnl'], 2))
    
    # Append current live point if not in history
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    chart_labels.append(now_str)
    chart_data.append(round(total_pnl, 2))

    return {
        'target_user': {
            'id': target_user['id'],
            'username': target_user['username'],
            'full_name': target_user['full_name'],
            'role': target_user['role']
        },
        'broker': {
            'name': b_cfg.get('name', 'DHAN'),
            'trading_mode': b_cfg.get('trading_mode', 'PAPER'),
            'product_type': b_cfg.get('product_type', 'CNC'),
            'client_id': b_cfg.get('dhan_client_id', '')
        },
        'auth': {
            'auto_login_enabled': bool(b_cfg.get('auto_login_enabled')),
            'auto_login_time': b_cfg.get('auto_login_time', '09:00'),
            'last_login_time': b_cfg.get('last_login_time', 'Never'),
            'last_login_status': b_cfg.get('last_login_status', 'NOT_CONFIGURED'),
            'last_login_message': b_cfg.get('last_login_message', ''),
            'has_pin': bool(b_cfg.get('dhan_pin')),
            'has_totp_secret': bool(b_cfg.get('totp_secret')),
            'has_api_key': bool(b_cfg.get('api_key'))
        },
        'metrics': {
            'total_allocated': round(total_allocated, 2),
            'total_invested': round(total_invested, 2),
            'realized_pnl': round(total_realized_pnl, 2),
            'unrealized_pnl': round(total_unrealized_pnl, 2),
            'total_pnl': round(total_pnl, 2),
            'open_positions_count': len(open_pos),
            'total_stocks_count': len(stocks),
            'active_stocks_count': len([s for s in stocks if s['enabled']])
        },
        'positions': enhanced_positions,
        'stocks': enhanced_stocks,
        'trades': trades[:20],
        'chart': {
            'labels': chart_labels[-40:], # Last 40 points for smooth curve
            'data': chart_data[-40:]
        }
    }

# ----------------- STOCK & POSITION MANAGEMENT -----------------

@app.post("/api/stock/update-capital")
def api_update_capital(req: CapitalUpdateRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    target_uid = resolve_target_user_id(current_user, req.user_id)
    UserManager.add_or_update_user_stock(target_uid, req.symbol, req.capital)
    return {'status': 'SUCCESS', 'symbol': req.symbol, 'capital': req.capital}

@app.post("/api/stock/add")
def api_add_stock(req: AddStockRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    target_uid = resolve_target_user_id(current_user, req.user_id)
    res = UserManager.add_or_update_user_stock(
        user_id=target_uid,
        symbol=req.symbol,
        allocated_capital=req.capital,
        sl_mode=req.sl_mode or "CANDLE_LOW",
        enabled=True,
        notes=req.notes or ""
    )
    return {'status': 'SUCCESS', 'stock': res}

@app.post("/api/stock/toggle-enabled")
def api_toggle_stock(req: ToggleStockRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    target_uid = resolve_target_user_id(current_user, req.user_id)
    new_state = UserManager.toggle_user_stock_enabled(target_uid, req.symbol)
    return {'status': 'SUCCESS', 'symbol': req.symbol, 'enabled': new_state}

@app.delete("/api/stock/{symbol}")
def api_remove_stock(symbol: str, user_id: Optional[int] = None, current_user: Dict[str, Any] = Depends(get_current_user)):
    target_uid = resolve_target_user_id(current_user, user_id)
    success = UserManager.remove_user_stock(target_uid, symbol)
    return {'status': 'SUCCESS' if success else 'NOT_FOUND', 'symbol': symbol}

@app.post("/api/position/close")
def api_close_position(req: ClosePositionRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    target_uid = resolve_target_user_id(current_user, req.user_id)
    ltp = data_fetcher.get_latest_price(req.symbol) or 0.0
    trade = UserManager.close_user_position(target_uid, req.symbol, exit_price=ltp, exit_reason="MANUAL_WEB_CLOSE")
    if not trade:
        raise HTTPException(status_code=400, detail="Position not found or already closed.")
    return {'status': 'SUCCESS', 'trade': trade}

@app.post("/api/auth/save-dhan")
def api_save_dhan(req: DhanAuthSettingsRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    target_uid = resolve_target_user_id(current_user, req.user_id)
    updates = {}
    if req.client_id is not None: updates['dhan_client_id'] = req.client_id.strip()
    if req.pin is not None and req.pin != "": updates['dhan_pin'] = req.pin.strip()
    if req.totp_secret is not None and req.totp_secret != "": updates['totp_secret'] = req.totp_secret.strip()
    if req.api_key is not None and req.api_key != "": updates['api_key'] = req.api_key.strip()
    if req.api_secret is not None and req.api_secret != "": updates['api_secret'] = req.api_secret.strip()
    if req.auto_login_enabled is not None: updates['auto_login_enabled'] = 1 if req.auto_login_enabled else 0
    if req.auto_login_time is not None: updates['auto_login_time'] = req.auto_login_time
    if req.trading_mode is not None: updates['trading_mode'] = req.trading_mode.upper()

    res = UserManager.update_user_broker_config(target_uid, **updates)
    return {'status': 'SUCCESS', 'config': res}

@app.post("/api/auth/login-dhan-now")
def api_login_dhan_now(user_id: Optional[int] = None, current_user: Dict[str, Any] = Depends(get_current_user)):
    target_uid = resolve_target_user_id(current_user, user_id)
    res = auth_mgr.execute_login(user_id=target_uid)
    return res

# ----------------- MARKET SCHEDULE & HOLIDAYS API -----------------

@app.get("/api/market/status")
def api_get_market_status():
    """Returns real-time market open/closed, weekend, and holiday status."""
    return MarketScheduleManager.get_market_status()

@app.get("/api/admin/holidays")
def api_get_holidays(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Returns all scheduled market holidays."""
    return {
        'holidays': MarketScheduleManager.get_all_holidays(),
        'market_status': MarketScheduleManager.get_market_status()
    }

@app.post("/api/admin/holidays")
def api_add_holiday(req: AddHolidayRequest, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Admin adds a market holiday."""
    if current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin privileges required.")
    try:
        res = MarketScheduleManager.add_holiday(req.holiday_date, req.name)
        return {'status': 'SUCCESS', 'holiday': res}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/admin/holidays/{holiday_id}")
def api_delete_holiday(holiday_id: int, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Admin deletes a market holiday."""
    if current_user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Admin privileges required.")
    deleted = MarketScheduleManager.delete_holiday(holiday_id)
    return {'status': 'SUCCESS', 'deleted': deleted}

# ----------------- SIGNAL SCANNER -----------------

@app.get("/api/scan")
def api_run_scan(user_id: Optional[int] = None, force: bool = False, current_user: Dict[str, Any] = Depends(get_current_user)):
    mkt = MarketScheduleManager.get_market_status()
    if not mkt['is_trading_day'] and not force:
        return {
            'market_closed': True,
            'message': f"Market is closed today ({mkt['message']}). Real-time scans and live order executions are paused.",
            'market_status': mkt,
            'results': [],
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

    target_uid = resolve_target_user_id(current_user, user_id)
    stocks = UserManager.get_user_stocks(target_uid, only_enabled=True)
    
    strat_cfg = config_mgr.strategy_config
    strategy = SuperTrendMACDStrategy(
        st_period=strat_cfg.get('supertrend_period', 8),
        st_multiplier=strat_cfg.get('supertrend_multiplier', 3.2),
        macd_fast=strat_cfg.get('macd_fast', 9),
        macd_slow=strat_cfg.get('macd_slow', 23),
        macd_signal=strat_cfg.get('macd_signal', 7)
    )

    results = []
    for s in stocks:
        sym = s['symbol']
        sl_mode = s.get('sl_mode', 'CANDLE_LOW')
        df_1h = data_fetcher.fetch_1h_data(sym, period='15d')
        if df_1h is None or len(df_1h) < 10:
            results.append({'symbol': sym, 'action': 'ERROR', 'reason': 'Data fetch failed', 'ltp': 0.0, 'st_direction': 'NEUTRAL'})
            continue

        eval_res = strategy.evaluate_signals(df_1h, sl_mode=sl_mode)
        results.append({
            'symbol': sym,
            'ltp': round(eval_res['current_price'], 2),
            'st_direction': 'BULLISH' if eval_res['is_st_bullish'] else 'BEARISH',
            'action': eval_res['action'],
            'reason': eval_res['reason'],
            'proposed_sl': eval_res.get('proposed_sl', 0.0)
        })
    return {'market_closed': False, 'results': results, 'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

@app.get("/api/logs")
def api_get_logs(current_user: Dict[str, Any] = Depends(get_current_user)):
    log_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs', 'trading_bot.log')
    if os.path.exists(log_file):
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            return {'logs': lines[-80:]}
        except Exception as e:
            return {'logs': [f"Error reading log file: {e}"]}
    return {'logs': ["Log file not yet initialized."]}

# ----------------- MODERN MULTI-USER DASHBOARD HTML -----------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Alpha Trading System</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body { background-color: #0b0f19; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .custom-scrollbar::-webkit-scrollbar { width: 5px; height: 5px; }
    .custom-scrollbar::-webkit-scrollbar-track { background: #0b0f19; }
    .custom-scrollbar::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 4px; }
  </style>
</head>
<body class="text-slate-100 min-h-screen">

  <!-- ================= AUTH CONTAINER (LOGIN) ================= -->
  <div id="authContainer" class="min-h-screen flex items-center justify-center p-4">
    <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-md w-full p-8 shadow-2xl space-y-6">
      <div class="text-center space-y-2">
        <div class="inline-flex items-center justify-center w-12 h-12 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 mb-2">
          <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"></path></svg>
        </div>
        <h2 class="text-2xl font-bold text-white tracking-tight">Alpha Trading System</h2>
        <p class="text-xs text-slate-400">Restricted Institutional Access. Sign in to your account.</p>
      </div>

      <div id="loginError" class="hidden p-3 rounded-lg bg-rose-950/80 border border-rose-800 text-xs text-rose-300"></div>

      <form onsubmit="handleLogin(event)" class="space-y-4">
        <div>
          <label class="block text-xs font-medium text-slate-300 mb-1">Username</label>
          <input id="loginUsername" type="text" required class="w-full px-3.5 py-2.5 bg-slate-950 border border-slate-800 rounded-xl text-white text-sm focus:border-emerald-500 focus:outline-none transition">
        </div>
        <div>
          <label class="block text-xs font-medium text-slate-300 mb-1">Password</label>
          <input id="loginPassword" type="password" required class="w-full px-3.5 py-2.5 bg-slate-950 border border-slate-800 rounded-xl text-white text-sm focus:border-emerald-500 focus:outline-none transition">
        </div>
        <button id="loginSubmitBtn" type="submit" class="w-full py-2.5 rounded-xl text-sm font-semibold bg-emerald-600 hover:bg-emerald-500 text-white transition shadow-lg shadow-emerald-950/50">
          Sign In
        </button>
      </form>
      <div class="text-center text-[11px] text-slate-500">
        Default administrator credentials: <span class="text-slate-400 font-mono">admin / admin123</span>
      </div>
    </div>
  </div>

  <!-- ================= MAIN APP CONTAINER ================= -->
  <div id="appContainer" class="hidden w-full max-w-full px-6 xl:px-10 py-6 space-y-6">

    <!-- HEADER -->
    <header class="flex flex-col xl:flex-row xl:items-center xl:justify-between pb-6 border-b border-slate-800 gap-4">
      <div class="flex items-center gap-4 flex-wrap">
        <div class="flex items-center gap-3">
          <div class="w-3 h-3 rounded-full bg-emerald-500 animate-ping"></div>
          <h1 class="text-2xl font-bold tracking-tight text-white whitespace-nowrap">Alpha Trading System</h1>
        </div>
        <!-- MARKET STATUS PILL -->
        <div id="marketStatusPill" class="px-3 py-1.5 rounded-xl text-xs font-semibold flex items-center gap-1.5 bg-slate-900 border border-slate-800 text-slate-400 whitespace-nowrap">
          <span class="w-2 h-2 rounded-full bg-slate-500"></span> Checking Market...
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-2.5">
        <!-- ADMIN USER SWITCHER DROPDOWN -->
        <div id="adminSwitcherBox" class="hidden flex items-center gap-2 bg-slate-900 border border-slate-800 px-3 py-1.5 rounded-xl">
          <span class="text-xs text-indigo-400 font-medium whitespace-nowrap">Viewing:</span>
          <select id="userSelectDropdown" onchange="switchUserView(this.value)" class="bg-slate-950 border border-slate-700 text-xs rounded-lg px-2 py-1 text-white focus:outline-none">
            <!-- Dynamically populated -->
          </select>
        </div>

        <!-- ADMIN HOLIDAYS MODAL BUTTON -->
        <button id="adminHolidaysBtn" onclick="openHolidaysModal()" class="hidden px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 text-slate-300 border border-slate-700 hover:bg-slate-700 transition whitespace-nowrap">
          📅 Holidays
        </button>

        <!-- MODE BUTTON -->
        <button id="modeBtn" onclick="toggleTradingMode()" class="hidden px-3 py-1.5 rounded-lg text-xs font-semibold tracking-wider transition border whitespace-nowrap">
          Loading...
        </button>

        <!-- DHAN AUTO-LOGIN MODAL BUTTON -->
        <button id="dhanLoginBtn" onclick="openDhanAuthModal()" class="hidden px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 text-slate-300 border border-slate-700 hover:bg-slate-700 transition whitespace-nowrap">
          Dhan Login ⚙
        </button>

        <!-- USER BADGE, CHANGE PASSWORD & LOGOUT -->
        <div class="flex items-center gap-2 pl-2 border-l border-slate-800">
          <span id="userBadge" class="text-xs text-slate-300 font-medium whitespace-nowrap">User</span>
          <button onclick="openOwnPasswordModal()" class="px-2.5 py-1.5 rounded-lg text-xs bg-slate-800 hover:bg-indigo-950 hover:text-indigo-300 text-slate-300 transition border border-slate-700 whitespace-nowrap" title="Change My Password">
            🔑 Password
          </button>
          <button onclick="handleLogout()" class="px-2.5 py-1.5 rounded-lg text-xs bg-slate-800 hover:bg-rose-950 hover:text-rose-300 text-slate-400 transition whitespace-nowrap" title="Log Out">
            Exit ⏻
          </button>
        </div>
      </div>
    </header>

    <!-- ADMIN OVERVIEW SECTION (Only visible when viewing All Users Overview) -->
    <div id="adminOverviewSection" class="hidden space-y-6">
      <div class="flex items-center justify-between">
        <div>
          <h2 class="text-lg font-bold text-white">Platform Performance & User Management</h2>
          <p class="text-xs text-slate-400">Compare trading performance across all active accounts.</p>
        </div>
        <button onclick="openCreateUserModal()" class="px-3.5 py-2 rounded-xl text-xs font-bold bg-indigo-600 hover:bg-indigo-500 text-white transition flex items-center gap-1.5 whitespace-nowrap">
          <span>+</span> Create New User
        </button>
      </div>

      <!-- ALL USERS PERFORMANCE TABLE -->
      <div class="bg-slate-900 border border-slate-800 rounded-xl overflow-x-auto">
        <table class="w-full text-left text-sm">
          <thead class="bg-slate-950 text-slate-400 text-xs uppercase tracking-wider">
            <tr>
              <th class="px-4 py-3">Trader</th>
              <th class="px-4 py-3">Role</th>
              <th class="px-4 py-3">Mode</th>
              <th class="px-4 py-3">Auto-Login</th>
              <th class="px-4 py-3">Active Stocks</th>
              <th class="px-4 py-3">Allocated (₹)</th>
              <th class="px-4 py-3">Invested (₹)</th>
              <th class="px-4 py-3">Realized PnL</th>
              <th class="px-4 py-3">Win Rate</th>
              <th class="px-4 py-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody id="allUsersTbody" class="divide-y divide-slate-800 text-slate-300">
            <!-- Dynamically populated -->
          </tbody>
        </table>
      </div>
    </div>

    <!-- SINGLE USER DASHBOARD SECTION (For Individual User or Admin Viewing a Specific User) -->
    <div id="singleUserSection" class="space-y-6">

      <!-- METRICS CARDS -->
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div class="bg-slate-900 border border-slate-800 p-4 rounded-xl">
          <span class="text-xs font-medium text-slate-400">Total Allocated Capital</span>
          <div id="metricAlloc" class="text-xl md:text-2xl font-bold text-white mt-1">₹0</div>
          <span id="metricStocksCount" class="text-xs text-slate-500">0 active stocks</span>
        </div>
        <div class="bg-slate-900 border border-slate-800 p-4 rounded-xl">
          <span class="text-xs font-medium text-slate-400">Currently Invested</span>
          <div id="metricInvested" class="text-xl md:text-2xl font-bold text-emerald-400 mt-1">₹0</div>
          <span id="metricOpenPos" class="text-xs text-slate-500">0 open positions</span>
        </div>
        <div class="bg-slate-900 border border-slate-800 p-4 rounded-xl">
          <span class="text-xs font-medium text-slate-400">Total PnL (Realized + Open)</span>
          <div id="metricTotalPnL" class="text-xl md:text-2xl font-bold text-slate-300 mt-1">₹0.00</div>
          <span id="metricPnlSplit" class="text-xs text-slate-500">Realized: ₹0 | Open: ₹0</span>
        </div>
        <div class="bg-slate-900 border border-slate-800 p-4 rounded-xl">
          <span class="text-xs font-medium text-slate-400">Dhan Broker Status</span>
          <div id="metricDhanStatus" class="text-base md:text-lg font-bold text-indigo-400 mt-1">PAPER</div>
          <span id="metricDhanLogin" class="text-xs text-slate-500">Auto-login: Off</span>
        </div>
      </div>

      <!-- PNL GRAPH CARD -->
      <div class="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <div class="flex items-center justify-between mb-4">
          <div>
            <h3 class="text-base font-bold text-white">Cumulative PnL Performance Curve</h3>
            <p class="text-xs text-slate-400">Real-time cumulative equity tracking (₹ INR)</p>
          </div>
          <div class="text-right">
            <span id="chartCurrentPnL" class="text-lg font-bold text-emerald-400">₹0.00</span>
          </div>
        </div>
        <div class="h-64 w-full">
          <canvas id="pnlChart"></canvas>
        </div>
      </div>

      <!-- MAIN TRADING WORKSPACE: 2 COLS -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">

        <!-- LEFT 2 COLS: POSITIONS & STOCKS -->
        <div class="lg:col-span-2 space-y-6">

          <!-- ACTIVE OPEN POSITIONS -->
          <div class="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
            <div class="px-5 py-4 border-b border-slate-800 flex items-center justify-between">
              <div class="flex items-center gap-2">
                <span class="text-base font-semibold text-white">Active Open Positions</span>
                <span id="posBadge" class="px-2 py-0.5 rounded text-xs font-bold bg-emerald-950 text-emerald-400 border border-emerald-800">0 Active</span>
              </div>
            </div>
            <div class="overflow-x-auto">
              <table class="w-full text-left text-sm">
                <thead class="bg-slate-950 text-slate-400 text-xs uppercase tracking-wider">
                  <tr>
                    <th class="px-4 py-3">Stock</th>
                    <th class="px-4 py-3">Shares</th>
                    <th class="px-4 py-3">Entry</th>
                    <th class="px-4 py-3">LTP</th>
                    <th class="px-4 py-3">Stop Loss</th>
                    <th class="px-4 py-3">Unrealized PnL</th>
                    <th class="px-4 py-3 text-right">Action</th>
                  </tr>
                </thead>
                <tbody id="positionsTbody" class="divide-y divide-slate-800 text-slate-300">
                  <tr><td colspan="7" class="px-4 py-6 text-center text-slate-500 text-xs">No active positions. Monitoring market signals.</td></tr>
                </tbody>
              </table>
            </div>
          </div>

          <!-- MONITORED STOCKS & CAPITAL ALLOCATION -->
          <div class="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
            <div class="px-5 py-4 border-b border-slate-800 flex items-center justify-between">
              <div>
                <span class="text-base font-semibold text-white">Stock Universe & Capital Allocation</span>
                <p class="text-xs text-slate-400">Toggle enabled/disabled at the beginning of each stock. Edit capital and save.</p>
              </div>
              <button onclick="openAddStockModal()" class="px-3 py-1.5 rounded-lg text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white transition flex items-center gap-1">
                <span>+</span> Add Stock
              </button>
            </div>
            <div class="overflow-x-auto">
              <table class="w-full text-left text-sm">
                <thead class="bg-slate-950 text-slate-400 text-xs uppercase tracking-wider">
                  <tr>
                    <th class="px-4 py-3">Trading</th>
                    <th class="px-4 py-3">Symbol</th>
                    <th class="px-4 py-3">Dhan SecID</th>
                    <th class="px-4 py-3">Allocated Capital (₹)</th>
                    <th class="px-4 py-3">SL Mode</th>
                    <th class="px-4 py-3 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody id="stocksTbody" class="divide-y divide-slate-800 text-slate-300">
                  <!-- Dynamically populated -->
                </tbody>
              </table>
            </div>
          </div>

          <!-- RECENT TRADES HISTORY -->
          <div class="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
            <div class="px-5 py-4 border-b border-slate-800">
              <span class="text-base font-semibold text-white">Recent Trade History</span>
            </div>
            <div class="overflow-x-auto max-h-60 custom-scrollbar">
              <table class="w-full text-left text-sm">
                <thead class="bg-slate-950 text-slate-400 text-xs uppercase tracking-wider sticky top-0">
                  <tr>
                    <th class="px-4 py-2.5">Symbol</th>
                    <th class="px-4 py-2.5">Shares</th>
                    <th class="px-4 py-2.5">Entry Time / Price</th>
                    <th class="px-4 py-2.5">Exit Time / Price</th>
                    <th class="px-4 py-2.5">Realized PnL</th>
                    <th class="px-4 py-2.5">Reason</th>
                  </tr>
                </thead>
                <tbody id="tradesTbody" class="divide-y divide-slate-800 text-slate-300 text-xs font-mono">
                  <tr><td colspan="6" class="px-4 py-6 text-center text-slate-500 font-sans">No closed trades yet.</td></tr>
                </tbody>
              </table>
            </div>
          </div>

        </div>

        <!-- RIGHT COL: SIGNAL SCANNER & BOT LOGS -->
        <div class="space-y-6">

          <!-- REAL-TIME SIGNAL SCANNER -->
          <div class="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
            <div class="px-5 py-4 border-b border-slate-800 flex items-center justify-between">
              <span class="text-base font-semibold text-white">Live Signal Scanner</span>
              <button id="scanBtn" onclick="runLiveScan()" class="px-3 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white transition">
                ⚡ Scan Now
              </button>
            </div>
            <div class="p-4 max-h-80 overflow-y-auto custom-scrollbar" id="scanContainer">
              <p class="text-xs text-slate-400 text-center py-4">Click "Scan Now" to evaluate real-time signals.</p>
            </div>
          </div>

          <!-- RECENT BOT LOGS -->
          <div class="bg-slate-900 border border-slate-800 rounded-xl p-5">
            <div class="flex items-center justify-between mb-3">
              <span class="text-sm font-semibold text-white">Bot Activity Logs</span>
              <button onclick="fetchLogs()" class="text-xs text-slate-400 hover:text-slate-200">⟳</button>
            </div>
            <div id="botLogs" class="bg-slate-950 p-3 rounded-lg border border-slate-800 text-[11px] font-mono text-slate-300 h-48 overflow-y-auto custom-scrollbar space-y-1">
              Loading logs...
            </div>
          </div>

        </div>

      </div>

    </div>

  </div>

  <!-- ================= MODALS ================= -->

  <!-- ADD STOCK MODAL -->
  <div id="addStockModal" class="hidden fixed inset-0 bg-black/70 flex items-center justify-center p-4 z-50">
    <div class="bg-slate-900 border border-slate-800 rounded-xl max-w-md w-full p-6 space-y-4">
      <h3 class="text-lg font-bold text-white">Add Stock to Universe</h3>
      <div class="space-y-3 text-xs">
        <div>
          <label class="block text-slate-400 mb-1">Stock Symbol (NSE)</label>
          <input id="newSym" type="text" placeholder="e.g. HAL, MAZDOCK, TRENT" class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono uppercase">
        </div>
        <div>
          <label class="block text-slate-400 mb-1">Allocated Capital (₹)</label>
          <input id="newCap" type="number" placeholder="100000" value="100000" class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono">
        </div>
        <div>
          <label class="block text-slate-400 mb-1">Stop Loss Mode</label>
          <select id="newSL" class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono">
            <option value="CANDLE_LOW">CANDLE_LOW (Buy Candle Low - Recommended)</option>
            <option value="1_PCT">1_PCT (Fixed 1% Stop Loss)</option>
          </select>
        </div>
      </div>
      <div class="flex justify-end gap-2 pt-2">
        <button onclick="closeAddStockModal()" class="px-4 py-2 rounded-lg text-xs bg-slate-800 text-slate-300 hover:bg-slate-700">Cancel</button>
        <button onclick="submitAddStock()" class="px-4 py-2 rounded-lg text-xs font-bold bg-emerald-600 text-white hover:bg-emerald-500">Save Stock</button>
      </div>
    </div>
  </div>

  <!-- DHAN AUTH CONFIG MODAL -->
  <div id="dhanAuthModal" class="hidden fixed inset-0 bg-black/70 flex items-center justify-center p-4 z-50">
    <div class="bg-slate-900 border border-slate-800 rounded-xl max-w-lg w-full p-6 space-y-4">
      <h3 class="text-lg font-bold text-white">Dhan Auto-Login & Broker Credentials</h3>
      <p class="text-xs text-slate-400">Configure your personal Dhan credentials to automate daily 09:00 AM token generation via TOTP and PIN.</p>
      
      <div class="space-y-3 text-xs">
        <div class="flex items-center justify-between p-3 bg-slate-950 rounded-lg border border-slate-800">
          <div>
            <span class="font-semibold text-white block">Enable Daily 09:00 AM Auto-Login</span>
            <span class="text-slate-400 text-[11px]">Generates token at 09:00 AM before market open</span>
          </div>
          <input id="authEnableCheck" type="checkbox" class="w-5 h-5 accent-emerald-500 cursor-pointer">
        </div>

        <div>
          <label class="block text-slate-400 mb-1">Trading Execution Mode</label>
          <select id="authTradingMode" class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono">
            <option value="PAPER">PAPER (Virtual Execution / Risk-Free)</option>
            <option value="LIVE">LIVE (Real Orders Routed to Dhan Account)</option>
          </select>
        </div>

        <div>
          <label class="block text-slate-400 mb-1">Dhan Client ID (10 digits)</label>
          <input id="authClientId" type="text" placeholder="e.g. 1100123456" class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono">
        </div>
        <div>
          <label class="block text-slate-400 mb-1">Dhan 6-Digit PIN</label>
          <input id="authPin" type="password" placeholder="••••••" class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono">
        </div>
        <div>
          <label class="block text-slate-400 mb-1">Google Authenticator TOTP Secret Key</label>
          <input id="authTotpSecret" type="text" placeholder="Base32 Secret from Dhan TOTP setup" class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono">
          <span class="text-[11px] text-slate-500">Automatically generates live 6-digit TOTP at 09:00 AM</span>
        </div>
        <div class="grid grid-cols-2 gap-2">
          <div>
            <label class="block text-slate-400 mb-1">API Key (Optional)</label>
            <input id="authApiKey" type="text" placeholder="Optional" class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono">
          </div>
          <div>
            <label class="block text-slate-400 mb-1">API Secret (Optional)</label>
            <input id="authApiSecret" type="password" placeholder="Optional" class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono">
          </div>
        </div>
      </div>

      <div class="flex items-center justify-between pt-2">
        <button onclick="triggerDhanLoginNow()" id="modalLoginTestBtn" class="px-3 py-2 rounded-lg text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-emerald-400 border border-slate-700">
          🔑 Test Login Now
        </button>
        <div class="flex gap-2">
          <button onclick="closeDhanAuthModal()" class="px-4 py-2 rounded-lg text-xs bg-slate-800 text-slate-300 hover:bg-slate-700">Cancel</button>
          <button onclick="saveDhanAuthSettings()" class="px-4 py-2 rounded-lg text-xs font-bold bg-indigo-600 text-white hover:bg-indigo-500">Save Credentials</button>
        </div>
      </div>
    </div>
  </div>

  <!-- ADMIN: CREATE USER MODAL -->
  <div id="createUserModal" class="hidden fixed inset-0 bg-black/70 flex items-center justify-center p-4 z-50">
    <div class="bg-slate-900 border border-slate-800 rounded-xl max-w-md w-full p-6 space-y-4">
      <h3 class="text-lg font-bold text-white">Create New Trader Account</h3>
      <div class="space-y-3 text-xs">
        <div>
          <label class="block text-slate-400 mb-1">Username</label>
          <input id="createUsername" type="text" placeholder="e.g. trader2" class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono">
        </div>
        <div>
          <label class="block text-slate-400 mb-1">Password</label>
          <input id="createPassword" type="password" placeholder="Min 6 characters" class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono">
        </div>
        <div>
          <label class="block text-slate-400 mb-1">Full Name</label>
          <input id="createFullName" type="text" placeholder="e.g. John Doe" class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono">
        </div>
        <div>
          <label class="block text-slate-400 mb-1">Role</label>
          <select id="createRole" class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono">
            <option value="user">Trader / Standard User</option>
            <option value="admin">Administrator</option>
          </select>
        </div>
      </div>
      <div class="flex justify-end gap-2 pt-2">
        <button onclick="closeCreateUserModal()" class="px-4 py-2 rounded-lg text-xs bg-slate-800 text-slate-300 hover:bg-slate-700">Cancel</button>
        <button onclick="submitCreateUser()" class="px-4 py-2 rounded-lg text-xs font-bold bg-indigo-600 text-white hover:bg-indigo-500">Create Account</button>
      </div>
    </div>
  </div>

  <!-- MARKET HOLIDAYS MODAL (ADMIN) -->
  <div id="holidaysModal" class="hidden fixed inset-0 bg-black/70 flex items-center justify-center p-4 z-50">
    <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-lg w-full p-6 space-y-4 shadow-2xl">
      <div class="flex items-center justify-between pb-3 border-b border-slate-800">
        <div>
          <h3 class="text-lg font-bold text-white flex items-center gap-2">
            <span>📅</span> Market Holidays Calendar
          </h3>
          <p class="text-xs text-slate-400">System pauses auto-logins and signal scans on scheduled holidays.</p>
        </div>
        <button onclick="closeHolidaysModal()" class="text-slate-400 hover:text-white text-lg">✕</button>
      </div>

      <!-- ADD NEW HOLIDAY FORM -->
      <form onsubmit="handleAddHoliday(event)" class="bg-slate-950 p-3.5 rounded-xl border border-slate-800 space-y-3">
        <div class="text-xs font-semibold text-slate-200">+ Add Market Holiday</div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
          <div>
            <label class="block text-slate-400 mb-1">Holiday Date</label>
            <input id="newHolidayDate" type="date" required class="w-full px-2.5 py-1.5 bg-slate-900 border border-slate-700 rounded-lg text-white font-mono focus:outline-none focus:border-indigo-500">
          </div>
          <div>
            <label class="block text-slate-400 mb-1">Holiday Name / Reason</label>
            <input id="newHolidayName" type="text" placeholder="e.g. Gurunanak Jayanti" required class="w-full px-2.5 py-1.5 bg-slate-900 border border-slate-700 rounded-lg text-white focus:outline-none focus:border-indigo-500">
          </div>
        </div>
        <button type="submit" class="w-full py-2 rounded-lg text-xs font-bold bg-indigo-600 hover:bg-indigo-500 text-white transition">
          Save Market Holiday
        </button>
      </form>

      <!-- HOLIDAYS TABLE -->
      <div class="max-h-56 overflow-y-auto custom-scrollbar border border-slate-800 rounded-xl">
        <table class="w-full text-left text-xs">
          <thead class="bg-slate-950 text-slate-400 sticky top-0 uppercase tracking-wider">
            <tr>
              <th class="px-3 py-2">Date</th>
              <th class="px-3 py-2">Holiday</th>
              <th class="px-3 py-2 text-right">Action</th>
            </tr>
          </thead>
          <tbody id="holidaysTableBody" class="divide-y divide-slate-800 text-slate-300">
            <!-- Dynamically populated -->
          </tbody>
        </table>
      </div>

      <div class="flex justify-end pt-2">
        <button type="button" onclick="closeHolidaysModal()" class="px-4 py-1.5 rounded-lg text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 transition">
          Close
        </button>
      </div>
    </div>
  </div>

  <!-- CHANGE OWN PASSWORD MODAL -->
  <div id="ownPasswordModal" class="hidden fixed inset-0 bg-black/70 flex items-center justify-center p-4 z-50">
    <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-sm w-full p-6 space-y-4 shadow-2xl">
      <div class="flex items-center justify-between pb-3 border-b border-slate-800">
        <h3 class="text-base font-bold text-white flex items-center gap-2">
          <span>🔑</span> Change My Password
        </h3>
        <button onclick="closeOwnPasswordModal()" class="text-slate-400 hover:text-white text-lg">✕</button>
      </div>
      <div id="ownPwdError" class="hidden p-2.5 rounded-lg bg-rose-950/80 border border-rose-800 text-xs text-rose-300"></div>
      <form onsubmit="submitOwnPassword(event)" class="space-y-3 text-xs">
        <div>
          <label class="block text-slate-400 mb-1">Current Password</label>
          <input id="ownCurrentPwd" type="password" required class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono focus:border-indigo-500 focus:outline-none">
        </div>
        <div>
          <label class="block text-slate-400 mb-1">New Password (min 6 chars)</label>
          <input id="ownNewPwd" type="password" required minlength="6" class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono focus:border-indigo-500 focus:outline-none">
        </div>
        <div>
          <label class="block text-slate-400 mb-1">Confirm New Password</label>
          <input id="ownConfirmPwd" type="password" required minlength="6" class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono focus:border-indigo-500 focus:outline-none">
        </div>
        <div class="flex justify-end gap-2 pt-2">
          <button type="button" onclick="closeOwnPasswordModal()" class="px-4 py-2 rounded-lg text-xs bg-slate-800 text-slate-300 hover:bg-slate-700">Cancel</button>
          <button type="submit" class="px-4 py-2 rounded-lg text-xs font-bold bg-indigo-600 text-white hover:bg-indigo-500">Update Password</button>
        </div>
      </form>
    </div>
  </div>

  <!-- ADMIN: CHANGE USER PASSWORD MODAL -->
  <div id="adminUserPasswordModal" class="hidden fixed inset-0 bg-black/70 flex items-center justify-center p-4 z-50">
    <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-sm w-full p-6 space-y-4 shadow-2xl">
      <div class="flex items-center justify-between pb-3 border-b border-slate-800">
        <div>
          <h3 class="text-base font-bold text-white flex items-center gap-2">
            <span>🔐</span> Reset User Password
          </h3>
          <p id="adminPwdTargetLabel" class="text-xs text-indigo-400 font-mono mt-0.5">Target User</p>
        </div>
        <button onclick="closeAdminUserPasswordModal()" class="text-slate-400 hover:text-white text-lg">✕</button>
      </div>
      <div id="adminPwdError" class="hidden p-2.5 rounded-lg bg-rose-950/80 border border-rose-800 text-xs text-rose-300"></div>
      <form onsubmit="submitAdminUserPassword(event)" class="space-y-3 text-xs">
        <input type="hidden" id="adminPwdTargetId">
        <div>
          <label class="block text-slate-400 mb-1">New Password (min 6 chars)</label>
          <input id="adminNewPwd" type="password" required minlength="6" class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono focus:border-indigo-500 focus:outline-none">
        </div>
        <div>
          <label class="block text-slate-400 mb-1">Confirm New Password</label>
          <input id="adminConfirmPwd" type="password" required minlength="6" class="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono focus:border-indigo-500 focus:outline-none">
        </div>
        <div class="flex justify-end gap-2 pt-2">
          <button type="button" onclick="closeAdminUserPasswordModal()" class="px-4 py-2 rounded-lg text-xs bg-slate-800 text-slate-300 hover:bg-slate-700">Cancel</button>
          <button type="submit" class="px-4 py-2 rounded-lg text-xs font-bold bg-indigo-600 text-white hover:bg-indigo-500">Save Password</button>
        </div>
      </form>
    </div>
  </div>

  <script>
    let currentUser = null;
    let selectedUserId = null; // null means 'admin overview' if admin, or user's own id
    let dashboardData = {};
    let allUsersList = [];
    let pnlChart = null;

    // --- INITIALIZATION ---
    async function initApp() {
      try {
        const res = await fetch('/api/auth/me');
        if (res.status === 401) {
          showLogin();
          return;
        }
        const data = await res.json();
        currentUser = data.user;
        showApp();
      } catch (e) {
        showLogin();
      }
    }

    function showLogin() {
      document.getElementById('authContainer').classList.remove('hidden');
      document.getElementById('appContainer').classList.add('hidden');
    }

    async function showApp() {
      document.getElementById('authContainer').classList.add('hidden');
      document.getElementById('appContainer').classList.remove('hidden');

      document.getElementById('userBadge').innerText = `${currentUser.full_name} (${currentUser.role.toUpperCase()})`;

      if (currentUser.role === 'admin') {
        document.getElementById('adminSwitcherBox').classList.remove('hidden');
        document.getElementById('adminHolidaysBtn').classList.remove('hidden');
        await loadAdminUsersList();
        selectedUserId = 'ALL'; // Default admin to Overview
      } else {
        document.getElementById('adminSwitcherBox').classList.add('hidden');
        document.getElementById('adminHolidaysBtn').classList.add('hidden');
        selectedUserId = currentUser.id;
      }

      await updateMarketStatusPill();
      await refreshCurrentView();
      initPnLChart();
      setInterval(refreshCurrentView, 15000);
      setInterval(fetchLogs, 15000);
      setInterval(updateMarketStatusPill, 60000);
    }

    // --- AUTH ACTIONS ---
    async function handleLogin(e) {
      e.preventDefault();
      const u = document.getElementById('loginUsername').value.trim();
      const p = document.getElementById('loginPassword').value;
      const errBox = document.getElementById('loginError');
      errBox.classList.add('hidden');

      try {
        const res = await fetch('/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: u, password: p })
        });
        const data = await res.json();
        if (res.ok) {
          currentUser = data.user;
          showApp();
        } else {
          errBox.innerText = data.detail || 'Login failed.';
          errBox.classList.remove('hidden');
        }
      } catch (err) {
        errBox.innerText = 'Network error during login.';
        errBox.classList.remove('hidden');
      }
    }

    async function handleLogout() {
      await fetch('/api/auth/logout', { method: 'POST' });
      currentUser = null;
      selectedUserId = null;
      showLogin();
    }

    // --- ADMIN USER SWITCHER ---
    async function loadAdminUsersList() {
      try {
        const res = await fetch('/api/admin/users');
        const data = await res.json();
        allUsersList = data.users || [];

        const dropdown = document.getElementById('userSelectDropdown');
        dropdown.innerHTML = `
          <option value="ALL">👥 All Users (Overview)</option>
          ${allUsersList.map(u => `<option value="${u.user_id}">👤 ${u.username} (${u.full_name})</option>`).join('')}
        `;
        if (selectedUserId) {
          dropdown.value = selectedUserId;
        }
      } catch (e) {
        console.error("Error loading admin users:", e);
      }
    }

    async function switchUserView(val) {
      selectedUserId = val;
      document.getElementById('userSelectDropdown').value = val;
      await refreshCurrentView();
    }

    // --- REFRESH CURRENT VIEW ---
    async function refreshCurrentView() {
      if (selectedUserId === 'ALL') {
        document.getElementById('adminOverviewSection').classList.remove('hidden');
        document.getElementById('singleUserSection').classList.add('hidden');
        document.getElementById('modeBtn').classList.add('hidden');
        document.getElementById('dhanLoginBtn').classList.add('hidden');
        await renderAdminOverview();
      } else {
        document.getElementById('adminOverviewSection').classList.add('hidden');
        document.getElementById('singleUserSection').classList.remove('hidden');
        document.getElementById('modeBtn').classList.remove('hidden');
        document.getElementById('dhanLoginBtn').classList.remove('hidden');
        await renderSingleUserDashboard(selectedUserId || currentUser.id);
      }
      fetchLogs();
    }

    // --- RENDER ADMIN OVERVIEW ---
    async function renderAdminOverview() {
      await loadAdminUsersList();
      const tbody = document.getElementById('allUsersTbody');
      tbody.innerHTML = allUsersList.map(u => `
        <tr class="hover:bg-slate-950/50 transition">
          <td class="px-4 py-3 font-bold text-white whitespace-nowrap">
            ${u.username}
            <span class="text-xs font-normal text-slate-400 block">${u.full_name}</span>
          </td>
          <td class="px-4 py-3 text-xs uppercase font-mono whitespace-nowrap">${u.role}</td>
          <td class="px-4 py-3 font-semibold text-xs whitespace-nowrap ${u.trading_mode === 'LIVE' ? 'text-rose-400' : 'text-emerald-400'}">${u.trading_mode}</td>
          <td class="px-4 py-3 text-xs whitespace-nowrap">${u.auto_login ? '🟢 ON (09:00)' : '⚪ OFF'}</td>
          <td class="px-4 py-3 text-xs whitespace-nowrap">${u.active_stocks} / ${u.total_stocks}</td>
          <td class="px-4 py-3 font-mono text-xs whitespace-nowrap">₹${u.total_allocated.toLocaleString('en-IN')}</td>
          <td class="px-4 py-3 font-mono text-xs text-emerald-400 whitespace-nowrap">₹${u.total_invested.toLocaleString('en-IN')}</td>
          <td class="px-4 py-3 font-mono text-xs font-bold whitespace-nowrap ${u.realized_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}">
            ${u.realized_pnl >= 0 ? '+' : ''}₹${u.realized_pnl.toLocaleString('en-IN', {minimumFractionDigits: 2})}
          </td>
          <td class="px-4 py-3 text-xs font-mono whitespace-nowrap">${u.win_rate_pct}% (${u.total_trades} trades)</td>
          <td class="px-4 py-3 text-right whitespace-nowrap">
            <div class="flex items-center justify-end gap-2">
              <button onclick="switchUserView(${u.user_id})" class="px-3 py-1.5 rounded-lg text-xs font-medium bg-indigo-900/60 hover:bg-indigo-700 text-indigo-200 border border-indigo-700 transition whitespace-nowrap">
                View Dashboard ➜
              </button>
              <button onclick="openAdminUserPasswordModal(${u.user_id}, '${u.username}')" class="px-2.5 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition whitespace-nowrap" title="Change User Password">
                🔑 Password
              </button>
              ${u.user_id !== currentUser.id ? `
                <button onclick="deleteUserAccount(${u.user_id}, '${u.username}')" class="px-2.5 py-1.5 rounded-lg text-xs font-medium bg-rose-950/60 hover:bg-rose-800 text-rose-300 border border-rose-800 transition whitespace-nowrap" title="Delete User">
                  🗑 Delete
                </button>
              ` : ''}
            </div>
          </td>
        </tr>
      `).join('');
    }

    // --- RENDER SINGLE USER DASHBOARD ---
    async function renderSingleUserDashboard(uid) {
      try {
        const res = await fetch(`/api/dashboard?user_id=${uid}`);
        dashboardData = await res.json();
        
        // Metrics
        document.getElementById('metricAlloc').innerText = '₹' + dashboardData.metrics.total_allocated.toLocaleString('en-IN');
        document.getElementById('metricStocksCount').innerText = `${dashboardData.metrics.active_stocks_count} active / ${dashboardData.metrics.total_stocks_count} total`;
        document.getElementById('metricInvested').innerText = '₹' + dashboardData.metrics.total_invested.toLocaleString('en-IN');
        document.getElementById('metricOpenPos').innerText = `${dashboardData.metrics.open_positions_count} open positions`;

        const tpnl = dashboardData.metrics.total_pnl;
        const tpnlEl = document.getElementById('metricTotalPnL');
        tpnlEl.innerText = (tpnl >= 0 ? '+₹' : '-₹') + Math.abs(tpnl).toLocaleString('en-IN', {minimumFractionDigits: 2});
        tpnlEl.className = 'text-xl md:text-2xl font-bold mt-1 ' + (tpnl >= 0 ? 'text-emerald-400' : 'text-rose-400');
        document.getElementById('metricPnlSplit').innerText = `Realized: ₹${dashboardData.metrics.realized_pnl.toFixed(2)} | Open: ₹${dashboardData.metrics.unrealized_pnl.toFixed(2)}`;

        // Mode button
        const modeBtn = document.getElementById('modeBtn');
        if (dashboardData.broker.trading_mode === 'LIVE') {
          modeBtn.innerText = '🔴 LIVE TRADING';
          modeBtn.className = 'px-3 py-1.5 rounded-lg text-xs font-bold bg-rose-950 text-rose-300 border border-rose-700 hover:bg-rose-900 transition';
          document.getElementById('metricDhanStatus').innerText = 'LIVE (Dhan)';
          document.getElementById('metricDhanStatus').className = 'text-base md:text-lg font-bold text-rose-400 mt-1';
        } else {
          modeBtn.innerText = '🟢 PAPER TRADING';
          modeBtn.className = 'px-3 py-1.5 rounded-lg text-xs font-bold bg-emerald-950 text-emerald-300 border border-emerald-700 hover:bg-emerald-900 transition';
          document.getElementById('metricDhanStatus').innerText = 'PAPER (Simulated)';
          document.getElementById('metricDhanStatus').className = 'text-base md:text-lg font-bold text-emerald-400 mt-1';
        }
        document.getElementById('metricDhanLogin').innerText = `Auto-login: ${dashboardData.auth.auto_login_enabled ? 'ON (09:00)' : 'OFF'}`;

        // Positions
        const posTbody = document.getElementById('positionsTbody');
        document.getElementById('posBadge').innerText = dashboardData.positions.length + ' Active';
        if (dashboardData.positions.length === 0) {
          posTbody.innerHTML = '<tr><td colspan="7" class="px-4 py-6 text-center text-slate-500 text-xs">No active positions. Monitoring market signals.</td></tr>';
        } else {
          posTbody.innerHTML = dashboardData.positions.map(p => `
            <tr class="hover:bg-slate-950/50">
              <td class="px-4 py-3 font-bold text-white">${p.symbol}</td>
              <td class="px-4 py-3">${p.shares}</td>
              <td class="px-4 py-3 font-mono">₹${p.entry_price.toFixed(2)}</td>
              <td class="px-4 py-3 font-mono text-slate-200">₹${p.ltp.toFixed(2)}</td>
              <td class="px-4 py-3 font-mono text-rose-400">₹${p.stop_loss_price.toFixed(2)}</td>
              <td class="px-4 py-3 font-mono ${p.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'} font-semibold">
                ${p.pnl >= 0 ? '+' : ''}₹${p.pnl.toFixed(2)} (${p.pnl_pct.toFixed(2)}%)
              </td>
              <td class="px-4 py-3 text-right">
                <button onclick="closePosition('${p.symbol}')" class="px-2.5 py-1 rounded text-xs bg-rose-900/60 hover:bg-rose-800 text-rose-200 border border-rose-800 transition">Close</button>
              </td>
            </tr>
          `).join('');
        }

        // Stocks Universe Table with Enable/Disable Toggle at Beginning
        const stocksTbody = document.getElementById('stocksTbody');
        stocksTbody.innerHTML = dashboardData.stocks.map(s => `
          <tr class="hover:bg-slate-950/50 transition ${s.enabled ? '' : 'opacity-60 bg-slate-950/40'}">
            <td class="px-4 py-3">
              ${s.enabled ? 
                `<button onclick="toggleStockEnabled('${s.symbol}')" class="px-2.5 py-1 rounded text-xs font-bold bg-emerald-950 text-emerald-400 border border-emerald-700 hover:bg-emerald-900 transition flex items-center gap-1.5 shadow-sm" title="Click to Disable Trading for ${s.symbol}">
                  <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span> Enabled
                </button>` : 
                `<button onclick="toggleStockEnabled('${s.symbol}')" class="px-2.5 py-1 rounded text-xs font-bold bg-slate-800 text-slate-400 border border-slate-700 hover:bg-slate-700 transition flex items-center gap-1.5 shadow-sm" title="Click to Enable Trading for ${s.symbol}">
                  <span class="w-2 h-2 rounded-full bg-slate-500"></span> Disabled
                </button>`
              }
            </td>
            <td class="px-4 py-3 font-bold text-white">${s.symbol}</td>
            <td class="px-4 py-3 font-mono text-xs text-slate-400">${s.security_id}</td>
            <td class="px-4 py-3">
              <div class="flex items-center gap-1.5">
                <span class="text-slate-400 text-xs">₹</span>
                <input id="cap_${s.symbol}" type="number" value="${s.allocated_capital}" class="w-28 px-2 py-1 bg-slate-950 border border-slate-700 rounded text-xs font-mono text-white">
                <button onclick="saveCapital('${s.symbol}')" class="px-2 py-1 rounded text-[11px] bg-slate-800 hover:bg-slate-700 text-emerald-400 border border-slate-700">Save</button>
              </div>
            </td>
            <td class="px-4 py-3 text-xs font-mono text-slate-300">${s.sl_mode}</td>
            <td class="px-4 py-3 text-right">
              <button onclick="removeStock('${s.symbol}')" class="text-xs text-slate-500 hover:text-rose-400">Remove</button>
            </td>
          </tr>
        `).join('');

        // Trade History
        const tradesTbody = document.getElementById('tradesTbody');
        if (dashboardData.trades && dashboardData.trades.length > 0) {
          tradesTbody.innerHTML = dashboardData.trades.map(t => `
            <tr class="hover:bg-slate-950/40">
              <td class="px-4 py-2 font-bold text-white">${t.symbol}</td>
              <td class="px-4 py-2">${t.shares}</td>
              <td class="px-4 py-2 text-slate-400">${t.entry_time.split(' ')[0]} @ ₹${t.entry_price.toFixed(2)}</td>
              <td class="px-4 py-2 text-slate-400">${t.exit_time.split(' ')[0]} @ ₹${t.exit_price.toFixed(2)}</td>
              <td class="px-4 py-2 font-bold ${t.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}">
                ${t.pnl >= 0 ? '+' : ''}₹${t.pnl.toFixed(2)} (${t.pnl_pct.toFixed(2)}%)
              </td>
              <td class="px-4 py-2 text-slate-400 text-[11px]">${t.exit_reason}</td>
            </tr>
          `).join('');
        } else {
          tradesTbody.innerHTML = '<tr><td colspan="6" class="px-4 py-6 text-center text-slate-500 font-sans">No closed trades yet.</td></tr>';
        }

        // Update PnL Chart
        updatePnLChart(dashboardData.chart.labels, dashboardData.chart.data);

      } catch (e) {
        console.error("Error rendering user dashboard:", e);
      }
    }

    // --- PNL CHART (CHART.JS) ---
    function initPnLChart() {
      const ctx = document.getElementById('pnlChart').getContext('2d');
      pnlChart = new Chart(ctx, {
        type: 'line',
        data: {
          labels: [],
          datasets: [{
            label: 'Total PnL (₹)',
            data: [],
            borderColor: '#10b981',
            backgroundColor: 'rgba(16, 185, 129, 0.1)',
            fill: true,
            tension: 0.35,
            borderWidth: 2,
            pointRadius: 2,
            pointHoverRadius: 5
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: function(context) {
                  return ' PnL: ₹' + context.parsed.y.toLocaleString('en-IN', {minimumFractionDigits: 2});
                }
              }
            }
          },
          scales: {
            x: {
              grid: { color: 'rgba(255, 255, 255, 0.05)' },
              ticks: { color: '#64748b', maxTicksLimit: 6, font: { size: 10 } }
            },
            y: {
              grid: { color: 'rgba(255, 255, 255, 0.05)' },
              ticks: {
                color: '#64748b',
                font: { size: 10 },
                callback: function(val) { return '₹' + val.toLocaleString('en-IN'); }
              }
            }
          }
        }
      });
    }

    function updatePnLChart(labels, data) {
      if (!pnlChart) return;
      pnlChart.data.labels = labels.map(l => l.split(' ')[0] || l);
      pnlChart.data.datasets[0].data = data;
      const lastVal = data[data.length - 1] || 0;
      
      const currentPnlEl = document.getElementById('chartCurrentPnL');
      currentPnlEl.innerText = (lastVal >= 0 ? '+₹' : '-₹') + Math.abs(lastVal).toLocaleString('en-IN', {minimumFractionDigits: 2});
      
      if (lastVal >= 0) {
        pnlChart.data.datasets[0].borderColor = '#10b981';
        pnlChart.data.datasets[0].backgroundColor = 'rgba(16, 185, 129, 0.12)';
        currentPnlEl.className = 'text-lg font-bold text-emerald-400';
      } else {
        pnlChart.data.datasets[0].borderColor = '#f43f5e';
        pnlChart.data.datasets[0].backgroundColor = 'rgba(244, 63, 94, 0.12)';
        currentPnlEl.className = 'text-lg font-bold text-rose-400';
      }
      pnlChart.update();
    }

    // --- STOCK & TRADING ACTIONS ---
    async function toggleStockEnabled(symbol) {
      await fetch('/api/stock/toggle-enabled', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: symbol, user_id: selectedUserId })
      });
      refreshCurrentView();
    }

    async function saveCapital(symbol) {
      const val = parseFloat(document.getElementById('cap_' + symbol).value);
      if (isNaN(val) || val <= 0) return alert('Invalid capital amount');
      await fetch('/api/stock/update-capital', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: symbol, capital: val, user_id: selectedUserId })
      });
      refreshCurrentView();
    }

    async function closePosition(symbol) {
      if (confirm(`Close active position for ${symbol} immediately?`)) {
        await fetch('/api/position/close', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ symbol: symbol, user_id: selectedUserId })
        });
        refreshCurrentView();
      }
    }

    async function removeStock(symbol) {
      if (confirm(`Remove ${symbol} from monitored universe?`)) {
        await fetch(`/api/stock/${symbol}?user_id=${selectedUserId || ''}`, { method: 'DELETE' });
        refreshCurrentView();
      }
    }

    async function toggleTradingMode() {
      const curr = dashboardData.broker.trading_mode;
      const target = curr === 'PAPER' ? 'LIVE' : 'PAPER';
      if (confirm(`Switch trading mode to ${target}?`)) {
        await fetch('/api/auth/save-dhan', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ trading_mode: target, user_id: selectedUserId })
        });
        refreshCurrentView();
      }
    }

    // --- MODALS ---
    function openAddStockModal() { document.getElementById('addStockModal').classList.remove('hidden'); }
    function closeAddStockModal() { document.getElementById('addStockModal').classList.add('hidden'); }

    async function submitAddStock() {
      const sym = document.getElementById('newSym').value.trim();
      const cap = parseFloat(document.getElementById('newCap').value);
      const sl = document.getElementById('newSL').value;
      if (!sym) return alert('Please enter stock symbol');
      await fetch('/api/stock/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: sym, capital: cap, sl_mode: sl, user_id: selectedUserId })
      });
      closeAddStockModal();
      document.getElementById('newSym').value = '';
      refreshCurrentView();
    }

    function openDhanAuthModal() {
      if (dashboardData.auth) {
        document.getElementById('authEnableCheck').checked = dashboardData.auth.auto_login_enabled;
        document.getElementById('authTradingMode').value = dashboardData.broker.trading_mode || 'PAPER';
        document.getElementById('authClientId').value = dashboardData.broker.client_id || '';
      }
      document.getElementById('dhanAuthModal').classList.remove('hidden');
    }
    function closeDhanAuthModal() { document.getElementById('dhanAuthModal').classList.add('hidden'); }

    async function saveDhanAuthSettings() {
      const payload = {
        client_id: document.getElementById('authClientId').value,
        pin: document.getElementById('authPin').value,
        totp_secret: document.getElementById('authTotpSecret').value,
        api_key: document.getElementById('authApiKey').value,
        api_secret: document.getElementById('authApiSecret').value,
        trading_mode: document.getElementById('authTradingMode').value,
        auto_login_enabled: document.getElementById('authEnableCheck').checked,
        user_id: selectedUserId
      };
      await fetch('/api/auth/save-dhan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      closeDhanAuthModal();
      alert('Dhan credentials saved successfully!');
      refreshCurrentView();
    }

    async function triggerDhanLoginNow() {
      const btn = document.getElementById('modalLoginTestBtn');
      btn.innerText = 'Logging in...';
      try {
        const res = await fetch(`/api/auth/login-dhan-now?user_id=${selectedUserId || ''}`, { method: 'POST' });
        const data = await res.json();
        alert(`Login Result: ${data.status}\n${data.message}`);
      } catch (e) {
        alert('Login failed: ' + e);
      } finally {
        btn.innerText = '🔑 Test Login Now';
        refreshCurrentView();
      }
    }

    // --- ADMIN: CREATE USER ---
    function openCreateUserModal() { document.getElementById('createUserModal').classList.remove('hidden'); }
    function closeCreateUserModal() { document.getElementById('createUserModal').classList.add('hidden'); }

    async function submitCreateUser() {
      const u = document.getElementById('createUsername').value.trim();
      const p = document.getElementById('createPassword').value;
      const f = document.getElementById('createFullName').value.trim();
      const r = document.getElementById('createRole').value;
      if (!u || !p || !f) return alert('All fields are required');

      const res = await fetch('/api/admin/users/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: u, password: p, full_name: f, role: r })
      });
      const data = await res.json();
      if (res.ok) {
        closeCreateUserModal();
        alert(`User ${u} created successfully!`);
        await loadAdminUsersList();
        renderAdminOverview();
      } else {
        alert('Failed: ' + (data.detail || 'Could not create user'));
      }
    }

    // --- USER: CHANGE OWN PASSWORD ---
    function openOwnPasswordModal() {
      document.getElementById('ownCurrentPwd').value = '';
      document.getElementById('ownNewPwd').value = '';
      document.getElementById('ownConfirmPwd').value = '';
      document.getElementById('ownPwdError').classList.add('hidden');
      document.getElementById('ownPasswordModal').classList.remove('hidden');
    }

    function closeOwnPasswordModal() {
      document.getElementById('ownPasswordModal').classList.add('hidden');
    }

    async function submitOwnPassword(e) {
      e.preventDefault();
      const currPwd = document.getElementById('ownCurrentPwd').value;
      const newPwd = document.getElementById('ownNewPwd').value;
      const confPwd = document.getElementById('ownConfirmPwd').value;
      const errBox = document.getElementById('ownPwdError');
      errBox.classList.add('hidden');

      if (newPwd !== confPwd) {
        errBox.innerText = 'New passwords do not match.';
        errBox.classList.remove('hidden');
        return;
      }

      try {
        const res = await fetch('/api/auth/change-password', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ current_password: currPwd, new_password: newPwd })
        });
        const data = await res.json();
        if (res.ok) {
          closeOwnPasswordModal();
          alert('✅ Your password has been updated successfully!');
        } else {
          errBox.innerText = data.detail || 'Failed to update password.';
          errBox.classList.remove('hidden');
        }
      } catch (err) {
        errBox.innerText = 'Network error updating password.';
        errBox.classList.remove('hidden');
      }
    }

    // --- ADMIN: CHANGE ANY USER PASSWORD & DELETE USER ---
    function openAdminUserPasswordModal(uid, username) {
      document.getElementById('adminPwdTargetId').value = uid;
      document.getElementById('adminPwdTargetLabel').innerText = `Target User: ${username}`;
      document.getElementById('adminNewPwd').value = '';
      document.getElementById('adminConfirmPwd').value = '';
      document.getElementById('adminPwdError').classList.add('hidden');
      document.getElementById('adminUserPasswordModal').classList.remove('hidden');
    }

    function closeAdminUserPasswordModal() {
      document.getElementById('adminUserPasswordModal').classList.add('hidden');
    }

    async function submitAdminUserPassword(e) {
      e.preventDefault();
      const uid = document.getElementById('adminPwdTargetId').value;
      const newPwd = document.getElementById('adminNewPwd').value;
      const confPwd = document.getElementById('adminConfirmPwd').value;
      const errBox = document.getElementById('adminPwdError');
      errBox.classList.add('hidden');

      if (newPwd !== confPwd) {
        errBox.innerText = 'New passwords do not match.';
        errBox.classList.remove('hidden');
        return;
      }

      try {
        const res = await fetch(`/api/admin/users/${uid}/password`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ new_password: newPwd })
        });
        const data = await res.json();
        if (res.ok) {
          closeAdminUserPasswordModal();
          alert('✅ User password updated successfully!');
        } else {
          errBox.innerText = data.detail || 'Failed to update user password.';
          errBox.classList.remove('hidden');
        }
      } catch (err) {
        errBox.innerText = 'Network error updating user password.';
        errBox.classList.remove('hidden');
      }
    }

    async function deleteUserAccount(uid, username) {
      if (!confirm(`⚠️ Delete trader "${username}"?\n\nThis will permanently delete this user account, all their stock allocations, Dhan credentials, and trade history. This action cannot be undone.`)) {
        return;
      }

      try {
        const res = await fetch(`/api/admin/users/${uid}`, { method: 'DELETE' });
        const data = await res.json();
        if (res.ok) {
          alert(`✅ User "${username}" deleted.`);
          await loadAdminUsersList();
          renderAdminOverview();
        } else {
          alert('Failed to delete user: ' + (data.detail || 'Unknown error'));
        }
      } catch (err) {
        alert('Network error deleting user.');
      }
    }

    // --- ADMIN: MARKET HOLIDAYS ---
    async function openHolidaysModal() {
      document.getElementById('newHolidayDate').value = new Date().toISOString().split('T')[0];
      document.getElementById('newHolidayName').value = '';
      document.getElementById('holidaysModal').classList.remove('hidden');
      await loadHolidaysList();
    }

    function closeHolidaysModal() {
      document.getElementById('holidaysModal').classList.add('hidden');
    }

    async function loadHolidaysList() {
      try {
        const res = await fetch('/api/admin/holidays');
        const data = await res.json();
        const tbody = document.getElementById('holidaysTableBody');
        const todayStr = new Date().toISOString().split('T')[0];
        if (!data.holidays || data.holidays.length === 0) {
          tbody.innerHTML = '<tr><td colspan="3" class="px-3 py-4 text-center text-slate-500">No market holidays scheduled.</td></tr>';
          return;
        }
        tbody.innerHTML = data.holidays.map(h => `
          <tr class="hover:bg-slate-950/60 ${h.holiday_date === todayStr ? 'bg-amber-950/30' : ''}">
            <td class="px-3 py-2 font-mono ${h.holiday_date === todayStr ? 'text-amber-400 font-bold' : ''}">
              ${h.holiday_date} ${h.holiday_date === todayStr ? '⭐ Today' : ''}
            </td>
            <td class="px-3 py-2 ${h.holiday_date === todayStr ? 'text-amber-300 font-semibold' : ''}">
              ${h.name}
            </td>
            <td class="px-3 py-2 text-right">
              <button onclick="handleDeleteHoliday(${h.id})" class="px-2 py-0.5 rounded bg-rose-950 hover:bg-rose-900 text-rose-300 border border-rose-800 text-[10px]">
                Delete
              </button>
            </td>
          </tr>
        `).join('');
      } catch (e) {
        console.error("Error loading holidays:", e);
      }
    }

    async function handleAddHoliday(e) {
      e.preventDefault();
      const date = document.getElementById('newHolidayDate').value;
      const name = document.getElementById('newHolidayName').value.trim();
      if (!date || !name) return;

      try {
        const res = await fetch('/api/admin/holidays', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ holiday_date: date, name: name })
        });
        if (res.ok) {
          document.getElementById('newHolidayName').value = '';
          await loadHolidaysList();
          await updateMarketStatusPill();
        } else {
          const err = await res.json();
          alert('Error: ' + (err.detail || 'Could not add holiday'));
        }
      } catch (e) {
        alert('Network error adding holiday');
      }
    }

    async function handleDeleteHoliday(id) {
      if (!confirm('Are you sure you want to remove this market holiday?')) return;
      try {
        const res = await fetch(`/api/admin/holidays/${id}`, { method: 'DELETE' });
        if (res.ok) {
          await loadHolidaysList();
          await updateMarketStatusPill();
        }
      } catch (e) {
        alert('Error deleting holiday');
      }
    }

    async function updateMarketStatusPill() {
      try {
        const res = await fetch('/api/market/status');
        const data = await res.json();
        const pill = document.getElementById('marketStatusPill');
        if (!pill) return;

        if (data.status === 'HOLIDAY') {
          pill.className = 'px-2.5 py-1.5 rounded-xl text-xs font-semibold flex items-center gap-1.5 bg-amber-950/70 border border-amber-800 text-amber-300';
          pill.innerHTML = `<span class="w-2 h-2 rounded-full bg-amber-400"></span> 🏖️ Market Holiday: ${data.holiday_name || 'Market Off'}`;
        } else if (data.status === 'WEEKEND') {
          pill.className = 'px-2.5 py-1.5 rounded-xl text-xs font-semibold flex items-center gap-1.5 bg-slate-900 border border-slate-800 text-slate-400';
          pill.innerHTML = `<span class="w-2 h-2 rounded-full bg-slate-500"></span> 🏖️ Market Closed (Weekend)`;
        } else if (data.status === 'OPEN') {
          pill.className = 'px-2.5 py-1.5 rounded-xl text-xs font-semibold flex items-center gap-1.5 bg-emerald-950/70 border border-emerald-800 text-emerald-300';
          pill.innerHTML = `<span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span> 🟢 Market Open`;
        } else {
          pill.className = 'px-2.5 py-1.5 rounded-xl text-xs font-semibold flex items-center gap-1.5 bg-slate-900 border border-slate-800 text-slate-400';
          pill.innerHTML = `<span class="w-2 h-2 rounded-full bg-slate-500"></span> ⚪ Market Closed`;
        }
      } catch (e) {
        console.error("Error updating market status:", e);
      }
    }

    // --- SCANNER & LOGS ---
    async function runLiveScan(force = false) {
      const btn = document.getElementById('scanBtn');
      btn.innerText = 'Scanning...';
      const container = document.getElementById('scanContainer');
      container.innerHTML = '<p class="text-xs text-indigo-400 py-3 text-center">Evaluating real-time signals...</p>';
      try {
        const url = `/api/scan?user_id=${selectedUserId || ''}${force ? '&force=true' : ''}`;
        const res = await fetch(url);
        const data = await res.json();

        if (data.market_closed) {
          container.innerHTML = `
            <div class="p-3.5 rounded-xl bg-amber-950/40 border border-amber-800/60 text-center space-y-2 my-2">
              <div class="text-amber-400 font-bold text-xs flex items-center justify-center gap-1.5">
                <span>🏖️</span> Market is Closed
              </div>
              <p class="text-slate-300 text-[11px] leading-relaxed">${data.message}</p>
              <button onclick="runLiveScan(true)" class="mt-2 px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition">
                ⚡ Run Off-Market Test Scan Anyway
              </button>
            </div>
          `;
          return;
        }

        if (!data.results || data.results.length === 0) {
          container.innerHTML = '<p class="text-xs text-slate-400 py-3 text-center">No active stocks configured to scan.</p>';
          return;
        }

        container.innerHTML = `
          <div class="space-y-2">
            ${data.results.map(r => `
              <div class="flex items-center justify-between p-2.5 rounded-lg bg-slate-950 border border-slate-800 text-xs">
                <div>
                  <span class="font-bold text-white">${r.symbol}</span>
                  <span class="text-slate-400 font-mono ml-2">₹${r.ltp.toFixed(2)}</span>
                  <div class="text-[11px] text-slate-400 mt-0.5">${r.reason}</div>
                </div>
                <div class="text-right">
                  <span class="px-2.5 py-0.5 rounded text-[11px] font-bold ${
                    r.action === 'BUY' ? 'bg-emerald-950 text-emerald-400 border border-emerald-800' :
                    r.action === 'SELL' ? 'bg-rose-950 text-rose-400 border border-rose-800' :
                    'bg-slate-800 text-slate-300'
                  }">${r.action}</span>
                  <div class="text-[10px] text-slate-400 font-mono mt-0.5">${r.st_direction || 'ACTIVE'}</div>
                </div>
              </div>
            `).join('')}
          </div>
        `;
      } catch (e) {
        container.innerHTML = `<p class="text-xs text-rose-400">Scan failed: ${e}</p>`;
      } finally {
        btn.innerText = '⚡ Scan Now';
      }
    }

    async function fetchLogs() {
      try {
        const res = await fetch('/api/logs');
        if (res.ok) {
          const data = await res.json();
          document.getElementById('botLogs').innerHTML = data.logs.map(l => `<div>${l}</div>`).join('') || 'No logs yet.';
        }
      } catch (e) {}
    }

    // Start on load
    initApp();
  </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(content=DASHBOARD_HTML)

def start_web_server(host: str = "127.0.0.1", port: int = 8000):
    """Starts the FastAPI Web UI server."""
    print(f"\n=======================================================")
    print(f"🚀 Starting Alpha Trading System Web UI on http://{host}:{port}")
    print(f"=======================================================\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")

if __name__ == "__main__":
    start_web_server()
