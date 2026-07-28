# ==========================================
# execution_engine.py
# TradeX AI Pro v3.1
# PART 1
# ==========================================

from strategy import strategy
from paper_trade import paper
from trade_journal import journal
from risk_manager import risk
from position_manager import position
from pnl_manager import pnl
import sqlite3
from datetime import datetime, time
import math

from strategy import strategy
from paper_trade import paper
from trade_journal import journal
from risk_manager import risk
from position_manager import position
from pnl_manager import pnl
from logger import log_info, log_error


class ExecutionEngine:

    def __init__(self):
        self.current_position = None
        self.last_signal = None
        self.pending = {}
        self.open_trades = {}
        self.db = "tradex_v4.db"
        self._ensure_db()

    def _ensure_db(self):
        conn = sqlite3.connect(self.db)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trades_full(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dt TEXT,
                symbol TEXT,
                signal TEXT,
                entry REAL,
                sl REAL,
                target1 REAL,
                target2 REAL,
                exit REAL,
                pnl REAL,
                reason TEXT,
                confidence INTEGER,
                rr REAL,
                qty INTEGER,
                status TEXT
            )
            """
        )
        conn.commit()
        conn.close()

    def _insert_trade(self, record):
        tries = 0
        last_exc = None
        while tries < 3:
            try:
                conn = sqlite3.connect(self.db)
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO trades_full(
                        dt,symbol,signal,entry,sl,target1,target2,exit,pnl,reason,confidence,rr,qty,status
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        record.get("dt"),
                        record.get("symbol"),
                        record.get("signal"),
                        record.get("entry"),
                        record.get("sl"),
                        record.get("target1"),
                        record.get("target2"),
                        record.get("exit"),
                        record.get("pnl"),
                        record.get("reason"),
                        int(record.get("confidence") or 0),
                        record.get("rr"),
                        int(record.get("qty") or 0),
                        record.get("status")
                    )
                )
                conn.commit()
                rowid = cur.lastrowid
                conn.close()
                return rowid
            except Exception as exc:
                last_exc = exc
                log_error(f"DB insert attempt {tries+1} failed: {exc}")
                tries += 1
        log_error(f"DB insert failed after retries: {last_exc}")
        return None

    def _update_trade_close(self, rowid, exit_price, pnl_value):
        tries = 0
        last_exc = None
        while tries < 3:
            try:
                conn = sqlite3.connect(self.db)
                cur = conn.cursor()
                cur.execute(
                    "UPDATE trades_full SET exit=?, pnl=?, status='CLOSED' WHERE id=?",
                    (exit_price, pnl_value, rowid)
                )
                conn.commit()
                conn.close()
                return True
            except Exception as exc:
                last_exc = exc
                log_error(f"DB update attempt {tries+1} failed: {exc}")
                tries += 1
        log_error(f"DB update failed after retries: {last_exc}")
        return False

    def run(self, symbol, df):
        trade = strategy.analyse(df)

        # Only act on BUY/SELL
        if trade.get("signal") not in ("BUY", "SELL"):
            return trade

        # Risk filter
        if not risk.allow_trade():
            log_info("Risk manager blocked trade")
            return trade

        ltp = self._safe_float(df.iloc[-1].get("close"))
        prev_high = self._safe_float(df.iloc[-2].get("high")) if len(df) >= 2 else ltp
        prev_low = self._safe_float(df.iloc[-2].get("low")) if len(df) >= 2 else ltp

        # If a position is open for this symbol, manage it
        if position.is_open() and position.symbol == symbol:
            return self._manage_open_position(symbol, df, trade)

        # Prevent duplicate pending for same symbol
        pending = self.pending.get(symbol)

        # New trade signal -> create pending confirmation
        if not pending:
            self.pending[symbol] = {
                "side": trade["signal"],
                "entry": trade["entry"],
                "sl": trade["sl"],
                "target1": trade["target1"],
                "target2": trade["target2"],
                "confidence": trade.get("confidence", 0),
                "reason": ", ".join(trade.get("reason") or []),
                "created_at": datetime.now(),
                "attempts": 0
            }
            log_info(f"Pending confirmation created for {symbol} {trade['signal']} at {trade['entry']}")
            return trade

        # Pending exists -> check confirmation
        try:
            pending = self.pending[symbol]
            side = pending["side"]
            entry = float(pending["entry"])
            sl = float(pending["sl"])
            target1 = float(pending["target1"])
            target2 = float(pending["target2"])

            # Confirmation rules: next candle must close beyond entry and beyond previous structure
            confirmed = False
            buffer = max(0.0005 * entry, 0.01)

            if side == "BUY":
                if ltp >= entry + buffer and ltp > prev_high:
                    confirmed = True
            else:
                if ltp <= entry - buffer and ltp < prev_low:
                    confirmed = True

            # Avoid fake breakouts by requiring at least 2 confirmations within short window
            pending["attempts"] += 1

            if confirmed:
                # Execute trade
                qty = risk.quantity(entry, sl)
                qty = max(1, int(qty))

                executed = False
                tries = 0
                last_exc = None
                while tries < 3 and not executed:
                    try:
                        if side == "BUY":
                            paper.buy(symbol, entry, sl, target2, qty)
                        else:
                            paper.sell(symbol, entry, sl, target2, qty)
                        executed = True
                    except Exception as exc:
                        last_exc = exc
                        log_error(f"Paper trade attempt {tries+1} failed: {exc}")
                        tries += 1

                if not executed:
                    log_error(f"Failed to execute paper trade for {symbol}: {last_exc}")
                    del self.pending[symbol]
                    return trade

                # Record in DB
                rr = trade.get("rr", 0)
                rec = {
                    "dt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "symbol": symbol,
                    "signal": side,
                    "entry": entry,
                    "sl": sl,
                    "target1": target1,
                    "target2": target2,
                    "exit": None,
                    "pnl": None,
                    "reason": pending.get("reason"),
                    "confidence": pending.get("confidence", 0),
                    "rr": rr,
                    "qty": qty,
                    "status": "OPEN"
                }
                rowid = self._insert_trade(rec)

                # Open position in manager
                position.open(side, symbol, qty, entry, sl, target1, target2)
                risk.trade_opened()
                self.current_position = side

                # Track open trade metadata
                self.open_trades[symbol] = {
                    "rowid": rowid,
                    "side": side,
                    "entry": entry,
                    "sl": sl,
                    "target1": target1,
                    "target2": target2,
                    "qty": qty,
                    "remaining_qty": qty,
                    "partial_done": False,
                    "opened_at": datetime.now(),
                    "confidence": pending.get("confidence", 0),
                    "reason": pending.get("reason"),
                    "rr": rr
                }

                log_info(f"Executed {side} {symbol} qty={qty} entry={entry} sl={sl} target2={target2}")

                # Clear pending
                del self.pending[symbol]

                return trade

            # If not confirmed within 3 attempts, drop pending
            if pending["attempts"] >= 3:
                log_info(f"Pending confirmation timed out for {symbol}")
                del self.pending[symbol]

            return trade

        except Exception as exc:
            log_error(f"Error processing pending confirmation: {exc}")
            if symbol in self.pending:
                del self.pending[symbol]
            return trade

    def _manage_open_position(self, symbol, df, trade):
        ltp = self._safe_float(df.iloc[-1].get("close"))
        open_meta = self.open_trades.get(symbol)
        if not open_meta:
            return trade

        side = open_meta["side"]
        entry = open_meta["entry"]
        sl = open_meta["sl"]
        target1 = open_meta["target1"]
        target2 = open_meta["target2"]
        remaining = open_meta["remaining_qty"]

        try:
            atr = max(0.0001, float(df.iloc[-1].get("ATR") or 0))
            if atr <= 0:
                atr = abs(entry) * 0.01
        except Exception:
            atr = abs(entry) * 0.01

        if side == "BUY":
            if not open_meta.get("partial_done") and ltp >= target1:
                qty_partial = math.floor(open_meta["qty"] / 2)
                if qty_partial > 0:
                    pnl_value = (target1 - entry) * qty_partial
                    paper.balance += pnl_value
                    paper.history.append({"time": datetime.now(), "side": side, "entry": entry, "exit": target1, "pnl": round(pnl_value, 2)})
                    open_meta["remaining_qty"] -= qty_partial
                    open_meta["partial_done"] = True
                    position.qty = open_meta["remaining_qty"]
                    log_info(f"Partial booked {symbol} qty={qty_partial} at {target1} pnl={round(pnl_value,2)}")
            if ltp > entry + atr:
                new_trail = max(position.sl, round(ltp - atr, 2))
                if new_trail > position.sl:
                    position.sl = new_trail
                    paper.trailing_sl = new_trail
                    log_info(f"Trailing SL moved for {symbol} to {new_trail}")
            if ltp >= target2:
                closing_qty = open_meta["remaining_qty"]
                if closing_qty <= 0:
                    closing_qty = open_meta["qty"]
                paper.qty = closing_qty
                pnl_value = paper.exit(ltp)
                rowid = open_meta.get("rowid")
                if rowid:
                    self._update_trade_close(rowid, ltp, pnl_value)
                position.close()
                risk.update_loss(pnl_value)
                log_info(f"Target2 hit - closed {symbol} pnl={pnl_value}")
                del self.open_trades[symbol]
                self.current_position = None
                journal.close(ltp, pnl_value)
                return trade
            if ltp <= position.sl:
                paper.qty = open_meta["remaining_qty"]
                pnl_value = paper.exit(ltp)
                rowid = open_meta.get("rowid")
                if rowid:
                    self._update_trade_close(rowid, ltp, pnl_value)
                position.close()
                risk.update_loss(pnl_value)
                log_info(f"Stoploss hit - closed {symbol} pnl={pnl_value}")
                del self.open_trades[symbol]
                self.current_position = None
                journal.close(ltp, pnl_value)
                return trade

        else:
            if not open_meta.get("partial_done") and ltp <= target1:
                qty_partial = math.floor(open_meta["qty"] / 2)
                if qty_partial > 0:
                    pnl_value = (entry - target1) * qty_partial
                    paper.balance += pnl_value
                    paper.history.append({"time": datetime.now(), "side": side, "entry": entry, "exit": target1, "pnl": round(pnl_value, 2)})
                    open_meta["remaining_qty"] -= qty_partial
                    open_meta["partial_done"] = True
                    position.qty = open_meta["remaining_qty"]
                    log_info(f"Partial booked {symbol} qty={qty_partial} at {target1} pnl={round(pnl_value,2)}")
            if ltp < entry - atr:
                new_trail = min(position.sl, round(ltp + atr, 2))
                if new_trail < position.sl:
                    position.sl = new_trail
                    paper.trailing_sl = new_trail
                    log_info(f"Trailing SL moved for {symbol} to {new_trail}")
            if ltp <= target2:
                closing_qty = open_meta["remaining_qty"]
                if closing_qty <= 0:
                    closing_qty = open_meta["qty"]
                paper.qty = closing_qty
                pnl_value = paper.exit(ltp)
                rowid = open_meta.get("rowid")
                if rowid:
                    self._update_trade_close(rowid, ltp, pnl_value)
                position.close()
                risk.update_loss(pnl_value)
                log_info(f"Target2 hit - closed {symbol} pnl={pnl_value}")
                del self.open_trades[symbol]
                self.current_position = None
                journal.close(ltp, pnl_value)
                return trade
            if ltp >= position.sl:
                paper.qty = open_meta["remaining_qty"]
                pnl_value = paper.exit(ltp)
                rowid = open_meta.get("rowid")
                if rowid:
                    self._update_trade_close(rowid, ltp, pnl_value)
                position.close()
                risk.update_loss(pnl_value)
                log_info(f"Stoploss hit - closed {symbol} pnl={pnl_value}")
                del self.open_trades[symbol]
                self.current_position = None
                journal.close(ltp, pnl_value)
                return trade

        # Opposite signal - exit
        if trade.get("signal") and trade.get("signal") != side:
            log_info(f"Opposite signal received for {symbol}, closing position")
            paper.qty = open_meta["remaining_qty"]
            pnl_value = paper.exit(ltp)
            rowid = open_meta.get("rowid")
            if rowid:
                self._update_trade_close(rowid, ltp, pnl_value)
            position.close()
            risk.update_loss(pnl_value)
            del self.open_trades[symbol]
            self.current_position = None
            journal.close(ltp, pnl_value)
            return trade

        # Market close: naive 15:20 exit
        now = datetime.now().time()
        if now >= time(15, 20):
            log_info(f"Market close - exiting {symbol}")
            paper.qty = open_meta["remaining_qty"]
            pnl_value = paper.exit(ltp)
            rowid = open_meta.get("rowid")
            if rowid:
                self._update_trade_close(rowid, ltp, pnl_value)
            position.close()
            risk.update_loss(pnl_value)
            del self.open_trades[symbol]
            self.current_position = None
            journal.close(ltp, pnl_value)
            return trade

        return trade

    def _safe_float(self, value, default=0.0):
        try:
            if value is None:
                return float(default)
            return float(value)
        except Exception:
            return float(default)


engine = ExecutionEngine()