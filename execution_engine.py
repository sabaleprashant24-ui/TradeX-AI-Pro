# ==========================================
# execution_engine.py
# TradeX AI Pro v4.0 - Advanced Order Execution Engine
# ==========================================

import sqlite3
import math
import time
import threading
from datetime import datetime, time as dtime
from typing import Dict, Any, Optional

from strategy import strategy
from paper_trade import paper
from trade_journal import journal
from risk_manager import risk
from position_manager import position
from pnl_manager import pnl
from logger import log_info, log_error, log_warning


class ExecutionEngine:
    """
    TradeX AI Pro v4.0 - Execution Engine
    Handles Signal Confirmation, Smart Trailing SL, Partial Profit Booking,
    Multi-Symbol Position Management, and SQLite Database Auditing.
    """

    def __init__(self, db_path: str = "tradex_v4.db"):
        self.db = db_path
        self.lock = threading.Lock()
        
        # State tracking
        self.pending: Dict[str, Dict[str, Any]] = {}
        self.open_trades: Dict[str, Dict[str, Any]] = {}
        self.current_position: Optional[str] = None
        self.last_signal: Optional[str] = None
        
        # System status
        self.is_active = True
        self.auto_squareoff_time = dtime(15, 20)
        
        self._ensure_db()
        log_info("Execution Engine v4.0 initialized successfully.")

    # ------------------------------------------------------------------
    # 1. DATABASE MANAGEMENT & INTEGRITY
    # ------------------------------------------------------------------

    def _ensure_db(self) -> None:
        """माहिती सुरक्षेसाठी डेटाबेस टेबल आणि इंडेक्स तयार करणे."""
        with self.lock:
            try:
                conn = sqlite3.connect(self.db)
                cur = conn.cursor()
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS trades_full(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        dt TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        signal TEXT NOT NULL,
                        entry REAL NOT NULL,
                        sl REAL NOT NULL,
                        target1 REAL NOT NULL,
                        target2 REAL NOT NULL,
                        exit REAL,
                        pnl REAL,
                        reason TEXT,
                        confidence INTEGER DEFAULT 0,
                        rr REAL DEFAULT 0.0,
                        qty INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        execution_mode TEXT DEFAULT 'PAPER'
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_symbol_status ON trades_full(symbol, status)"
                )
                conn.commit()
                conn.close()
            except Exception as exc:
                log_error(f"Execution Engine DB Initialization Failed: {exc}")

    def _insert_trade(self, record: Dict[str, Any]) -> Optional[int]:
        """नवीन सुरू झालेला ट्रेड डेटाबेसमध्ये सेव्ह करणे (विथ ३ रीट्राय)."""
        with self.lock:
            for tries in range(3):
                try:
                    conn = sqlite3.connect(self.db, timeout=10)
                    cur = conn.cursor()
                    cur.execute(
                        """
                        INSERT INTO trades_full(
                            dt, symbol, signal, entry, sl, target1, target2,
                            exit, pnl, reason, confidence, rr, qty, status, execution_mode
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                            float(record.get("rr") or 0.0),
                            int(record.get("qty") or 0),
                            record.get("status", "OPEN"),
                            record.get("execution_mode", "PAPER")
                        )
                    )
                    conn.commit()
                    rowid = cur.lastrowid
                    conn.close()
                    return rowid
                except Exception as exc:
                    log_warning(f"DB insert attempt {tries+1} failed for {record.get('symbol')}: {exc}")
                    time.sleep(0.2)
            
            log_error(f"Critical DB Insert Failure for {record.get('symbol')}")
            return None

    def _update_trade_close(self, rowid: int, exit_price: float, pnl_value: float) -> bool:
        """ट्रेड बंद झाल्यावर PnL आणि Exit Price अपडेट करणे."""
        if not rowid:
            return False
            
        with self.lock:
            for tries in range(3):
                try:
                    conn = sqlite3.connect(self.db, timeout=10)
                    cur = conn.cursor()
                    cur.execute(
                        """
                        UPDATE trades_full 
                        SET exit=?, pnl=?, status='CLOSED' 
                        WHERE id=?
                        """,
                        (exit_price, round(pnl_value, 2), rowid)
                    )
                    conn.commit()
                    conn.close()
                    return True
                except Exception as exc:
                    log_warning(f"DB update attempt {tries+1} failed for rowid {rowid}: {exc}")
                    time.sleep(0.2)
            
            log_error(f"Critical DB Update Failure for Trade Row ID: {rowid}")
            return False

    # ------------------------------------------------------------------
    # 2. MAIN TICK / CANDLE EXECUTION PIPELINE
    # ------------------------------------------------------------------

    def run(self, symbol: str, df: Any) -> Dict[str, Any]:
        """
        मुख्य एक्झिक्युशन सायकल:
        1. मार्केट डेटा तपासणी
        2. सुरू असलेल्या पोझिशनचे व्यवस्थापन (Trailing SL / Profit Targets)
        3. नवीन सिग्नल्सचे विश्लेषण व Confirmation Filter
        """
        if not self.is_active:
            log_warning("Execution Engine is currently disabled.")
            return {}

        if df is None or df.empty or len(df) < 2:
            return {}

        # 1. सुरू असलेली पोझिशन असेल तर ट्रॅकिंग आणि एक्झिट लॉजिक
        if symbol in self.open_trades:
            return self._manage_open_position(symbol, df)

        # 2. स्ट्रॅटेजी कडून टेक्निकल सिग्नल विश्लेषित करणे
        trade = strategy.analyse(df)
        signal = trade.get("signal")

        if signal not in ("BUY", "SELL"):
            return trade

        # 3. रिस्क मॅनेजर परमिशन चेक
        if not risk.allow_trade():
            log_info(f"Risk Manager blocked trade entry for {symbol}")
            return trade

        ltp = self._safe_float(df.iloc[-1].get("close"))
        prev_high = self._safe_float(df.iloc[-2].get("high"), default=ltp)
        prev_low = self._safe_float(df.iloc[-2].get("low"), default=ltp)

        # 4. पेंडिंग कन्फर्मेशन चेकिंग
        pending_item = self.pending.get(symbol)

        # नवीन सिग्नल आल्यास Confirmation साठी Pending Queue मध्ये ठेवणे
        if not pending_item:
            self.pending[symbol] = {
                "side": signal,
                "entry": trade.get("entry", ltp),
                "sl": trade.get("sl", 0.0),
                "target1": trade.get("target1", 0.0),
                "target2": trade.get("target2", 0.0),
                "confidence": trade.get("confidence", 0),
                "reason": ", ".join(trade.get("reason") or []),
                "created_at": datetime.now(),
                "attempts": 0
            }
            log_info(f"Pending breakout confirmation created for {symbol} [{signal}] at {trade.get('entry')}")
            return trade

        # 5. ब्रेकआऊट कन्फर्मेशन तपासणी (Fake Breakout Avoidance)
        return self._process_pending_confirmation(symbol, df, trade, pending_item, ltp, prev_high, prev_low)

    # ------------------------------------------------------------------
    # 3. CONFIRMATION AND ORDER ENTRY LOGIC
    # ------------------------------------------------------------------

    def _process_pending_confirmation(
        self, symbol: str, df: Any, trade: Dict[str, Any], pending: Dict[str, Any], 
        ltp: float, prev_high: float, prev_low: float
    ) -> Dict[str, Any]:
        """पुढच्या कॅन्डलवर ब्रेकआऊट कन्फर्म झाल्यावर खरी ऑर्डर प्लेस करणे."""
        try:
            side = pending["side"]
            entry = float(pending["entry"])
            sl = float(pending["sl"])
            target1 = float(pending["target1"])
            target2 = float(pending["target2"])

            confirmed = False
            buffer = max(0.0005 * entry, 0.05)

            # कन्फर्मेशन नियम: LTP ने एंट्री लेव्हल आणि आधीच्या कॅन्डलचा हाय/लो तोडला पाहिजे
            if side == "BUY":
                if ltp >= (entry + buffer) and ltp > prev_high:
                    confirmed = True
            elif side == "SELL":
                if ltp <= (entry - buffer) and ltp < prev_low:
                    confirmed = True

            pending["attempts"] += 1

            if confirmed:
                # पोझिशन साईझिंग काढणे
                qty = risk.quantity(entry, sl)
                qty = max(1, int(qty))

                # पेपर ट्रेडिंग एक्झिक्युशन
                executed = False
                for tries in range(3):
                    try:
                        if side == "BUY":
                            paper.buy(symbol, entry, sl, target2, qty)
                        else:
                            paper.sell(symbol, entry, sl, target2, qty)
                        executed = True
                        break
                    except Exception as exc:
                        log_error(f"Paper trade attempt {tries+1} failed for {symbol}: {exc}")
                        time.sleep(0.1)

                if not executed:
                    log_error(f"Failed to execute paper trade for {symbol} after retries.")
                    if symbol in self.pending:
                        del self.pending[symbol]
                    return trade

                # डेटाबेस एंट्री तयार करणे
                rr = float(trade.get("rr") or 0.0)
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
                    "status": "OPEN",
                    "execution_mode": "PAPER"
                }
                rowid = self._insert_trade(rec)

                # ग्लोबल स्टेट अपडेट्स
                position.open(side, symbol, qty, entry, sl, target1, target2)
                risk.trade_opened()
                self.current_position = side

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
                    "total_pnl": 0.0,
                    "opened_at": datetime.now(),
                    "confidence": pending.get("confidence", 0),
                    "reason": pending.get("reason"),
                    "rr": rr
                }

                log_info(f"ORDER EXECUTED: {side} {symbol} | Qty: {qty} | Entry: {entry} | SL: {sl} | T2: {target2}")
                del self.pending[symbol]
                return trade

            # ३ वेळा कॅन्डल कन्फर्मेशन न झाल्यास पेंडिंग सिग्नल रद्द करा
            if pending["attempts"] >= 3:
                log_info(f"Pending order confirmation expired/timed out for {symbol}")
                del self.pending[symbol]

            return trade

        except Exception as exc:
            log_error(f"Error processing pending confirmation for {symbol}: {exc}")
            if symbol in self.pending:
                del self.pending[symbol]
            return trade

    # ------------------------------------------------------------------
    # 4. ACTIVE POSITION MANAGEMENT & TRAILING
    # ------------------------------------------------------------------

    def _manage_open_position(self, symbol: str, df: Any) -> Dict[str, Any]:
        """सुरू असलेल्या ट्रेडवर Target 1, Target 2, Trailing SL आणि Intraday Auto-Squareoff चे लक्ष ठेवणे."""
        ltp = self._safe_float(df.iloc[-1].get("close"))
        open_meta = self.open_trades.get(symbol)
        
        if not open_meta:
            return {}

        side = open_meta["side"]
        entry = open_meta["entry"]
        target1 = open_meta["target1"]
        target2 = open_meta["target2"]

        # ATR आधारित व्होलॅटिलिटी स्टॉप-लॉस काढणे
        try:
            atr = max(0.0001, float(df.iloc[-1].get("ATR") or 0))
            if atr <= 0:
                atr = abs(entry) * 0.01
        except Exception:
            atr = abs(entry) * 0.01

        # A. BUY POSITION MANAGEMENT
        if side == "BUY":
            # 1. Partial Profit Booking (Target 1)
            if not open_meta.get("partial_done") and ltp >= target1:
                qty_partial = math.floor(open_meta["qty"] / 2)
                if qty_partial > 0:
                    pnl_partial = (target1 - entry) * qty_partial
                    paper.balance += pnl_partial
                    open_meta["total_pnl"] += pnl_partial
                    open_meta["remaining_qty"] -= qty_partial
                    open_meta["partial_done"] = True
                    
                    paper.history.append({
                        "time": datetime.now(),
                        "side": side,
                        "symbol": symbol,
                        "entry": entry,
                        "exit": target1,
                        "pnl": round(pnl_partial, 2)
                    })
                    log_info(f"PARTIAL TARGET 1 HIT [{symbol}]: Booked Qty={qty_partial} at {target1} | Profit: +{round(pnl_partial, 2)}")

            # 2. Dynamic Trailing Stop Loss
            if ltp > entry + atr:
                new_trail = max(position.sl, round(ltp - atr, 2))
                if new_trail > position.sl:
                    position.sl = new_trail
                    paper.trailing_sl = new_trail
                    log_info(f"Trailing SL Updated [{symbol}]: New SL -> {new_trail}")

            # 3. Exit Conditions Check
            if ltp >= target2:
                self._close_position_completely(symbol, ltp, "Target 2 Reached")
                return {}
            if ltp <= position.sl:
                self._close_position_completely(symbol, ltp, "Stop Loss / Trailing Hit")
                return {}

        # B. SELL POSITION MANAGEMENT
        elif side == "SELL":
            # 1. Partial Profit Booking (Target 1)
            if not open_meta.get("partial_done") and ltp <= target1:
                qty_partial = math.floor(open_meta["qty"] / 2)
                if qty_partial > 0:
                    pnl_partial = (entry - target1) * qty_partial
                    paper.balance += pnl_partial
                    open_meta["total_pnl"] += pnl_partial
                    open_meta["remaining_qty"] -= qty_partial
                    open_meta["partial_done"] = True
                    
                    paper.history.append({
                        "time": datetime.now(),
                        "side": side,
                        "symbol": symbol,
                        "entry": entry,
                        "exit": target1,
                        "pnl": round(pnl_partial, 2)
                    })
                    log_info(f"PARTIAL TARGET 1 HIT [{symbol}]: Booked Qty={qty_partial} at {target1} | Profit: +{round(pnl_partial, 2)}")

            # 2. Dynamic Trailing Stop Loss
            if ltp < entry - atr:
                new_trail = min(position.sl, round(ltp + atr, 2))
                if new_trail < position.sl:
                    position.sl = new_trail
                    paper.trailing_sl = new_trail
                    log_info(f"Trailing SL Updated [{symbol}]: New SL -> {new_trail}")

            # 3. Exit Conditions Check
            if ltp <= target2:
                self._close_position_completely(symbol, ltp, "Target 2 Reached")
                return {}
            if ltp >= position.sl:
                self._close_position_completely(symbol, ltp, "Stop Loss / Trailing Hit")
                return {}

        # C. OPPOSITE SIGNAL REVERSAL EXIT
        trade = strategy.analyse(df)
        if trade.get("signal") and trade.get("signal") != side and trade.get("signal") in ("BUY", "SELL"):
            log_info(f"Opposite Signal Detected for {symbol}. Triggering Emergency Reversal Exit.")
            self._close_position_completely(symbol, ltp, "Opposite Signal Exit")
            return {}

        # D. TIME-BASED AUTO SQUARE OFF (3:20 PM IST)
        now_time = datetime.now().time()
        if now_time >= self.auto_squareoff_time:
            log_info(f"Market Auto-Squareoff Time Reached (15:20). Closing Position [{symbol}].")
            self._close_position_completely(symbol, ltp, "Auto Square-Off (15:20)")
            return {}

        return {}

    # ------------------------------------------------------------------
    # 5. COMPLETE POSITION CLOSURE HELPER
    # ------------------------------------------------------------------

    def _close_position_completely(self, symbol: str, exit_price: float, reason: str) -> None:
        """उरलेली सर्व क्वांटिटी बंद करून PnL अपडेट करणे."""
        open_meta = self.open_trades.get(symbol)
        if not open_meta:
            return

        closing_qty = open_meta["remaining_qty"]
        paper.qty = closing_qty

        # उरलेल्या पोझिशनचा PnL काढणे
        remaining_pnl = paper.exit(exit_price)
        total_pnl = round(open_meta.get("total_pnl", 0.0) + remaining_pnl, 2)

        rowid = open_meta.get("rowid")
        if rowid:
            self._update_trade_close(rowid, exit_price, total_pnl)

        position.close()
        risk.update_loss(total_pnl)
        
        try:
            journal.close(exit_price, total_pnl)
        except Exception as exc:
            log_warning(f"Journal logging error on position close: {exc}")

        log_info(f"POSITION CLOSED [{symbol}] | Reason: {reason} | Exit Price: {exit_price} | Final Total PnL: {total_pnl}")

        # स्टेट क्लियर करणे
        if symbol in self.open_trades:
            del self.open_trades[symbol]
        self.current_position = None

    # ------------------------------------------------------------------
    # 6. UTILITY FUNCTIONS & EMERGENCY CONTROLS
    # ------------------------------------------------------------------

    def force_close_all_positions(self, current_prices: Dict[str, float]) -> None:
        """इमर्जन्सी किंवा सिस्टीम बंद करताना सर्व पोझिशन्स तातडीने क्लोज करणे."""
        log_warning("EMERGENCY: Force closing all active positions!")
        symbols = list(self.open_trades.keys())
        for sym in symbols:
            ltp = current_prices.get(sym, self.open_trades[sym]["entry"])
            self._close_position_completely(sym, ltp, "Emergency Force Close")

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        """सुरक्षित प्रकारे डेटा टाईप फ्लोटमध्ये रूपांतरित करणे."""
        try:
            if value is None or math.isnan(float(value)):
                return float(default)
            return float(value)
        except Exception:
            return float(default)

    def get_open_positions_summary(self) -> Dict[str, Any]:
        """डॅशबोर्ड किंवा मॉनिटरसाठी सध्याच्या ट्रेड्सची माहिती देणे."""
        return {
            "total_open": len(self.open_trades),
            "pending_confirmations": len(self.pending),
            "active_symbols": list(self.open_trades.keys()),
            "details": self.open_trades
        }


# Global Singleton Instance for Execution Engine
engine = ExecutionEngine()
