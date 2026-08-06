"""
TradeX AI Pro v4.0 - Risk & Portfolio Governance Engine
File: risk_manager.py

Provides Enterprise-Grade Capital Protection & Portfolio Risk Governance:
- Startup Risk Config Validation Engine (Range & Type Safety)
- Thread-Safe Shared State Management using Re-entrant Lock (threading.RLock)
- Dynamic Daily Loss Breaker & Profit Protection Lock (50% Profit Retention)
- Strict Consecutive Loss Cooldown Engine (Time-based Block)
- Dynamic Position Sizing Calculation
- Granular Exposure Controls (Symbol, Sector, Option CE/PE, Overnight Limits)
- Dynamic Volatility Sizing (ATR % & India VIX Matrix)

Compatible with Python 3.13 and Pydroid 3.
"""

from datetime import datetime, date, timedelta
import math
import threading
from typing import Dict, Any, Optional, List

from logger import LOGGER

# Safe Config Import with Defaults Fallback
try:
    from config import CONFIG
    _rc = CONFIG.risk
    RISK_CONFIG = {
        "max_daily_loss_percent": getattr(_rc, "max_daily_loss_pct", 2.0),
        "max_drawdown_percent": 10.0,
        "max_consecutive_losses": 3,
        "cooldown_minutes": 30,
        "profit_lock_threshold_percent": 3.0,
        "max_symbol_exposure_percent": 20.0,
        "max_sector_exposure_percent": 35.0,
        "max_options_ce_exposure_percent": 30.0,
        "max_options_pe_exposure_percent": 30.0,
        "max_overnight_exposure_percent": 40.0,
        "risk_per_trade_pct": getattr(_rc, "risk_per_trade_pct", 1.0),
    }
except (ImportError, AttributeError):
    RISK_CONFIG = {
        "max_daily_loss_percent": 2.0,
        "max_drawdown_percent": 10.0,
        "max_consecutive_losses": 3,
        "cooldown_minutes": 30,
        "profit_lock_threshold_percent": 3.0,
        "max_symbol_exposure_percent": 20.0,
        "max_sector_exposure_percent": 35.0,
        "max_options_ce_exposure_percent": 30.0,
        "max_options_pe_exposure_percent": 30.0,
        "max_overnight_exposure_percent": 40.0,
        "risk_per_trade_pct": 1.0,
    }


class ConfigValidationError(ValueError):
    """Custom exception raised when Risk Configuration parameters are invalid."""
    pass


def validate_risk_config(config: Dict[str, Any]) -> None:
    """Validates Risk Configuration metrics at startup to ensure parameters stay within logical ranges."""
    percentage_keys = [
        "max_daily_loss_percent",
        "max_drawdown_percent",
        "profit_lock_threshold_percent",
        "max_symbol_exposure_percent",
        "max_sector_exposure_percent",
        "max_options_ce_exposure_percent",
        "max_options_pe_exposure_percent",
        "max_overnight_exposure_percent",
        "risk_per_trade_pct",
    ]

    for key in percentage_keys:
        if key in config:
            val = config[key]
            if not isinstance(val, (int, float)):
                raise ConfigValidationError(f"Invalid type for {key}: Expected float/int, got {type(val).__name__}.")
            if val <= 0.0 or val > 100.0:
                raise ConfigValidationError(f"Invalid value for {key}: ({val}). Must be between 0 and 100.")

    integer_keys = ["max_consecutive_losses", "cooldown_minutes"]
    for key in integer_keys:
        if key in config:
            val = config[key]
            if not isinstance(val, int) or val <= 0:
                raise ConfigValidationError(f"Invalid value for {key}: ({val}). Must be a positive integer.")

    LOGGER.info("RISK CONFIG VALIDATION PASSED: All risk parameters are safe and verified.")


# Execute Config Validation at Module Import
validate_risk_config(RISK_CONFIG)


