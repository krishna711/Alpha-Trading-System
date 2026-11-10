import uuid
import requests
from typing import Dict, Any, Optional
from .dhan_resolver import DhanResolver

class DhanBrokerClient:
    """
    Dhan Broker Client supporting both PAPER and LIVE execution.
    - Resolves Dhan Security IDs automatically.
    - Handles order routing and position queries.
    """
    def __init__(self, client_id: str, access_token: str, mode: str = "PAPER", product_type: str = "CNC"):
        self.client_id = client_id
        self.access_token = access_token
        self.mode = mode.upper() # 'PAPER' or 'LIVE'
        self.product_type = product_type.upper() # 'CNC' or 'INTRADAY'
        self.resolver = DhanResolver()
        self._dhan_sdk = None
        
        if self.mode == "LIVE":
            self._init_live_sdk()

    def _init_live_sdk(self):
        try:
            from dhanhq import dhanhq
            self._dhan_sdk = dhanhq(self.client_id, self.access_token)
            print("[DhanClient] Initialized DhanHQ SDK for LIVE trading.")
        except Exception as e:
            print(f"[DhanClient] Warning: Failed to initialize DhanHQ SDK: {e}. Will use direct REST API.")

    def test_connection(self) -> Dict[str, Any]:
        """Tests API authentication with Dhan."""
        if self.mode == "PAPER":
            return {'status': 'SUCCESS', 'message': 'Paper Trading Mode Active (Virtual Execution)'}
            
        url = "https://api.dhan.co/v2/fundlimit"
        headers = {
            "access-token": self.access_token,
            "client-id": self.client_id,
            "Content-Type": "application/json"
        }
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                return {'status': 'SUCCESS', 'data': data, 'message': 'Dhan API Connection Verified'}
            else:
                return {'status': 'ERROR', 'code': r.status_code, 'message': r.text}
        except Exception as e:
            return {'status': 'ERROR', 'message': str(e)}

    def place_order(self, symbol: str, transaction_type: str, quantity: int, price: float = 0.0) -> Dict[str, Any]:
        """
        Places order in Dhan (or simulates in PAPER mode).
        transaction_type: 'BUY' or 'SELL'
        """
        symbol_clean = symbol.upper().replace('.NS', '')
        sec_id = self.resolver.get_security_id(symbol_clean)
        
        if not sec_id:
            return {
                'status': 'FAILED',
                'reason': f"Could not resolve Dhan Security ID for {symbol_clean}"
            }

        # 1. PAPER TRADING EXECUTION
        if self.mode == "PAPER":
            order_id = f"PAPER_{uuid.uuid4().hex[:8].upper()}"
            print(f"[PAPER TRADING] {transaction_type} {quantity} shares of {symbol_clean} (SecID: {sec_id}) @ Rs. {price:.2f} | Order ID: {order_id}")
            return {
                'status': 'SUCCESS',
                'order_id': order_id,
                'mode': 'PAPER',
                'symbol': symbol_clean,
                'security_id': sec_id,
                'quantity': quantity,
                'executed_price': price,
                'transaction_type': transaction_type
            }

        # 2. LIVE TRADING EXECUTION (DhanHQ)
        try:
            if self._dhan_sdk:
                from dhanhq import dhanhq
                d_trans = dhanhq.BUY if transaction_type.upper() == 'BUY' else dhanhq.SELL
                d_prod = dhanhq.CNC if self.product_type == 'CNC' else dhanhq.INTRADAY
                
                resp = self._dhan_sdk.place_order(
                    security_id=sec_id,
                    exchange_segment=dhanhq.NSE,
                    transaction_type=d_trans,
                    quantity=quantity,
                    order_type=dhanhq.MARKET,
                    product_type=d_prod,
                    price=0.0
                )
                print(f"[LIVE TRADING] Dhan order response: {resp}")
                if resp.get('status') == 'success' or 'orderId' in resp:
                    return {
                        'status': 'SUCCESS',
                        'order_id': resp.get('data', {}).get('orderId', resp.get('orderId')),
                        'raw_response': resp
                    }
                else:
                    return {'status': 'FAILED', 'raw_response': resp}
            else:
                # Direct REST API fallback
                url = "https://api.dhan.co/v2/orders"
                headers = {
                    "access-token": self.access_token,
                    "client-id": self.client_id,
                    "Content-Type": "application/json"
                }
                payload = {
                    "dhanClientId": self.client_id,
                    "correlationId": uuid.uuid4().hex[:10],
                    "transactionType": transaction_type.upper(),
                    "exchangeSegment": "NSE_EQ",
                    "productType": self.product_type,
                    "orderType": "MARKET",
                    "validity": "DAY",
                    "tradingSymbol": symbol_clean,
                    "securityId": sec_id,
                    "quantity": int(quantity),
                    "price": 0
                }
                r = requests.post(url, json=payload, headers=headers, timeout=10)
                data = r.json()
                if r.status_code == 200 and 'orderId' in data:
                    return {'status': 'SUCCESS', 'order_id': data['orderId'], 'data': data}
                else:
                    return {'status': 'FAILED', 'error': data}
        except Exception as e:
            return {'status': 'ERROR', 'message': str(e)}

    def get_fund_limits(self) -> Dict[str, Any]:
        """Fetches available funds in Dhan account."""
        if self.mode == "PAPER":
            return {'available_cash': 1000000.0, 'mode': 'PAPER'}
        url = "https://api.dhan.co/v2/fundlimit"
        headers = {
            "access-token": self.access_token,
            "client-id": self.client_id,
            "Content-Type": "application/json"
        }
        try:
            r = requests.get(url, headers=headers, timeout=10)
            return r.json() if r.status_code == 200 else {}
        except Exception as e:
            print(f"[DhanClient] Error fetching fund limits: {e}")
            return {}

