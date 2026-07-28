"""
TradeX AI Pro v4.0 - Advanced Market Scanner & Orchestration Layer
File: scanner.py

Provides Orchestration across Indicators, Strategies, Risk, and Market Regimes:
- Multi-Timeframe Alignment Analysis (5m, 15m, 1h, Daily)
- Dynamic Strategy Engine Resolution via callable `get_strategy()`
- Configurable Time-based Duplicate Signal Cooldown (TTL Timeout via SCANNER_CONFIG)
- Market Regime & Volatility Profiling (Trending, Range, Volatility, India VIX)
- Circuit Breaker & Risk Governance Gatekeeper Check (via RiskManager)
- Unified Signal Output with Score & Confidence Metrics

Compatible with Python 3.13 and Pydroid 3.
"""

from datetime import datetime, timedelta
import logging
from typing import Dict, Any, List, Optional
import pandas as pd

from logger import LOGGER
from risk_manager import RISK_MANAGER

# Safe Import of Indicators Engine
try:
    from indicators import INDICATORS
except ImportError:
    INDICATORS = None

# Safe Import of Scanner Config
try:
    from config import SCANNER_CONFIG
except ImportError:
    SCANNER_CONFIG = {
        "duplicate_ttl_minutes": 30
    }

# Robust Strategy Loader via Registry Function
try:
    from strategies import get_strategy
except ImportError:
    def get_strategy(strategy_name: str):
        return None


class MarketRegimeProfiler:
    """Classifies Market Regime and Volatility Environment."""

    @staticmethod
    def detect_regime(df_daily: pd.DataFrame, india_vix: float = 0.0) -> Dict[str, Any]:
        """
        Determines if Market is Trending, Ranging, or High Volatility based on ATR% & VIX.
        """
        if df_daily is None or df_daily.empty or len(df_daily) < 20:
            return {"regime": "UNKNOWN", "volatility": "NORMAL", "vix": india_vix}

        close = df_daily["close"].iloc[-1]
        atr = df_daily["ATR"].iloc[-1] if "ATR" in df_daily.columns else 0.0
        atr_pct = (atr / close) * 100.0 if close > 0 else 0.0

        ema20 = df_daily["EMA_20"].iloc[-1] if "EMA_20" in df_daily.columns else close
        ema50 = df_daily["EMA_50"].iloc[-1] if "EMA_50" in df_daily.columns else close

        # Volatility Profiling
        volatility = "NORMAL"
        if india_vix >= 22.0 or atr_pct >= 2.5:
            volatility = "HIGH"
        elif 0 < india_vix <= 12.0:
            volatility = "LOW"

        # Trend Profiling
        ema_diff_pct = abs(ema20 - ema50) / close * 100.0
        regime = "TRENDING" if ema_diff_pct > 0.8 else "RANGEBOUND"

        return {
            "regime": regime,
            "volatility": volatility,
            "atr_percent": round(atr_pct, 2),
            "india_vix": round(india_vix, 2),
        }


class DuplicateSignalFilter:
    """Prevents duplicate spam alerts with a Configurable Time-To-Live (TTL) Cooldown."""

    def __init__(self, ttl_minutes: Optional[int] = None):
        self.ttl_minutes = ttl_minutes or SCANNER_CONFIG.get("duplicate_ttl_minutes", 30)
        # Stores last processed signal: { "NIFTY": {"signal": "BUY", "timestamp": datetime_obj} }
        self._history: Dict[str, Dict[str, Any]] = {}

    def is_duplicate(self, symbol: str, current_signal: str) -> bool:
        """
        Checks if signal is duplicate. Returns True only if same signal occurs
        within the active TTL Window. Reset occurs if signal changes or TTL expires.
        """
        if current_signal == "NEUTRAL":
            return False

        now = datetime.now()
        last_entry = self._history.get(symbol)

        if last_entry:
            last_signal = last_entry.get("signal")
            last_time = last_entry.get("timestamp")
            
            # Check if same signal direction and within Cooldown Window
            if last_signal == current_signal and (now - last_time) < timedelta(minutes=self.ttl_minutes):
                remaining_mins = int((timedelta(minutes=self.ttl_minutes) - (now - last_time)).total_seconds() / 60)
                LOGGER.debug(f"DUPLICATE FILTER: {symbol} {current_signal} blocked. Cooldown active for {remaining_mins}m.")
                return True  # Reject Duplicate

        # Reset / Update State with current Timestamp
        self._history[symbol] = {
            "signal": current_signal,
            "timestamp": now,
        }
        return False


