"""
TradeX AI Pro v5.2 - Production Trade Journal Engine
File: trade_journal.py

Features:
1. Thread-Safe SQLite DB Connection Pool / Locks.
2. Specific Trade Settle & Exit Tracking via Trade ID / Position ID.
3. Full Charges & Net PnL Integration.
4. Comprehensive Analytics (Win Rate, Profit Factor, Total Charges, Net PnL).
5. Structured Logging & Pandas DataFrame Export Support.
"""

import logging
import sqlite3
import threading
import pandas as pd
from datetime import datetime
from contextlib import contextmanager
from typing import Dict, Any, List, Optional

logger = logging.getLogger("TradeX_TradeJournal")


class TradeJournal:

    def __init__(self, db_path: str = "tradex.db"):
        self.db_path = db_path
        self._db_lock = threading.Lock()
        self._init_db()

    @contextmanager
    def _get_connection(self):
        """Thread-safe context manager for SQLite DB access."""
        with self._db_lock:
            conn = sqlite3.connect(self.db_path, timeout=15.0, check_same_thread=False)
            try:
                yield conn
                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.error(f"TradeJournal DB Transaction Error: {e}")
                raise e
            finally:
                conn.close()

    def _init_db(self):
        """Creates the necessary schema if it does not exist."""
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id TEXT UNIQUE,
                    symbol TEXT,
                    side TEXT,
                    asset_type TEXT,
                    qty INTEGER,
                    entry_price REAL,
                    sl REAL,
                    target REAL,
                    exit_price REAL DEFAULT 0.0,
                    gross_pnl REAL DEFAULT 0.0,
                    charges REAL DEFAULT 0.0,
                    net_pnl REAL DEFAULT 0.0,
                    status TEXT,
                    open_time DATETIME,
                    close_time DATETIME
                )
                """)
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")

    # ---------------------------------
    # Save / Open Trade
    # ---------------------------------
    def save_trade(
        self,
        trade_id: str,
        symbol: str,
        side: str,
        qty: int,
        entry_price: float,
        sl: float = 0.0,
        target: float = 0.0,
        asset_type: str = "OPTIONS",
        status: str = "OPEN"
    ) -> bool:
        """Records a new open trade entry in the database."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO trades (
                        trade_id, symbol, side, asset_type, qty, entry_price, sl, target, status, open_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (trade_id, symbol, side.upper(), asset_type, qty, float(entry_price), float(sl), float(target), status, now_str))
            logger.info(f"Journal Recorded Open Trade: [{trade_id}] {side} {symbol} x {qty} @ ₹{entry_price}")
            return True
        except Exception as e:
            logger.error(f"Error saving open trade: {e}")
            return False

    # ---------------------------------
    # Close Trade by Specific Trade ID
    # ---------------------------------
    def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        charges: float = 0.0
    ) -> bool:
        """Closes a specific trade by trade_id and calculates Net PnL."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                # Fetch trade details to calculate PnL
                cur.execute("SELECT side, qty, entry_price FROM trades WHERE trade_id = ? AND status = 'OPEN'", (trade_id,))
                row = cur.fetchone()

                if not row:
                    logger.warning(f"Trade ID {trade_id} not found or already closed.")
                    return False

                side, qty, entry_price = row
                exit_price = float(exit_price)

                if side == "BUY":
                    gross_pnl = (exit_price - entry_price) * qty
                else:
                    gross_pnl = (entry_price - exit_price) * qty

                net_pnl = round(gross_pnl - charges, 2)

                cur.execute("""
                    UPDATE trades
                    SET exit_price = ?,
                        gross_pnl = ?,
                        charges = ?,
                        net_pnl = ?,
                        status = 'CLOSED',
                        close_time = ?
                    WHERE trade_id = ?
                """, (exit_price, round(gross_pnl, 2), round(charges, 2), net_pnl, now_str, trade_id))

            logger.info(f"Journal Closed Trade: [{trade_id}] Exit: ₹{exit_price} | Net PnL: ₹{net_pnl}")
            return True
        except Exception as e:
            logger.error(f"Error closing trade {trade_id}: {e}")
            return False

    # ---------------------------------
    # Advanced Trade Analytics
    # ---------------------------------
    def stats(self) -> Dict[str, Any]:
        """Calculates advanced trading performance metrics."""
        try:
            with self._get_connection() as conn:
                df = pd.read_sql_query("SELECT * FROM trades WHERE status = 'CLOSED'", conn)

            if df.empty:
                return {
                    "total_trades": 0,
                    "winning_trades": 0,
                    "losing_trades": 0,
                    "win_rate_%": 0.0,
                    "gross_pnl": 0.0,
                    "total_charges": 0.0,
                    "net_pnl": 0.0,
                    "profit_factor": 0.0,
                    "average_pnl": 0.0
                }

            total_trades = len(df)
            winning_trades = df[df["net_pnl"] > 0]
            losing_trades = df[df["net_pnl"] < 0]

            win_rate = (len(winning_trades) / total_trades) * 100.0
            gross_pnl = df["gross_pnl"].sum()
            total_charges = df["charges"].sum()
            net_pnl = df["net_pnl"].sum()

            gross_profit = winning_trades["net_pnl"].sum()
            gross_loss = abs(losing_trades["net_pnl"].sum())
            profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

            return {
                "total_trades": total_trades,
                "winning_trades": len(winning_trades),
                "losing_trades": len(losing_trades),
                "win_rate_%": round(win_rate, 2),
                "gross_pnl": round(gross_pnl, 2),
                "total_charges": round(total_charges, 2),
                "net_pnl": round(net_pnl, 2),
                "profit_factor": round(profit_factor, 2),
                "average_pnl": round(df["net_pnl"].mean(), 2)
            }
        except Exception as e:
            logger.error(f"Error calculating stats: {e}")
            return {"error": str(e)}

    def get_all_trades_df(self) -> pd.DataFrame:
        """Returns entire journal entries as Pandas DataFrame."""
        try:
            with self._get_connection() as conn:
                return pd.read_sql_query("SELECT * FROM trades ORDER BY id DESC", conn)
        except Exception as e:
            logger.error(f"Error fetching DataFrame: {e}")
            return pd.DataFrame()


# Global Singleton Instance
journal = TradeJournal()
