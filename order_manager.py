"""
TradeX AI Pro v4.0 - Order Execution & Position Management Engine
File: order_manager.py

Provides Enterprise-Grade Order Lifecycle & Position Governance:
- Strong Type Safety via Enum (OrderStatus & TradeAction)
- Multi-Target Support with Dynamic Partial Profit Booking (Partial Exit)
- Thread-Safe Dynamic Position Sizing based on ATR & Risk Capital
- Resilient Broker Gateway Sync with Response Validation & Robust Exception Handling
- Circuit Breaker Sync via PortfolioRiskManager

Compatible with Python 3.13 and Pydroid 3.
"""

from datetime import datetime
from enum import Enum
import logging
import threading
from typing import Dict, Any, List, Optional
import uuid

from logger import LOGGER
from risk_manager import RISK_MANAGER

# Safe Config Import with Defaults
try:
    from config import ORDER_CONFIG
except ImportError:
    ORDER_CONFIG = {
        "default_risk_per_trade_percent": 1.0,  # 1% risk per trade
        "risk_reward_ratio": 2.0,               # 1:2 Risk to Reward
        "enable_trailing_sl": True,
        "trailing_sl_atr_multiplier": 1.5,
        "default_product_type": "MIS",           # Intraday
    }

# Safe Broker Gateway Import
try:
    from broker import BROKER_GATEWAY
except ImportError:
    BROKER_GATEWAY = None


class OrderStatus(str, Enum):
    """Strongly Typed Enums for Order Lifecycle States."""
    OPEN = "OPEN"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    SUCCESS = "SUCCESS"


class TradeAction(str, Enum):
    """Strongly Typed Enums for Directional Signals."""
    BUY = "BUY"
    SELL = "SELL"


