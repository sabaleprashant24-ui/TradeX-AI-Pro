"""
TradeX AI Pro v4.0 - Angel One SmartAPI Production Module
File: angel_api.py

Handles SmartConnect Authentication, TOTP Session Generation, Background Token
Refresh, Symbol Master Lookup, Live WebSocket Engine with Reconnect Limits,
Live Option Chain Fetching, Order Execution, GTT Orders, Holdings, Profile,
Human-Readable Error Mapping, and Graceful Shutdown.

Compatible with Python 3.13 and Pydroid 3.
"""

from datetime import datetime, timedelta
import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Union
import pyotp

SmartConnect = None
SmartWebSocketV2 = None


def _load_smartapi() -> bool:
    global SmartConnect, SmartWebSocketV2

    if SmartConnect and SmartWebSocketV2:
        return True

    try:
        from SmartApi import SmartConnect as smart_connect_factory
        from SmartApi.smartWebSocketV2 import SmartWebSocketV2 as smart_websocket_factory

        SmartConnect = smart_connect_factory
        SmartWebSocketV2 = smart_websocket_factory
        return True
    except ImportError:
        SmartConnect = None
        SmartWebSocketV2 = None
        return False

from config import CONFIG
from logger import LOGGER
from utils import retry

# Human Readable Broker Error Code Mapping
ERROR_CODE_MAP = {
    "AB1001": "Invalid Credentials / Client ID",
    "AB1002": "Invalid TOTP / Password",
    "AB1003": "Session Expired. Please Login Again.",
    "AB1004": "Rate Limit Exceeded. Too Many Requests.",
    "AB1005": "Insufficient Funds / Margin Deficit",
    "AG8001": "Symbol Token Not Found",
    "AG8002": "Invalid Order Type or Price Tick",
    "AG8003": "Market Closed for Trading",
    "AG8004": "Order Rejection by RMS/Exchange",
    "PARSE_ERR": "Failed to Parse Broker Response",
    "NET_ERR": "Network Timeout / Broker Server Down",
}


