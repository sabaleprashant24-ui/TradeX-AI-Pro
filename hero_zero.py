from datetime import datetime
from typing import Dict, Any, Optional
import pandas as pd
import numpy as np

from logger import LOGGER
from indicators import INDICATORS


class HeroZeroEngine:
    """Specialized Option Scalping Engine for Expiry Day Hero-Zero Signals."""

    def __init__(
        self,
        min_volume_multiplier: float = 2.5,
        min_price: float = 5.0,
        max_price: float = 60.0,
    ):
        self.min_vol_mult = min_volume_multiplier
        self.min_price = min_price
        self.max_price = max_price

    def evaluate(self, df: pd.DataFrame, current_time: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Evaluates OTM Option Data for Expiry Day Momentum / Gamma Spike.
        """
        if df is None or df.empty or len(df) < 15:
            LOGGER.warning("HeroZeroEngine: Insufficient dataframe rows for evaluation.")
            return {
                "signal": "NEUTRAL",
                "confidence": 0.0,
                "reason": "Insufficient Data",
                "ai_probability": 0.0,
                "trade_grade": "N/A",
                "entry_window": "N/A",
                "exit_window": "N/A",
                "risk_score": 1.0,
                "signal_age": 0,
                "multi_timeframe_confirmation": False,
            }

        now = current_time or datetime.now()

        # 1. Expiry Day Timing Filter (Post 12:30 PM IST)
        if now.hour < 12 or (now.hour == 12 and now.minute < 30):
            return {
                "signal": "NEUTRAL",
                "confidence": 0.0,
                "reason": "Hero-Zero Strategy active only after 12:30 PM on Expiry Day.",
                "ai_probability": 0.0,
                "trade_grade": "N/A",
                "entry_window": "N/A",
                "exit_window": "N/A",
                "risk_score": 1.0,
                "signal_age": 0,
                "multi_timeframe_confirmation": False,
            }

        # 2. Indicator Calculation Safeguard
        required_cols = {"rsi", "supertrend_dir", "macd_hist", "vwap", "atr"}
        if not required_cols.issubset(df.columns):
            df = INDICATORS.add_all_indicators(df, clean_nan=True)

        latest = df.iloc[-1]
        close_price = float(latest.get("close", 0.0))
        volume = float(latest.get("volume", 0.0))
        vwap = float(latest.get("vwap", close_price))
        atr = float(latest.get("atr", close_price * 0.15))

        # 3. Premium Budget Verification
        if not (self.min_price <= close_price <= self.max_price):
            return {
                "signal": "NEUTRAL",
                "confidence": 0.0,
                "reason": f"Price ₹{close_price} outside Hero-Zero range (₹{self.min_price}-₹{self.max_price})",
                "ai_probability": 0.0,
                "trade_grade": "N/A",
                "entry_window": "N/A",
                "exit_window": "N/A",
                "risk_score": 1.0,
                "signal_age": 0,
                "multi_timeframe_confirmation": False,
            }

        # 4. Relative Volume Spike Calculation
        vol_series = df["volume"].tail(10)
        vol_sma = vol_series.mean() if len(vol_series) > 0 else 1.0
        vol_ratio = (volume / vol_sma) if vol_sma > 0 else 0.0

        # 5. Technical Momentum Indicators
        rsi = float(latest.get("rsi", 0.0))
        st_dir = int(latest.get("supertrend_dir", 0))
        macd_hist = float(latest.get("macd_hist", 0.0))
        oi_change = float(latest.get("oi_change_pct", 0.0))  # Percentage OI Unwinding/Spike

        # 6. Hero-Zero Buy Condition Execution Logic
        is_volume_spike = vol_ratio >= self.min_vol_mult
        is_momentum_bullish = rsi >= 60.0 and st_dir == 1 and macd_hist > 0
        is_vwap_confirmed = close_price > vwap

        if is_volume_spike and is_momentum_bullish and is_vwap_confirmed:
            # Scaled confidence based on Volume ratio and Short Unwinding (OI decrease)
            base_confidence = 0.70
            vol_boost = min(0.20, (vol_ratio - self.min_vol_mult) * 0.05)
            oi_boost = 0.05 if oi_change < -5.0 else 0.0  # OI Unwinding adds conviction
            
            confidence = round(min(0.95, base_confidence + vol_boost + oi_boost), 2)
            ai_probability = round(min(0.99, confidence + 0.03 + (0.01 if oi_change < -5.0 else 0.0)), 2)

            # Volatility-Based (ATR) Stop Loss and Target Calculation
            stop_loss = max(round(close_price - (atr * 1.2), 2), round(close_price * 0.4, 2))
            target_price = round(close_price + ((close_price - stop_loss) * 2.5), 2)

            if confidence >= 0.85:
                trade_grade = "A"
            elif confidence >= 0.75:
                trade_grade = "B"
            else:
                trade_grade = "C"

            entry_window = f"{round(max(close_price * 0.995, close_price - atr * 0.25), 2)} - {round(close_price + atr * 0.25, 2)}"
            exit_window = f"{round(target_price * 0.98, 2)} - {round(target_price, 2)}"
            risk_score = round(min(1.0, max(0.2, 0.35 + (atr / max(close_price, 1.0)) + (0.1 if vol_ratio < 4.0 else 0.0))), 2)
            multi_timeframe_confirmation = bool(is_volume_spike and is_momentum_bullish and is_vwap_confirmed and abs(rsi - 70.0) < 20.0)

            LOGGER.info(
                f"HERO-ZERO TRIGGER DETECTED: Price=₹{close_price}, Vol Ratio={round(vol_ratio, 1)}x, SL=₹{stop_loss}, TGT=₹{target_price}"
            )

            return {
                "signal": "BUY",
                "confidence": confidence,
                "reason": f"GAMMA SPIKE: Vol {round(vol_ratio, 1)}x | RSI: {round(rsi, 1)} | Above VWAP",
                "target_price": target_price,
                "stop_loss": stop_loss,
                "ai_probability": ai_probability,
                "trade_grade": trade_grade,
                "entry_window": entry_window,
                "exit_window": exit_window,
                "risk_score": risk_score,
                "signal_age": 0,
                "multi_timeframe_confirmation": multi_timeframe_confirmation,
            }

        return {
            "signal": "NEUTRAL",
            "confidence": 0.50,
            "reason": f"No Gamma Spike detected. Vol Ratio: {round(vol_ratio, 1)}x | RSI: {round(rsi, 1)}",
            "ai_probability": round(max(0.05, 0.50 - max(0.0, (self.min_vol_mult - vol_ratio) * 0.08)), 2),
            "trade_grade": "D",
            "entry_window": "N/A",
            "exit_window": "N/A",
            "risk_score": 0.8,
            "signal_age": 0,
            "multi_timeframe_confirmation": False,
        }


# Global Engine Instance
HERO_ZERO_ENGINE = HeroZeroEngine()
HERO_ZERO = HERO_ZERO_ENGINE
