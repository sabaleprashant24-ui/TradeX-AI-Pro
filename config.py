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
from dotenv import load_dotenv

load_dotenv()

class Config:
    # .env मधून TOTP Key घेऊन त्यातील स्पेस आणि कॅपिटल/स्मॉल लेटर्सचा प्रॉब्लेम फिक्स करणे
    TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET", "").replace(" ", "").upper().strip()


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
    api_key: str = field(default_factory=lambda: os.getenv("ANGEL_API_KEY", ""))
    client_id: str = field(default_factory=lambda: os.getenv("ANGEL_CLIENT_ID", ""))
    password: str = field(default_factory=lambda: os.getenv("ANGEL_PASSWORD", ""))
    totp_secret: str = field(default_factory=lambda: os.getenv("ANGEL_TOTP_SECRET", ""))
    
    # Session Details (Dynamically updated post login)
    jwt_token: str = ""
    refresh_token: str = ""
    feed_token: str = ""


@dataclass
class RiskConfig:
    """Risk Management and Capital Allocation Rules."""
    total_capital: float = 100000.0   # Total trading account capital in INR
    risk_per_trade_pct: float = 1.0   # Maximum 1% capital risk per trade
    max_daily_loss_pct: float = 2.0   # Stop trading if daily loss hits 2%
    max_open_positions: int = 3       # Max simultaneous active positions
    reward_to_risk_ratio: float = 2.0 # Minimum 1:2 Risk to Reward
    default_sl_pct: float = 0.5       # Default Stop Loss %
    default_target_pct: float = 1.0   # Default Target Profit %
    enable_trailing_sl: bool = True
    trailing_sl_step_pct: float = 0.2  # Trail SL every 0.2% movement in favor

    symbol_exposure_limits_pct: Dict[str, float] = field(
        default_factory=lambda: {
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
    )


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
            "CRUDEOIL": 100,
            "GOLD": 100,
            "SILVER": 30,
            "NATURALGAS": 1250,
        }
    )


@dataclass
class MarketTimingConfig:
    """Market Timings and Execution Windows."""
    # Equity / F&O Timings
    market_open: time = time(9, 15)
    market_close: time = time(15, 30)
    no_new_entry_after: time = time(15, 0)
    square_off_time: time = time(15, 15)
    
    # MCX Commodity Timings
    mcx_open: time = time(9, 0)
    mcx_close: time = time(23, 30)
    mcx_square_off: time = time(23, 15)
    
    # Strategy Windows
    hero_zero_start: time = time(13, 30)
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
    
    # Database Settings
    DB_PATH: str = "tradex_ai.db"
    DB_TIMEOUT: int = 30
    LOG_DIRECTORY: str = "logs"

    @property
    def INITIAL_CAPITAL(self) -> float:
        return float(os.getenv("CAPITAL", self.risk.total_capital))

    @property
    def BROKER_TYPE(self) -> str:
        return os.getenv("BROKER_TYPE", os.getenv("TRADING_MODE", self.env.value)).upper()

    @property
    def EXECUTION_MODE(self) -> str:
        return os.getenv("EXECUTION_MODE", "PAPER_TRADE" if self.BROKER_TYPE == "PAPER" else self.mode.value).upper()

    @property
    def SCAN_INTERVAL(self) -> int:
        return int(os.getenv("SCAN_INTERVAL", self.scanner.scan_interval_seconds))

    @property
    def DEFAULT_SYMBOLS(self) -> list[str]:
        symbols = os.getenv("DEFAULT_SYMBOLS", "NIFTY,BANKNIFTY")
        return [symbol.strip().upper() for symbol in symbols.split(",") if symbol.strip()]


# Global Config Instances
CONFIG = AppConfig()
Config = CONFIG  # Alias for Database/Logger modules compatibility

LIVE_BROKER_CONFIG = {
    "active_broker": CONFIG.BROKER_TYPE,
    "api_key": os.getenv("ANGEL_API_KEY", os.getenv("API_KEY", "")),
    "client_code": os.getenv("ANGEL_CLIENT_ID", os.getenv("CLIENT_CODE", "")),
    "pin": os.getenv("ANGEL_PASSWORD", os.getenv("PASSWORD", "")),
    "totp_secret": os.getenv("ANGEL_TOTP_SECRET", os.getenv("TOTP_SECRET", "")).replace(" ", "").upper().strip(),
}
