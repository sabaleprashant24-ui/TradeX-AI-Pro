"""
TradeX AI Pro v4.0 - Configuration Module
File: config.py

Central Configuration Management for Angel One SmartAPI, Risk Parameters,
Market Timing, Lot Sizes, Timeframes, and Trading Modes. Loads sensitive
credentials securely from environment variables.

Compatible with Python 3.13 and Pydroid 3.
"""

from dataclasses import dataclass, field
from datetime import time
from enum import Enum
import os
from typing import Dict


class TradingEnvironment(Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"


class ExecutionMode(Enum):
    INTRADAY = "INTRADAY"
    HERO_ZERO = "HERO_ZERO"
    SCALPING = "SCALPING"


@dataclass
class APIConfig:
    """Angel One SmartAPI Parameters & Credentials fetched securely."""
    api_key: str = os.getenv("ANGEL_API_KEY", "")
    client_id: str = os.getenv("ANGEL_CLIENT_ID", "")
    password: str = os.getenv("ANGEL_PASSWORD", "")
    totp_secret: str = os.getenv("ANGEL_TOTP_SECRET", "")
    
    # Session Details (Dynamically updated post login)
    jwt_token: str = ""
    refresh_token: str = ""
    feed_token: str = ""


@dataclass
class RiskConfig:
    """Risk Management and Capital Allocation Rules."""
    total_capital: float = 100000.0  # Total trading account capital in INR
    risk_per_trade_pct: float = 1.0   # Maximum 1% capital risk per trade
    max_daily_loss_pct: float = 2.0   # Stop trading if daily loss hits 2%
    max_open_positions: int = 3       # Max simultaneous active positions
    reward_to_risk_ratio: float = 2.0 # Minimum 1:2 Risk to Reward
    default_sl_pct: float = 0.5       # Default Stop Loss %
    default_target_pct: float = 1.0   # Default Target Profit %
    enable_trailing_sl: bool = True
    trailing_sl_step_pct: float = 0.2  # Trail SL every 0.2% movement in favor
# config.py मधील RiskConfig मध्ये:
symbol_exposure_limits_pct: Dict[str, float] = {
    "NIFTY": 20.0,
    "BANKNIFTY": 20.0,
    "FINNIFTY": 15.0,
    "MIDCPNIFTY": 15.0,
    "SENSEX": 20.0,
    "CRUDEOIL": 15.0,
    "GOLD": 15.0,
    "NATURALGAS": 15.0,
    "DEFAULT": 15.0,
}

@dataclass
class LotSizeConfig:
    """Indian Exchange Index & Asset Lot Sizes."""
    lots: Dict[str, int] = field(
        default_factory=lambda: {
            "NIFTY": 25,
            "BANKNIFTY": 15,
            "FINNIFTY": 25,
            "MIDCPNIFTY": 50,
            "SENSEX": 10,
            "BANKEX": 15,
            "MCX_CRUDEOIL": 100,
            "MCX_GOLD": 100,
            "MCX_SILVER": 30,
        }
    )


@dataclass
class MarketTimingConfig:
    """Market Timings and Execution Windows."""
    market_open: time = time(9, 15)
    market_close: time = time(15, 30)
    no_new_entry_after: time = time(15, 0)
    square_off_time: time = time(15, 15)
    hero_zero_start: time = time(13, 30)  # Hero Zero activation window
    hero_zero_end: time = time(15, 10)


@dataclass
class TimeframeConfig:
    """Supported Chart Candles Timeframes."""
    scalping_tf: str = "ONE_MINUTE"
    intraday_tf: str = "FIVE_MINUTE"
    trend_tf: str = "FIFTEEN_MINUTE"
    daily_tf: str = "ONE_DAY"


@dataclass
class ScannerConfig:
    """Scanner Settings."""
    volume_spike_threshold: float = 2.5  # 2.5x average volume
    momentum_rsi_upper: float = 65.0
    momentum_rsi_lower: float = 35.0
    scan_interval_seconds: int = 5


@dataclass
class AppConfig:
    """Global Application Main Configuration Manager."""
    env: TradingEnvironment = TradingEnvironment.PAPER
    mode: ExecutionMode = ExecutionMode.INTRADAY
    api: APIConfig = field(default_factory=APIConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    lots: LotSizeConfig = field(default_factory=LotSizeConfig)
    timing: MarketTimingConfig = field(default_factory=MarketTimingConfig)
    timeframe: TimeframeConfig = field(default_factory=TimeframeConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    database_path: str = "tradex_ai.db"
    log_directory: str = "logs"


# Global Config Instance
CONFIG = AppConfig()