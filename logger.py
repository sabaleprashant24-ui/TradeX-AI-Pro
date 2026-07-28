"""
TradeX AI Pro v4.0 - Comprehensive Logging Framework
File: logger.py

Provides Console, File, Error, Trade Execution, and Performance metrics
logging with automatic directory creation and custom log formatting.

Compatible with Python 3.13 and Pydroid 3.
"""

from datetime import datetime
import logging
import os
from typing import Optional


class CustomFormatter(logging.Formatter):
    """Custom Log Formatter for TradeX Engine."""

    def format(self, record: logging.LogRecord) -> str:
        log_fmt = "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"
        formatter = logging.Formatter(log_fmt, datefmt="%Y-%m-%d %H:%M:%S")
        return formatter.format(record)


class TradeXLogger:
    """Central Logger Factory for Managing Application Logs."""

    def __init__(self, log_dir: str = "logs"):
        self.log_dir = log_dir
        self._ensure_log_directory()

        # Dedicated Loggers
        self.console_logger = self._setup_logger(
            "TradeX_Core", "system.log", level=logging.INFO
        )
        self.error_logger = self._setup_logger(
            "TradeX_Error", "error.log", level=logging.ERROR
        )
        self.trade_logger = self._setup_logger(
            "TradeX_Trade", "trades.log", level=logging.INFO
        )
        self.perf_logger = self._setup_logger(
            "TradeX_Perf", "performance.log", level=logging.INFO
        )

    def _ensure_log_directory(self):
        """Creates log folder if it does not exist."""
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir, exist_ok=True)

    def _setup_logger(
        self, name: str, log_file: str, level: int = logging.INFO
    ) -> logging.Logger:
        """Helper to create and configure dedicated logger instances."""
        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.propagate = False

        if not logger.handlers:
            # File Handler
            file_path = os.path.join(self.log_dir, log_file)
            file_handler = logging.FileHandler(file_path, encoding="utf-8")
            file_handler.setFormatter(CustomFormatter())
            file_handler.setLevel(level)
            logger.addHandler(file_handler)

            # Console Handler
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(CustomFormatter())
            console_handler.setLevel(level)
            logger.addHandler(console_handler)

        return logger

    def info(self, message: str):
        """Standard System Info Log."""
        self.console_logger.info(message)

    def warning(self, message: str):
        """Warning Log."""
        self.console_logger.warning(message)

    def error(self, message: str, exc_info: bool = False):
        """Error Logging with optional stack trace."""
        self.console_logger.error(message, exc_info=exc_info)
        self.error_logger.error(message, exc_info=exc_info)

    def log_trade(
        self,
        symbol: str,
        action: str,
        quantity: int,
        price: float,
        order_type: str,
        status: str,
        pnl: Optional[float] = None,
    ):
        """Formats and logs trade execution records."""
        pnl_str = f" | PnL: ₹{pnl:.2f}" if pnl is not None else ""
        log_msg = (
            f"TRADE | {action} | Symbol: {symbol} | Qty: {quantity} | "
            f"Price: ₹{price:.2f} | Type: {order_type} | Status: {status}{pnl_str}"
        )
        self.trade_logger.info(log_msg)
        self.console_logger.info(log_msg)

    def log_performance(
        self,
        total_trades: int,
        win_rate: float,
        total_pnl: float,
        drawdown: float,
    ):
        """Logs engine performance analytics summary."""
        perf_msg = (
            f"PERFORMANCE | Total Trades: {total_trades} | Win Rate: {win_rate:.2f}% | "
            f"Total PnL: ₹{total_pnl:.2f} | Max Drawdown: {drawdown:.2f}%"
        )
        self.perf_logger.info(perf_msg)
        self.console_logger.info(perf_msg)


# Global Logger Instance
LOGGER = TradeXLogger()