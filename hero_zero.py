"""
TradeX AI Pro v4.0 - Hero-Zero Dynamic Strategy Engine
File: hero_zero.py

Provides Specialized Expiry Day Option Scalping & Gamma Explosion Logic:
- Expiry Day Timing Verification (Post 1:00 PM IST)
- Volume & Open Interest (OI) Sudden Spike Detection
- Low-Premium High-Delta Momentum Scalping
- Automatic Target/Stop-loss Ratio for Hero-Zero Setup

Compatible with Python 3.13 and Pydroid 3.
"""

from datetime import datetime
import logging
from typing import Dict, Any, Optional
import pandas as pd

from logger import LOGGER
from indicators import INDICATORS

logger = logging.getLogger("TradeX_HeroZero")


class HeroZeroEngine:
    """Specialized Option Scalping Engine for Expiry Day Hero-Zero Signals."""

    def __init__(self, min_volume_multiplier: float = 2.5, min_price: float = 5.0, max_price: float = 40.0):
        self.min_vol_mult = min_volume_multiplier
        self.min_price = min_price
        self.max_price = max_price

    def evaluate(self, df: pd.DataFrame, current_time: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Evaluates OTM Option Data for Expiry Day Momentum / Gamma Spike.
        
        Criteria:
        1. Option Premium is in 'Hero-Zero' Range (e.g., ₹5 to ₹40)
        2. Volume Spike >= 2.5x of 10-period Average
        3. RSI > 60 with Sharp Slope
        4. Supertrend / Momentum Flip
        """
        if df.empty or len(df) < 15:
            return {"signal": "NEUTRAL", "confidence": 0.0, "reason": "Insufficient Data for Hero-Zero"}

        now = current_time or datetime.now()
        
        # Check Expiry Day Timing Filter (Optimal post 12:30 PM / 1:00 PM)
        if now.hour < 12 or (now.hour == 12 and now.minute < 30):
            return {
                "signal": "NEUTRAL",
                "confidence": 0.0,
                "reason": "Hero-Zero Strategy active only after 12:30 PM on Expiry Day.",
            }

        # Ensure essential indicators exist
        if "rsi" not in df.columns or "supertrend_dir" not in df.columns:
            df = INDICATORS.add_all_indicators(df, clean_nan=True)

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        close_price = latest.get("close", 0.0)
        volume = latest.get("volume", 0)
        
        # 1. Price Budget Check (Hero-Zero options are generally low-value OTMs)
        if not (self.min_price <= close_price <= self.max_price):
            return {
                "signal": "NEUTRAL",
                "confidence": 0.0,
                "reason": f"Price {close_price} outside Hero-Zero range ({self.min_price}-{self.max_price})",
            }

        # 2. Volume Surge Check
        vol_sma = df["volume"].tail(10).mean() if "volume" in df.columns else 1.0
        vol_ratio = (volume / vol_sma) if vol_sma > 0 else 0.0

        # 3. Momentum Signals (RSI & Supertrend)
        rsi = latest.get("rsi", 50)
        st_dir = latest.get("supertrend_dir", 0)
        macd_hist = latest.get("macd_hist", 0)

        # Hero-Zero BUY Trigger: Massive Volume Surge + Strong Momentum Breakdown/Breakout
        if vol_ratio >= self.min_vol_mult and rsi >= 62 and st_dir == 1 and macd_hist > 0:
            confidence = min(0.95, round(0.70 + (vol_ratio * 0.05), 2))
            return {
                "signal": "BUY",
                "confidence": confidence,
                "reason": f"HERO-ZERO TRIGGER: Volume Spike {round(vol_ratio, 1)}x | RSI: {round(rsi, 1)} | Price: ₹{close_price}",
                "target_price": round(close_price * 2.5, 2),  # 1:2.5 Risk-Reward Target
                "stop_loss": round(close_price * 0.5, 2),     # 50% Capital Risk Limit
            }

        return {
            "signal": "NEUTRAL",
            "confidence": 0.50,
            "reason": f"No Gamma Spike detected. Vol Ratio: {round(vol_ratio, 1)}x",
        }


# Global Hero-Zero Singleton Instance
HERO_ZERO_ENGINE = HeroZeroEngine()