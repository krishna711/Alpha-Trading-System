import os
import json
import pandas as pd
from datetime import datetime
from typing import Dict, Any, Optional, List

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'state')
POSITIONS_FILE = os.path.join(STATE_DIR, 'positions.json')
TRADE_HISTORY_FILE = os.path.join(STATE_DIR, 'trade_history.csv')

class PositionManager:
    """
    Manages active trading positions, stop losses, and trade history.
    Persists state across runs.
    """
    def __init__(self):
        os.makedirs(STATE_DIR, exist_ok=True)
        self.positions: Dict[str, Dict[str, Any]] = self._load_positions()

    def _load_positions(self) -> Dict[str, Dict[str, Any]]:
        if os.path.exists(POSITIONS_FILE):
            try:
                with open(POSITIONS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"[PositionManager] Error loading positions: {e}")
        return {}

    def _save_positions(self):
        with open(POSITIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.positions, f, indent=2)

    def is_in_position(self, symbol: str) -> bool:
        sym = symbol.upper()
        pos = self.positions.get(sym)
        return pos is not None and pos.get('status') == 'OPEN'

    def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        sym = symbol.upper()
        return self.positions.get(sym)

    def open_position(self, symbol: str, entry_price: float, shares: int, 
                      allocated_capital: float, stop_loss_price: float, 
                      sl_mode: str, entry_bar_time: str = "", order_id: str = "") -> Dict[str, Any]:
        """Records a newly opened position."""
        sym = symbol.upper()
        pos_data = {
            'symbol': sym,
            'status': 'OPEN',
            'entry_price': float(entry_price),
            'entry_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'entry_bar_time': str(entry_bar_time),
            'shares': int(shares),
            'allocated_capital': float(allocated_capital),
            'invested_amount': round(shares * entry_price, 2),
            'stop_loss_price': float(stop_loss_price),
            'sl_mode': sl_mode,
            'order_id': order_id,
            'highest_price_seen': float(entry_price)
        }
        self.positions[sym] = pos_data
        self._save_positions()
        return pos_data

    def close_position(self, symbol: str, exit_price: float, exit_reason: str, exit_order_id: str = "") -> Optional[Dict[str, Any]]:
        """Closes an open position, logs trade to CSV, and removes from active positions."""
        sym = symbol.upper()
        pos = self.positions.get(sym)
        if not pos or pos.get('status') != 'OPEN':
            return None

        entry_price = pos['entry_price']
        shares = pos['shares']
        pnl = (exit_price - entry_price) * shares
        pnl_pct = (exit_price / entry_price - 1.0) * 100.0
        exit_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        trade_record = {
            'Symbol': sym,
            'Entry_Time': pos['entry_time'],
            'Entry_Price': entry_price,
            'Exit_Time': exit_time,
            'Exit_Price': exit_price,
            'Shares': shares,
            'Invested_Amount': pos.get('invested_amount', shares * entry_price),
            'Allocated_Capital': pos.get('allocated_capital', 0.0),
            'SL_Price': pos['stop_loss_price'],
            'SL_Mode': pos['sl_mode'],
            'PnL': round(pnl, 2),
            'PnL_%': round(pnl_pct, 2),
            'Exit_Reason': exit_reason,
            'Entry_Order_ID': pos.get('order_id', ''),
            'Exit_Order_ID': exit_order_id
        }

        # Append to CSV
        trade_df = pd.DataFrame([trade_record])
        if os.path.exists(TRADE_HISTORY_FILE):
            trade_df.to_csv(TRADE_HISTORY_FILE, mode='a', header=False, index=False)
        else:
            trade_df.to_csv(TRADE_HISTORY_FILE, mode='w', header=True, index=False)

        # Mark closed in memory
        del self.positions[sym]
        self._save_positions()
        return trade_record

    def check_stop_loss_hit(self, symbol: str, current_low: float) -> Tuple[bool, Optional[float]]:
        """Checks if current price or candle low crossed below the stop loss price."""
        sym = symbol.upper()
        pos = self.positions.get(sym)
        if pos and pos.get('status') == 'OPEN':
            sl = pos.get('stop_loss_price', 0.0)
            if current_low <= sl:
                return True, sl
        return False, None

    def get_all_open_positions(self) -> List[Dict[str, Any]]:
        return [p for p in self.positions.values() if p.get('status') == 'OPEN']
