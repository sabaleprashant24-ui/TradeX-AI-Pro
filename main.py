"""
TradeX AI Pro v5.2 - Master Orchestrator Engine
File: main.py

System Flow:
Config & Logger Initialization -> Database Connection -> Broker Authentication ->
Option Chain & Market Scanner Sync -> Risk & Order Engine Execution -> Streamlit Dashboard Launch / Continuous Trading Loop
"""

import os
import sys
import time
import logging
import subprocess
from datetime import datetime

# Direct Imports from Core Sub-Systems
from logger import logger
from config import config
from database import db
from broker import BrokerConnector
from scanner import MarketScanner
from option_chain import OptionChainEngine
from pnl_manager import PnLManager
from risk_manager import RiskManager
from order_manager import OrderManager
from paper_trade import PaperTrader

class TradeXOrchestrator:
    def __init__(self):
        logger.info("Initializing TradeX AI Pro v5.2 Master Workstation Systems...")
        self.config = config
        self.db = db
        
        # Initialize Core Modules
        self.pnl_manager = PnLManager(initial_capital=self.config.INITIAL_CAPITAL)
        self.risk_manager = RiskManager(max_capital=self.config.INITIAL_CAPITAL)
        self.order_manager = OrderManager()
        self.paper_trader = PaperTrader(initial_capital=self.config.INITIAL_CAPITAL)
        
        # Connect Brokers and Engines
        self.broker = BrokerConnector(broker_type=getattr(self.config, 'BROKER_TYPE', 'PAPER'))
        self.option_engine = OptionChainEngine(symbol="NIFTY")
        self.market_scanner = MarketScanner()

    def run_preflight_checks(self) -> bool:
        """ Ensures system database, loggers, and network bridges are operational """
        logger.info("Running System Pre-Flight Diagnostics...")
        try:
            # DB Health Check
            if hasattr(self.db, 'log_event'):
                self.db.log_event("SYSTEM", "PREFLIGHT_CHECK", "Checking DB Integrity")
            
            # Broker Connection Test
            if hasattr(self.broker, 'check_connection'):
                broker_status = self.broker.check_connection()
                logger.info(f"Broker Bridge Status: {broker_status}")
            else:
                logger.info("Broker Connector Initialized Successfully.")
            
            return True
        except Exception as e:
            logger.critical(f"Pre-flight Checks Failed: {e}")
            return False

    def execute_market_cycle(self):
        """ Runs 1-iteration cycle across Scanner, Option Engine, and Risk Checks """
        logger.info("--- Executing Live Market Scanning Cycle ---")
        try:
            # 1. Fetch Option Chain & Hero-Zero Analysis
            chain_df, pcr, max_pain, spot = self.option_engine.fetch_live_chain()
            hero_zero_signal = self.option_engine.analyze_hero_zero(chain_df, spot)
            logger.info(f"Spot: {spot} | PCR: {pcr} | Max Pain: {max_pain} | Hero-Zero Signal: {hero_zero_signal.get('recommendation', 'NEUTRAL')}")

            # 2. Run Market Scanner Engine
            symbols_to_scan = getattr(self.config, 'DEFAULT_SYMBOLS', ['NIFTY', 'BANKNIFTY'])
            scan_results = self.market_scanner.scan_all_symbols(symbols_to_scan)
            logger.info(f"Active Market Scanning Signals Detected: {len(scan_results)}")

            # 3. Get Realized PnL from Analytics for Risk Manager Evaluation
            analytics = self.pnl_manager.get_advanced_analytics()
            current_pnl = analytics.get("total_net_pnl", 0.0)

            # 4. Process Signals through Risk Manager and Order Routing
            for signal in scan_results:
                is_allowed, risk_reason = self.risk_manager.evaluate_risk(
                    symbol=signal['symbol'],
                    trade_type=signal['signal'],
                    price=signal['price'],
                    current_pnl=current_pnl
                )

                if is_allowed:
                    logger.info(f"Risk Approved Signal for {signal['symbol']}. Routing Order...")
                    execution_mode = getattr(self.config, 'EXECUTION_MODE', 'PAPER_TRADE')
                    
                    if execution_mode == "PAPER_TRADE":
                        self.paper_trader.execute_paper_order(
                            symbol=signal['symbol'], 
                            signal_type=signal['signal'], 
                            qty=25, 
                            price=signal['price']
                        )
                    else:
                        self.order_manager.place_order(
                            symbol=signal['symbol'], 
                            signal_type=signal['signal'], 
                            qty=25, 
                            price=signal['price']
                        )
                else:
                    logger.warning(f"Risk Rejected Trade for {signal['symbol']}: {risk_reason}")

        except Exception as e:
            logger.exception(f"Error during market cycle execution: {e}")

    def run_continuous_loop(self):
        """ Runs continuous market scanning loop with safety exception handling """
        scan_interval = getattr(self.config, 'SCAN_INTERVAL', 10)
        logger.info(f"Starting Production Continuous Trading Loop (Scan Interval: {scan_interval}s)...")
        
        while True:
            try:
                self.execute_market_cycle()
                time.sleep(scan_interval)

            except KeyboardInterrupt:
                logger.info("Keyboard Interrupt detected. Gracefully shutting down TradeX Orchestrator...")
                break

            except Exception as e:
                logger.exception(f"Unexpected error in continuous trading loop: {e}")
                time.sleep(5)  # Pause to avoid rapid error looping

    def launch_dashboard(self):
        """ Spawns Streamlit Dashboard UI via Secure Subprocess """
        logger.info("Launching Production Dashboard UI via Subprocess...")
        try:
            subprocess.run(["streamlit", "run", "dashboard.py"], check=False)
        except Exception as e:
            logger.error(f"Failed to launch dashboard subprocess: {e}")


if __name__ == "__main__":
    orchestrator = TradeXOrchestrator()
    if orchestrator.run_preflight_checks():
        print("=====================================================")
        print("⚡ TradeX AI Pro v5.2 Master Controller Initialized")
        print("=====================================================")
        
        # CLI Modes: --cli (Single Run), --loop (Continuous Trading), Default (Dashboard)
        if len(sys.argv) > 1:
            mode = sys.argv[1].lower()
            if mode == "--cli":
                orchestrator.execute_market_cycle()
            elif mode == "--loop":
                orchestrator.run_continuous_loop()
            else:
                orchestrator.launch_dashboard()
        else:
            orchestrator.launch_dashboard()