class PositionSizer:
    """Calculates Optimal Trade Position Size based on Account Risk and ATR/Volatility."""

    @staticmethod
    def calculate_qty(
        capital: float,
        entry_price: float,
        stop_loss_price: float,
        risk_percent: float = 1.0,
        lot_size: int = 1,
    ) -> int:
        """
        Calculates exact quantity based on maximum allowed risk per trade.
        """
        if entry_price <= 0 or stop_loss_price <= 0 or entry_price == stop_loss_price:
            return lot_size

        risk_per_share = abs(entry_price - stop_loss_price)
        max_risk_amount = capital * (risk_percent / 100.0)

        raw_qty = max_risk_amount / risk_per_share
        
        # Round down to nearest Lot Size
        final_qty = int(raw_qty // lot_size) * lot_size
        return max(lot_size, final_qty)


class OrderManager:
    """Thread-Safe Order Management & Lifecycle Execution Engine."""

    def __init__(self):
        self._lock = threading.RLock()
        self.active_orders: Dict[str, Dict[str, Any]] = {}
        self.open_positions: Dict[str, Dict[str, Any]] = {}
        self.order_history: List[Dict[str, Any]] = []  # Archival for closed orders

    def execute_signal(
        self,
        signal_payload: Dict[str, Any],
        custom_risk_percent: Optional[float] = None,
        lot_size: int = 1,
    ) -> Dict[str, Any]:
        """
        Processes Scanner Signal, calculates SL/TP/Qty, verifies Risk, and routes to Broker.
        """
        with self._lock:
            symbol = signal_payload.get("symbol")
            raw_action = str(signal_payload.get("signal", "")).upper()
            price = signal_payload.get("price", 0.0)
            sector = signal_payload.get("sector", "GENERAL")
            option_type = signal_payload.get("option_type", "NONE")

            if not symbol or raw_action not in [TradeAction.BUY.value, TradeAction.SELL.value] or price <= 0:
                return {"status": OrderStatus.REJECTED.value, "reason": f"Invalid Signal or Price for {symbol}"}

            action = TradeAction(raw_action)

            # 1. Duplicate Open Position Protection Check
            if symbol in self.open_positions:
                LOGGER.warning(f"ORDER REJECTED: Position for {symbol} is already open.")
                return {
                    "status": OrderStatus.REJECTED.value,
                    "reason": f"Duplicate Order Blocked: Position for {symbol} is already open.",
                }

            # 2. Get Account Metrics from Risk Manager
            status_info = RISK_MANAGER.get_status()
            current_balance = status_info.get("current_balance", 100000.0)

            # 3. Dynamic Risk Multiplier Adjustment
            vix = signal_payload.get("india_vix", 0.0)
            risk_mult = RISK_MANAGER.get_adjusted_risk_multiplier(india_vix=vix)
            
            base_risk_pct = custom_risk_percent or ORDER_CONFIG.get("default_risk_per_trade_percent", 1.0)
            effective_risk_pct = base_risk_pct * risk_mult

            # 4. Calculate Dynamic Stop Loss & Target Price
            atr = signal_payload.get("atr", price * 0.01) # Default 1% if ATR missing
            if atr <= 0:
                atr = price * 0.01

            rr_ratio = ORDER_CONFIG.get("risk_reward_ratio", 2.0)
            sl_atr_mult = ORDER_CONFIG.get("trailing_sl_atr_multiplier", 1.5)

            if action == TradeAction.BUY:
                stop_loss = round(price - (sl_atr_mult * atr), 2)
                target = round(price + (sl_atr_mult * atr * rr_ratio), 2)
            else: # SELL
                stop_loss = round(price + (sl_atr_mult * atr), 2)
                target = round(price - (sl_atr_mult * atr * rr_ratio), 2)

            # 5. Position Sizing
            qty = PositionSizer.calculate_qty(
                capital=current_balance,
                entry_price=price,
                stop_loss_price=stop_loss,
                risk_percent=effective_risk_pct,
                lot_size=lot_size,
            )

            required_capital = qty * price

            # 6. Risk Gatekeeper Validation
            active_pos_list = list(self.open_positions.values())
            risk_check = RISK_MANAGER.can_trade(
                requested_capital=required_capital,
                symbol=symbol,
                sector=sector,
                option_type=option_type,
                active_positions_metadata=active_pos_list,
            )

            if not risk_check["allowed"]:
                LOGGER.warning(f"ORDER EXECUTION REJECTED by RiskManager: {risk_check['reason']}")
                return {"status": OrderStatus.REJECTED.value, "reason": risk_check["reason"]}

            # 7. Construct Order Request Payload
            order_id = f"TRX-{uuid.uuid4().hex[:8].upper()}"
            order_data = {
                "order_id": order_id,
                "symbol": symbol,
                "action": action.value,
                "total_qty": qty,
                "remaining_qty": qty,
                "entry_price": price,
                "stop_loss": stop_loss,
                "target": target,
                "atr": atr,
                "sector": sector,
                "option_type": option_type,
                "capital": required_capital,
                "status": OrderStatus.OPEN.value,
                "realized_pnl": 0.0,
                "timestamp": datetime.now().isoformat(),
            }

            # 8. Route Order to Broker Gateway with Validation & Exception Handling
            broker_response = None
            if BROKER_GATEWAY and hasattr(BROKER_GATEWAY, "place_order"):
                try:
                    broker_response = BROKER_GATEWAY.place_order(order_data)
                    
                    if broker_response and isinstance(broker_response, dict):
                        is_success = broker_response.get("success", True)
                        if not is_success:
                            err_msg = broker_response.get("message", "Broker rejected order execution")
                            LOGGER.error(f"BROKER REJECTION for {symbol}: {err_msg}")
                            return {
                                "status": OrderStatus.FAILED.value,
                                "reason": f"Broker Rejected Order: {err_msg}",
                                "order_id": order_id,
                            }
                except Exception as e:
                    LOGGER.error(f"BROKER GATEWAY ERROR for {symbol}: {str(e)}", exc_info=True)
                    return {
                        "status": OrderStatus.FAILED.value,
                        "reason": f"Broker Gateway Exception: {str(e)}",
                        "order_id": order_id,
                    }

            # Record Order & Position State internally after successful execution
            self.active_orders[order_id] = order_data
            self.open_positions[symbol] = order_data

            LOGGER.info(
                f"ORDER EXECUTED: {order_id} | {action.value} {qty} x {symbol} @ ₹{price} | SL: ₹{stop_loss} | TP: ₹{target}"
            )

            return {
                "status": OrderStatus.SUCCESS.value,
                "order_id": order_id,
                "details": order_data,
                "broker_response": broker_response,
            }

    def close_position_partial(
        self, 
        symbol: str, 
        exit_price: float, 
        exit_percentage: float = 50.0, 
        reason: str = "PARTIAL TARGET REACHED"
    ) -> Dict[str, Any]:
        """
        Supports Partial Profit Booking (e.g. Exit 50% at Target 1, trail rest).
        """
        with self._lock:
            pos = self.open_positions.get(symbol)
            if not pos:
                return {"status": OrderStatus.FAILED.value, "reason": f"No active open position found for {symbol}"}

            remaining_qty = pos["remaining_qty"]
            if remaining_qty <= 0:
                return {"status": OrderStatus.FAILED.value, "reason": "No remaining quantity to exit."}

            # Calculate Exit Quantity
            exit_qty = int(remaining_qty * (exit_percentage / 100.0))
            if exit_qty <= 0 or exit_qty >= remaining_qty:
                # Fallback to full exit if percentage rounds to full or zero
                return self.close_position(symbol=symbol, exit_price=exit_price, reason=reason)

            entry_price = pos["entry_price"]
            action = pos["action"]

            # Calculate Partial Realized PnL
            if action == TradeAction.BUY.value:
                partial_pnl = (exit_price - entry_price) * exit_qty
            else: # SELL
                partial_pnl = (entry_price - exit_price) * exit_qty

            pos["remaining_qty"] -= exit_qty
            pos["realized_pnl"] = round(pos["realized_pnl"] + partial_pnl, 2)
            pos["status"] = OrderStatus.PARTIALLY_CLOSED.value

            # Synchronize with Risk Manager Account State
            current_bal = RISK_MANAGER.current_balance + partial_pnl
            RISK_MANAGER.update_account_state(current_balance=current_bal, closed_pnl=partial_pnl)

            # Broker Partial Exit Sync
            if BROKER_GATEWAY and hasattr(BROKER_GATEWAY, "close_position"):
                try:
                    BROKER_GATEWAY.close_position(symbol=symbol, qty=exit_qty)
                except Exception as e:
                    LOGGER.error(f"BROKER PARTIAL CLOSE ERROR for {symbol}: {str(e)}", exc_info=True)

            LOGGER.info(
                f"PARTIAL EXIT EXECUTED: {symbol} | Exited {exit_qty} shares @ ₹{exit_price} | PnL: ₹{partial_pnl:.2f} ({reason})"
            )

            return {
                "status": OrderStatus.SUCCESS.value,
                "symbol": symbol,
                "exited_qty": exit_qty,
                "remaining_qty": pos["remaining_qty"],
                "partial_pnl": round(partial_pnl, 2),
            }

    def close_position(self, symbol: str, exit_price: float, reason: str = "TARGET/SL REACHED") -> Dict[str, Any]:
        """
        Closes active open position fully (100%), archives active orders, and synchronizes PnL with PortfolioRiskManager.
        """
        with self._lock:
            pos = self.open_positions.pop(symbol, None)
            if not pos:
                return {"status": OrderStatus.FAILED.value, "reason": f"No active open position found for {symbol}"}

            order_id = pos.get("order_id")
            remaining_qty = pos["remaining_qty"]
            entry_price = pos["entry_price"]
            action = pos["action"]

            # Calculate Final Realized PnL
            if action == TradeAction.BUY.value:
                final_pnl = (exit_price - entry_price) * remaining_qty
            else: # SELL
                final_pnl = (entry_price - exit_price) * remaining_qty

            total_pnl = round(pos["realized_pnl"] + final_pnl, 2)

            pos["remaining_qty"] = 0
            pos["exit_price"] = exit_price
            pos["pnl"] = total_pnl
            pos["status"] = OrderStatus.CLOSED.value
            pos["close_reason"] = reason
            pos["closed_at"] = datetime.now().isoformat()

            # Active Order Cleanup & Archiving
            if order_id and order_id in self.active_orders:
                archived_order = self.active_orders.pop(order_id)
                archived_order.update(pos)
                self.order_history.append(archived_order)
            else:
                self.order_history.append(pos)

            # Update Account State in Risk Governance Engine
            current_bal = RISK_MANAGER.current_balance + final_pnl
            updated_risk_status = RISK_MANAGER.update_account_state(
                current_balance=current_bal,
                closed_pnl=final_pnl,
            )

            # Sync with Broker Gateway if available with Exception Handling
            if BROKER_GATEWAY and hasattr(BROKER_GATEWAY, "close_position"):
                try:
                    BROKER_GATEWAY.close_position(symbol=symbol, qty=remaining_qty)
                except Exception as e:
                    LOGGER.error(f"BROKER CLOSE POSITION ERROR for {symbol}: {str(e)}", exc_info=True)

            LOGGER.info(f"POSITION FULLY CLOSED: {symbol} | OrderID: {order_id} | Total PnL: ₹{total_pnl:.2f} ({reason})")

            return {
                "status": OrderStatus.SUCCESS.value,
                "symbol": symbol,
                "order_id": order_id,
                "total_pnl": total_pnl,
                "risk_status": updated_risk_status,
            }


# Global Order Manager Singleton Instance
ORDER_MANAGER = OrderManager()