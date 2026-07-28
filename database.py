"""
TradeX AI Pro v4.0 - Database Management Module
File: database.py

SQLite Database layer for persisting Orders, Active Positions,
Trade History, Daily PnL Summaries, and Backtest Results.

Compatible with Python 3.13 and Pydroid 3.
"""

from datetime import datetime
import json
import sqlite3
from typing import Any, Dict, List, Optional


class DatabaseManager:
    """Manages SQLite tables and CRUD operations for TradeX AI Pro."""

    def __init__(self, db_path: str = "tradex_ai.db"):
        self.db_path = db_path
        self._initialize_tables()

    def _get_connection(self) -> sqlite3.Connection:
        """Returns SQLite connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize_tables(self):
        """Creates required database tables if they do not exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Orders Table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    segment TEXT NOT NULL,
                    transaction_type TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    stop_loss REAL DEFAULT 0.0,
                    target REAL DEFAULT 0.0,
                    status TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """
            )

            # Positions Table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    position_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    segment TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    current_price REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    target REAL NOT NULL,
                    trailing_sl REAL NOT NULL,
                    transaction_type TEXT NOT NULL,
                    pnl REAL DEFAULT 0.0,
                    status TEXT NOT NULL,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT
                )
            """
            )

            # Trade History Table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    pnl REAL NOT NULL,
                    strategy TEXT NOT NULL,
                    entry_timestamp TEXT NOT NULL,
                    exit_timestamp TEXT NOT NULL
                )
            """
            )

            # Daily Summary Table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_summary (
                    date TEXT PRIMARY KEY,
                    total_trades INTEGER NOT NULL,
                    winning_trades INTEGER NOT NULL,
                    losing_trades INTEGER NOT NULL,
                    gross_pnl REAL NOT NULL,
                    net_pnl REAL NOT NULL,
                    max_drawdown REAL NOT NULL
                )
            """
            )

            # Backtest Results Table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS backtest_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    total_trades INTEGER NOT NULL,
                    win_rate REAL NOT NULL,
                    total_pnl REAL NOT NULL,
                    max_drawdown REAL NOT NULL,
                    metrics_json TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """
            )

            conn.commit()

    def insert_order(self, order_data: Dict[str, Any]):
        """Inserts a new order into the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO orders 
                (order_id, symbol, segment, transaction_type, order_type, quantity, price, stop_loss, target, status, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    order_data["order_id"],
                    order_data["symbol"],
                    order_data["segment"],
                    order_data["transaction_type"],
                    order_data["order_type"],
                    order_data["quantity"],
                    order_data["price"],
                    order_data.get("stop_loss", 0.0),
                    order_data.get("target", 0.0),
                    order_data["status"],
                    order_data.get(
                        "timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    ),
                ),
            )
            conn.commit()

    def update_position(self, pos_data: Dict[str, Any]):
        """Inserts or updates active position state."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO positions
                (position_id, symbol, segment, quantity, entry_price, current_price, stop_loss, target, trailing_sl, transaction_type, pnl, status, entry_time, exit_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    pos_data["position_id"],
                    pos_data["symbol"],
                    pos_data["segment"],
                    pos_data["quantity"],
                    pos_data["entry_price"],
                    pos_data["current_price"],
                    pos_data["stop_loss"],
                    pos_data["target"],
                    pos_data["trailing_sl"],
                    pos_data["transaction_type"],
                    pos_data.get("pnl", 0.0),
                    pos_data["status"],
                    pos_data["entry_time"],
                    pos_data.get("exit_time"),
                ),
            )
            conn.commit()

    def record_completed_trade(self, trade_data: Dict[str, Any]):
        """Logs closed position into trade_history."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO trade_history
                (symbol, action, quantity, entry_price, exit_price, pnl, strategy, entry_timestamp, exit_timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    trade_data["symbol"],
                    trade_data["action"],
                    trade_data["quantity"],
                    trade_data["entry_price"],
                    trade_data["exit_price"],
                    trade_data["pnl"],
                    trade_data["strategy"],
                    trade_data["entry_timestamp"],
                    trade_data.get(
                        "exit_timestamp",
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                ),
            )
            conn.commit()

    def get_active_positions(self) -> List[Dict[str, Any]]:
        """Fetches all currently OPEN positions."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM positions WHERE status = 'OPEN'")
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def save_daily_summary(
        self,
        date_str: str,
        total_trades: int,
        winning_trades: int,
        losing_trades: int,
        gross_pnl: float,
        net_pnl: float,
        max_dd: float,
    ):
        """Saves daily PnL and trade analytics summary."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO daily_summary
                (date, total_trades, winning_trades, losing_trades, gross_pnl, net_pnl, max_drawdown)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    date_str,
                    total_trades,
                    winning_trades,
                    losing_trades,
                    gross_pnl,
                    net_pnl,
                    max_dd,
                ),
            )
            conn.commit()

    def save_backtest_result(
        self,
        strategy_name: str,
        symbol: str,
        timeframe: str,
        total_trades: int,
        win_rate: float,
        total_pnl: float,
        max_drawdown: float,
        metrics: Dict[str, Any],
    ):
        """Stores backtest result report."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO backtest_results
                (strategy_name, symbol, timeframe, total_trades, win_rate, total_pnl, max_drawdown, metrics_json, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    strategy_name,
                    symbol,
                    timeframe,
                    total_trades,
                    win_rate,
                    total_pnl,
                    max_drawdown,
                    json.dumps(metrics),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            conn.commit()


# Global Database Instance
DB = DatabaseManager()