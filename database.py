"""
TradeX AI Pro v4.0 - Database Management Module
File: database.py

SQLite Database layer for persisting Orders, Active Positions,
Trade History, Daily PnL Summaries, and Backtest Results.

Compatible with Python 3.13 and Pydroid 3.
"""

from contextlib import contextmanager
from datetime import datetime
import json
import pathlib
import sqlite3
import threading
import time
from typing import Any, Dict, Generator, List, Optional

try:
    from config import Config
except ImportError:
    Config = None

from logger import LOGGER


# Thread lock to guarantee thread-safety across concurrent database writes
DB_LOCK = threading.RLock()


def retry_on_db_lock(max_retries: int = 3, initial_delay: float = 0.1):
    """Decorator to retry database operations on sqlite3.OperationalError (database is locked)."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            delay = initial_delay
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    if "locked" in str(e).lower() and attempt < max_retries:
                        LOGGER.warning(f"Database locked, retrying attempt {attempt}/{max_retries} in {delay:.2f}s...")
                        time.sleep(delay)
                        delay *= 2  # Exponential Backoff
                    else:
                        raise
        return wrapper
    return decorator


class DatabaseManager:
    """Manages SQLite tables and CRUD operations for TradeX AI Pro."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None and Config and hasattr(Config, 'DB_PATH'):
            self.db_path = Config.DB_PATH
        else:
            self.db_path = db_path or "tradex_ai.db"
            
        self._initialize_tables()

    def get_connection(self) -> sqlite3.Connection:
        """Creates and returns a thread-safe connection with WAL mode and performance PRAGMAs enabled."""
        timeout_val = getattr(Config, 'DB_TIMEOUT', 30) if Config else 30
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=timeout_val
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA temp_store=MEMORY;")
        except Exception as e:
            LOGGER.warning(f"Could not set PRAGMA statements: {e}")
        return conn

    @contextmanager
    def session(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager to ensure automatic commit, rollback, thread-safety, and connection cleanup."""
        with DB_LOCK:
            @retry_on_db_lock(max_retries=3, initial_delay=0.1)
            def _get_conn():
                return self.get_connection()

            conn = _get_conn()
            try:
                yield conn
                conn.commit()
            except Exception as e:
                conn.rollback()
                LOGGER.error(f"Database transaction error: {e}", exc_info=True)
                raise
            finally:
                conn.close()

    def _initialize_tables(self):
        """Creates required database tables if they do not exist."""
        with self.session() as conn:
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

    def insert_order(self, order_data: Dict[str, Any]):
        """Inserts a new order into the database."""
        with self.session() as conn:
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

    def update_position(self, pos_data: Dict[str, Any]):
        """Inserts or updates active position state."""
        with self.session() as conn:
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

    def record_completed_trade(self, trade_data: Dict[str, Any]):
        """Logs closed position into trade_history."""
        with self.session() as conn:
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

    def get_active_positions(self) -> List[Dict[str, Any]]:
        """Fetches all currently OPEN positions."""
        with self.session() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM positions WHERE status = 'OPEN'")
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def get_trade_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetches completed trades history."""
        with self.session() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trade_history ORDER BY id DESC LIMIT ?", (limit,))
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
        with self.session() as conn:
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
        with self.session() as conn:
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


# Global Database Instance
DB = DatabaseManager()
