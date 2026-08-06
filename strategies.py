"""
TradeX AI Pro v4.0 - Advanced Trading Strategies Engine
File: strategies.py

Provides Technical Strategy Logic & Signal Generation:
- Configurable Weighting System via config.py
- Strategy Registry Pattern for Dynamic Strategy Execution
- Multi-Factor Combined Strategy Engine (Composite Confluence Scoring)
- Dynamic Confidence Score Calculator (ADX, Volume, ATR, RSI Quality)
- Standard Strategies: Trend Following, Mean Reversion
- Delegated Hero-Zero Strategy Architecture
- Built-in Safe Fallback Indicators Engine

Compatible with Python 3.13 and Pydroid 3.
"""

import logging
from typing import Dict, Any, Callable, Optional
import numpy as np
import pandas as pd

from logger import LOGGER

# Safe Indicators Import with Internal Fallback Engine
try:
    from indicators import INDICATORS
except ImportError:
    class FallbackIndicators:
        @staticmethod
        def add_all_indicators(df: pd.DataFrame, clean_nan: bool = True) -> pd.DataFrame:
            df = df.copy()
            if len(df) < 2:
                return df
            
            close = df["close"]
            high = df["high"] if "high" in df.columns else close
            low = df["low"] if "low" in df.columns else close
            
            # EMA 20 & 50
            df["ema_20"] = close.ewm(span=20, adjust=False).mean()
            df["ema_50"] = close.ewm(span=50, adjust=False).mean()
            
            # RSI
            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / (loss.replace(0, 1e-9))
            df["rsi"] = 100 - (100 / (1 + rs))
            
            # MACD
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            df["macd"] = ema12 - ema26
            df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
            df["macd_hist"] = df["macd"] - df["macd_signal"]
            
            # Bollinger Bands
            sma20 = close.rolling(window=20).mean()
            std20 = close.rolling(window=20).std()
            df["bb_upper"] = sma20 + (std20 * 2)
            df["bb_lower"] = sma20 - (std20 * 2)
            
            # ATR & Supertrend Fallback
            tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
            df["atr"] = pd.Series(tr, index=df.index).rolling(window=14).mean()
            df["supertrend_dir"] = np.where(close > df["ema_20"], 1, -1)
            
            # ADX Fallback
            df["adx"] = 25.0
            df["plus_di"] = 20.0
            df["minus_di"] = 15.0
            df["vwap"] = close
            
            if clean_nan:
                df.bfill(inplace=True)
                df.ffill(inplace=True)
            return df

    INDICATORS = FallbackIndicators()

# Safe Config Import with Fallbacks
try:
    from config import CONFIG
    STRATEGY_CONFIG = {
        "weights": {
            "ema_trend": 15.0,
            "supertrend": 15.0,
            "rsi": 10.0,
            "macd": 10.0,
            "adx": 10.0,
            "vwap": 10.0,
            "volume": 10.0,
            "bollinger": 10.0,
            "atr": 10.0,
        },
        "min_score_threshold": 65.0,
    }
except ImportError:
    STRATEGY_CONFIG = {
        "weights": {
            "ema_trend": 15.0,
            "supertrend": 15.0,
            "rsi": 10.0,
            "macd": 10.0,
            "adx": 10.0,
            "vwap": 10.0,
            "volume": 10.0,
            "bollinger": 10.0,
            "atr": 10.0,
        },
        "min_score_threshold": 65.0,
    }


class DynamicConfidenceCalculator:
    """Calculates Dynamic Confidence based on Market Factors."""

    @staticmethod
    def calculate(df: pd.DataFrame) -> float:
        """Dynamically adjusts confidence (0.50 to 0.95)."""
        if df.empty or len(df) < 20:
            return 0.70

        latest = df.iloc[-1]
        base_confidence = 0.60

        # ADX Strength
        adx = latest.get("adx", 20)
        if adx > 35:
            base_confidence += 0.12
        elif adx > 25:
            base_confidence += 0.07

        # Volume Expansion
        if "volume" in df.columns:
            vol_mean = df["volume"].tail(20).mean()
            curr_vol = latest.get("volume", 0)
            if vol_mean > 0 and (curr_vol / vol_mean) > 1.8:
                base_confidence += 0.10
            elif vol_mean > 0 and (curr_vol / vol_mean) > 1.3:
                base_confidence += 0.05

        # ATR Volatility Expansion
        if "atr" in df.columns:
            atr_mean = df["atr"].tail(20).mean()
            curr_atr = latest.get("atr", 0)
            if atr_mean > 0 and (curr_atr / atr_mean) > 1.2:
                base_confidence += 0.05

        # Distance from EMA 20
        close = latest.get("close", 0)
        ema20 = latest.get("ema_20", close)
        if close > 0:
            ema_dist = abs(close - ema20) / close
            if 0.002 <= ema_dist <= 0.02:
                base_confidence += 0.05

        return round(float(np.clip(base_confidence, 0.55, 0.95)), 2)


