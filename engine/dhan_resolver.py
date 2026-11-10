import os
import time
import requests
import pandas as pd
from typing import Optional, Dict

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'cache')
CACHE_FILE = os.path.join(CACHE_DIR, 'dhan_scrip_master.csv')
DHAN_SCRIP_URL = 'https://images.dhan.co/api-data/api-scrip-master.csv'

class DhanResolver:
    _instance = None
    _df_nse = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DhanResolver, cls).__new__(cls)
            cls._instance._init_data()
        return cls._instance

    def _init_data(self):
        os.makedirs(CACHE_DIR, exist_ok=True)
        need_download = True
        
        if os.path.exists(CACHE_FILE):
            # Check file age (24 hours = 86400 seconds)
            file_age = time.time() - os.path.getmtime(CACHE_FILE)
            if file_age < 86400:
                need_download = False
        
        if need_download:
            try:
                print(f"[DhanResolver] Downloading latest scrip master from Dhan...")
                r = requests.get(DHAN_SCRIP_URL, timeout=30)
                if r.status_code == 200:
                    with open(CACHE_FILE, 'wb') as f:
                        f.write(r.content)
                    print(f"[DhanResolver] Saved {len(r.content)} bytes to {CACHE_FILE}")
            except Exception as e:
                print(f"[DhanResolver] Warning: Could not download scrip master: {e}")
        
        if os.path.exists(CACHE_FILE):
            try:
                df = pd.read_csv(CACHE_FILE, low_memory=False)
                # Filter NSE Equities
                self._df_nse = df[(df['SEM_EXM_EXCH_ID'] == 'NSE') & 
                                  (df['SEM_INSTRUMENT_NAME'] == 'EQUITY')].copy()
                # Build lookup dict
                self._symbol_map = dict(zip(self._df_nse['SEM_TRADING_SYMBOL'].str.upper(), 
                                            self._df_nse['SEM_SMST_SECURITY_ID'].astype(str)))
                print(f"[DhanResolver] Loaded {len(self._symbol_map)} NSE Equity instruments.")
            except Exception as e:
                print(f"[DhanResolver] Error reading scrip cache: {e}")
                self._symbol_map = {}
        else:
            self._symbol_map = {}

    def get_security_id(self, symbol: str) -> Optional[str]:
        """Resolves symbol (e.g. 'BSE', 'MCX') to Dhan NSE security_id."""
        sym = symbol.upper().replace('.NS', '').strip()
        sec_id = self._symbol_map.get(sym)
        if sec_id:
            return sec_id
        
        # Fallback for common aliases or dashes
        sym_clean = sym.replace('-', '').replace('&', '')
        for k, v in self._symbol_map.items():
            if k.replace('-', '').replace('&', '') == sym_clean:
                return v
        return None

    def get_instrument_details(self, symbol: str) -> Optional[Dict[str, str]]:
        """Returns details such as tick size, lot size, trading symbol."""
        sym = symbol.upper().replace('.NS', '').strip()
        if self._df_nse is not None:
            match = self._df_nse[self._df_nse['SEM_TRADING_SYMBOL'] == sym]
            if not match.empty:
                row = match.iloc[0]
                return {
                    'security_id': str(row['SEM_SMST_SECURITY_ID']),
                    'trading_symbol': str(row['SEM_TRADING_SYMBOL']),
                    'tick_size': float(row['SEM_TICK_SIZE']) if pd.notna(row['SEM_TICK_SIZE']) else 0.05,
                    'lot_size': int(row['SEM_LOT_UNITS']) if pd.notna(row['SEM_LOT_UNITS']) else 1
                }
        return None