class AngelOneAPI:
    """Production Grade Angel One SmartAPI & WebSocket Wrapper."""

    def __init__(self):
        self.api_key = CONFIG.api.api_key
        self.client_id = CONFIG.api.client_id
        self.password = CONFIG.api.password
        self.totp_secret = CONFIG.api.totp_secret

        self.smart_api: Optional[Any] = None
        self.ws_client: Optional[Any] = None
        self.is_connected = False
        self.jwt_token = ""
        self.refresh_token = ""
        self.feed_token = ""

        # Auto Token Refresh Thread
        self._refresh_thread: Optional[threading.Thread] = None
        self._stop_refresh_event = threading.Event()

        # Symbol Master Cache
        self._symbol_cache: Dict[str, str] = {}

        # WebSocket State & Reconnect Limits
        self.on_tick_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self.ws_connected = False
        self.reconnect_counter = 0
        self.max_reconnect_attempts = 5
        self.base_reconnect_delay = 2.0  # Seconds for exponential backoff
        self.active_token_list: List[Dict[str, Any]] = []

    def get_error_message(self, error_code: str) -> str:
        """Returns human-readable explanation for broker error codes."""
        return ERROR_CODE_MAP.get(
            error_code, f"Unknown Error Code: {error_code}"
        )

    def generate_totp(self) -> str:
        """Generates 6-digit TOTP code using secret key."""
        if not self.totp_secret:
            raise ValueError("TOTP Secret Key is missing in Configuration.")
        totp = pyotp.TOTP(self.totp_secret)
        return totp.now()

    @retry(max_retries=3, delay=2.0)
    def connect(self) -> bool:
        """Authenticates with SmartAPI, retrieves tokens, and starts auto refresh thread."""
        if not _load_smartapi():
            LOGGER.warning(
                "SmartApi library not installed. Running in Mock/Simulated Connection Mode."
            )
            self.is_connected = True
            return True

        try:
            self.smart_api = SmartConnect(api_key=self.api_key)
            totp_code = self.generate_totp()

            data = self.smart_api.generateSession(
                clientCode=self.client_id,
                password=self.password,
                totp=totp_code,
            )

            if data and data.get("status"):
                self.jwt_token = data["data"]["jwtToken"]
                self.refresh_token = data["data"]["refreshToken"]
                self.feed_token = data["data"]["feedToken"]

                CONFIG.api.jwt_token = self.jwt_token
                CONFIG.api.refresh_token = self.refresh_token
                CONFIG.api.feed_token = self.feed_token

                self.is_connected = True
                LOGGER.info(
                    f"Successfully logged into Angel One SmartAPI for Client: {self.client_id}"
                )

                # Start Background Auto Token Refresh Worker
                self._start_auto_token_refresh()
                return True
            else:
                err_code = data.get("errorcode", "AB1001") if data else "AB1001"
                err_desc = self.get_error_message(err_code)
                msg = data.get('message') if data else "No response"
                LOGGER.error(
                    f"Angel One Login Failed [{err_code}]: {err_desc} - {msg}"
                )
                self.is_connected = False
                return False

        except Exception as e:
            LOGGER.error(f"Exception during SmartAPI login: {e}", exc_info=True)
            self.is_connected = False
            return False

    def _start_auto_token_refresh(self):
        """Launches a background daemon thread for periodic token refresh."""
        if self._refresh_thread and self._refresh_thread.is_alive():
            return

        self._stop_refresh_event.clear()
        self._refresh_thread = threading.Thread(
            target=self._auto_token_refresh_worker, daemon=True
        )
        self._refresh_thread.start()
        LOGGER.info("Auto Token Refresh Background Thread Started.")

    def _auto_token_refresh_worker(self):
        """Worker thread executing every 3 hours to renew JWT token."""
        while not self._stop_refresh_event.is_set():
            if self._stop_refresh_event.wait(10800):  # 3 hours
                break
            LOGGER.info("Executing periodic Auto Token Refresh...")
            self.refresh_session_token()

    def refresh_session_token(self) -> bool:
        """Refreshes expired JWT Session Token."""
        if not self.smart_api or not self.refresh_token:
            return self.connect()

        try:
            token_data = self.smart_api.renewToken(self.refresh_token)
            if token_data and token_data.get("status"):
                self.jwt_token = token_data["data"]["jwtToken"]
                CONFIG.api.jwt_token = self.jwt_token
                LOGGER.info("SmartAPI JWT Token successfully refreshed.")
                return True
            else:
                LOGGER.warning("Token refresh failed. Re-authenticating...")
                return self.connect()
        except Exception as e:
            LOGGER.error(f"Error refreshing token: {e}")
            return self.connect()

    def get_symbol_token(self, symbol: str, exchange: str = "NSE") -> str:
        """Retrieves and caches Angel One Symbol Token for a trading symbol."""
        cache_key = f"{exchange}:{symbol.upper()}"
        if cache_key in self._symbol_cache:
            return self._symbol_cache[cache_key]

        if not self.is_connected or not self.smart_api:
            return ""

        try:
            common_tokens = {
                "NSE:NIFTY": "99926000",
                "NSE:BANKNIFTY": "99926009",
                "NSE:FINNIFTY": "99926037",
                "NSE:MIDCPNIFTY": "99926014",
                "BSE:SENSEX": "99919000",
                "BSE:BANKEX": "99919012",
            }

            if cache_key in common_tokens:
                token = common_tokens[cache_key]
                self._symbol_cache[cache_key] = token
                return token

            search_res = self.smart_api.searchScrip(
                exchange=exchange, searchtext=symbol
            )
            if search_res and search_res.get("status"):
                data = search_res.get("data", [])
                if data:
                    token = str(data[0].get("symboltoken", ""))
                    self._symbol_cache[cache_key] = token
                    return token

            LOGGER.warning(f"Symbol Token not found for {symbol} on {exchange}")
            return ""
        except Exception as e:
            LOGGER.error(f"Error finding Symbol Token for {symbol}: {e}")
            return ""

    def get_profile(self) -> Dict[str, Any]:
        """Fetches User Profile details."""
        if not self.is_connected or not self.smart_api:
            return {}
        try:
            profile = self.smart_api.getProfile(self.refresh_token)
            return profile.get("data", {}) if profile else {}
        except Exception as e:
            LOGGER.error(f"Error fetching Profile: {e}")
            return {}

    def get_holdings(self) -> List[Dict[str, Any]]:
        """Fetches Long Term Equity Holdings."""
        if not self.is_connected or not self.smart_api:
            return []
        try:
            holdings = self.smart_api.getHolding()
            return holdings.get("data", []) if holdings else []
        except Exception as e:
            LOGGER.error(f"Error fetching Holdings: {e}")
            return []

    # =========================================================================
    # LIVE OPTION CHAIN HELPER (LIVE DATA)
    # =========================================================================

    def _fetch_live_quote_data(
        self, exchange: str, symbol: str, token: str
    ) -> Dict[str, Any]:
        """Fetches live Market Depth / Full Quote data for OI and LTP."""
        if not self.is_connected or not self.smart_api or not token:
            return {"ltp": 0.0, "oi": 0, "oi_change": 0, "volume": 0}

        try:
            res = self.smart_api.getMarketData(
                mode="FULL", exchangeTokens={exchange: [token]}
            )
            if res and res.get("status") and res.get("data"):
                fetched = res["data"]["fetched"][0]
                return {
                    "ltp": float(fetched.get("ltp", 0.0)),
                    "oi": int(fetched.get("opnInterest", 0)),
                    "oi_change": int(fetched.get("netChange", 0)),
                    "volume": int(fetched.get("tradeVolume", 0)),
                }
        except Exception as e:
            LOGGER.warning(f"Failed to fetch market data for {symbol}: {e}")

        # Fallback to LTP
        ltp = self.get_ltp(exchange, symbol, token)
        return {"ltp": ltp, "oi": 0, "oi_change": 0, "volume": 0}

    def fetch_option_chain_helper(
        self, spot_symbol: str, spot_price: float, expiry_str: str = "", step: float = 50.0
    ) -> Dict[str, Any]:
        """Fetches real-time Option Chain data for ATM Call and Put options."""
        atm_strike = round(spot_price / step) * step
        
        ce_symbol = f"{spot_symbol}{expiry_str}{int(atm_strike)}CE"
        pe_symbol = f"{spot_symbol}{expiry_str}{int(atm_strike)}PE"

        ce_token = self.get_symbol_token(ce_symbol, "NFO")
        pe_token = self.get_symbol_token(pe_symbol, "NFO")

        ce_data = self._fetch_live_quote_data("NFO", ce_symbol, ce_token)
        pe_data = self._fetch_live_quote_data("NFO", pe_symbol, pe_token)

        return {
            "spot_symbol": spot_symbol,
            "spot_price": spot_price,
            "atm_strike": atm_strike,
            "ce_option": {
                "symbol": ce_symbol,
                "token": ce_token,
                "strike": atm_strike,
                "type": "CE",
                "ltp": ce_data["ltp"],
                "oi": ce_data["oi"],
                "oi_change": ce_data["oi_change"],
                "volume": ce_data["volume"],
            },
            "pe_option": {
                "symbol": pe_symbol,
                "token": pe_token,
                "strike": atm_strike,
                "type": "PE",
                "ltp": pe_data["ltp"],
                "oi": pe_data["oi"],
                "oi_change": pe_data["oi_change"],
                "volume": pe_data["volume"],
            },
        }

    # =========================================================================
    # WEBSOCKET ENGINE (EXPONENTIAL BACKOFF & RECONNECT LIMIT)
    # =========================================================================

    def start_websocket(
        self,
        token_list: List[Dict[str, Any]],
        on_tick: Callable[[Dict[str, Any]], None],
    ):
        """Starts SmartWebSocket V2 with exponential backoff and max reconnect limit."""
        if not _load_smartapi() or SmartWebSocketV2 is None:
            LOGGER.warning("SmartWebSocketV2 library unavailable.")
            return

        self.on_tick_callback = on_tick
        self.active_token_list = token_list

        def _on_data(wsapp, message):
            if self.on_tick_callback:
                self.on_tick_callback(message)

        def _on_open(wsapp):
            LOGGER.info("WebSocket Connected successfully! Resetting reconnect counter.")
            self.ws_connected = True
            self.reconnect_counter = 0
            mode = 1  # 1: LTP, 2: Quote, 3: SnapQuote
            self.ws_client.subscribe("tradex_sub", mode, self.active_token_list)

        def _on_close(wsapp):
            self.ws_connected = False
            LOGGER.warning("WebSocket Connection Closed.")

            if self.reconnect_counter < self.max_reconnect_attempts:
                self.reconnect_counter += 1
                backoff_delay = self.base_reconnect_delay * (2 ** (self.reconnect_counter - 1))
                LOGGER.info(
                    f"Attempting Reconnect ({self.reconnect_counter}/{self.max_reconnect_attempts}) in {backoff_delay:.1f} seconds..."
                )
                time.sleep(backoff_delay)
                self.start_websocket(self.active_token_list, self.on_tick_callback)
            else:
                LOGGER.error(
                    f"Max WebSocket Reconnect attempts ({self.max_reconnect_attempts}) reached. Stopping reconnects."
                )

        def _on_error(wsapp, error):
            LOGGER.error(f"WebSocket Error: {error}")

        try:
            self.ws_client = SmartWebSocketV2(
                jwt_token=self.jwt_token,
                api_key=self.api_key,
                client_code=self.client_id,
                feed_token=self.feed_token,
            )
            self.ws_client.on_data = _on_data
            self.ws_client.on_open = _on_open
            self.ws_client.on_close = _on_close
            self.ws_client.on_error = _on_error

            ws_thread = threading.Thread(
                target=self.ws_client.connect, daemon=True
            )
            ws_thread.start()
            LOGGER.info("Live WebSocket Client Thread Launched.")
        except Exception as e:
            LOGGER.error(f"Failed to initialize WebSocket: {e}")

    # =========================================================================
    # GRACEFUL SHUTDOWN
    # =========================================================================

    def disconnect(self):
        """Safely stops threads, closes active WebSocket, and terminates session."""
        LOGGER.info("Initiating Graceful Shutdown of AngelOneAPI...")

        self._stop_refresh_event.set()
        if self._refresh_thread and self._refresh_thread.is_alive():
            self._refresh_thread.join(timeout=2.0)
            LOGGER.info("Background Auto Refresh Thread stopped.")

        if self.ws_client:
            try:
                if hasattr(self.ws_client, "close_connection"):
                    self.ws_client.close_connection()
                LOGGER.info("WebSocket connection closed successfully.")
            except Exception as e:
                LOGGER.error(f"Error closing WebSocket: {e}")

        self.ws_connected = False
        self.is_connected = False
        LOGGER.info("AngelOneAPI disconnected cleanly.")

    # =========================================================================
    # ORDER EXECUTION & GTT ORDERS
    # =========================================================================

    def get_ltp(
        self, exchange: str, trading_symbol: str, symbol_token: str
    ) -> float:
        """Fetches Last Traded Price (LTP)."""
        if not self.is_connected or not self.smart_api:
            return 0.0

        try:
            data = self.smart_api.ltpData(
                exchange=exchange,
                tradingsymbol=trading_symbol,
                symboltoken=symbol_token,
            )
            if data and data.get("status"):
                return float(data["data"]["ltp"])
            return 0.0
        except Exception as e:
            LOGGER.error(f"Error fetching LTP for {trading_symbol}: {e}")
            return 0.0

    def place_order(
        self,
        variety: str,
        tradingsymbol: str,
        symboltoken: str,
        transactiontype: str,
        exchange: str,
        ordertype: str,
        producttype: str,
        duration: str,
        price: float,
        quantity: int,
        stoploss: float = 0.0,
    ) -> Dict[str, Any]:
        """Places a Live Order via Angel One API."""
        if not self.is_connected or not self.smart_api:
            return {"status": False, "message": "API Not Connected"}

        try:
            order_params = {
                "variety": variety,
                "tradingsymbol": tradingsymbol,
                "symboltoken": symboltoken,
                "transactiontype": transactiontype,
                "exchange": exchange,
                "ordertype": ordertype,
                "producttype": producttype,
                "duration": duration,
                "price": str(price),
                "quantity": str(quantity),
            }
            if stoploss > 0:
                order_params["triggerprice"] = str(stoploss)

            response = self.smart_api.placeOrder(order_params)
            return response
        except Exception as e:
            LOGGER.error(f"Exception while placing order: {e}")
            return {"status": False, "message": str(e)}

    def place_gtt_order(
        self,
        tradingsymbol: str,
        symboltoken: str,
        exchange: str,
        transactiontype: str,
        price: float,
        trigger_price: float,
        quantity: int,
        disclosedqty: int = 0,
    ) -> Dict[str, Any]:
        """Places a GTT (Good-Till-Triggered) Order."""
        if not self.is_connected or not self.smart_api:
            return {"status": False, "message": "API Not Connected"}

        try:
            gtt_params = {
                "tradingsymbol": tradingsymbol,
                "symboltoken": symboltoken,
                "exchange": exchange,
                "transactiontype": transactiontype,
                "price": price,
                "triggerprice": trigger_price,
                "qty": quantity,
                "disclosedqty": disclosedqty,
            }
            response = self.smart_api.gttCreateRule(gtt_params)
            LOGGER.info(f"GTT Rule Placement Response: {response}")
            return response
        except Exception as e:
            LOGGER.error(f"Error placing GTT order: {e}")
            return {"status": False, "message": str(e)}

    def cancel_gtt_order(
        self, id: int, symboltoken: str, exchange: str
    ) -> Dict[str, Any]:
        """Cancels an existing GTT Order Rule."""
        if not self.is_connected or not self.smart_api:
            return {"status": False, "message": "API Not Connected"}

        try:
            params = {"id": id, "symboltoken": symboltoken, "exchange": exchange}
            response = self.smart_api.gttCancelRule(params)
            return response
        except Exception as e:
            LOGGER.error(f"Error canceling GTT order {id}: {e}")
            return {"status": False, "message": str(e)}

    def get_order_book(self) -> List[Dict[str, Any]]:
        """Fetches Order Book from broker."""
        if not self.is_connected or not self.smart_api:
            return []
        try:
            data = self.smart_api.orderBook()
            return data.get("data", []) if data else []
        except Exception as e:
            LOGGER.error(f"Error fetching order book: {e}")
            return []

    def get_position_book(self) -> List[Dict[str, Any]]:
        """Fetches Position Book from broker."""
        if not self.is_connected or not self.smart_api:
            return []
        try:
            data = self.smart_api.position()
            return data.get("data", []) if data else []
        except Exception as e:
            LOGGER.error(f"Error fetching position book: {e}")
            return []

    def get_funds_margin(self) -> Dict[str, Any]:
        """Fetches available Funds and Margin details."""
        if not self.is_connected or not self.smart_api:
            return {}
        try:
            data = self.smart_api.rmsLimit()
            return data.get("data", {}) if data else {}
        except Exception as e:
            LOGGER.error(f"Error fetching funds margin: {e}")
            return {}


# Global Angel API Instance
ANGEL_API = AngelOneAPI()
angel_api = ANGEL_API