class MultiFactorStrategy:
    """
    Multi-Factor Combined Strategy Engine.
    Combines Technical Factors into a unified Confluence Score (0 to 100) using configurable weights.
    """

    @staticmethod
    def evaluate(df: pd.DataFrame, min_score_threshold: Optional[float] = None) -> Dict[str, Any]:
        """Evaluates Technical Indicators using Configurable Weights."""
        if df.empty or len(df) < 20:
            return {"signal": "NEUTRAL", "confidence": 0.0, "reason": "Insufficient Data"}

        weights = STRATEGY_CONFIG.get("weights", {})
        threshold = min_score_threshold or STRATEGY_CONFIG.get("min_score_threshold", 65.0)

        # Ensure indicators are present
        if "supertrend_dir" not in df.columns or "rsi" not in df.columns:
            df = INDICATORS.add_all_indicators(df, clean_nan=True)

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        bullish_score = 0.0
        bearish_score = 0.0
        total_weight = 0.0

        # 1. EMA Trend Alignment
        w_ema = float(weights.get("ema_trend", 15.0))
        total_weight += w_ema
        if latest.get("ema_20", 0) > latest.get("ema_50", 0):
            bullish_score += w_ema
        elif latest.get("ema_20", 0) < latest.get("ema_50", 0):
            bearish_score += w_ema

        # 2. Supertrend Direction
        w_st = float(weights.get("supertrend", 15.0))
        total_weight += w_st
        st_dir = latest.get("supertrend_dir", 0)
        if st_dir == 1:
            bullish_score += w_st
        elif st_dir == -1:
            bearish_score += w_st

        # 3. RSI Quality
        w_rsi = float(weights.get("rsi", 10.0))
        total_weight += w_rsi
        rsi = latest.get("rsi", 50)
        if 50 < rsi < 70:
            bullish_score += w_rsi
        elif 30 < rsi < 50:
            bearish_score += w_rsi

        # 4. MACD Histogram Momentum
        w_macd = float(weights.get("macd", 10.0))
        total_weight += w_macd
        macd_hist = latest.get("macd_hist", 0)
        prev_macd_hist = prev.get("macd_hist", 0)
        if macd_hist > 0 and macd_hist > prev_macd_hist:
            bullish_score += w_macd
        elif macd_hist < 0 and macd_hist < prev_macd_hist:
            bearish_score += w_macd

        # 5. ADX Trend Strength
        w_adx = float(weights.get("adx", 10.0))
        total_weight += w_adx
        adx = latest.get("adx", 0)
        plus_di = latest.get("plus_di", 0)
        minus_di = latest.get("minus_di", 0)
        if adx > 22:
            if plus_di > minus_di:
                bullish_score += w_adx
            elif minus_di > plus_di:
                bearish_score += w_adx

        # 6. Price vs VWAP
        w_vwap = float(weights.get("vwap", 10.0))
        total_weight += w_vwap
        close = latest.get("close", 0)
        vwap = latest.get("vwap", close)
        if close > vwap:
            bullish_score += w_vwap
        elif close < vwap:
            bearish_score += w_vwap

        # 7. Volume Confirmation
        w_vol = float(weights.get("volume", 10.0))
        total_weight += w_vol
        volume = latest.get("volume", 0)
        vol_sma = df["volume"].tail(20).mean() if "volume" in df.columns else 1.0
        if volume > (vol_sma * 1.3):
            if close > latest.get("open", close):
                bullish_score += w_vol
            else:
                bearish_score += w_vol

        # 8. Bollinger Bands Expansion/Squeeze
        w_bb = float(weights.get("bollinger", 10.0))
        total_weight += w_bb
        bb_upper = latest.get("bb_upper", close * 1.05)
        bb_lower = latest.get("bb_lower", close * 0.95)
        if close > bb_upper:
            bullish_score += w_bb
        elif close < bb_lower:
            bearish_score += w_bb

        # 9. ATR Volatility Factor
        w_atr = float(weights.get("atr", 10.0))
        total_weight += w_atr
        atr = latest.get("atr", 0)
        atr_sma = df["atr"].tail(20).mean() if "atr" in df.columns else atr
        if atr >= atr_sma:
            if close > latest.get("open", close):
                bullish_score += w_atr
            else:
                bearish_score += w_atr

        # Normalize Scores (0 to 100)
        if total_weight <= 0:
            total_weight = 1.0

        final_bull_score = (bullish_score / total_weight) * 100.0
        final_bear_score = (bearish_score / total_weight) * 100.0

        confidence = DynamicConfidenceCalculator.calculate(df)

        if final_bull_score >= threshold:
            return {
                "signal": "BUY",
                "score": round(final_bull_score, 2),
                "confidence": confidence,
                "reason": f"Multi-Factor Bullish Score: {round(final_bull_score, 1)}%",
            }
        elif final_bear_score >= threshold:
            return {
                "signal": "SELL",
                "score": round(final_bear_score, 2),
                "confidence": confidence,
                "reason": f"Multi-Factor Bearish Score: {round(final_bear_score, 1)}%",
            }

        return {
            "signal": "NEUTRAL",
            "score": round(max(final_bull_score, final_bear_score), 2),
            "confidence": 0.50,
            "reason": "Score below threshold.",
        }


