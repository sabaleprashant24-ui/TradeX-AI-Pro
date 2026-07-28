"""
TradeX AI Pro v4.0 - Technical Indicators Engine
File: indicators.py

Provides Vectorized Technical Indicator Calculations & Master Dataset Pipeline:
- Master Multi-Indicator Generator (add_all_indicators)
- Selective Indicator Column NaN Cleanups (Preserves Raw OHLCV)
- Standard Indicators: EMA, RSI, ATR, Supertrend, VWAP, MACD, Bollinger Bands
- Advanced Optional Indicators: ADX (+DI, -DI), OBV, CMF, StochRSI, Donchian, Keltner

Compatible with Python 3.13 and Pydroid 3.
"""

import logging
from typing import Dict, Tuple, Union
import numpy as np
import pandas as pd

from logger import LOGGER

logger = logging.getLogger("TradeX_Indicators")


class TechnicalIndicators:
    """Production Grade Technical Analysis & Indicator Engine."""

    # =========================================================================
    # 1. MASTER MULTI-INDICATOR GENERATOR & SELECTIVE NAN CLEANUP
    # =========================================================================

    @staticmethod
    def add_all_indicators(
        df: pd.DataFrame,
        clean_nan: bool = True,
        include_advanced: bool = True,
    ) -> pd.DataFrame:
        """
        Master Pipeline Function: Calculates and attaches all core and advanced
        technical indicators to the DataFrame in a single efficient call.
        """
        if df.empty or len(df) < 5:
            LOGGER.warning("DataFrame is empty or too short for indicator calculation.")
            return df

        df = df.copy()
        raw_columns = list(df.columns)  # Save original OHLCV column names

        # --- Core Indicators ---
        df["ema_9"] = TechnicalIndicators.calculate_ema(df, period=9)
        df["ema_20"] = TechnicalIndicators.calculate_ema(df, period=20)
        df["ema_50"] = TechnicalIndicators.calculate_ema(df, period=50)
        df["ema_200"] = TechnicalIndicators.calculate_ema(df, period=200)

        df["rsi"] = TechnicalIndicators.calculate_rsi(df, period=14)
        df["atr"] = TechnicalIndicators.calculate_atr(df, period=14)

        # Supertrend
        st_df = TechnicalIndicators.calculate_supertrend(df, period=10, multiplier=3.0)
        if not st_df.empty:
            df["supertrend"] = st_df["supertrend"]
            df["supertrend_dir"] = st_df["supertrend_dir"]

        # VWAP
        df["vwap"] = TechnicalIndicators.calculate_vwap(df)

        # MACD
        macd_df = TechnicalIndicators.calculate_macd(df)
        if not macd_df.empty:
            df["macd"] = macd_df["macd"]
            df["macd_signal"] = macd_df["macd_signal"]
            df["macd_hist"] = macd_df["macd_hist"]

        # Bollinger Bands
        bb_df = TechnicalIndicators.calculate_bollinger_bands(df)
        if not bb_df.empty:
            df["bb_upper"] = bb_df["bb_upper"]
            df["bb_middle"] = bb_df["bb_middle"]
            df["bb_lower"] = bb_df["bb_lower"]

        # --- Advanced Indicators (Scanners & Hero-Zero) ---
        if include_advanced:
            adx_df = TechnicalIndicators.calculate_adx(df, period=14)
            if not adx_df.empty:
                df["adx"] = adx_df["adx"]
                df["plus_di"] = adx_df["plus_di"]
                df["minus_di"] = adx_df["minus_di"]

            if "volume" in df.columns:
                df["obv"] = TechnicalIndicators.calculate_obv(df)
                df["cmf"] = TechnicalIndicators.calculate_cmf(df, period=20)

            stoch_rsi_df = TechnicalIndicators.calculate_stoch_rsi(df)
            if not stoch_rsi_df.empty:
                df["stoch_rsi_k"] = stoch_rsi_df["stoch_rsi_k"]
                df["stoch_rsi_d"] = stoch_rsi_df["stoch_rsi_d"]

            donchian_df = TechnicalIndicators.calculate_donchian_channels(df)
            if not donchian_df.empty:
                df["donchian_high"] = donchian_df["donchian_high"]
                df["donchian_low"] = donchian_df["donchian_low"]

            keltner_df = TechnicalIndicators.calculate_keltner_channels(df)
            if not keltner_df.empty:
                df["keltner_upper"] = keltner_df["keltner_upper"]
                df["keltner_lower"] = keltner_df["keltner_lower"]

        # --- Selective NaN Cleanup (Only on calculated Indicator Columns) ---
        if clean_nan:
            indicator_cols = [col for col in df.columns if col not in raw_columns]
            df[indicator_cols] = df[indicator_cols].bfill().ffill()

        return df

    # =========================================================================
    # 2. CORE INDICATORS
    # =========================================================================

    @staticmethod
    def calculate_ema(df: pd.DataFrame, period: int = 20, column: str = "close") -> pd.Series:
        """Calculates Exponential Moving Average (EMA)."""
        if df.empty or column not in df.columns:
            return pd.Series(dtype=float)
        return df[column].ewm(span=period, adjust=False).mean()

    @staticmethod
    def calculate_rsi(df: pd.DataFrame, period: int = 14, column: str = "close") -> pd.Series:
        """Calculates Relative Strength Index (RSI)."""
        if df.empty or column not in df.columns:
            return pd.Series(dtype=float)

        delta = df[column].diff()
        gain = delta.clip(lower=0)
        loss = -1 * delta.clip(upper=0)

        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

        rs = avg_gain / (avg_loss + 1e-10)
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculates Average True Range (ATR)."""
        if df.empty or not {"high", "low", "close"}.issubset(df.columns):
            return pd.Series(dtype=float)

        high = df["high"]
        low = df["low"]
        prev_close = df["close"].shift(1)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()

        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    @staticmethod
    def calculate_supertrend(
        df: pd.DataFrame, period: int = 10, multiplier: float = 3.0
    ) -> pd.DataFrame:
        """Calculates Supertrend Indicator."""
        if df.empty or not {"high", "low", "close"}.issubset(df.columns):
            return pd.DataFrame()

        df_st = df.copy()
        atr = TechnicalIndicators.calculate_atr(df_st, period=period)

        hl2 = (df_st["high"] + df_st["low"]) / 2.0
        basic_upperband = hl2 + (multiplier * atr)
        basic_lowerband = hl2 - (multiplier * atr)

        final_upperband = basic_upperband.copy()
        final_lowerband = basic_lowerband.copy()

        for i in range(1, len(df_st)):
            if (
                basic_upperband.iloc[i] < final_upperband.iloc[i - 1]
                or df_st["close"].iloc[i - 1] > final_upperband.iloc[i - 1]
            ):
                final_upperband.iloc[i] = basic_upperband.iloc[i]
            else:
                final_upperband.iloc[i] = final_upperband.iloc[i - 1]

            if (
                basic_lowerband.iloc[i] > final_lowerband.iloc[i - 1]
                or df_st["close"].iloc[i - 1] < final_lowerband.iloc[i - 1]
            ):
                final_lowerband.iloc[i] = basic_lowerband.iloc[i]
            else:
                final_lowerband.iloc[i] = final_lowerband.iloc[i - 1]

        supertrend = pd.Series(index=df_st.index, dtype=float)
        direction = pd.Series(index=df_st.index, dtype=int)

        for i in range(len(df_st)):
            if i == 0:
                supertrend.iloc[i] = final_upperband.iloc[i]
                direction.iloc[i] = -1
            else:
                if (
                    supertrend.iloc[i - 1] == final_upperband.iloc[i - 1]
                    and df_st["close"].iloc[i] <= final_upperband.iloc[i]
                ):
                    supertrend.iloc[i] = final_upperband.iloc[i]
                    direction.iloc[i] = -1
                elif (
                    supertrend.iloc[i - 1] == final_upperband.iloc[i - 1]
                    and df_st["close"].iloc[i] > final_upperband.iloc[i]
                ):
                    supertrend.iloc[i] = final_lowerband.iloc[i]
                    direction.iloc[i] = 1
                elif (
                    supertrend.iloc[i - 1] == final_lowerband.iloc[i - 1]
                    and df_st["close"].iloc[i] >= final_lowerband.iloc[i]
                ):
                    supertrend.iloc[i] = final_lowerband.iloc[i]
                    direction.iloc[i] = 1
                elif (
                    supertrend.iloc[i - 1] == final_lowerband.iloc[i - 1]
                    and df_st["close"].iloc[i] < final_lowerband.iloc[i]
                ):
                    supertrend.iloc[i] = final_upperband.iloc[i]
                    direction.iloc[i] = -1

        return pd.DataFrame({"supertrend": supertrend, "supertrend_dir": direction})

    @staticmethod
    def calculate_vwap(df: pd.DataFrame) -> pd.Series:
        """Calculates Intraday Volume Weighted Average Price (VWAP)."""
        if df.empty or not {"high", "low", "close", "volume"}.issubset(df.columns):
            return pd.Series(dtype=float)

        typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
        tp_v = typical_price * df["volume"]

        if "timestamp" in df.columns:
            df_temp = df.copy()
            df_temp["date"] = pd.to_datetime(df_temp["timestamp"]).dt.date
            cum_tp_v = tp_v.groupby(df_temp["date"]).cumsum()
            cum_vol = df_temp["volume"].groupby(df_temp["date"]).cumsum()
        else:
            cum_tp_v = tp_v.cumsum()
            cum_vol = df["volume"].cumsum()

        return cum_tp_v / (cum_vol + 1e-10)

    @staticmethod
    def calculate_macd(
        df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9, column: str = "close"
    ) -> pd.DataFrame:
        """Calculates MACD Line, Signal Line, and MACD Histogram."""
        if df.empty or column not in df.columns:
            return pd.DataFrame()

        ema_fast = df[column].ewm(span=fast, adjust=False).mean()
        ema_slow = df[column].ewm(span=slow, adjust=False).mean()

        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()

        return pd.DataFrame(
            {"macd": macd_line, "macd_signal": signal_line, "macd_hist": macd_line - signal_line}
        )

    @staticmethod
    def calculate_bollinger_bands(
        df: pd.DataFrame, period: int = 20, std_dev: float = 2.0, column: str = "close"
    ) -> pd.DataFrame:
        """Calculates Bollinger Bands (Upper, Middle, Lower)."""
        if df.empty or column not in df.columns:
            return pd.DataFrame()

        sma = df[column].rolling(window=period).mean()
        rstd = df[column].rolling(window=period).std()

        return pd.DataFrame(
            {
                "bb_upper": sma + (std_dev * rstd),
                "bb_middle": sma,
                "bb_lower": sma - (std_dev * rstd),
            }
        )

    # =========================================================================
    # 3. ADVANCED OPTIONAL INDICATORS
    # =========================================================================

    @staticmethod
    def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """Calculates Average Directional Index (ADX) along with +DI and -DI."""
        if df.empty or not {"high", "low", "close"}.issubset(df.columns):
            return pd.DataFrame()

        up = df["high"].diff()
        down = -df["low"].diff()

        plus_dm = np.where((up > down) & (up > 0), up, 0.0)
        minus_dm = np.where((down > up) & (down > 0), down, 0.0)

        atr = TechnicalIndicators.calculate_atr(df, period=period)

        plus_di = 100 * (
            pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean()
            / (atr + 1e-10)
        )
        minus_di = 100 * (
            pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean()
            / (atr + 1e-10)
        )

        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
        adx = dx.ewm(alpha=1 / period, adjust=False).mean()

        return pd.DataFrame({"adx": adx, "plus_di": plus_di, "minus_di": minus_di})

    @staticmethod
    def calculate_obv(df: pd.DataFrame) -> pd.Series:
        """Calculates On-Balance Volume (OBV)."""
        if df.empty or not {"close", "volume"}.issubset(df.columns):
            return pd.Series(dtype=float)

        direction = np.where(
            df["close"] > df["close"].shift(1),
            1,
            np.where(df["close"] < df["close"].shift(1), -1, 0),
        )
        return (df["volume"] * direction).cumsum()

    @staticmethod
    def calculate_cmf(df: pd.DataFrame, period: int = 20) -> pd.Series:
        """Calculates Chaikin Money Flow (CMF)."""
        if df.empty or not {"high", "low", "close", "volume"}.issubset(df.columns):
            return pd.Series(dtype=float)

        mf_multiplier = (
            (df["close"] - df["low"]) - (df["high"] - df["close"])
        ) / ((df["high"] - df["low"]) + 1e-10)
        mf_volume = mf_multiplier * df["volume"]

        return mf_volume.rolling(period).sum() / (df["volume"].rolling(period).sum() + 1e-10)

    @staticmethod
    def calculate_stoch_rsi(
        df: pd.DataFrame, period: int = 14, k_period: int = 3, d_period: int = 3
    ) -> pd.DataFrame:
        """Calculates Stochastic RSI (%K, %D)."""
        rsi = TechnicalIndicators.calculate_rsi(df, period=period)
        if rsi.empty:
            return pd.DataFrame()

        min_rsi = rsi.rolling(period).min()
        max_rsi = rsi.rolling(period).max()

        stoch_rsi = (rsi - min_rsi) / ((max_rsi - min_rsi) + 1e-10)
        stoch_k = stoch_rsi.rolling(k_period).mean() * 100.0
        stoch_d = stoch_k.rolling(d_period).mean()

        return pd.DataFrame({"stoch_rsi_k": stoch_k, "stoch_rsi_d": stoch_d})

    @staticmethod
    def calculate_donchian_channels(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
        """Calculates Donchian Channel High and Low bands."""
        if df.empty or not {"high", "low"}.issubset(df.columns):
            return pd.DataFrame()

        return pd.DataFrame(
            {
                "donchian_high": df["high"].rolling(period).max(),
                "donchian_low": df["low"].rolling(period).min(),
            }
        )

    @staticmethod
    def calculate_keltner_channels(
        df: pd.DataFrame, period: int = 20, multiplier: float = 2.0
    ) -> pd.DataFrame:
        """Calculates Keltner Channels (Upper, Lower)."""
        if df.empty or not {"high", "low", "close"}.issubset(df.columns):
            return pd.DataFrame()

        ema = TechnicalIndicators.calculate_ema(df, period=period)
        atr = TechnicalIndicators.calculate_atr(df, period=period)

        return pd.DataFrame(
            {
                "keltner_upper": ema + (multiplier * atr),
                "keltner_lower": ema - (multiplier * atr),
            }
        )


# Global Technical Indicators Instance
INDICATORS = TechnicalIndicators()