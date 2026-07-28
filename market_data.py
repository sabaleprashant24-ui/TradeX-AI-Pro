"""
TradeX AI Pro v4.0 - Production Market Data Engine
File: market_data.py

Handles Realtime Tick Streaming, Live Tick-to-Candle Resampling, Multi-Timeframe Caching
with Expiry, Stale Tick Validation, Batch Historical Data Downloads, and Option Premium Sync.

Compatible with Python 3.13 and Pydroid 3.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import logging
import threading
from typing import Any, Dict, List, Optional
import pandas as pd

from angel_api import ANGEL_API
from config import CONFIG
from logger import LOGGER
from utils import MathUtils, retry

logger = logging.getLogger("TradeX_MarketData")


class MarketDataManager:
    """Production Grade Market Data Engine."""

    def __init__(self, max_tick_age_seconds: int = 5):
        self.angel_api = ANGEL_API
        self.max_tick_age_seconds = max_tick_age_seconds

        # Realtime Tick Cache: { "TOKEN": {"ltp": 24500.0, "timestamp": datetime} }
        self._live_ticks: Dict[str, Dict[str, Any]] = {}

        # Multi-Timeframe Candle Cache: { "SYMBOL_INTERVAL": {"df": pd.DataFrame, "last_updated": datetime} }
        self._candle_cache: Dict[str, Dict[str, Any]] = {}

        # Live Resampled Candles Storage for WebSocket Ticks
        # { "TOKEN": { "ONE_MINUTE": [...], "FIVE_MINUTE": [...] } }
        self._live_resampled_candles: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

        self._lock = threading.Lock()

    # =========================================================================
    # 1. LIVE TICK VALIDATION & WEBSOCKET ENGINE
    # =========================================================================

    def start_live_feed(self, symbols: List[Dict[str, str]]):
        """Subscribes to live WebSocket feeds for a list of tokens/symbols."""
        LOGGER.info("Initializing Live Market Data WebSocket Feed...")
        self.angel_api.start_websocket(
            token_list=symbols, on_tick=self._process_live_tick
        )

    def _process_live_tick(self, tick_message: Dict[str, Any]):
        """Processes live tick, updates tick cache, and triggers live candle builder."""
        try:
            token = str(tick_message.get("token", ""))
            ltp = (
                float(tick_message.get("last_traded_price", 0.0)) / 100.0
            )  # Convert paisa if applicable
            now = datetime.now()

            tick_data = {
                "ltp": ltp,
                "high": float(tick_message.get("high_price", 0.0)) / 100.0,
                "low": float(tick_message.get("low_price", 0.0)) / 100.0,
                "open": float(tick_message.get("open_price", 0.0)) / 100.0,
                "close": float(tick_message.get("close_price", 0.0)) / 100.0,
                "volume": int(tick_message.get("volume_traded", 0)),
                "timestamp": now,
            }

            with self._lock:
                self._live_ticks[token] = tick_data

            # 2. Trigger Auto Candle Resampling Engine
            self._update_live_candles_from_tick(token, ltp, tick_data["volume"], now)

        except Exception as e:
            LOGGER.error(f"Error processing live websocket tick: {e}")

    def get_live_tick_validated(
        self, token: str, exchange: str = "NSE", symbol: str = ""
    ) -> float:
        """
        Validates tick timestamp. If tick is older than `max_tick_age_seconds`,
        it flags it as stale and falls back to Direct REST API LTP.
        """
        with self._lock:
            tick_info = self._live_ticks.get(token)

        if tick_info:
            tick_age = (datetime.now() - tick_info["timestamp"]).total_seconds()
            if tick_age <= self.max_tick_age_seconds and tick_info["ltp"] > 0:
                return tick_info["ltp"]
            else:
                LOGGER.warning(
                    f"Stale WebSocket Tick detected for Token {token} (Age: {tick_age:.1f}s). Falling back to REST API LTP."
                )

        # REST API Fallback
        return self.angel_api.get_ltp(exchange, symbol, token)

    # =========================================================================
    # 2. AUTO CANDLE RESAMPLING ENGINE (1m, 5m, 15m)
    # =========================================================================

    def _update_live_candles_from_tick(
        self, token: str, price: float, volume: int, timestamp: datetime
    ):
        """Constructs live OHLCV candles (1m, 5m, 15m) in real-time from WebSocket ticks."""
        if price <= 0:
            return

        timeframes = {
            "ONE_MINUTE": 1,
            "FIVE_MINUTE": 5,
            "FIFTEEN_MINUTE": 15,
        }

        with self._lock:
            if token not in self._live_resampled_candles:
                self._live_resampled_candles[token] = {tf: [] for tf in timeframes}

            for tf, tf_mins in timeframes.items():
                candles_list = self._live_resampled_candles[token][tf]

                # Round timestamp down to candle bucket
                bucket_minute = (timestamp.minute // tf_mins) * tf_mins
                candle_time = timestamp.replace(
                    minute=bucket_minute, second=0, microsecond=0
                )

                if candles_list and candles_list[-1]["timestamp"] == candle_time:
                    # Update active candle
                    current = candles_list[-1]
                    current["high"] = max(current["high"], price)
                    current["low"] = min(current["low"], price)
                    current["close"] = price
                    current["volume"] += volume
                else:
                    # Append new candle
                    candles_list.append(
                        {
                            "timestamp": candle_time,
                            "open": price,
                            "high": price,
                            "low": price,
                            "close": price,
                            "volume": volume,
                        }
                    )
                    # Limit memory footprint
                    if len(candles_list) > 500:
                        candles_list.pop(0)

    # =========================================================================
    # 3. CACHE EXPIRY MANAGEMENT
    # =========================================================================

    def _is_cache_valid(self, cache_entry: Dict[str, Any], interval: str) -> bool:
        """Determines if the cached DataFrame is still valid based on timeframe expiry."""
        if not cache_entry or "last_updated" not in cache_entry:
            return False

        expiry_minutes_map = {
            "ONE_MINUTE": 1,
            "FIVE_MINUTE": 5,
            "FIFTEEN_MINUTE": 15,
            "ONE_DAY": 1440,
        }
        max_valid_seconds = expiry_minutes_map.get(interval, 5) * 60
        elapsed_seconds = (
            datetime.now() - cache_entry["last_updated"]
        ).total_seconds()

        return elapsed_seconds < max_valid_seconds

    def fetch_ohlcv_dataframe(
        self,
        symbol: str,
        symbol_token: str,
        exchange: str = "NSE",
        interval: str = "FIVE_MINUTE",
        days_back: int = 5,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """Fetches OHLCV DataFrame with active Cache Expiry management."""
        cache_key = f"{symbol}_{interval}"

        with self._lock:
            cache_entry = self._candle_cache.get(cache_key)
            if (
                not force_refresh
                and cache_entry
                and self._is_cache_valid(cache_entry, interval)
            ):
                return cache_entry["df"]

        to_date = datetime.now().strftime("%Y-%m-%d %H:%M")
        from_date = (datetime.now() - timedelta(days=days_back)).strftime(
            "%Y-%m-%d 09:15"
        )

        raw_candles = self.angel_api.get_historical_data(
            exchange=exchange,
            symbol_token=symbol_token,
            interval=interval,
            from_date=from_date,
            to_date=to_date,
        )

        if not raw_candles:
            LOGGER.warning(f"No historical candles retrieved for {symbol} [{interval}]")
            return pd.DataFrame()

        df = pd.DataFrame(
            raw_candles,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["open"] = pd.to_numeric(df["open"], errors="coerce")
        df["high"] = pd.to_numeric(df["high"], errors="coerce")
        df["low"] = pd.to_numeric(df["low"], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
        df.sort_values(by="timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)

        with self._lock:
            self._candle_cache[cache_key] = {
                "df": df,
                "last_updated": datetime.now(),
            }

        return df

    # =========================================================================
    # 4. BATCH HISTORICAL DOWNLOADER
    # =========================================================================

    def download_batch_historical_data(
        self,
        symbols_info: List[Dict[str, str]],
        interval: str = "FIVE_MINUTE",
        days_back: int = 5,
    ) -> Dict[str, pd.DataFrame]:
        """
        Downloads historical candles for multiple symbols concurrently.
        Example: symbols_info = [
            {"symbol": "NIFTY", "token": "99926000", "exchange": "NSE"},
            {"symbol": "BANKNIFTY", "token": "99926009", "exchange": "NSE"}
        ]
        """
        LOGGER.info(
            f"Starting Batch Historical Download for {len(symbols_info)} symbols..."
        )
        results: Dict[str, pd.DataFrame] = {}

        def _download_task(item: Dict[str, str]):
            sym = item["symbol"]
            tok = item["token"]
            exc = item.get("exchange", "NSE")
            df = self.fetch_ohlcv_dataframe(
                symbol=sym,
                symbol_token=tok,
                exchange=exc,
                interval=interval,
                days_back=days_back,
                force_refresh=True,
            )
            return sym, df

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [
                executor.submit(_download_task, item) for item in symbols_info
            ]
            for future in as_completed(futures):
                try:
                    sym, df = future.result()
                    results[sym] = df
                    LOGGER.info(f"Downloaded {len(df)} candles for {sym}")
                except Exception as e:
                    LOGGER.error(f"Failed batch download item: {e}")

        return results

    # =========================================================================
    # OPTION PREMIUM & SPOT DATA
    # =========================================================================

    def get_spot_price(self, symbol: str, exchange: str = "NSE") -> float:
        """Fetches spot price using timestamp validation."""
        token = self.angel_api.get_symbol_token(symbol, exchange)
        if not token:
            return 0.0
        return self.get_live_tick_validated(token, exchange, symbol)

    def get_option_premium(
        self,
        spot_symbol: str,
        strike_price: float,
        option_type: str,
        expiry_str: str = "",
    ) -> Dict[str, Any]:
        """Retrieves Option Premium data with tick validation."""
        opt_type_upper = option_type.upper()
        option_symbol = f"{spot_symbol}{expiry_str}{int(strike_price)}{opt_type_upper}"
        token = self.angel_api.get_symbol_token(option_symbol, "NFO")

        if not token:
            return {"symbol": option_symbol, "ltp": 0.0, "token": ""}

        ltp = self.get_live_tick_validated(token, "NFO", option_symbol)
        return {
            "symbol": option_symbol,
            "token": token,
            "strike": strike_price,
            "option_type": opt_type_upper,
            "ltp": ltp,
        }


# Global Market Data Instance
MARKET_DATA = MarketDataManager()