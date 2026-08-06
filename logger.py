"""
TradeX AI Pro v4.0 - Comprehensive Logging Framework
File: logger.py

Provides Console, File, Error, Trade Execution, and Performance metrics
logging with automatic directory creation, log rotation, and custom formatting.

Compatible with Python 3.13 and Pydroid 3.
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


class CustomFormatter(logging.Formatter):
    """Custom Log Formatter for TradeX Engine using efficient static formatting."""

    def __init__(
        self,
        fmt: str = "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
        datefmt: str = "%Y-%m-%d %H:%M:%S",
    ):
        super().__init__(fmt=fmt, datefmt=datefmt)


class TradeXLogger:
    """Central Logger Factory for Managing Application Logs."""

    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self._ensure_log_directory()

        # Custom Shared Formatter Instance
        self.formatter = CustomFormatter()

        # Dedicated Loggers
        self.console_logger = self._setup_logger(
            "TradeX_Core", "system.log", level=logging.INFO, enable_console=True
        )
        self.error_logger = self._setup_logger(
            "TradeX_Error", "error.log", level=logging.ERROR, enable_console=False
        )
        self.trade_logger = self._setup_logger(
            "TradeX_Trade", "trades.log", level=logging.INFO, enable_console=False
        )
        self.perf_logger = self._setup_logger(
            "TradeX_Perf", "performance.log", level=logging.INFO, enable_console=False
        )

    def _ensure_log_directory(self):
        """Creates log folder if it does not exist using pathlib."""
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _setup_logger(
        self,
        name: str,
        log_file: str,
        level: int = logging.INFO,
        enable_console: bool = False,
    ) -> logging.Logger:
        """Helper to create and configure dedicated logger instances with file rotation."""
        logger_obj = logging.getLogger(name)
        logger_obj.setLevel(level)
        logger_obj.propagate = False

        if not logger_obj.handlers:
            # Rotating File Handler (Max 5MB per file, max 3 backup files)
            file_path = self.log_dir / log_file
            file_handler = RotatingFileHandler(
                file_path,
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setFormatter(self.formatter)
            file_handler.setLevel(level)
            logger_obj.addHandler(file_handler)

            # Console Handler (Only attached if specifically enabled)
            if enable_console:
                console_handler = logging.StreamHandler()
                console_handler.setFormatter(self.formatter)
                console_handler.setLevel(level)
                logger_obj.addHandler(console_handler)

        return logger_obj

    def debug(self, message: str):
        """Standard System Debug Log."""
        self.console_logger.debug(message)

    def info(self, message: str):
        """Standard System Info Log."""
        self.console_logger.info(message)

    def warning(self, message: str):
        """Warning Log."""
        self.console_logger.warning(message)

    def error(self, message: str, exc_info: bool = False):
        """Error Logging with optional stack trace across console and error logs."""
        self.console_logger.error(message, exc_info=exc_info)
        self.error_logger.error(message, exc_info=exc_info)

    def exception(self, message: str):
        """Error Logging shortcut that includes current exception traceback."""
        self.error(message, exc_info=True)

    def critical(self, message: str, exc_info: bool = True):
        """Critical Error Log."""
        self.console_logger.critical(message, exc_info=exc_info)
        self.error_logger.critical(message, exc_info=exc_info)

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
        """Formats and logs trade execution records cleanly without duplication."""
        pnl_str = f" | PnL: Rs.{pnl:.2f}" if pnl is not None else ""
        log_msg = (
            f"TRADE | {action} | Symbol: {symbol} | Qty: {quantity} | "
            f"Price: Rs.{price:.2f} | Type: {order_type} | Status: {status}{pnl_str}"
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
            f"Total PnL: Rs.{total_pnl:.2f} | Max Drawdown: {drawdown:.2f}%"
        )
        self.perf_logger.info(perf_msg)
        self.console_logger.info(perf_msg)


# Global Logger Instances (Supports imports as 'LOGGER' or 'logger')
LOGGER = TradeXLogger()
logger = LOGGER


def log_info(message: str):
    LOGGER.info(message)


def log_warning(message: str):
    LOGGER.warning(message)


def log_error(message: str):
    LOGGER.error(message)
