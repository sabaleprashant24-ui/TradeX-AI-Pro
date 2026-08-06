"""
TradeX AI Pro v4.0 - Production Market Data Engine
File: market_data.py

Handles Realtime Tick Streaming, Live Tick-to-Candle Resampling, Multi-Timeframe Caching
with Expiry, Stale Tick Validation, Batch Historical Data Downloads, and Option Premium Sync.

Compatible with Python 3.13 and Pydroid 3.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import queue
import threading
import time
from typing import Any, Dict, List, Optional
import pandas as pd

try:
    from angel_api import ANGEL_API
except ImportError:
    ANGEL_API = None

try:
    from config import Config
except ImportError:
    Config = None

from logger import logger


class MarketDataManager:
    """Production Grade Market Data Engine."""

    def __init__(self, max_tick_age_seconds: int = 5):
        self.angel_api = ANGEL_API
        self.max_tick_age_seconds = max_tick_age_seconds

        # Realtime Tick Cache: { "TOKEN": {"ltp": 24500.0, "timestamp": datetime, "cum_volume": int} }
        self._live_ticks: Dict[str, Dict[str, Any]] = {}

        # Multi-Timeframe Candle Cache: { "SYMBOL_INTERVAL": {"df": pd.DataFrame, "last_updated": datetime} }
        self._candle_cache: Dict[str, Dict[str, Any]] = {}

        # Live Resampled Candles Storage for WebSocket Ticks
        self._live_resampled_candles: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

        # Thread-safe feed state for live tick streaming and dashboard health
        self._tick_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=1000)
        self._feed_health: Dict[str, Any] = {
            "connected": False,
            "status": "DISCONNECTED",
            "last_tick_timestamp": None,
            "last_tick_token": None,
            "latency_ms": 0.0,
            "queue_size": 0,
            "reconnect_attempts": 0,
            "last_heartbeat": None,
            "last_seen": None,
        }
        self._feed_monitor_thread: Optional[threading.Thread] = None
        self._stop_feed_monitor_event = threading.Event()
        self._last_tick_received_at: Optional[float] = None

        self._lock = threading.RLock()

    # =========================================================================
    # 1. LIVE TICK VALIDATION & WEBSOCKET ENGINE
    # =========================================================================

    def start_live_feed(self, symbols: List[Dict[str, str]]):
        """Subscribes to live WebSocket feeds for a list of tokens/symbols."""
        logger.info("Initializing Live Market Data WebSocket Feed...")
        self._feed_health["status"] = "CONNECTING"
        self._feed_health["connected"] = False
        self._ensure_feed_monitor()
        if self.angel_api and hasattr(self.angel_api, "start_websocket"):
            self.angel_api.start_websocket(
                token_list=symbols, on_tick=self._process_live_tick
            )
        else:
            logger.warning("ANGEL_API WebSocket interface unavailable.")

    @staticmethod
    def _normalize_price(val: Any) -> float:
        """Safely normalizes raw price values from API/WebSocket feeds."""
        try:
            price = float(val)
            # Standard Angel SmartAPI paisa scaling check
            return price / 100.0 if price > 500000.0 else price
        except (ValueError, TypeError):
            return 0.0

    def _process_live_tick(self, tick_message: Dict[str, Any]):
        """Processes live tick with volume delta calculation and triggers candle construction."""
        try:
            token = str(tick_message.get("token", ""))
            if not token:
                return

            ltp = self._normalize_price(tick_message.get("last_traded_price", 0.0))
            now = datetime.now()

            raw_cum_vol = int(tick_message.get("volume_traded", 0))

            with self._lock:
                prev_tick = self._live_ticks.get(token, {})
                prev_cum_vol = prev_tick.get("cum_volume", raw_cum_vol)

                # Calculate tick delta volume to prevent sum inflation
                volume_delta = max(0, raw_cum_vol - prev_cum_vol)

                tick_data = {
                    "token": token,
                    "ltp": ltp,
                    "high": self._normalize_price(tick_message.get("high_price", ltp)),
                    "low": self._normalize_price(tick_message.get("low_price", ltp)),
                    "open": self._normalize_price(tick_message.get("open_price", ltp)),
                    "close": self._normalize_price(tick_message.get("close_price", ltp)),
                    "volume_delta": volume_delta,
                    "cum_volume": raw_cum_vol,
                    "timestamp": now,
                }

                self._live_ticks[token] = tick_data

            self._enqueue_tick(tick_data)
            self._update_feed_health(token=token, latency_ms=self._calculate_latency_ms(tick_message))

            # Trigger Auto Candle Resampling Engine with volume delta
            self._update_live_candles_from_tick(token, ltp, volume_delta, now)

        except Exception as e:
            logger.error(f"Error processing live websocket tick: {e}")

    def _enqueue_tick(self, tick_data: Dict[str, Any]) -> None:
        """Pushes a parsed tick into the thread-safe queue for dashboard and downstream consumers."""
        try:
            if self._tick_queue.full():
                self._tick_queue.get_nowait()
            self._tick_queue.put_nowait(tick_data)
        except Exception:
            pass

    def get_live_tick(self, token: str) -> Optional[Dict[str, Any]]:
        """Returns the latest cached tick for a token without altering the existing API flow."""
        with self._lock:
            return self._live_ticks.get(token)

    def get_latest_ticks(self, count: int = 10) -> List[Dict[str, Any]]:
        """Returns the most recently queued ticks for dashboards and debug panels."""
        items = []
        while count > 0 and not self._tick_queue.empty():
            try:
                items.append(self._tick_queue.get_nowait())
            except Exception:
                break
            count -= 1
        for item in reversed(items):
            self._tick_queue.put_nowait(item)
        return items

    def _calculate_latency_ms(self, tick_message: Dict[str, Any]) -> float:
        """Best-effort latency estimate from the incoming feed payload."""
        raw_ts = tick_message.get("timestamp") or tick_message.get("exchange_timestamp")
        if isinstance(raw_ts, (int, float)):
            try:
                return round(max(0.0, (time.time() - float(raw_ts)) * 1000.0), 2)
            except Exception:
                return 0.0
        if isinstance(raw_ts, str):
            try:
                parsed = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                if parsed.tzinfo is not None:
                    parsed = parsed.astimezone().replace(tzinfo=None)
                return round(max(0.0, (time.time() - parsed.timestamp()) * 1000.0), 2)
            except Exception:
                return 0.0
        return 0.0

    def _ensure_feed_monitor(self) -> None:
        """Starts a lightweight monitor to keep feed health state current."""
        if self._feed_monitor_thread and self._feed_monitor_thread.is_alive():
            return
        self._stop_feed_monitor_event.clear()
        self._feed_monitor_thread = threading.Thread(target=self._feed_monitor_loop, daemon=True)
        self._feed_monitor_thread.start()

    def _feed_monitor_loop(self) -> None:
        """Keeps feed health markers current for dashboards and reconnection visibility."""
        while not self._stop_feed_monitor_event.is_set():
            if self._stop_feed_monitor_event.wait(5):
                break
            with self._lock:
                self._feed_health["queue_size"] = self._tick_queue.qsize()
                self._feed_health["last_heartbeat"] = datetime.now().isoformat(timespec="seconds")
                if self.angel_api and hasattr(self.angel_api, "ws_connected"):
                    self._feed_health["connected"] = bool(getattr(self.angel_api, "ws_connected"))
                    self._feed_health["reconnect_attempts"] = int(getattr(self.angel_api, "reconnect_counter", 0))
                if self._last_tick_received_at is not None:
                    stale_seconds = time.time() - self._last_tick_received_at
                    if stale_seconds > max(5.0, self.max_tick_age_seconds):
                        self._feed_health["status"] = "STALE"
                    elif self._feed_health.get("connected"):
                        self._feed_health["status"] = "CONNECTED"
                elif self._feed_health.get("connected"):
                    self._feed_health["status"] = "CONNECTED"
                else:
                    self._feed_health["status"] = self._feed_health.get("status", "DISCONNECTED")

    def _update_feed_health(self, token: Optional[str] = None, latency_ms: Optional[float] = None) -> None:
        """Refreshes health markers after a tick arrives."""
        now = datetime.now()
        self._last_tick_received_at = time.time()
        with self._lock:
            self._feed_health["connected"] = True
            self._feed_health["status"] = "CONNECTED"
            self._feed_health["last_tick_timestamp"] = now.isoformat(timespec="seconds")
            self._feed_health["last_tick_token"] = token
            self._feed_health["last_seen"] = now.isoformat(timespec="seconds")
            if latency_ms is not None:
                self._feed_health["latency_ms"] = float(latency_ms)
            self._feed_health["queue_size"] = self._tick_queue.qsize()
            self._feed_health["last_heartbeat"] = now.isoformat(timespec="seconds")

    def get_feed_health(self) -> Dict[str, Any]:
        """Returns a lightweight public health snapshot for monitoring and dashboards."""
        with self._lock:
            self._feed_health["queue_size"] = self._tick_queue.qsize()
            if self.angel_api and hasattr(self.angel_api, "ws_connected"):
                self._feed_health["connected"] = bool(getattr(self.angel_api, "ws_connected"))
                self._feed_health["reconnect_attempts"] = int(getattr(self.angel_api, "reconnect_counter", 0))
            return dict(self._feed_health)

    def get_live_tick_validated(
        self, token: str, exchange: str = "NSE", symbol: str = ""
    ) -> float:
        """
        Validates tick timestamp. If tick is older than `max_tick_age_seconds`,
        it flags it as stale and falls back to Direct REST API LTP.
        """
        is_stale = False
        ltp = 0.0

        with self._lock:
            tick_info = self._live_ticks.get(token)
            if tick_info:
                tick_age = (datetime.now() - tick_info["timestamp"]).total_seconds()
                if tick_age <= self.max_tick_age_seconds and tick_info["ltp"] > 0:
                    return tick_info["ltp"]
                is_stale = True

        if is_stale:
            logger.warning(
                f"Stale WebSocket Tick detected for Token {token}. Falling back to REST API."
            )

        # REST API Fallback outside lock to prevent blocking
        if self.angel_api and hasattr(self.angel_api, "get_ltp"):
            try:
                return float(self.angel_api.get_ltp(exchange, symbol, token))
            except Exception as e:
                logger.error(f"Error fetching REST API LTP for {symbol} ({token}): {e}")
        return ltp

    # =========================================================================
    # 2. AUTO CANDLE RESAMPLING ENGINE (1m, 5m, 15m)
    # =========================================================================

    def _update_live_candles_from_tick(
        self, token: str, price: float, volume_delta: int, timestamp: datetime
    ):
        """Constructs live OHLCV candles (1m, 5m, 15m) using tick volume deltas."""
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

                bucket_minute = (timestamp.minute // tf_mins) * tf_mins
                candle_time = timestamp.replace(
                    minute=bucket_minute, second=0, microsecond=0
                )

                if candles_list and candles_list[-1]["timestamp"] == candle_time:
                    current = candles_list[-1]
                    current["high"] = max(current["high"], price)
                    current["low"] = min(current["low"], price)
                    current["close"] = price
                    current["volume"] += volume_delta
                else:
                    candles_list.append(
                        {
                            "timestamp": candle_time,
                            "open": price,
                            "high": price,
                            "low": price,
                            "close": price,
                            "volume": volume_delta,
                        }
                    )
                    if len(candles_list) > 500:
                        candles_list.pop(0)

    # =========================================================================
    # 3. CACHE EXPIRY MANAGEMENT & DATAFRAME FETCHING
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
        elapsed_seconds = (datetime.now() - cache_entry["last_updated"]).total_seconds()

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
                return cache_entry["df"].copy()

        to_date = datetime.now().strftime("%Y-%m-%d %H:%M")
        from_date = (datetime.now() - timedelta(days=days_back)).strftime(
            "%Y-%m-%d 09:15"
        )

        raw_candles = []
        if self.angel_api and hasattr(self.angel_api, "get_historical_data"):
            try:
                raw_candles = self.angel_api.get_historical_data(
                    exchange=exchange,
                    symbol_token=symbol_token,
                    interval=interval,
                    from_date=from_date,
                    to_date=to_date,
                )
            except Exception as e:
                logger.error(f"API Error fetching historical candles for {symbol}: {e}")

        if not raw_candles:
            logger.warning(f"No historical candles retrieved for {symbol} [{interval}]")
            return pd.DataFrame()

        try:
            df = pd.DataFrame(
                raw_candles,
                columns=["timestamp", "open", "high", "low", "close", "volume"],
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            numeric_cols = ["open", "high", "low", "close", "volume"]
            df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce")
            df.sort_values(by="timestamp", inplace=True)
            df.reset_index(drop=True, inplace=True)

            with self._lock:
                self._candle_cache[cache_key] = {
                    "df": df,
                    "last_updated": datetime.now(),
                }

            return df
        except Exception as e:
            logger.error(f"Error parsing dataframe for {symbol}: {e}")
            return pd.DataFrame()

    # =========================================================================
    # 4. BATCH HISTORICAL DOWNLOADER
    # =========================================================================

    def download_batch_historical_data(
        self,
        symbols_info: List[Dict[str, str]],
        interval: str = "FIVE_MINUTE",
        days_back: int = 5,
    ) -> Dict[str, pd.DataFrame]:
        """Downloads historical candles for multiple symbols concurrently."""
        logger.info(
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
                    logger.info(f"Downloaded {len(df)} candles for {sym}")
                except Exception as e:
                    logger.error(f"Failed batch download item: {e}")

        return results

    # =========================================================================
    # 5. OPTION PREMIUM & SPOT DATA
    # =========================================================================

    def get_spot_price(self, symbol: str, exchange: str = "NSE") -> float:
        """Fetches spot price using timestamp validation."""
        token = ""
        if self.angel_api and hasattr(self.angel_api, "get_symbol_token"):
            try:
                token = self.angel_api.get_symbol_token(symbol, exchange)
            except Exception as e:
                logger.error(f"Error fetching token for symbol {symbol}: {e}")

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

        token = ""
        if self.angel_api and hasattr(self.angel_api, "get_symbol_token"):
            try:
                token = self.angel_api.get_symbol_token(option_symbol, "NFO")
            except Exception as e:
                logger.error(f"Error getting option symbol token for {option_symbol}: {e}")

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
market_data = MARKET_DATA
