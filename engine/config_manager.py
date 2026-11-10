import json
import os
from typing import Dict, Any, Optional

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'trading_config.json')
EXAMPLE_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'trading_config.example.json')

DEFAULT_CONFIG = {
    "broker": {
        "name": "DHAN",
        "client_id": "",
        "access_token": "",
        "trading_mode": "PAPER",
        "product_type": "CNC"
    },
    "strategy": {
        "timeframe": "1h",
        "supertrend_period": 8,
        "supertrend_multiplier": 3.2,
        "macd_fast": 9,
        "macd_slow": 23,
        "macd_signal": 7,
        "default_sl_mode": "CANDLE_LOW"
    },
    "execution": {
        "poll_interval_seconds": 60,
        "check_stoploss_intraday": True,
        "market_open_time": "09:15",
        "market_close_time": "15:30",
        "candle_close_minutes": [15]
    },
    "stocks": {},
    "auth": {
        "auto_login_enabled": False,
        "auto_login_time": "09:00",
        "client_id": "",
        "pin": "",
        "totp_secret": "",
        "api_key": "",
        "api_secret": ""
    }
}

class ConfigManager:
    def __init__(self, config_path: str = CONFIG_PATH):
        self.config_path = config_path
        self._last_mtime = 0
        self._cached_config = {}
        self.load()

    def load(self) -> Dict[str, Any]:
        """Loads configuration from JSON file with auto-reload if modified, initializing defaults if missing."""
        if not os.path.exists(self.config_path):
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            if os.path.exists(EXAMPLE_CONFIG_PATH):
                with open(EXAMPLE_CONFIG_PATH, 'r', encoding='utf-8') as ef:
                    self._cached_config = json.load(ef)
            else:
                self._cached_config = DEFAULT_CONFIG.copy()
            self.save()
        
        mtime = os.path.getmtime(self.config_path)
        if mtime != self._last_mtime:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self._cached_config = json.load(f)
            self._last_mtime = mtime
        return self._cached_config

    def save(self):
        """Saves current cached configuration back to JSON file."""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(self._cached_config, f, indent=2)
        self._last_mtime = os.path.getmtime(self.config_path)

    @property
    def broker_config(self) -> Dict[str, Any]:
        return self.load().get('broker', {})

    @property
    def strategy_config(self) -> Dict[str, Any]:
        return self.load().get('strategy', {})

    @property
    def execution_config(self) -> Dict[str, Any]:
        return self.load().get('execution', {})

    @property
    def stocks(self) -> Dict[str, Any]:
        """Returns all configured stocks."""
        return self.load().get('stocks', {})

    def get_active_stocks(self) -> Dict[str, Any]:
        """Returns only enabled stocks."""
        return {sym: cfg for sym, cfg in self.stocks.items() if cfg.get('enabled', True)}

    def get_stock_capital(self, symbol: str) -> float:
        """Returns the manually allocated capital for a specific stock."""
        stock_cfg = self.stocks.get(symbol.upper(), {})
        return float(stock_cfg.get('allocated_capital', 100000.0))

    def update_stock_capital(self, symbol: str, new_capital: float) -> bool:
        """Updates the manually allocated capital for a stock and saves to disk."""
        sym = symbol.upper()
        conf = self.load()
        if sym in conf.get('stocks', {}):
            conf['stocks'][sym]['allocated_capital'] = float(new_capital)
            self._cached_config = conf
            self.save()
            return True
        return False

    def add_or_update_stock(self, symbol: str, allocated_capital: float, 
                            sl_mode: str = "CANDLE_LOW", exchange: str = "NSE", 
                            enabled: bool = True, notes: str = "") -> None:
        """Adds a new stock or updates an existing one in the configuration."""
        sym = symbol.upper()
        conf = self.load()
        if 'stocks' not in conf:
            conf['stocks'] = {}
        
        conf['stocks'][sym] = {
            "symbol": sym,
            "exchange": exchange,
            "allocated_capital": float(allocated_capital),
            "sl_mode": sl_mode,
            "enabled": enabled,
            "notes": notes
        }
        self._cached_config = conf
        self.save()

    def toggle_stock_enabled(self, symbol: str) -> bool:
        """Toggles a stock's enabled status between True and False without removing it."""
        sym = symbol.upper()
        conf = self.load()
        if sym in conf.get('stocks', {}):
            curr_state = conf['stocks'][sym].get('enabled', True)
            new_state = not curr_state
            conf['stocks'][sym]['enabled'] = new_state
            self._cached_config = conf
            self.save()
            return new_state
        return False

    def set_stock_enabled(self, symbol: str, enabled: bool) -> bool:
        """Sets a stock's enabled status explicitly."""
        sym = symbol.upper()
        conf = self.load()
        if sym in conf.get('stocks', {}):
            conf['stocks'][sym]['enabled'] = bool(enabled)
            self._cached_config = conf
            self.save()
            return True
        return False

    def remove_stock(self, symbol: str) -> bool:
        """Removes a stock from configuration."""
        sym = symbol.upper()
        conf = self.load()
        if sym in conf.get('stocks', {}):
            del conf['stocks'][sym]
            self._cached_config = conf
            self.save()
            return True
        return False