class PortfolioRiskManager:
    """Enterprise Risk Governance Engine for Capital Protection (Thread-Safe via RLock)."""

    def __init__(self, initial_capital: float = 100000.0):
        self._lock = threading.RLock()

        self.initial_capital = float(initial_capital)
        self.starting_daily_balance = float(initial_capital)
        self.current_balance = float(initial_capital)
        self.peak_balance = float(initial_capital)
        
        self.daily_pnl: float = 0.0
        self.peak_daily_profit: float = 0.0
        self.consecutive_losses: int = 0
        self.cooldown_until: Optional[datetime] = None
        
        self.trading_locked: bool = False
        self.lock_reason: str = ""
        self.current_date: date = datetime.now().date()

    def _check_and_reset_daily_stats(self):
        """Resets daily loss counter at start of new trading day. (Assumes lock held)."""
        today = datetime.now().date()
        if today > self.current_date:
            LOGGER.info(f"New Trading Day Recognized ({today}). Resetting Daily PnL Limits.")
            self.current_date = today
            self.starting_daily_balance = self.current_balance
            self.daily_pnl = 0.0
            self.peak_daily_profit = 0.0
            self.consecutive_losses = 0
            self.cooldown_until = None
            self.trading_locked = False
            self.lock_reason = ""

    def update_account_state(self, current_balance: float, closed_pnl: float) -> Dict[str, Any]:
        """Thread-safe update of account balance, daily PnL, profit locks & cooldowns."""
        with self._lock:
            self._check_and_reset_daily_stats()

            self.current_balance = float(current_balance)
            self.daily_pnl += float(closed_pnl)

            # Track Peak Daily Profit for Profit Lock
            if self.daily_pnl > self.peak_daily_profit:
                self.peak_daily_profit = self.daily_pnl

            # Track Overall Peak Balance for Drawdown calculation
            if self.current_balance > self.peak_balance:
                self.peak_balance = self.current_balance

            # Consecutive Loss & Cooldown Logic
            if closed_pnl < 0:
                self.consecutive_losses += 1
                max_streak = RISK_CONFIG.get("max_consecutive_losses", 3)
                if self.consecutive_losses >= max_streak:
                    cd_mins = RISK_CONFIG.get("cooldown_minutes", 30)
                    self.cooldown_until = datetime.now() + timedelta(minutes=cd_mins)
                    LOGGER.warning(f"COOLING PERIOD ACTIVATED: {self.consecutive_losses} losses in a row. Blocked for {cd_mins} mins.")
            elif closed_pnl > 0:
                self.consecutive_losses = 0

            # Evaluate Circuit Breakers
            self._evaluate_risk_limits()

            return self.get_status()

    def _evaluate_risk_limits(self):
        """Evaluates Daily Loss, Drawdown, and Profit Lock Limits. (Assumes lock held)."""
        if self.trading_locked:
            return

        # 1. Daily Max Loss Limit Check
        max_daily_loss = self.starting_daily_balance * (RISK_CONFIG.get("max_daily_loss_percent", 2.0) / 100.0)
        if self.daily_pnl <= -max_daily_loss:
            self.trading_locked = True
            self.lock_reason = f"DAILY LOSS LIMIT BREACHED (-₹{abs(self.daily_pnl):.2f} >= ₹{max_daily_loss:.2f})"
            LOGGER.error(f"CIRCUIT BREAKER TRIGGERED: {self.lock_reason}")
            return

        # 2. Maximum Drawdown Check
        max_dd_percent = RISK_CONFIG.get("max_drawdown_percent", 10.0)
        current_drawdown = ((self.peak_balance - self.current_balance) / self.peak_balance) * 100.0 if self.peak_balance > 0 else 0.0
        if current_drawdown >= max_dd_percent:
            self.trading_locked = True
            self.lock_reason = f"MAX DRAWDOWN BREACHED ({current_drawdown:.2f}% >= {max_dd_percent}%)"
            LOGGER.error(f"CIRCUIT BREAKER TRIGGERED: {self.lock_reason}")
            return

        # 3. Dynamic Profit Protection Lock (50% Profit Retention)
        profit_lock_pct = RISK_CONFIG.get("profit_lock_threshold_percent", 3.0)
        profit_threshold = self.starting_daily_balance * (profit_lock_pct / 100.0)

        if self.peak_daily_profit >= profit_threshold:
            locked_profit_floor = self.peak_daily_profit * 0.50
            if self.daily_pnl < locked_profit_floor:
                self.trading_locked = True
                self.lock_reason = f"PROFIT LOCK TRIGGERED: Daily profit dropped from ₹{self.peak_daily_profit:.2f} below floor ₹{locked_profit_floor:.2f}"
                LOGGER.warning(f"CIRCUIT BREAKER TRIGGERED: {self.lock_reason}")

    def get_adjusted_risk_multiplier(
        self,
        atr_percent: float = 0.0,
        india_vix: float = 0.0,
    ) -> float:
        """Thread-safe calculation of dynamic risk multiplier based on ATR %, India VIX, and Profit State."""
        with self._lock:
            multiplier = 1.0

            # Dynamic Volatility Scaling via India VIX
            if india_vix > 28.0:
                multiplier *= 0.25
                LOGGER.info(f"VOLATILITY RISK SCALED: Extreme India VIX ({india_vix}) -> Risk Multiplier: 0.25x")
            elif india_vix > 22.0 or atr_percent > 2.5:
                multiplier *= 0.50
                LOGGER.info(f"VOLATILITY RISK SCALED: High VIX/ATR ({india_vix} VIX / {atr_percent}% ATR) -> Risk Multiplier: 0.5x")

            # Dynamic Profit Protection Scaling
            profit_threshold = self.starting_daily_balance * (RISK_CONFIG.get("profit_lock_threshold_percent", 3.0) / 100.0)
            if self.daily_pnl >= profit_threshold:
                multiplier *= 0.50
                LOGGER.info("PROFIT PROTECTION ACTIVE: Risk per trade reduced by 50% to lock gains.")

            return round(multiplier, 2)

    def calculate_position_size(
        self,
        entry_price: float,
        stop_loss_price: float,
        lot_size: int = 1,
        risk_multiplier: float = 1.0,
    ) -> int:
        """Calculates lot-adjusted quantity based on risk per trade and stop loss points."""
        with self._lock:
            if entry_price <= 0 or stop_loss_price <= 0:
                return 0

            risk_per_share = abs(entry_price - stop_loss_price)
            if risk_per_share == 0:
                return 0

            base_risk_pct = RISK_CONFIG.get("risk_per_trade_pct", 1.0)
            max_risk_amount = self.current_balance * (base_risk_pct / 100.0) * risk_multiplier

            raw_qty = max_risk_amount / risk_per_share

            # Round down to nearest valid lot size
            if lot_size > 1:
                number_of_lots = math.floor(raw_qty / lot_size)
                final_qty = number_of_lots * lot_size
            else:
                final_qty = math.floor(raw_qty)

            return max(final_qty, 0)

    def can_trade(
        self,
        requested_capital: float = 0.0,
        symbol: str = "",
        sector: str = "",
        option_type: str = "NONE",  # "CE", "PE", or "NONE"
        is_overnight: bool = False,
        active_positions_metadata: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Thread-safe Gatekeeper: Validates Circuit Breaker, Cooldown, Symbol/Sector/Options/Overnight Exposure."""
        with self._lock:
            self._check_and_reset_daily_stats()

            # 1. Check Circuit Breaker Lock
            if self.trading_locked:
                return {"allowed": False, "reason": f"TRADING LOCKED: {self.lock_reason}"}

            # 2. Check Cooling Period Timer
            if self.cooldown_until and datetime.now() < self.cooldown_until:
                remaining_sec = int((self.cooldown_until - datetime.now()).total_seconds())
                return {
                    "allowed": False,
                    "reason": f"COOLING PERIOD ACTIVE: Wait {remaining_sec // 60}m {remaining_sec % 60}s remaining.",
                }

            active_positions_metadata = active_positions_metadata or []
            symbol_upper = symbol.upper() if symbol else ""
            sector_upper = sector.upper() if sector else ""
            opt_type_upper = option_type.upper() if option_type else "NONE"

            # 3. Symbol Exposure Limit
            max_sym_pct = RISK_CONFIG.get("max_symbol_exposure_percent", 20.0)
            max_allowed_sym = self.current_balance * (max_sym_pct / 100.0)
            existing_sym_cap = sum(
                float(pos.get("capital", 0.0))
                for pos in active_positions_metadata
                if str(pos.get("symbol", "")).upper() == symbol_upper
            )
            if (existing_sym_cap + requested_capital) > max_allowed_sym:
                return {
                    "allowed": False,
                    "reason": f"SYMBOL EXPOSURE BREACHED for {symbol}: Requested ₹{requested_capital:.2f} + Existing ₹{existing_sym_cap:.2f} exceeds limit ₹{max_allowed_sym:.2f}.",
                }

            # 4. Sector Exposure Limit
            if sector_upper and sector_upper != "GENERAL":
                max_sec_pct = RISK_CONFIG.get("max_sector_exposure_percent", 35.0)
                max_allowed_sec = self.current_balance * (max_sec_pct / 100.0)
                existing_sec_cap = sum(
                    float(pos.get("capital", 0.0))
                    for pos in active_positions_metadata
                    if str(pos.get("sector", "")).upper() == sector_upper
                )
                if (existing_sec_cap + requested_capital) > max_allowed_sec:
                    return {
                        "allowed": False,
                        "reason": f"SECTOR EXPOSURE BREACHED for {sector}: Total ₹{existing_sec_cap + requested_capital:.2f} exceeds sector limit ₹{max_allowed_sec:.2f}.",
                    }

            # 5. Options CE / PE Directional Exposure Limit
            if opt_type_upper in ["CE", "PE"]:
                opt_key = f"max_options_{opt_type_upper.lower()}_exposure_percent"
                max_opt_pct = RISK_CONFIG.get(opt_key, 30.0)
                max_allowed_opt = self.current_balance * (max_opt_pct / 100.0)
                existing_opt_cap = sum(
                    float(pos.get("capital", 0.0))
                    for pos in active_positions_metadata
                    if str(pos.get("option_type", "")).upper() == opt_type_upper
                )
                if (existing_opt_cap + requested_capital) > max_allowed_opt:
                    return {
                        "allowed": False,
                        "reason": f"OPTION {opt_type_upper} EXPOSURE BREACHED: Total ₹{existing_opt_cap + requested_capital:.2f} exceeds limit ₹{max_allowed_opt:.2f}.",
                    }

            # 6. Overnight Exposure Limit
            if is_overnight:
                max_ovn_pct = RISK_CONFIG.get("max_overnight_exposure_percent", 40.0)
                max_allowed_ovn = self.current_balance * (max_ovn_pct / 100.0)
                existing_ovn_cap = sum(
                    float(pos.get("capital", 0.0))
                    for pos in active_positions_metadata
                    if bool(pos.get("is_overnight", False))
                )
                if (existing_ovn_cap + requested_capital) > max_allowed_ovn:
                    return {
                        "allowed": False,
                        "reason": f"OVERNIGHT EXPOSURE BREACHED: Total ₹{existing_ovn_cap + requested_capital:.2f} exceeds overnight limit ₹{max_allowed_ovn:.2f}.",
                    }

            return {"allowed": True, "reason": "Risk & Exposure checks passed."}

    def get_status(self) -> Dict[str, Any]:
        """Thread-safe public method to return active risk status metrics."""
        with self._lock:
            drawdown_pct = ((self.peak_balance - self.current_balance) / self.peak_balance) * 100.0 if self.peak_balance > 0 else 0.0
            daily_return_pct = (self.daily_pnl / self.starting_daily_balance) * 100.0 if self.starting_daily_balance > 0 else 0.0

            return {
                "current_balance": round(self.current_balance, 2),
                "daily_pnl": round(self.daily_pnl, 2),
                "peak_daily_profit": round(self.peak_daily_profit, 2),
                "daily_return_pct": round(daily_return_pct, 2),
                "current_drawdown_pct": round(drawdown_pct, 2),
                "consecutive_losses": self.consecutive_losses,
                "cooldown_active": self.cooldown_until is not None and datetime.now() < self.cooldown_until,
                "trading_locked": self.trading_locked,
                "lock_reason": self.lock_reason,
            }


# Backwards Compatibility Alias and Singleton Instance Creation
RiskManager = PortfolioRiskManager
RISK_MANAGER = PortfolioRiskManager()
risk = RISK_MANAGER
