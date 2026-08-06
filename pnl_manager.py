"""
TradeX AI Pro v5.2 - Production-Grade PnL & Portfolio Analytics Engine
File: pnl_manager.py

Enhancements & Performance Updates:
1. Thread-Safe SQLite Operations via Threading Lock and check_same_thread=False.
2. Exact Exit Timestamp Support in record_closed_trade() for pinpoint Holding Time analysis.
3. Multi-Tab Excel Export (Includes Daily, Weekly, Monthly, Trade Log, and Equity Curve).
4. Advanced Portfolio Analytics: Recovery Factor, Calmar Ratio, Avg Holding Time, Avg Win/Loss Ratio.
"""

import math
import logging
import sqlite3
import threading
import pandas as pd
import numpy as np
from datetime import datetime, date
from contextlib import contextmanager

logger = logging.getLogger("TradeX_PnLManager")

# --- DEFAULT CONFIGURATION FALLBACK ---
DEFAULT_CHARGE_CONFIG = {
    "BROKERAGE_PER_ORDER": 20.0,       # Max Flat fee per executed order
    "STT_DELIVERY": 0.001,             # 0.1% Buy/Sell Delivery
    "STT_INTRADAY_SELL": 0.00025,      # 0.025% Sell Intraday
    "STT_FUTURES_SELL": 0.000125,      # 0.0125% Sell Futures
    "STT_OPTIONS_SELL": 0.001,         # 0.1% Premium Sell Options
    "EXCHANGE_TURNOVER_NSE": 0.0000297,# NSE Turnover Charge
    "STAMP_DUTY_BUY": 0.00003,         # Stamp duty on Buy orders
    "GST_RATE": 0.18,                  # 18% GST on (Brokerage + Exchange Charges)
    "SEBI_TURNOVER_CHARGE": 0.000001   # SEBI charges
}

