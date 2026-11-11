import pandas as pd
import numpy as np
from typing import Dict, Any, Tuple, Optional

class SuperTrendMACDStrategy:
    """
    1-Hour Momentum Swing Strategy:
    - Indicators: SuperTrend (Period=8, Multiplier=3.2), MACD (9, 23, 7)
    - Entry: SuperTrend is Green (Bullish) AND MACD > Signal Line
    - Exit: MACD Crosses below Signal Line OR Stop Loss Hit
    - Stop Loss Options: 'CANDLE_LOW' (Buy Candle Low) or '1_PCT' (Fixed 1% SL)
    """
    def __init__(self, 
                 st_period: int = 8, 
                 st_multiplier: float = 3.2,
                 macd_fast: int = 9, 
                 macd_slow: int = 23, 
                 macd_signal: int = 7):
        self.st_period = st_period
        self.st_multiplier = st_multiplier
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Computes SuperTrend and MACD indicators on the 1-hour dataframe."""
        df = df.copy()
        
        # 1. SuperTrend Calculation
        high = df['High']
        low = df['Low']
        close = df['Close']
        
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1.0/self.st_period, adjust=False).mean()
        
        hl2 = (high + low) / 2.0
        basic_upper = hl2 + (self.st_multiplier * atr)
        basic_lower = hl2 - (self.st_multiplier * atr)
        
        final_upper = basic_upper.copy()
        final_lower = basic_lower.copy()
        
        for i in range(1, len(df)):
            # Upper band
            if basic_upper.iloc[i] < final_upper.iloc[i-1] or close.iloc[i-1] > final_upper.iloc[i-1]:
                final_upper.iloc[i] = basic_upper.iloc[i]
            else:
                final_upper.iloc[i] = final_upper.iloc[i-1]
                
            # Lower band
            if basic_lower.iloc[i] > final_lower.iloc[i-1] or close.iloc[i-1] < final_lower.iloc[i-1]:
                final_lower.iloc[i] = basic_lower.iloc[i]
            else:
                final_lower.iloc[i] = final_lower.iloc[i-1]
                
        st_dir = pd.Series(1, index=df.index)
        st_val = pd.Series(0.0, index=df.index)
        
        for i in range(1, len(df)):
            prev_dir = st_dir.iloc[i-1]
            if prev_dir == 1:
                if close.iloc[i] < final_lower.iloc[i]:
                    st_dir.iloc[i] = -1
                    st_val.iloc[i] = final_upper.iloc[i]
                else:
                    st_dir.iloc[i] = 1
                    st_val.iloc[i] = final_lower.iloc[i]
            else:
                if close.iloc[i] > final_upper.iloc[i]:
                    st_dir.iloc[i] = 1
                    st_val.iloc[i] = final_lower.iloc[i]
                else:
                    st_dir.iloc[i] = -1
                    st_val.iloc[i] = final_upper.iloc[i]
                    
        df['ST_Direction'] = st_dir
        df['SuperTrend'] = st_val
        
        # 2. MACD Calculation
        ema_fast = close.ewm(span=self.macd_fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.macd_slow, adjust=False).mean()
        macd = ema_fast - ema_slow
        signal = macd.ewm(span=self.macd_signal, adjust=False).mean()
        hist = macd - signal
        
        df['MACD'] = macd
        df['Signal'] = signal
        df['MACD_Hist'] = hist
        
        return df

    @staticmethod
    def drop_unclosed_bars(df: pd.DataFrame, now_ist: Optional[pd.Timestamp] = None) -> pd.DataFrame:
        """
        Drops any trailing hourly bar that has not yet closed in IST (Asia/Kolkata).
        Yahoo Finance emits a partial in-progress bar immediately at bar open (e.g. 11:15:00),
        whose Low/Close equal the opening price until it finishes forming at 12:15:00.
        """
        if df is None or df.empty:
            return df

        if now_ist is None:
            now_ist = pd.Timestamp.now(tz='Asia/Kolkata')
        elif now_ist.tzinfo is None:
            now_ist = now_ist.tz_localize('Asia/Kolkata')
        else:
            now_ist = now_ist.tz_convert('Asia/Kolkata')

        df_clean = df.copy()
        if df_clean.index.tz is None:
            df_clean.index = df_clean.index.tz_localize('Asia/Kolkata')
        else:
            df_clean.index = df_clean.index.tz_convert('Asia/Kolkata')

        while not df_clean.empty:
            last_ts = df_clean.index[-1]
            # Standard NSE hourly candle runs 60m; the 15:15 candle runs 15m until 15:30 market close
            duration_mins = 15 if (last_ts.hour == 15 and last_ts.minute == 15) else 60
            bar_close_ts = last_ts + pd.Timedelta(minutes=duration_mins)
            if now_ist < bar_close_ts:
                df_clean = df_clean.iloc[:-1]
            else:
                break
        return df_clean

    def evaluate_signals(self, df: pd.DataFrame, sl_mode: str = "CANDLE_LOW", only_closed: bool = True) -> Dict[str, Any]:
        """
        Evaluates current signal at the latest completed hourly bar.
        Matches the exact backtest entry/exit transitions:
        - BUY: Both SuperTrend & MACD are bullish on latest closed bar AND were NOT both bullish on prior bar.
        - SELL: MACD crossed below Signal Line on latest closed bar.
        """
        df_eval = self.drop_unclosed_bars(df) if only_closed else df
        if df_eval is None or len(df_eval) < 2:
            return {'action': 'HOLD', 'reason': 'Insufficient closed bars', 'current_price': 0.0, 'candle_low': 0.0, 'proposed_sl': 0.0, 'is_st_bullish': False, 'is_macd_bullish': False, 'macd': 0.0, 'signal': 0.0, 'timestamp': ''}

        df_ind = self.compute_indicators(df_eval)
        if len(df_ind) < 2:
            return {'action': 'HOLD', 'reason': 'Insufficient bars', 'current_price': 0.0, 'candle_low': 0.0, 'proposed_sl': 0.0, 'is_st_bullish': False, 'is_macd_bullish': False, 'macd': 0.0, 'signal': 0.0, 'timestamp': ''}
            
        curr = df_ind.iloc[-1]
        prev = df_ind.iloc[-2]
        
        st_bullish = (curr['ST_Direction'] == 1)
        prev_st_bullish = (prev['ST_Direction'] == 1)
        macd_above_signal = (curr['MACD'] > curr['Signal'])
        prev_macd_above_signal = (prev['MACD'] > prev['Signal'])

        macd_cross_down = (prev['MACD'] >= prev['Signal']) and (curr['MACD'] < curr['Signal'])
        macd_cross_up = (prev['MACD'] <= prev['Signal']) and (curr['MACD'] > curr['Signal'])

        curr_both_buy = st_bullish and macd_above_signal
        prev_both_buy = prev_st_bullish and prev_macd_above_signal
        is_fresh_entry = curr_both_buy and (not prev_both_buy)
        
        current_price = round(float(curr['Close']), 2)
        candle_low = round(float(curr['Low']), 2)
        
        if sl_mode.upper() == 'CANDLE_LOW':
            proposed_sl = candle_low
        else: # 1% SL
            proposed_sl = round(current_price * 0.99, 2)
            
        result = {
            'timestamp': curr.name.strftime('%Y-%m-%d %H:%M:%S'),
            'current_price': current_price,
            'candle_low': candle_low,
            'proposed_sl': proposed_sl,
            'st_direction': int(curr['ST_Direction']),
            'is_st_bullish': bool(st_bullish),
            'macd': round(float(curr['MACD']), 2),
            'signal': round(float(curr['Signal']), 2),
            'is_macd_bullish': bool(macd_above_signal),
            'macd_cross_down': bool(macd_cross_down),
            'is_fresh_entry': bool(is_fresh_entry),
            'action': 'HOLD',
            'reason': 'No change in signal'
        }
        
        # 1. Exit Signal
        if macd_cross_down:
            result['action'] = 'SELL'
            result['reason'] = 'Bearish momentum exit signal'
            return result
            
        # 2. Fresh Entry Signal (matches backtest rule: both bullish now AND not both bullish on prior bar)
        if is_fresh_entry:
            result['action'] = 'BUY'
            if macd_cross_up:
                result['reason'] = 'Fresh bullish breakout signal'
            else:
                result['reason'] = 'Fresh bullish trend alignment signal'
            return result

        # 3. Sustained trend (already active from prior candle - do not chase or re-buy post SL)
        if curr_both_buy:
            result['action'] = 'HOLD'
            result['reason'] = 'Bullish trend active (waiting for fresh entry or holding)'
            return result
            
        return result

