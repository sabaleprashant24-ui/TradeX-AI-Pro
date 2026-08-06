"""
TradeX AI Pro v5.2 - Production Position Manager
File: position_manager.py

Features:
1. Multi-Position Support using Unique Position IDs (No Overwriting)
2. Thread-Safe Operations using Threading RLock
3. Real-Time MTM / PnL Calculation per Position
4. Target (T1, T2) & Stop Loss Trigger Verification Logic
5. Structured Logging & Complete Audit Trail Integration
"""

import logging
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger("TradeX_PositionManager")


class PositionManager:
    """Thread-Safe Multi-Position Tracking & Management Engine."""

    def __init__(self):
        self._lock = threading.RLock()
        # Active positions mapped by position_id (e.g. POS-XXXXXX)
        self.active_positions: Dict[str, Dict[str, Any]] = {}
        # Historical / Closed positions
        self.closed_positions_history: List[Dict[str, Any]] = []

    # ---------------------------------
    # Open Position
    # ---------------------------------
    def open_position(
        self,
        position_id: str,
        symbol: str,
        side: str,  # "BUY" or "SELL"
        qty: int,
        entry_price: float,
        sl: float = 0.0,
        target1: float = 0.0,
        target2: float = 0.0,
        asset_type: str = "OPTIONS",  # "EQUITY", "FUTURES", "OPTIONS"
        trade_type: str = "INTRADAY"   # "INTRADAY", "DELIVERY"
    ) -> Dict[str, Any]:
        """Opens and registers a new position safely."""
        with self._lock:
            side = side.upper()
            pos_data = {
                "position_id": position_id,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "initial_qty": qty,
                "entry_price": float(entry_price),
                "current_ltp": float(entry_price),
                "sl": float(sl),
                "target1": float(target1),
                "target2": float(target2),
                "t1_hit": False,
                "t2_hit": False,
                "unrealized_pnl": 0.0,
                "realized_pnl": 0.0,
                "asset_type": asset_type,
                "trade_type": trade_type,
                "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "is_active": True
            }

            self.active_positions[position_id] = pos_data
            logger.info(f"POSITION OPENED: [{position_id}] {side} {symbol} x {qty} @ ₹{entry_price:.2f} | SL: ₹{sl:.2f} | T1: ₹{target1:.2f} | T2: ₹{target2:.2f}")
            return pos_data

    # ---------------------------------
    # Update Market Price & MTM
    # ---------------------------------
    def update_price(self, symbol: str, current_ltp: float) -> List[Dict[str, Any]]:
        """Updates MTM PnL for all active positions matching the symbol and checks SL/Targets."""
        triggered_events = []
        with self._lock:
            for pos_id, pos in self.active_positions.items():
                if pos["symbol"] == symbol and pos["is_active"]:
                    pos["current_ltp"] = float(current_ltp)
                    
                    # Calculate Unrealized PnL
                    if pos["side"] == "BUY":
                        pos["unrealized_pnl"] = round((current_ltp - pos["entry_price"]) * pos["qty"], 2)
                    else:
                        pos["unrealized_pnl"] = round((pos["entry_price"] - current_ltp) * pos["qty"], 2)

                    # Trigger Check Rules
                    event = self._check_triggers(pos, current_ltp)
                    if event:
                        triggered_events.append(event)

        return triggered_events

    def _check_triggers(self, pos: Dict[str, Any], ltp: float) -> Optional[Dict[str, Any]]:
        """Internal evaluation for Stop Loss and Target breaches."""
        side = pos["side"]
        
        # Stop Loss Check
        if pos["sl"] > 0:
            sl_hit = (ltp <= pos["sl"]) if side == "BUY" else (ltp >= pos["sl"])
            if sl_hit:
                return {"position_id": pos["position_id"], "symbol": pos["symbol"], "event": "SL_HIT", "price": ltp}

        # Target 1 Check
        if pos["target1"] > 0 and not pos["t1_hit"]:
            t1_hit = (ltp >= pos["target1"]) if side == "BUY" else (ltp <= pos["target1"])
            if t1_hit:
                pos["t1_hit"] = True
                return {"position_id": pos["position_id"], "symbol": pos["symbol"], "event": "TARGET1_HIT", "price": ltp}

        # Target 2 Check
        if pos["target2"] > 0 and not pos["t2_hit"]:
            t2_hit = (ltp >= pos["target2"]) if side == "BUY" else (ltp <= pos["target2"])
            if t2_hit:
                pos["t2_hit"] = True
                return {"position_id": pos["position_id"], "symbol": pos["symbol"], "event": "TARGET2_HIT", "price": ltp}

        return None

    # ---------------------------------
    # Close Position (Full / Partial)
    # ---------------------------------
    def close_position(self, position_id: str, exit_price: float, close_qty: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Closes position fully or partially and moves to history if fully closed."""
        with self._lock:
            if position_id not in self.active_positions:
                logger.warning(f"Close failed: Position ID {position_id} not found.")
                return None

            pos = self.active_positions[position_id]
            qty_to_close = close_qty if (close_qty and close_qty < pos["qty"]) else pos["qty"]

            # Calculate Realized PnL for closed batch
            if pos["side"] == "BUY":
                pnl = (exit_price - pos["entry_price"]) * qty_to_close
            else:
                pnl = (pos["entry_price"] - exit_price) * qty_to_close

            realized_pnl = round(pnl, 2)
            pos["realized_pnl"] += realized_pnl

            if qty_to_close == pos["qty"]:
                # Full Exit
                pos["qty"] = 0
                pos["is_active"] = False
                pos["exit_price"] = float(exit_price)
                pos["exit_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                closed_record = self.active_positions.pop(position_id)
                self.closed_positions_history.append(closed_record)
                
                logger.info(f"POSITION CLOSED FULLY: [{position_id}] {pos['symbol']} | Exit: ₹{exit_price:.2f} | Realized PnL: ₹{realized_pnl:,.2f}")
                return closed_record
            else:
                # Partial Exit
                pos["qty"] -= qty_to_close
                logger.info(f"PARTIAL EXIT: [{position_id}] {pos['symbol']} | Closed Qty: {qty_to_close} | Remaining: {pos['qty']} | PnL: ₹{realized_pnl:,.2f}")
                return pos

    # ---------------------------------
    # Getters & Utility
    # ---------------------------------
    def get_position(self, position_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self.active_positions.get(position_id)

    def get_all_active(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self.active_positions.values())

    def is_open(self, position_id: str = None) -> bool:
        """Returns True if any position is open, or if a specific position_id is open."""
        with self._lock:
            if position_id:
                return position_id in self.active_positions
            return len(self.active_positions) > 0

    def reset(self):
        """Resets all position tracking state."""
        with self._lock:
            self.active_positions.clear()
            self.closed_positions_history.clear()
            logger.info("PositionManager state reset successfully.")


# Global Singleton Instance
position_manager = PositionManager()
position = position_manager
