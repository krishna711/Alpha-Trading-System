import os
import re
import json
import time
import base64
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List

try:
    import pyotp
except ImportError:
    pyotp = None

from .config_manager import ConfigManager
from .user_manager import UserManager

LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs', 'trading_bot.log')
IST_OFFSET = timezone(timedelta(hours=5, minutes=30))

def _log_auth_event(msg: str):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass

def _decode_jwt_expiry(token: str) -> Optional[datetime]:
    """Extracts JWT 'exp' claim (as IST naive datetime) without external dependencies."""
    if not token or token.count('.') != 2:
        return None
    try:
        payload_b64 = token.split('.')[1]
        padding = '=' * (-len(payload_b64) % 4)
        decoded_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
        payload = json.loads(decoded_bytes.decode('utf-8'))
        exp = payload.get('exp')
        if exp:
            return datetime.fromtimestamp(float(exp), tz=IST_OFFSET).replace(tzinfo=None)
    except Exception:
        pass
    return None

class DhanAuthManager:
    """
    Automated Dhan Authentication Manager (Multi-User Aware):
    - Generates live TOTP codes from user's TOTP secret key
    - Performs automated daily login at 09:00 AM using PIN + TOTP + API Keys per user
    - Tracks access token generation & JWT expiry status per user
    - Saves fresh access token to user's database configuration
    - Supports Enable/Disable toggle per user
    """
    def __init__(self, config_mgr: Optional[ConfigManager] = None):
        self.config_mgr = config_mgr or ConfigManager()

    def get_auth_config(self, user_id: int = 1) -> Dict[str, Any]:
        """Returns auth settings for a specific user from DB."""
        cfg = UserManager.get_user_broker_config(user_id)
        if not cfg:
            conf = self.config_mgr.load()
            return conf.get('auth', {})
        return cfg

    @property
    def auth_config(self) -> Dict[str, Any]:
        return self.get_auth_config(user_id=1)

    def get_token_status(self, user_id: int = 1) -> Dict[str, Any]:
        """
        Inspects the user's current Dhan access token and login state.
        Returns a structured dictionary indicating whether token is generated & active today.
        """
        cfg = self.get_auth_config(user_id)
        token = (cfg.get('access_token') or '').strip()
        last_time = cfg.get('last_login_time') or ''
        last_status = cfg.get('last_login_status') or 'NOT_CONFIGURED'
        last_msg = cfg.get('last_login_message') or ''
        today_str = datetime.now().strftime('%Y-%m-%d')

        has_token = bool(token)
        exp_dt = _decode_jwt_expiry(token) if has_token else None
        now_dt = datetime.now()

        if exp_dt is not None:
            is_expired = now_dt >= exp_dt
            expires_at_str = exp_dt.strftime('%Y-%m-%d %H:%M:%S')
        else:
            is_expired = False
            expires_at_str = ''

        logged_in_today = bool(last_time.startswith(today_str) and last_status == 'SUCCESS')
        # Token is considered active if present, not expired by JWT exp, and not failed on last attempt
        is_logged_in = has_token and (not is_expired) and (last_status == 'SUCCESS' or exp_dt is not None)

        token_preview = f"{token[:6]}...{token[-6:]}" if len(token) > 14 else ("***" if has_token else "")

        if is_logged_in:
            if logged_in_today:
                label = f"Token Active (Logged in today at {last_time.split(' ')[-1]})"
            else:
                label = "Token Active (Logged in to Dhan)"
        elif has_token and is_expired:
            label = f"Token Expired (Expired at {expires_at_str}) — Re-Login Needed"
        elif last_status == 'FAILED':
            label = "Login Failed — Check Credentials"
        else:
            label = "Not Logged In (No Token Generated)"

        return {
            'has_token': has_token,
            'is_logged_in': is_logged_in,
            'is_expired': is_expired,
            'logged_in_today': logged_in_today,
            'expires_at': expires_at_str,
            'token_preview': token_preview,
            'last_login_time': last_time or 'Never',
            'last_login_status': last_status,
            'last_login_message': last_msg,
            'label': label
        }

    def update_auth_config(self, user_id: int = 1, **kwargs) -> Dict[str, Any]:
        """Updates authentication settings for a user."""
        res = UserManager.update_user_broker_config(user_id, **kwargs)
        if user_id == 1:
            conf = self.config_mgr.load()
            if 'auth' not in conf:
                conf['auth'] = {}
            for k, v in kwargs.items():
                conf['auth'][k] = v
                if k == 'dhan_client_id':
                    conf['broker']['client_id'] = v
                    conf['auth']['client_id'] = v
            self.config_mgr._cached_config = conf
            self.config_mgr.save()
        return res

    def generate_current_totp(self, user_id: int = 1) -> Optional[str]:
        """Generates the current 6-digit TOTP code using the user's secret key."""
        auth_cfg = self.get_auth_config(user_id)
        raw_secret = auth_cfg.get('totp_secret') or ''
        secret = raw_secret.replace(' ', '').replace('-', '').upper().strip()
        if not secret:
            return None
        if pyotp is None:
            raise ImportError("pyotp package is required to generate TOTP. Run `pip install pyotp`.")
        try:
            totp = pyotp.TOTP(secret)
            return totp.now()
        except Exception as e:
            _log_auth_event(f"[DhanAuth User {user_id}] Error generating TOTP: {e}")
            return None

    def execute_login(self, user_id: int = 1, source: str = "MANUAL") -> Dict[str, Any]:
        """
        Executes automated login to Dhan for a specific user:
        1. Generates fresh TOTP from user's TOTP Secret
        2. Submits PIN + TOTP + Client ID to Dhan Auth API
        3. Extracts Access Token and updates user configuration
        """
        user_info = UserManager.get_user_by_id(user_id)
        uname = user_info['username'] if user_info else f"id={user_id}"

        auth_cfg = self.get_auth_config(user_id)
        client_id = (auth_cfg.get('dhan_client_id') or auth_cfg.get('client_id') or '').strip()
        pin = str(auth_cfg.get('dhan_pin') or auth_cfg.get('pin') or '').strip()
        totp_secret = (auth_cfg.get('totp_secret') or '').strip()

        if not client_id or not pin or not totp_secret:
            msg = "Missing credentials: Dhan Client ID, PIN, and TOTP Secret must be configured."
            self._update_login_status(user_id, "FAILED", msg)
            _log_auth_event(f"❌ Dhan {source} Login FAILED for User '{uname}': {msg}")
            return {'status': 'FAILED', 'message': msg}

        totp_code = self.generate_current_totp(user_id)
        if not totp_code:
            msg = "Failed to generate TOTP code. Verify totp_secret format."
            self._update_login_status(user_id, "FAILED", msg)
            _log_auth_event(f"❌ Dhan {source} Login FAILED for User '{uname}': {msg}")
            return {'status': 'FAILED', 'message': msg}

        _log_auth_event(f"🔑 Dhan {source} Login Triggered for User '{uname}' (Client ID: {client_id})...")

        # 1. Try DhanHQ SDK DhanLogin
        try:
            from dhanhq import DhanLogin
            dl = DhanLogin(client_id)
            resp = dl.generate_token(pin=pin, totp=totp_code)
            print(f"[DhanAuth User {user_id}] DhanLogin response: {resp}")
            
            token = None
            if isinstance(resp, dict):
                token = resp.get('accessToken') or resp.get('access_token') or resp.get('token')
                if not token and resp.get('status') == 'success' and 'data' in resp:
                    token = resp['data'].get('accessToken')

            if token:
                self._save_token(user_id, token)
                exp_dt = _decode_jwt_expiry(token)
                exp_str = exp_dt.strftime('%Y-%m-%d %H:%M:%S') if exp_dt else "24h"
                msg = f"Logged in ({source}). Access token generated & active (Expires: {exp_str})."
                self._update_login_status(user_id, "SUCCESS", msg)
                _log_auth_event(f"✅ Dhan Token Generated ({source}) for User '{uname}' | Active until {exp_str}")
                return {'status': 'SUCCESS', 'access_token': token, 'expires_at': exp_str, 'message': msg}
            else:
                err_msg = resp.get('remarks') or resp.get('message') or str(resp)
        except Exception as e:
            err_msg = str(e)
            print(f"[DhanAuth User {user_id}] DhanHQ SDK login failed: {e}. Trying direct REST API...")

        # 2. Fallback: Direct REST API call
        try:
            url = "https://auth.dhan.co/app/generateAccessToken"
            params = {
                "dhanClientId": client_id,
                "pin": pin,
                "totp": totp_code
            }
            headers = {"Content-Type": "application/json"}
            
            if auth_cfg.get('api_key'):
                headers['app_id'] = auth_cfg.get('api_key')
            if auth_cfg.get('api_secret'):
                headers['app_secret'] = auth_cfg.get('api_secret')

            r = requests.post(url, params=params, headers=headers, timeout=15)
            try:
                data = r.json()
            except Exception:
                data = {'raw': r.text}

            token = data.get('accessToken') or data.get('access_token') or data.get('token')
            if token:
                self._save_token(user_id, token)
                exp_dt = _decode_jwt_expiry(token)
                exp_str = exp_dt.strftime('%Y-%m-%d %H:%M:%S') if exp_dt else "24h"
                msg = f"Logged in ({source} REST). Access token generated & active (Expires: {exp_str})."
                self._update_login_status(user_id, "SUCCESS", msg)
                _log_auth_event(f"✅ Dhan Token Generated ({source}) for User '{uname}' | Active until {exp_str}")
                return {'status': 'SUCCESS', 'access_token': token, 'expires_at': exp_str, 'message': msg}
            else:
                fail_msg = data.get('remarks') or data.get('message') or f"HTTP {r.status_code}: {r.text}"
                self._update_login_status(user_id, "FAILED", fail_msg)
                _log_auth_event(f"❌ Dhan {source} Login FAILED for User '{uname}': {fail_msg}")
                return {'status': 'FAILED', 'message': fail_msg, 'response': data}

        except Exception as e:
            msg = f"Network or API error during Dhan login: {e}"
            self._update_login_status(user_id, "FAILED", msg)
            _log_auth_event(f"❌ Dhan {source} Login FAILED for User '{uname}': {msg}")
            return {'status': 'FAILED', 'message': msg}

    def _save_token(self, user_id: int, token: str):
        """Saves generated token into user config."""
        UserManager.update_user_broker_config(user_id, access_token=token)
        if user_id == 1:
            conf = self.config_mgr.load()
            conf['broker']['access_token'] = token
            self.config_mgr._cached_config = conf
            self.config_mgr.save()
        print(f"[DhanAuth User {user_id}] ✅ Successfully updated access_token")

    def _update_login_status(self, user_id: int, status: str, message: str):
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        UserManager.update_user_broker_config(
            user_id,
            last_login_time=now_str,
            last_login_status=status,
            last_login_message=message
        )
        if user_id == 1:
            conf = self.config_mgr.load()
            if 'auth' not in conf:
                conf['auth'] = {}
            conf['auth']['last_login_time'] = now_str
            conf['auth']['last_login_status'] = status
            conf['auth']['last_login_message'] = message
            self.config_mgr._cached_config = conf
            self.config_mgr.save()

    def check_and_trigger_all_auto_logins(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """
        Called every minute by bot daemon.
        Iterates over all users who have enabled auto-login and triggers login at scheduled time.
        """
        now = now or datetime.now()
        if now.weekday() > 4:
            return []

        results = []
        users = UserManager.get_all_users()
        today_str = now.strftime('%Y-%m-%d')

        for u in users:
            uid = u['id']
            auth_cfg = self.get_auth_config(uid)
            if not auth_cfg.get('auto_login_enabled'):
                continue

            login_time_str = auth_cfg.get('auto_login_time', '09:00')
            try:
                target_h, target_m = map(int, login_time_str.split(':'))
            except Exception:
                target_h, target_m = 9, 0

            last_login = auth_cfg.get('last_login_time', '')
            if last_login.startswith(today_str) and auth_cfg.get('last_login_status') == 'SUCCESS':
                continue # Already logged in today

            target_dt = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
            diff_sec = (now - target_dt).total_seconds()

            if 0 <= diff_sec <= 600:
                res = self.execute_login(uid, source="AUTO")
                results.append({'user_id': uid, 'username': u['username'], 'result': res})

        return results

    def check_and_trigger_auto_login(self, now: Optional[datetime] = None) -> bool:
        """Backward compatible check for user 1."""
        res = self.check_and_trigger_all_auto_logins(now)
        return any(r['result'].get('status') == 'SUCCESS' for r in res)
