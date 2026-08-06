"""
TradeX AI Pro v4.0 - Utilities Module
File: utils.py

Provides Time Calculations, Price Rounding, ATM Strike Calculation,
Expiry Logic, Mathematical Helpers, JSON Handling, and Retry Decorator.

Compatible with Python 3.13 and Pydroid 3.
"""

from datetime import datetime, time, timedelta
import functools
import json
import math
import time as time_lib
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from logger import LOGGER


def retry(
    max_retries: int = 3, delay: float = 1.0, backoff: float = 2.0
) -> Callable:
    """Decorator to retry a function call with exponential backoff."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            current_delay = delay
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    LOGGER.warning(
                        f"Attempt {attempt}/{max_retries} failed for {func.__name__}: {e}"
                    )
                    if attempt == max_retries:
                        LOGGER.error(
                            f"Max retries reached for {func.__name__}. Raising exception."
                        )
                        raise e
                    time_lib.sleep(current_delay)
                    current_delay *= backoff

        return wrapper

    return decorator


class TimeUtils:
    """Helper utilities for time and market session checks."""

    @staticmethod
    def get_current_timestamp() -> str:
        """Returns current time in formatted string."""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def is_market_open(
        open_time: time = time(9, 15),
        close_time: time = time(15, 30),
        is_mcx: bool = False,
    ) -> bool:
        """
        Checks if current time falls within market hours and is a weekday.
        Supports MCX segment timings (up to 23:30 / 23:55).
        """
        now_dt = datetime.now()
        # Check if today is Saturday (5) or Sunday (6)
        if now_dt.weekday() in (5, 6):
            return False

        # If MCX is enabled, set close time to 23:30 by default
        if is_mcx and close_time == time(15, 30):
            close_time = time(23, 30)

        now_time = now_dt.time()
        return open_time <= now_time <= close_time

    @staticmethod
    def is_expiry_day(expiry_weekday: int = 3) -> bool:
        """Checks if today is expiry day (Default: Thursday = 3)."""
        return datetime.now().weekday() == expiry_weekday

    @staticmethod
    def seconds_until_market_close(
        close_time: time = time(15, 30)
    ) -> float:
        """Calculates remaining seconds until market close."""
        now = datetime.now()
        market_close = datetime.combine(now.date(), close_time)
        delta = (market_close - now).total_seconds()
        return max(0.0, delta)


class MathUtils:
    """Mathematical and Price Calculation Helpers."""

    @staticmethod
    def round_to_tick(price: float, tick_size: float = 0.05) -> float:
        """Rounds price to nearest valid exchange tick size (e.g., 0.05)."""
        if tick_size <= 0:
            return round(price, 2)
        return round(round(price / tick_size) * tick_size, 2)

    @staticmethod
    def calculate_atm_strike(spot_price: float, step: float = 50.0) -> float:
        """Calculates At-The-Money (ATM) Strike price."""
        if step <= 0:
            return spot_price
        return round(spot_price / step) * step

    @staticmethod
    def get_strike_step(symbol: str) -> float:
        """Returns default strike step distance for Indian indices and commodities."""
        symbol_upper = symbol.upper()
        if "BANKNIFTY" in symbol_upper:
            return 100.0
        elif "SENSEX" in symbol_upper:
            return 100.0
        elif "BANKEX" in symbol_upper:
            return 100.0
        elif "MIDCPNIFTY" in symbol_upper:
            return 25.0
        elif "FINNIFTY" in symbol_upper:
            return 50.0
        elif "CRUDEOIL" in symbol_upper:
            return 100.0
        elif "GOLD" in symbol_upper:
            return 100.0
        elif "NATURALGAS" in symbol_upper:
            return 0.50
        else:  # NIFTY and Default
            return 50.0

    @staticmethod
    def calculate_pnl(
        entry_price: float,
        exit_price: float,
        quantity: int,
        is_buy: bool = True,
    ) -> float:
        """Calculates Gross PnL for a trade position."""
        if is_buy:
            return round((exit_price - entry_price) * quantity, 2)
        else:
            return round((entry_price - exit_price) * quantity, 2)


class JSONUtils:
    """JSON serialization and deserialization helpers."""

    @staticmethod
    def to_json(data: Any) -> str:
        """Converts Python object to JSON string safely."""
        try:
            return json.dumps(data, default=str)
        except Exception as e:
            LOGGER.error(f"Error converting to JSON: {e}")
            return "{}"

    @staticmethod
    def from_json(json_str: str) -> Dict[str, Any]:
        """Parses JSON string to Python dictionary."""
        try:
            return json.loads(json_str)
        except Exception as e:
            LOGGER.error(f"Error parsing JSON: {e}")
            return {}