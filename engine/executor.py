import time
import pandas as pd
from datetime import datetime
from typing import Dict, Any, List, Optional

from .config_manager import ConfigManager
from .data_fetcher import FreeDataFetcher
from .strategy import SuperTrendMACDStrategy
from .position_manager import PositionManager
from .dhan_client import DhanBrokerClient
from .user_manager import UserManager

class TradingExecutor:
    """
    Multi-User Core Execution Engine:
    - Ingests free 1-hour candles (with smart caching per cycle)
    - Evaluates SuperTrend (8, 3.2) + MACD (9, 23, 7)
    - Executes independent positions, capital, and stop-losses per user
    - Routes orders to each user's respective Dhan credentials
    """
    def __init__(self):
        self.config_mgr = ConfigManager()
        self.data_fetcher = FreeDataFetcher()
        self.pos_mgr = PositionManager()
        
        strat_cfg = self.config_mgr.strategy_config
        self.strategy = SuperTrendMACDStrategy(
            st_period=strat_cfg.get('supertrend_period', 8),
            st_multiplier=strat_cfg.get('supertrend_multiplier', 3.2),
            macd_fast=strat_cfg.get('macd_fast', 9),
            macd_slow=strat_cfg.get('macd_slow', 23),
            macd_signal=strat_cfg.get('macd_signal', 7)
        )
        self._data_cache: Dict[str, pd.DataFrame] = {}

    def get_user_broker_client(self, user_id: int) -> DhanBrokerClient:
        """Instantiates a DhanBrokerClient configured with the user's specific credentials."""
        b_cfg = UserManager.get_user_broker_config(user_id)
        return DhanBrokerClient(
            client_id=b_cfg.get('dhan_client_id') or '',
            access_token=b_cfg.get('access_token') or '',
            mode=b_cfg.get('trading_mode', 'PAPER'),
            product_type=b_cfg.get('product_type', 'CNC')
        )

    def fetch_candle_cached(self, symbol: str) -> Optional[pd.DataFrame]:
        """Fetches 1-hour candles with caching so multiple users sharing a stock share 1 network request."""
        sym = symbol.upper().strip()
        if sym in self._data_cache:
            return self._data_cache[sym]
        df = self.data_fetcher.fetch_1h_data(sym, period='30d')
        if df is not None:
            self._data_cache[sym] = df
        return df

    def run_cycle_for_user(self, user_id: int) -> List[Dict[str, Any]]:
        """Executes a full evaluation and order management cycle for a single user."""
        user = UserManager.get_user_by_id(user_id)
        if not user or not user['is_active']:
            return []

        broker = self.get_user_broker_client(user_id)
        stocks = UserManager.get_user_stocks(user_id, only_enabled=True)
        open_positions = {p['symbol']: p for p in UserManager.get_user_open_positions(user_id)}
        
        cycle_results = []
        user_total_unrealized = 0.0

        for s in stocks:
            sym = s['symbol']
            allocated_cap = float(s['allocated_capital'])
            sl_mode = s.get('sl_mode', 'CANDLE_LOW')
            pos_info = open_positions.get(sym)
            in_pos = pos_info is not None

            # 1. Fetch 1h candles
            df_1h = self.fetch_candle_cached(sym)
            if df_1h is None or len(df_1h) < 10:
                cycle_results.append({'symbol': sym, 'action': 'ERROR', 'reason': 'Data fetch failed'})
                continue

            # 2. Evaluate strategy
            signal_eval = self.strategy.evaluate_signals(df_1h, sl_mode=sl_mode)
            current_price = signal_eval['current_price']
            candle_low = signal_eval['candle_low']
            action = signal_eval['action']
            reason = signal_eval['reason']

            summary = {
                'user_id': user_id,
                'symbol': sym,
                'current_price': current_price,
                'st_direction': 'BULLISH' if signal_eval['is_st_bullish'] else 'BEARISH',
                'macd': signal_eval['macd'],
                'signal': signal_eval['signal'],
                'action': action,
                'in_position': in_pos,
                'action_taken': 'NONE'
            }

            # 3. Position Management (If holding position)
            if in_pos and pos_info:
                entry_price = pos_info['entry_price']
                shares = pos_info['shares']
                sl_price = pos_info['stop_loss_price']
                current_pnl = (current_price - entry_price) * shares
                user_total_unrealized += current_pnl

                # Check Stop Loss
                is_entry_bar = (pos_info.get('entry_bar_time') == signal_eval['timestamp'])
                sl_hit = False
                if not is_entry_bar and candle_low <= sl_price:
                    sl_hit = True
                elif current_price < sl_price:
                    sl_hit = True

                if sl_hit:
                    print(f"[User {user['username']} | {sym}] ⚠️ STOP LOSS HIT! Price: {current_price:.2f} <= SL: {sl_price:.2f}")
                    order_resp = broker.place_order(sym, 'SELL', shares, price=current_price)
                    if order_resp.get('status') == 'SUCCESS':
                        trade = UserManager.close_user_position(
                            user_id=user_id,
                            symbol=sym,
                            exit_price=current_price,
                            exit_reason=f'SL_HIT_{sl_mode}',
                            exit_order_id=order_resp.get('order_id', '')
                        )
                        summary['action_taken'] = f"SL HIT -> SOLD {shares} @ {current_price:.2f}"
                    cycle_results.append(summary)
                    continue

                # Check MACD Exit
                if action == 'SELL' and not is_entry_bar:
                    print(f"[User {user['username']} | {sym}] 🔴 MACD Bearish Cross Exit! Selling {shares} @ {current_price:.2f}")
                    order_resp = broker.place_order(sym, 'SELL', shares, price=current_price)
                    if order_resp.get('status') == 'SUCCESS':
                        trade = UserManager.close_user_position(
                            user_id=user_id,
                            symbol=sym,
                            exit_price=current_price,
                            exit_reason='MACD_CROSS_DOWN',
                            exit_order_id=order_resp.get('order_id', '')
                        )
                        summary['action_taken'] = f"MACD EXIT -> SOLD {shares} @ {current_price:.2f}"
                    cycle_results.append(summary)
                    continue

                summary['action_taken'] = f"HOLDING {shares} @ {entry_price:.2f} | PnL: Rs. {current_pnl:.2f}"
                cycle_results.append(summary)
                continue

            # 4. Entry Management (If flat)
            if not in_pos:
                if action == 'BUY':
                    shares = int(allocated_cap // current_price)
                    if shares <= 0:
                        summary['action_taken'] = "INSUFFICIENT_CAPITAL"
                        cycle_results.append(summary)
                        continue

                    proposed_sl = signal_eval['proposed_sl']
                    print(f"[User {user['username']} | {sym}] 🟢 BUY SIGNAL! Shares: {shares} @ Rs. {current_price:.2f} | SL: {proposed_sl:.2f}")
                    order_resp = broker.place_order(sym, 'BUY', shares, price=current_price)
                    if order_resp.get('status') == 'SUCCESS':
                        UserManager.open_user_position(
                            user_id=user_id,
                            symbol=sym,
                            entry_price=current_price,
                            shares=shares,
                            allocated_capital=allocated_cap,
                            stop_loss_price=proposed_sl,
                            sl_mode=sl_mode,
                            entry_bar_time=signal_eval['timestamp'],
                            order_id=order_resp.get('order_id', '')
                        )
                        summary['action_taken'] = f"BOUGHT {shares} @ {current_price:.2f} | SL: {proposed_sl:.2f}"
                else:
                    summary['action_taken'] = f"FLAT ({reason})"

            cycle_results.append(summary)

        # Record PnL snapshot for graph
        UserManager.record_pnl_snapshot(user_id, unrealized_pnl=user_total_unrealized)
        return cycle_results

    def run_cycle_all_users(self) -> Dict[str, Any]:
        """Runs strategy execution across all active users in the system."""
        self._data_cache.clear() # Reset cache for fresh hourly cycle
        users = UserManager.get_all_users()
        total_executed = 0
        all_results = {}

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print("\n" + "=" * 90)
        print(f"[{now_str}] MULTI-USER STRATEGY CYCLE | Active Users: {len([u for u in users if u['is_active']])}")
        print("=" * 90)

        for u in users:
            if not u['is_active']:
                continue
            uid = u['id']
            res = self.run_cycle_for_user(uid)
            all_results[u['username']] = res
            total_executed += len(res)

        print("-" * 90)
        return {'status': 'SUCCESS', 'users_evaluated': len(all_results), 'results': all_results}

    def run_cycle(self) -> List[Dict[str, Any]]:
        """Backward compatible execution method."""
        res_dict = self.run_cycle_all_users()
        # Flatten for legacy callers
        flattened = []
        for r_list in res_dict.get('results', {}).values():
            flattened.extend(r_list)
        return flattened