class PnLManager:
    def __init__(self, initial_capital: float = 100000.0, charge_config: dict = None, db_path: str = "pnl_data.db"):
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.config = charge_config if charge_config else DEFAULT_CHARGE_CONFIG
        self.db_path = db_path
        self._db_lock = threading.RLock()  # Allows nested DB helper calls during startup
        
        # Initialize Database Engine
        self._init_db()
        self._load_state_from_db()

    # --- HIGH PERFORMANCE THREAD-SAFE CONTEXT MANAGER ---
    @contextmanager
    def _get_connection(self):
        """ Thread-safe Context Manager for concurrent SQLite database operations """
        with self._db_lock:
            conn = sqlite3.connect(self.db_path, timeout=15.0, check_same_thread=False)
            try:
                yield conn
                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.error(f"Database Transaction Error: {e}")
                raise e
            finally:
                conn.close()

    def _init_db(self):
        """ Initializes SQLite database tables for PnL persistence """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Trades Table with explicit open_time and close_time
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS trades (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT,
                        trade_type TEXT,
                        asset_type TEXT,
                        qty INTEGER,
                        buy_price REAL,
                        sell_price REAL,
                        gross_pnl REAL,
                        charges REAL,
                        net_pnl REAL,
                        open_time DATETIME,
                        close_time DATETIME
                    )
                """)
                
                # Equity Curve Table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS equity_curve (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        capital REAL
                    )
                """)
        except Exception as e:
            logger.error(f"Error initializing PnL database: {e}")

    def _load_state_from_db(self):
        """ Restores Capital state from SQLite persistence """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT capital FROM equity_curve ORDER BY id DESC LIMIT 1")
                row = cursor.fetchone()
                if row:
                    self.current_capital = row[0]
                else:
                    self._record_equity_point(self.initial_capital)
        except Exception as e:
            logger.error(f"Error loading PnL state: {e}")

    def _record_equity_point(self, capital_val: float):
        """ Appends capital point to Database Equity Curve """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT INTO equity_curve (capital) VALUES (?)", (capital_val,))
        except Exception as e:
            logger.error(f"Error writing to Equity Curve: {e}")

    # --- CHARGES CALCULATOR ENGINE ---
    def calculate_charges(self, qty: int, buy_price: float, sell_price: float, asset_type: str = "OPTIONS", trade_type: str = "INTRADAY") -> dict:
        """ Calculates exact statutory charges based on active configuration """
        turnover_buy = qty * buy_price
        turnover_sell = qty * sell_price
        total_turnover = turnover_buy + turnover_sell

        # Brokerage
        brokerage = min(self.config["BROKERAGE_PER_ORDER"], turnover_buy * 0.0003) + min(self.config["BROKERAGE_PER_ORDER"], turnover_sell * 0.0003)

        # STT
        if asset_type == "EQUITY":
            stt = (turnover_buy + turnover_sell) * self.config["STT_DELIVERY"] if trade_type == "DELIVERY" else turnover_sell * self.config["STT_INTRADAY_SELL"]
        elif asset_type == "FUTURES":
            stt = turnover_sell * self.config["STT_FUTURES_SELL"]
        elif asset_type == "OPTIONS":
            stt = turnover_sell * self.config["STT_OPTIONS_SELL"]
        else:
            stt = 0.0

        # Exchange, SEBI, Stamp Duty
        exchange_txn = total_turnover * self.config["EXCHANGE_TURNOVER_NSE"]
        sebi_fee = total_turnover * self.config["SEBI_TURNOVER_CHARGE"]
        stamp_duty = turnover_buy * self.config["STAMP_DUTY_BUY"]

        # GST
        gst = (brokerage + exchange_txn) * self.config["GST_RATE"]

        total_charges = round(brokerage + stt + exchange_txn + sebi_fee + stamp_duty + gst, 2)

        return {
            "brokerage": round(brokerage, 2),
            "stt": round(stt, 2),
            "exchange_txn": round(exchange_txn, 2),
            "stamp_duty": round(stamp_duty, 2),
            "gst": round(gst, 2),
            "total_charges": total_charges
        }

    # --- TRADE RECORDING ENGINE ---
    def record_closed_trade(self, symbol: str, qty: int, buy_price: float, sell_price: float, asset_type: str = "OPTIONS", trade_type: str = "INTRADAY", open_time: str = None, close_time: str = None):
        """ Records executed closed trade with accurate open and close timestamps """
        gross_pnl = (sell_price - buy_price) * qty
        charge_details = self.calculate_charges(qty, buy_price, sell_price, asset_type, trade_type)
        charges = charge_details["total_charges"]
        net_pnl = round(gross_pnl - charges, 2)

        self.current_capital += net_pnl
        self._record_equity_point(self.current_capital)

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        open_ts = open_time if open_time else now_str
        close_ts = close_time if close_time else now_str

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO trades (symbol, trade_type, asset_type, qty, buy_price, sell_price, gross_pnl, charges, net_pnl, open_time, close_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (symbol, trade_type, asset_type, qty, buy_price, sell_price, gross_pnl, charges, net_pnl, open_ts, close_ts))
        except Exception as e:
            logger.error(f"Error persisting trade to DB: {e}")

        return net_pnl

    # --- BROKER POSITION SYNC ENGINE ---
    def sync_broker_positions(self, broker_positions_list: list) -> float:
        """ Syncs Live MTM from Angel One / Broker API Positions """
        total_live_mtm = 0.0
        for pos in broker_positions_list:
            live_pnl = float(pos.get("unrealised", 0.0)) + float(pos.get("realised", 0.0))
            total_live_mtm += live_pnl
        return round(total_live_mtm, 2)

    # --- TIME-BASED PnL SUMMARIES ---
    def get_pnl_summary_by_period(self, period: str = "DAILY") -> pd.DataFrame:
        """ Aggregates Net PnL by DAILY, WEEKLY, or MONTHLY periods """
        try:
            with self._get_connection() as conn:
                df = pd.read_sql_query("SELECT * FROM trades", conn)

            if df.empty:
                return pd.DataFrame()

            df['close_time'] = pd.to_datetime(df['close_time'])

            if period == "DAILY":
                grouped = df.groupby(df['close_time'].dt.date)['net_pnl'].agg(['sum', 'count']).reset_index()
                grouped.columns = ['Date', 'Net_PnL', 'Total_Trades']
            elif period == "WEEKLY":
                grouped = df.groupby(df['close_time'].dt.to_period('W'))['net_pnl'].agg(['sum', 'count']).reset_index()
                grouped.columns = ['Week', 'Net_PnL', 'Total_Trades']
                grouped['Week'] = grouped['Week'].astype(str)
            elif period == "MONTHLY":
                grouped = df.groupby(df['close_time'].dt.to_period('M'))['net_pnl'].agg(['sum', 'count']).reset_index()
                grouped.columns = ['Month', 'Net_PnL', 'Total_Trades']
                grouped['Month'] = grouped['Month'].astype(str)

            return grouped
        except Exception as e:
            logger.error(f"Error generating PnL summary: {e}")
            return pd.DataFrame()

    # --- PERFORMANCE & RISK METRICS ENGINE ---
    def get_max_drawdown(self) -> tuple:
        """ Returns Max Drawdown % and Peak-to-Trough Value """
        try:
            with self._get_connection() as conn:
                df = pd.read_sql_query("SELECT capital FROM equity_curve", conn)

            if df.empty or len(df) < 2:
                return 0.0, 0.0

            equity = df['capital'].values
            peak = np.maximum.accumulate(equity)
            drawdowns = peak - equity
            drawdown_pct = (equity - peak) / peak

            max_dd_val = np.max(drawdowns)
            max_dd_pct = abs(np.min(drawdown_pct)) * 100.0

            return round(max_dd_pct, 2), round(max_dd_val, 2)
        except Exception as e:
            logger.error(f"Error calculating Max Drawdown: {e}")
            return 0.0, 0.0

    def calculate_cagr(self) -> float:
        """ Calculates Compound Annual Growth Rate (CAGR) """
        try:
            with self._get_connection() as conn:
                df = pd.read_sql_query("SELECT timestamp, capital FROM equity_curve ORDER BY id ASC", conn)

            if df.empty or len(df) < 2:
                return 0.0

            df['timestamp'] = pd.to_datetime(df['timestamp'])
            start_val = self.initial_capital
            end_val = df['capital'].iloc[-1]

            days = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days
            if days <= 0 or start_val <= 0:
                return 0.0

            years = days / 365.25
            cagr = ((end_val / start_val) ** (1.0 / years) - 1.0) * 100.0
            return round(cagr, 2)
        except Exception as e:
            logger.error(f"Error calculating CAGR: {e}")
            return 0.0

    def get_advanced_analytics(self) -> dict:
        """ Calculates Institutional Grade Portfolio Analytics & Risk Metrics """
        try:
            with self._get_connection() as conn:
                df = pd.read_sql_query("SELECT net_pnl, open_time, close_time FROM trades", conn)

            if df.empty:
                return {"status": "NO_TRADES"}

            pnl_series = df['net_pnl']
            total_trades = len(pnl_series)
            winning_trades = pnl_series[pnl_series > 0]
            losing_trades = pnl_series[pnl_series < 0]

            win_rate = (len(winning_trades) / total_trades) * 100.0 if total_trades > 0 else 0.0
            
            gross_profit = winning_trades.sum()
            gross_loss = abs(losing_trades.sum())
            profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

            avg_win = winning_trades.mean() if not winning_trades.empty else 0.0
            avg_loss = abs(losing_trades.mean()) if not losing_trades.empty else 0.0
            avg_win_loss_ratio = (avg_win / avg_loss) if avg_loss > 0 else avg_win

            # Holding Time Calculation (Minutes)
            if 'open_time' in df.columns and 'close_time' in df.columns:
                df['open_time'] = pd.to_datetime(df['open_time'])
                df['close_time'] = pd.to_datetime(df['close_time'])
                holding_times = (df['close_time'] - df['open_time']).dt.total_seconds() / 60.0
                avg_holding_time_mins = round(holding_times.mean(), 1)
            else:
                avg_holding_time_mins = 0.0

            # Drawdown and Recovery Metrics
            max_dd_pct, max_dd_val = self.get_max_drawdown()
            total_net_pnl = pnl_series.sum()
            
            recovery_factor = (total_net_pnl / max_dd_val) if max_dd_val > 0 else (total_net_pnl if total_net_pnl > 0 else 0.0)
            
            cagr_val = self.calculate_cagr()
            calmar_ratio = (cagr_val / max_dd_pct) if max_dd_pct > 0 else 0.0

            # Risk-Adjusted Ratios
            daily_returns = pnl_series / self.initial_capital
            std_dev = daily_returns.std()
            sharpe_ratio = (daily_returns.mean() / (std_dev + 1e-9)) * math.sqrt(252) if std_dev > 0 else 0.0

            downside_returns = daily_returns[daily_returns < 0]
            downside_std = downside_returns.std()
            sortino_ratio = (daily_returns.mean() / (downside_std + 1e-9)) * math.sqrt(252) if downside_std > 0 else 0.0

            return {
                "total_trades": total_trades,
                "win_rate_%": round(win_rate, 2),
                "profit_factor": round(profit_factor, 2),
                "avg_win_loss_ratio": round(avg_win_loss_ratio, 2),
                "avg_holding_time_mins": avg_holding_time_mins,
                "recovery_factor": round(recovery_factor, 2),
                "calmar_ratio": round(calmar_ratio, 2),
                "sharpe_ratio": round(sharpe_ratio, 2),
                "sortino_ratio": round(sortino_ratio, 2),
                "cagr_%": cagr_val,
                "max_drawdown_%": max_dd_pct,
                "total_net_pnl": round(total_net_pnl, 2)
            }
        except Exception as e:
            logger.error(f"Error calculating analytics: {e}")
            return {"status": "ERROR"}

    # --- EXPORT REPORTING ENGINE ---
    def export_reports_to_excel(self, filename: str = "TradeX_PnL_Report.xlsx"):
        """ Exports Trades and Multi-Period Summaries into Excel Workbook """
        try:
            with self._get_connection() as conn:
                trades_df = pd.read_sql_query("SELECT * FROM trades", conn)
                equity_df = pd.read_sql_query("SELECT * FROM equity_curve", conn)

            daily_df = self.get_pnl_summary_by_period("DAILY")
            weekly_df = self.get_pnl_summary_by_period("WEEKLY")
            monthly_df = self.get_pnl_summary_by_period("MONTHLY")

            with pd.ExcelWriter(filename, engine='openpyxl') as writer:
                trades_df.to_excel(writer, sheet_name='Trade_Log', index=False)
                daily_df.to_excel(writer, sheet_name='Daily_PnL', index=False)
                weekly_df.to_excel(writer, sheet_name='Weekly_PnL', index=False)
                monthly_df.to_excel(writer, sheet_name='Monthly_PnL', index=False)
                equity_df.to_excel(writer, sheet_name='Equity_Curve', index=False)

            logger.info(f"Report exported successfully: {filename}")
            return True
        except Exception as e:
            logger.error(f"Excel Export Error: {e}")
            return False


pnl = PnLManager()