class MarketScanner:
    """Central Orchestration Engine for TradeX AI Pro v4.0."""

    def __init__(self):
        self.regime_profiler = MarketRegimeProfiler()
        self.duplicate_filter = DuplicateSignalFilter()

    def analyze_symbol_mtf(
        self,
        symbol: str,
        mtf_data: Dict[str, pd.DataFrame],  # Expects keys e.g.: "5m", "15m", "1h", "1d"
        sector: str = "GENERAL",
        asset_class: str = "EQUITY",
        option_type: str = "NONE",
        india_vix: float = 0.0,
        is_expiry_day: bool = False,
        requested_capital: float = 25000.0,
    ) -> Dict[str, Any]:
        """
        Orchestrates Indicators -> Callable Strategies -> Risk -> Duplicate Filter.
        """
        # 1. Indicator Calculation Across Timeframes
        processed_mtf = {}
        for tf, df in mtf_data.items():
            if df is not None and not df.empty:
                processed_mtf[tf] = INDICATORS.add_all_indicators(df) if INDICATORS else df

        primary_df = processed_mtf.get("15m") or processed_mtf.get("5m")
        if primary_df is None or primary_df.empty:
            return {"symbol": symbol, "signal": "NEUTRAL", "reason": "Insufficient primary candle data"}

        # 2. Detect Market Regime & Volatility
        daily_df = processed_mtf.get("1d") if "1d" in processed_mtf else primary_df
        regime_info = self.regime_profiler.detect_regime(daily_df, india_vix=india_vix)

        # 3. Dynamic Strategy Resolution via Callable Functions
        strategy_result = {"signal": "NEUTRAL", "score": 0, "confidence": 0.0, "reason": "No Strategy Match"}

        # Expiry Special Hero-Zero Strategy Engine
        if is_expiry_day and option_type in ["CE", "PE"]:
            hero_zero_strat = get_strategy("hero_zero") or get_strategy("HERO_ZERO")
            if callable(hero_zero_strat):
                strategy_result = hero_zero_strat(primary_df, option_type=option_type)

        # Core MultiFactor Strategy Engine Fallback
        if strategy_result.get("signal", "NEUTRAL") == "NEUTRAL":
            multi_factor_strat = get_strategy("multi_factor") or get_strategy("MULTI_FACTOR")
            if callable(multi_factor_strat):
                strategy_result = multi_factor_strat(primary_df)

        raw_signal = strategy_result.get("signal", "NEUTRAL")
        score = strategy_result.get("score", 0)
        confidence = strategy_result.get("confidence", 0.0)

        if raw_signal == "NEUTRAL":
            return {
                "symbol": symbol,
                "signal": "NEUTRAL",
                "score": score,
                "confidence": confidence,
                "reason": strategy_result.get("reason", "No valid setup confirmed"),
            }

        # 4. Multi-Timeframe Alignment Check
        mtf_trend_alignment = self._evaluate_mtf_alignment(processed_mtf, raw_signal)
        if not mtf_trend_alignment["is_aligned"]:
            return {
                "symbol": symbol,
                "signal": "NEUTRAL",
                "score": score,
                "confidence": confidence,
                "reason": f"MTF Conflict: {mtf_trend_alignment['reason']}",
            }

        # Boost score and confidence if MTF aligned
        score = min(100, score + 10)
        confidence = min(100.0, confidence + 5.0)

        # 5. Risk Governance & Exposure Gatekeeper Check
        risk_check = RISK_MANAGER.can_trade(
            requested_capital=requested_capital,
            symbol=symbol,
            sector=sector,
            option_type=option_type,
            active_positions_metadata=[],
        )

        if not risk_check["allowed"]:
            LOGGER.warning(f"SCANNER REJECTED {symbol}: Risk Manager Blocked -> {risk_check['reason']}")
            return {
                "symbol": symbol,
                "signal": "NEUTRAL",
                "score": score,
                "confidence": confidence,
                "reason": f"RISK REJECTED: {risk_check['reason']}",
            }

        # 6. Duplicate Signal Filter Check (Time-to-Live Cooldown Window)
        if self.duplicate_filter.is_duplicate(symbol, raw_signal):
            return {
                "symbol": symbol,
                "signal": "NEUTRAL",
                "score": score,
                "confidence": confidence,
                "reason": f"DUPLICATE FILTER: Signal {raw_signal} within active {self.duplicate_filter.ttl_minutes}m cooldown",
            }

        # 7. Final Consolidated Output Payload
        last_row = primary_df.iloc[-1]
        final_payload = {
            "symbol": symbol,
            "signal": raw_signal,
            "score": score,
            "confidence": f"{confidence:.1f}%",
            "price": round(float(last_row["close"]), 2),
            "sector": sector.upper(),
            "asset_class": asset_class.upper(),
            "option_type": option_type.upper(),
            "market_regime": regime_info["regime"],
            "volatility": regime_info["volatility"],
            "india_vix": regime_info["india_vix"],
            "reason": strategy_result.get("reason", "Multi-Factor MTF Signal Confirmed"),
            "timestamp": datetime.now().isoformat(),
        }

        LOGGER.info(f"FINAL SIGNAL GENERATED: {symbol} -> {raw_signal} | Score: {score} | Conf: {confidence:.1f}%")
        return final_payload

    def _evaluate_mtf_alignment(self, mtf_data: Dict[str, pd.DataFrame], raw_signal: str) -> Dict[str, Any]:
        """Checks if higher timeframes agree with the trading signal direction."""
        aligned_count = 0
        total_timeframes = 0

        for tf, df in mtf_data.items():
            if df is None or len(df) < 20:
                continue
            total_timeframes += 1
            last_row = df.iloc[-1]
            close = last_row["close"]
            ema50 = last_row.get("EMA_50", close)

            if raw_signal == "BUY" and close >= ema50:
                aligned_count += 1
            elif raw_signal == "SELL" and close <= ema50:
                aligned_count += 1

        if total_timeframes == 0:
            return {"is_aligned": True, "reason": "No MTF data available"}

        alignment_ratio = aligned_count / total_timeframes
        if alignment_ratio >= 0.5:
            return {"is_aligned": True, "reason": f"Aligned across {aligned_count}/{total_timeframes} TFs"}
        else:
            return {"is_aligned": False, "reason": f"MTF Mismatch (Only {aligned_count}/{total_timeframes} aligned)"}


# Global Market Scanner Singleton Instance
MARKET_SCANNER = MarketScanner()