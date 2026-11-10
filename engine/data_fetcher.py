import os
import sys
from . import silence_warnings

import yfinance as yf
import pandas as pd
import numpy as np
from typing import Optional, Tuple

class FreeDataFetcher:
    """
    Fetches free 1-Hour candles for Indian NSE equities using Yahoo Finance.
    Zero subscription, zero API key required, updated in real-time during market hours.
    """
    def __init__(self):
        pass

    @staticmethod
    def format_symbol(symbol: str) -> str:
        sym = symbol.strip().upper()
        if not sym.endswith('.NS') and not sym.endswith('.BO'):
            return f"{sym}.NS"
        return sym

    def fetch_1h_data(self, symbol: str, period: str = '30d') -> Optional[pd.DataFrame]:
        """
        Fetches 1-hour candles for the given stock.
        Returns a DataFrame with columns: ['Open', 'High', 'Low', 'Close', 'Volume']
        Indexed by Datetime (IST).
        """
        ticker = self.format_symbol(symbol)
        try:
            df = yf.download(ticker, period=period, interval='1h', progress=False)
            if df.empty:
                return None
            
            # Handle MultiIndex columns from newer yfinance versions
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
            df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
            
            # Ensure timezone is IST (UTC+5:30)
            if df.index.tz is None:
                df.index = df.index.tz_localize('Asia/Kolkata')
            else:
                df.index = df.index.tz_convert('Asia/Kolkata')
                
            return df
        except Exception as e:
            print(f"[FreeDataFetcher] Error fetching data for {symbol}: {e}")
            return None

    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Fetches the latest trading price (LTP) from the most recent 1h or 1m bar."""
        ticker = self.format_symbol(symbol)
        try:
            t = yf.Ticker(ticker)
            fast_info = getattr(t, 'fast_info', None)
            if fast_info and 'last_price' in fast_info and fast_info['last_price'] is not None:
                return float(fast_info['last_price'])
            
            df = yf.download(ticker, period='1d', interval='1m', progress=False)
            if not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                return float(df['Close'].iloc[-1])
        except Exception:
            pass
        return None
