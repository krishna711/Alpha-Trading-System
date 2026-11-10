import os
import sys
import warnings

os.environ["PYTHONWARNINGS"] = "ignore"
warnings.simplefilter("ignore")

_orig_showwarning = warnings.showwarning

def _silent_showwarning(message, category, filename, lineno, file=None, line=None):
    cat_name = getattr(category, '__name__', str(category))
    msg_str = str(message)
    # Silence Pandas4Warning, PandasDeprecationWarning, and yfinance inplace warnings
    if any(k in cat_name for k in ['Pandas4Warning', 'Pandas', 'DeprecationWarning', 'FutureWarning', 'UserWarning']):
        return
    if any(k in msg_str for k in ['inplace', 'sort_index', 'PDEP-8', 'deprecated']):
        return
    if 'yfinance' in str(filename):
        return

warnings.showwarning = _silent_showwarning