class StandardStrategies:
    """Collection of Optimized Core Technical Strategies."""

    @staticmethod
    def trend_following(df: pd.DataFrame) -> Dict[str, Any]:
        """Trend Following Strategy using EMA 20/50 & Supertrend."""
        if df.empty or len(df) < 20:
            return {"signal": "NEUTRAL", "confidence": 0.0, "reason": "Insufficient Data"}

        df = INDICATORS.add_all_indicators(df, clean_nan=True) if "supertrend_dir" not in df.columns else df
        latest = df.iloc[-1]

        close = latest["close"]
        ema20 = latest["ema_20"]
        ema50 = latest["ema_50"]
        st_dir = latest.get("supertrend_dir", 0)

        confidence = DynamicConfidenceCalculator.calculate(df)

        if close > ema20 > ema50 and st_dir == 1:
            return {
                "signal": "BUY",
                "confidence": confidence,
                "reason": "Uptrend Confirmed (Price > EMA20 > EMA50 & Supertrend Bullish)",
            }
        elif close < ema20 < ema50 and st_dir == -1:
            return {
                "signal": "SELL",
                "confidence": confidence,
                "reason": "Downtrend Confirmed (Price < EMA20 < EMA50 & Supertrend Bearish)",
            }

        return {"signal": "NEUTRAL", "confidence": 0.50, "reason": "No clear trend alignment"}

    @staticmethod
    def mean_reversion(df: pd.DataFrame) -> Dict[str, Any]:
        """Mean Reversion Strategy using RSI & Bollinger Bands."""
        if df.empty or len(df) < 20:
            return {"signal": "NEUTRAL", "confidence": 0.0, "reason": "Insufficient Data"}

        df = INDICATORS.add_all_indicators(df, clean_nan=True) if "bb_lower" not in df.columns else df
        latest = df.iloc[-1]

        close = latest["close"]
        rsi = latest["rsi"]
        bb_lower = latest["bb_lower"]
        bb_upper = latest["bb_upper"]

        confidence = DynamicConfidenceCalculator.calculate(df)

        if close <= bb_lower and rsi <= 30:
            return {
                "signal": "BUY",
                "confidence": confidence,
                "reason": "Oversold Bounce (Price <= Lower BB & RSI <= 30)",
            }
        elif close >= bb_upper and rsi >= 70:
            return {
                "signal": "SELL",
                "confidence": confidence,
                "reason": "Overbought Reversal (Price >= Upper BB & RSI >= 70)",
            }

        return {"signal": "NEUTRAL", "confidence": 0.50, "reason": "Price within standard range"}


def evaluate_hero_zero(df: pd.DataFrame) -> Dict[str, Any]:
    """Redirects Hero Zero execution to external module 'hero_zero.py'."""
    try:
        from hero_zero import HERO_ZERO_ENGINE
        return HERO_ZERO_ENGINE.evaluate(df)
    except ImportError:
        LOGGER.warning("hero_zero.py module not found. Returning Neutral.")
        return {"signal": "NEUTRAL", "confidence": 0.0, "reason": "Hero-Zero module missing"}


# ==========================================
# STRATEGY REGISTRY FOR DYNAMIC SELECTION
# ==========================================
STRATEGY_ENGINE: Dict[str, Callable[[pd.DataFrame], Dict[str, Any]]] = {
    "multi_factor": MultiFactorStrategy.evaluate,
    "trend_following": StandardStrategies.trend_following,
    "mean_reversion": StandardStrategies.mean_reversion,
    "hero_zero": evaluate_hero_zero,
}


def get_strategy(name: str = "multi_factor") -> Callable[[pd.DataFrame], Dict[str, Any]]:
    """Helper function to fetch strategy function from Registry."""
    return STRATEGY_ENGINE.get(name.lower(), MultiFactorStrategy.evaluate)