"""
TradeX AI Pro v4.0 - Institutional Trading Workstation
File: dashboard.py

Production Architecture:
Authentication (RBAC) -> Settings Persistence -> Broker Bridge -> Market Engine (with Circuit Breaker) -> Option Adapter -> Core Backtester
"""

import math
import json
import os
import time
import requests
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from datetime import datetime, timedelta
from scipy.stats import norm

# Direct Import from Core Modules
from backtester import Backtester, SECTOR_MAP
import yfinance as yf

# ==========================================
# 0. PAGE CONFIGURATION (MUST BE VERY FIRST ST COMMAND)
# ==========================================
st.set_page_config(page_title="TradeX AI Pro v4.0", page_icon="⚡", layout="wide")

# Custom UI CSS
st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .stMetric { background-color: #1e222d; padding: 12px; border-radius: 8px; border: 1px solid #2a2e39; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 1. SETTINGS PERSISTENCE & SESSION INIT
# ==========================================
CONFIG_FILE = "dashboard_settings.json"

DEFAULT_SETTINGS = {
    "broker": "ANGEL_ONE",
    "mode": "PAPER_TRADE",
    "capital": 500000,
    "strategy": "EMA 9/21 Crossover",
    "auto_refresh_sec": 5,
    "api_key": "",
    "jwt_token": "",
    "symbols": ["RELIANCE", "TCS", "INFY", "HDFCBANK", "TATAMOTORS"]
}

def load_settings():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return DEFAULT_SETTINGS
    return DEFAULT_SETTINGS

def save_settings(settings_dict):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(settings_dict, f, indent=4)
    except Exception as e:
        st.error(f"Settings Save Error: {e}")

# Early Session State Initializations
if "app_settings" not in st.session_state:
    st.session_state["app_settings"] = load_settings()

if "market_data_cache" not in st.session_state:
    st.session_state["market_data_cache"] = {}

if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
    st.session_state["user_role"] = None
    st.session_state["username"] = None

# ==========================================
# 2. AUTHENTICATION & RBAC ENGINE
# ==========================================
USER_DB = {
    "admin": {"password": "adminpassword123", "role": "Admin", "name": "Chief Risk Officer"},
    "trader": {"password": "traderpassword123", "role": "Trader", "name": "Quant Trader"},
    "viewer": {"password": "viewerpassword123", "role": "Viewer", "name": "Guest Viewer"}
}

def authenticate_user():
    if not st.session_state["authenticated"]:
        st.markdown("## 🔐 TradeX AI Pro v4.0 Login")
        col_l1, col_l2, col_l3 = st.columns([1, 2, 1])
        with col_l2:
            st.info("Demo Credentials: admin/adminpassword123 | trader/traderpassword123 | viewer/viewerpassword123")
            user_input = st.text_input("Username")
            pass_input = st.text_input("Password", type="password")
            login_btn = st.button("Unlock Station", type="primary", width="stretch")

            if login_btn:
                if user_input in USER_DB and USER_DB[user_input]["password"] == pass_input:
                    st.session_state["authenticated"] = True
                    st.session_state["user_role"] = USER_DB[user_input]["role"]
                    st.session_state["username"] = USER_DB[user_input]["name"]
                    st.success(f"Welcome {st.session_state['username']}!")
                    st.rerun()
                else:
                    st.error("Invalid Credentials")
        return False
    return True

if not authenticate_user():
    st.stop()

def has_permission(required_role):
    roles = {"Viewer": 1, "Trader": 2, "Admin": 3}
    user_perm = roles.get(st.session_state.get("user_role", "Viewer"), 1)
    req_perm = roles.get(required_role, 3)
    return user_perm >= req_perm


def render_feed_status_panel():
    try:
        from broker import LIVE_BROKER
        from market_data import market_data

        broker_health = LIVE_BROKER.get_health_status()
        feed_health = market_data.get_feed_health()
        st.subheader("Live Feed Status")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Feed", feed_health.get("status", "UNKNOWN"))
        c2.metric("Connected", "YES" if feed_health.get("connected") else "NO")
        c3.metric("Last Tick", feed_health.get("last_tick_timestamp") or broker_health.get("feed_last_tick_timestamp") or "-")
        c4.metric("Latency", f"{feed_health.get('latency_ms', broker_health.get('feed_latency_ms', 0.0)):.2f} ms")
    except Exception:
        st.caption("Feed status unavailable in this session.")

# Dashboard integration point for live feed monitoring
render_feed_status_panel()

# ==========================================
# 3. BROKER API BRIDGE (ANGEL ONE / SMARTAPI COMPATIBLE)
# ==========================================
class LiveBrokerBridge:
    def __init__(self, api_key: str = "", jwt_token: str = ""):
        self.api_key = api_key
        self.jwt_token = jwt_token
        self.base_url = "https://apiconnect.angelbroking.com"

    def _get_headers(self):
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": "127.0.0.1",
            "X-ClientPublicIP": "127.0.0.1",
            "X-MACAddress": "fe80::1",
            "X-PrivateKey": self.api_key,
            "Authorization": f"Bearer {self.jwt_token}"
        }

    def fetch_live_orders(self) -> pd.DataFrame:
        if not self.jwt_token or not self.api_key:
            return pd.DataFrame([
                {"order_id": "DISCONNECTED", "symbol": "API Credentials Required", "type": "-", "qty": 0, "price": 0.0, "status": "PENDING_AUTH", "time": "-"}
            ])

        try:
            url = f"{self.base_url}/rest/secure/angelbroking/order/v1/getOrderBook"
            response = requests.get(url, headers=self._get_headers(), timeout=5)
            res_json = response.json()

            if res_json.get("status") and res_json.get("data"):
                orders = res_json["data"]
                return pd.DataFrame([
                    {
                        "order_id": o.get("orderid"),
                        "symbol": o.get("tradingsymbol"),
                        "type": o.get("transactiontype"),
                        "qty": o.get("quantity"),
                        "price": o.get("price"),
                        "status": o.get("status"),
                        "time": o.get("updatetime")
                    } for o in orders
                ])
        except Exception as e:
            st.caption(f"Broker Feed Note: {e}")

        return pd.DataFrame([])

    def fetch_live_positions(self) -> pd.DataFrame:
        if not self.jwt_token or not self.api_key:
            return pd.DataFrame([
                {"symbol": "API Credentials Required", "product": "-", "qty": 0, "avg_price": 0.0, "ltp": 0.0, "pnl": 0.0}
            ])

        try:
            url = f"{self.base_url}/rest/secure/angelbroking/order/v1/getPosition"
            response = requests.get(url, headers=self._get_headers(), timeout=5)
            res_json = response.json()

            if res_json.get("status") and res_json.get("data"):
                positions = res_json["data"]
                return pd.DataFrame([
                    {
                        "symbol": p.get("tradingsymbol"),
                        "product": p.get("producttype"),
                        "qty": int(p.get("netqty", 0)),
                        "avg_price": float(p.get("avgprice", 0.0)),
                        "ltp": float(p.get("ltp", 0.0)),
                        "pnl": float(p.get("pnl", 0.0))
                    } for p in positions
                ])
        except Exception as e:
            st.caption(f"Position Feed Note: {e}")

        return pd.DataFrame([])

# ==========================================
# 4. LIVE OPTION CHAIN ENGINE WITH SAFE PARSING
# ==========================================
def calculate_option_greeks(S, K, T, r, sigma, option_type="CE"):
    T = max(T, 1e-5)
    sigma = max(sigma, 1e-4)
    if S <= 0 or K <= 0:
        return {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}

    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    gamma = norm.pdf(d1) / (S * sigma * math.sqrt(T))
    vega = (S * norm.pdf(d1) * math.sqrt(T)) / 100.0

    if option_type == "CE":
        delta = norm.cdf(d1)
        theta = (- (S * norm.pdf(d1) * sigma) / (2 * math.sqrt(T)) - r * K * math.exp(-r * T) * norm.cdf(d2)) / 365.0
    else:
        delta = norm.cdf(d1) - 1.0
        theta = (- (S * norm.pdf(d1) * sigma) / (2 * math.sqrt(T)) + r * K * math.exp(-r * T) * norm.cdf(-d2)) / 365.0

    return {
        "delta": round(delta, 3),
        "gamma": round(gamma, 4),
        "vega": round(vega, 3),
        "theta": round(theta, 2)
    }

def fetch_live_nse_option_chain(symbol="NIFTY") -> tuple:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br"
    }
    
    url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}" if symbol in ["NIFTY", "BANKNIFTY"] else f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"
    
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=3)
        response = session.get(url, headers=headers, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            underlying_price = data['records']['underlyingValue']
            records = data['records']['data']
            
            rows = []
            total_ce_oi, total_pe_oi = 0, 0
            T, r = 7 / 365.0, 0.07

            atm_strike = round(underlying_price / 50) * 50
            
            for item in records:
                strike = item.get('strikePrice')
                if abs(strike - atm_strike) <= 250:
                    ce = item.get('CE', {})
                    pe = item.get('PE', {})
                    
                    ce_oi = ce.get('openInterest', 0)
                    pe_oi = pe.get('openInterest', 0)
                    total_ce_oi += ce_oi
                    total_pe_oi += pe_oi
                    
                    ce_iv = ce.get('impliedVolatility', 15.0) / 100.0
                    pe_iv = pe.get('impliedVolatility', 15.0) / 100.0
                    
                    ce_greeks = calculate_option_greeks(underlying_price, strike, T, r, ce_iv if ce_iv > 0 else 0.15, "CE")
                    pe_greeks = calculate_option_greeks(underlying_price, strike, T, r, pe_iv if pe_iv > 0 else 0.15, "PE")
                    
                    rows.append({
                        "CE_Delta": ce_greeks["delta"],
                        "CE_IV(%)": round(ce_iv * 100, 1),
                        "CE_OI": ce_oi,
                        "CE_LTP": ce.get('lastPrice', 0.0),
                        "STRIKE": strike,
                        "PE_LTP": pe.get('lastPrice', 0.0),
                        "PE_OI": pe_oi,
                        "PE_IV(%)": round(pe_iv * 100, 1),
                        "PE_Delta": pe_greeks["delta"]
                    })

            pcr = round(total_pe_oi / (total_ce_oi + 1e-9), 2)
            return pd.DataFrame(rows), pcr, underlying_price
    except Exception as e:
        st.caption(f"Note: NSE API unreachable ({e}). Active structural fallback engaged.")

    # Structural Dynamic Fallback Engine
    spot = 22500.0 if symbol == "NIFTY" else (48000.0 if symbol == "BANKNIFTY" else 2800.0)
    atm_strike = round(spot / 50) * 50
    strikes = [atm_strike + (i * 50) for i in range(-5, 6)]
    rows, total_ce, total_pe = [], 0, 0
    for s in strikes:
        ce_oi, pe_oi = np.random.randint(10000, 100000), np.random.randint(10000, 100000)
        total_ce += ce_oi
        total_pe += pe_oi
        rows.append({
            "CE_Delta": 0.5, "CE_IV(%)": 15.2, "CE_OI": ce_oi, "CE_LTP": 120.0,
            "STRIKE": s, "PE_LTP": 110.0, "PE_OI": pe_oi, "PE_IV(%)": 14.8, "PE_Delta": -0.5
        })
    return pd.DataFrame(rows), round(total_pe/(total_ce+1e-9), 2), spot

# ==========================================
# 5. MARKET DATA ENGINE (CIRCUIT BREAKER & CACHE)
# ==========================================
def fetch_live_market_data(symbols: list, period="1mo", interval="15m") -> dict:
    """
    Market Data Feed Engine with Circuit Breaker Logic:
    - Live Download Stream (YFinance)
    - Session Cache Fallback if network drops/throttles
    """
    data_dict = {}
    cached_store = st.session_state.get("market_data_cache", {})

    for sym in symbols:
        ticker_symbol = f"{sym}.NS" if not sym.endswith(".NS") and sym not in ["NIFTY", "BANKNIFTY"] else sym
        if ticker_symbol == "NIFTY": ticker_symbol = "^NSEI"
        if ticker_symbol == "BANKNIFTY": ticker_symbol = "^NSEBANK"
        
        try:
            df = yf.download(ticker_symbol, period=period, interval=interval, progress=False)
            if not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.reset_index()
                df.rename(columns={
                    "Datetime": "timestamp", "Date": "timestamp",
                    "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"
                }, inplace=True)
                
                data_dict[sym] = df
                st.session_state["market_data_cache"][sym] = df
                continue
        except Exception as e:
            st.caption(f"Network Stream Note for {sym}: {e}")

        # Circuit Breaker: Fallback to Cached Market Data
        if sym in cached_store:
            st.info(f"Circuit Breaker Active: Using cached feed for {sym}")
            data_dict[sym] = cached_store[sym]

    return data_dict

def ema_crossover_strategy(symbol: str, df: pd.DataFrame) -> dict:
    if df is None or len(df) < 21:
        return {"signal": "HOLD", "reason": "Insufficient Data"}

    df_calc = df.copy()
    df_calc['EMA9'] = df_calc['close'].ewm(span=9, adjust=False).mean()
    df_calc['EMA21'] = df_calc['close'].ewm(span=21, adjust=False).mean()

    curr_ema9, curr_ema21 = df_calc['EMA9'].iloc[-1], df_calc['EMA21'].iloc[-1]
    prev_ema9, prev_ema21 = df_calc['EMA9'].iloc[-2], df_calc['EMA21'].iloc[-2]

    if prev_ema9 <= prev_ema21 and curr_ema9 > curr_ema21:
        return {"signal": "BUY", "reason": "EMA Bullish Crossover", "price": df_calc['close'].iloc[-1]}
    elif prev_ema9 >= prev_ema21 and curr_ema9 < curr_ema21:
        return {"signal": "SELL", "reason": "EMA Bearish Crossover", "price": df_calc['close'].iloc[-1]}

    return {"signal": "HOLD", "reason": "No Crossover"}


def get_dashboard_status_snapshot() -> dict:
    """Builds a lightweight workstation status snapshot from existing APIs."""
    snapshot = {
        "indices": {},
        "broker_status": "UNKNOWN",
        "websocket_status": "DISCONNECTED",
        "database_status": "OFFLINE",
        "api_latency": 0.0,
        "live_orders": 0,
        "open_positions": 0,
        "today_pnl": 0.0,
        "session_info": f"{st.session_state.get('username', 'Guest')} ({st.session_state.get('user_role', 'Viewer')})",
        "refresh_interval": 0,
        "analytics": {},
    }

    try:
        from broker import LIVE_BROKER
        from market_data import market_data
        from database import DB

        broker_health = LIVE_BROKER.get_health_status()
        feed_health = market_data.get_feed_health()
        snapshot["broker_status"] = str(broker_health.get("status", "UNKNOWN")).upper()
        snapshot["websocket_status"] = str(feed_health.get("status", "DISCONNECTED")).upper()
        snapshot["api_latency"] = round(float(feed_health.get("latency_ms", broker_health.get("feed_latency_ms", 0.0)) or 0.0), 2)

        try:
            with DB.session() as conn:
                order_count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
                position_count = conn.execute("SELECT COUNT(*) FROM positions WHERE status = 'OPEN'").fetchone()[0]
                today_pnl = conn.execute("SELECT COALESCE(SUM(pnl), 0.0) FROM trade_history WHERE date(entry_timestamp) = date('now')").fetchone()[0]
            snapshot["database_status"] = "ONLINE"
            snapshot["live_orders"] = int(order_count or 0)
            snapshot["open_positions"] = int(position_count or 0)
            snapshot["today_pnl"] = float(today_pnl or 0.0)
        except Exception:
            snapshot["database_status"] = "OFFLINE"

        try:
            from pnl_manager import pnl
            snapshot["analytics"] = pnl.get_advanced_analytics()
        except Exception:
            snapshot["analytics"] = {}

        try:
            index_frames = fetch_live_market_data(["NIFTY", "BANKNIFTY"])
            for symbol in ["NIFTY", "BANKNIFTY"]:
                frame = index_frames.get(symbol)
                if frame is not None and not frame.empty:
                    snapshot["indices"][symbol] = float(frame.iloc[-1].get("close", frame.iloc[-1].get("Close", 0.0)))
        except Exception:
            snapshot["indices"] = {}
    except Exception:
        pass

    return snapshot

# ==========================================
# 6. SIDEBAR CONTROLS & PERSISTENCE
# ==========================================
st.sidebar.title(f"👤 {st.session_state['username']}")
st.sidebar.caption(f"Role: **{st.session_state['user_role']}**")

if st.sidebar.button("🚪 Logout"):
    st.session_state["authenticated"] = False
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.title("⚙️ Engine Settings")

curr_settings = st.session_state["app_settings"]

execution_mode = st.sidebar.radio(
    "🔥 Execution Mode",
    ["PAPER_TRADE", "LIVE_BROKER"],
    index=0 if curr_settings.get("mode") == "PAPER_TRADE" else 1,
    disabled=not has_permission("Trader")
)

selected_broker = st.sidebar.selectbox("Broker Connector", ["ANGEL_ONE", "ZERODHA"], index=0)
api_key_input = st.sidebar.text_input("Broker API Key", value=curr_settings.get("api_key", ""), type="password")
jwt_token_input = st.sidebar.text_input("Broker JWT Token", value=curr_settings.get("jwt_token", ""), type="password")

auto_refresh_sec = st.sidebar.slider("⏱️ Auto-Refresh (Sec)", min_value=0, max_value=30, value=curr_settings.get("auto_refresh_sec", 5))
initial_cap = st.sidebar.number_input("Portfolio Capital (₹)", value=curr_settings.get("capital", 500000), step=50000)

active_symbols = st.sidebar.multiselect(
    "Portfolio Symbols",
    [s for s in SECTOR_MAP.keys() if s not in ["NIFTY", "BANKNIFTY"]],
    default=curr_settings.get("symbols", ["RELIANCE", "TCS", "INFY"])
)

if st.sidebar.button("💾 Save Settings", disabled=not has_permission("Admin")):
    new_settings = {
        "broker": selected_broker,
        "mode": execution_mode,
        "capital": initial_cap,
        "auto_refresh_sec": auto_refresh_sec,
        "api_key": api_key_input,
        "jwt_token": jwt_token_input,
        "symbols": active_symbols
    }
    save_settings(new_settings)
    st.session_state["app_settings"] = new_settings
    st.sidebar.success("Settings Saved!")

# Header
st.title("⚡ TradeX AI Pro v4.0 — Institutional Station")
st.caption(f"Mode: **{execution_mode}** | Broker: **{selected_broker}** | Refresh: **{auto_refresh_sec}s**")

if auto_refresh_sec > 0:
    st.autorefresh(interval=auto_refresh_sec * 1000, key="institutional_dashboard_refresh")

snapshot = get_dashboard_status_snapshot()
snapshot["refresh_interval"] = auto_refresh_sec

st.markdown("### 🧭 Institutional Workstation")
col_idx1, col_idx2, col_broker, col_ws, col_db = st.columns(5)
col_idx1.metric("NIFTY", f"₹{snapshot['indices'].get('NIFTY', 0):,.2f}", help="Live NIFTY index")
col_idx2.metric("BANKNIFTY", f"₹{snapshot['indices'].get('BANKNIFTY', 0):,.2f}", help="Live BANKNIFTY index")
col_broker.metric("Broker Status", snapshot["broker_status"], help="Current broker connection state")
col_ws.metric("WebSocket Status", snapshot["websocket_status"], help="Live feed connection state")
col_db.metric("Database Status", snapshot["database_status"], help="SQLite persistence status")

col_lat, col_orders, col_positions, col_pnl, col_session = st.columns(5)
col_lat.metric("API Latency", f"{snapshot['api_latency']:.2f} ms", help="Feed latency from live stream")
col_orders.metric("Live Orders", snapshot["live_orders"], help="Count of orders in the local database")
col_positions.metric("Open Positions", snapshot["open_positions"], help="Count of open positions in the local database")
col_pnl.metric("Today's PnL", f"₹{snapshot['today_pnl']:,.2f}", help="PnL from today's trade history")
col_session.metric("Session", snapshot["session_info"], help="Current dashboard session")

st.caption(f"Auto Refresh: every {auto_refresh_sec}s | Session: {snapshot['session_info']}")

analytics = snapshot.get("analytics", {})
if analytics:
    st.markdown("### 📊 Professional Performance Reports")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Equity Curve", f"₹{snapshot['today_pnl'] + (analytics.get('total_net_pnl', 0) or 0):,.2f}", help="Current equity estimate")
    c2.metric("Win Rate", f"{analytics.get('win_rate_%', 0):.2f}%")
    c3.metric("Drawdown", f"{analytics.get('max_drawdown_%', 0):.2f}%")
    c4.metric("Profit Factor", f"{analytics.get('profit_factor', 0):.2f}")
    c5.metric("Expectancy", f"₹{analytics.get('total_net_pnl', 0) / max(analytics.get('total_trades', 1), 1):,.2f}")

    try:
        from pnl_manager import pnl
        monthly_df = pnl.get_pnl_summary_by_period("MONTHLY")
        if not monthly_df.empty:
            st.subheader("📅 Monthly Report")
            st.dataframe(monthly_df, use_container_width=True, hide_index=True)

        equity_points = []
        try:
            with pnl._get_connection() as conn:
                equity_points = pd.read_sql_query("SELECT timestamp, capital FROM equity_curve ORDER BY id ASC", conn)
        except Exception:
            equity_points = pd.DataFrame(columns=["timestamp", "capital"])
        if not equity_points.empty:
            st.subheader("📈 Equity Curve")
            fig = px.line(equity_points, x="timestamp", y="capital", template="plotly_dark", title="Equity Curve")
            st.plotly_chart(fig, use_container_width=True)

        if not monthly_df.empty:
            st.subheader("🔥 Trade Heatmap")
            heatmap_df = monthly_df.rename(columns={"Month": "Month", "Net_PnL": "PnL"})
            heatmap_df["Month"] = heatmap_df["Month"].astype(str)
            fig_heat = px.bar(heatmap_df, x="Month", y="PnL", color="PnL", template="plotly_dark", title="Monthly PnL Heatmap")
            st.plotly_chart(fig_heat, use_container_width=True)
    except Exception:
        st.caption("Performance report preview unavailable in this session.")

# Navigation Tabs
tabs = st.tabs([
    "💼 Live Order & Position Book",
    "⛓️ Option Chain Matrix",
    "🚀 Backtest Engine",
    "🔄 Rolling Walk-Forward",
    "🎲 Monte Carlo Stress-Test",
    "🛡️ Sector Exposure"
])

# ==========================================
# TAB 1: LIVE ORDERS & POSITIONS
# ==========================================
with tabs[0]:
    st.subheader(f"📡 Real Live Broker Feed ({selected_broker})")
    broker_bridge = LiveBrokerBridge(api_key=api_key_input, jwt_token=jwt_token_input)
    
    col_p, col_o = st.columns(2)
    with col_p:
        st.markdown("### 💼 Position Book")
        pos_df = broker_bridge.fetch_live_positions()
        st.dataframe(pos_df, width="stretch")
        total_pnl = pos_df["pnl"].sum() if "pnl" in pos_df.columns else 0.0
        st.metric("Total Open P&L", f"₹{total_pnl:,.2f}", delta=f"₹{total_pnl:,.2f}")

    with col_o:
        st.markdown("### 📜 Order Book")
        ord_df = broker_bridge.fetch_live_orders()
        st.dataframe(ord_df, width="stretch")

# ==========================================
# TAB 2: OPTION CHAIN MATRIX
# ==========================================
with tabs[1]:
    st.subheader("⛓️ Live NSE Option Chain Matrix")
    col_opt1, col_opt2, col_opt3 = st.columns([1, 1, 2])
    underlying_sym = col_opt1.selectbox("Underlying Symbol", ["NIFTY", "BANKNIFTY", "RELIANCE"])
    
    chain_df, pcr_val, spot_val = fetch_live_nse_option_chain(underlying_sym)
    
    col_opt2.metric("Spot Price", f"₹{spot_val:,.2f}")
    pcr_color = "normal" if 0.8 <= pcr_val <= 1.2 else ("inverse" if pcr_val < 0.8 else "normal")
    col_opt3.metric("Put-Call Ratio (PCR)", f"{pcr_val}", delta="Bullish (>1.0)" if pcr_val > 1 else "Bearish (<0.8)", delta_color=pcr_color)

    st.dataframe(
        chain_df.style.background_gradient(subset=['CE_OI'], cmap='Greens')
                      .background_gradient(subset=['PE_OI'], cmap='Reds'),
        width="stretch",
        height=360
    )

# ==========================================
# TAB 3: BACKTESTING ENGINE
# ==========================================
with tabs[2]:
    st.subheader("Multi-Asset Portfolio Backtest")
    col_b1, col_b2 = st.columns([1, 3])
    with col_b1:
        prod_type = st.selectbox("Product Type", ["INTRADAY", "DELIVERY"])
        run_bt_btn = st.button("▶ Run Backtest", type="primary", width="stretch", disabled=not has_permission("Trader"))

    if run_bt_btn:
        with st.spinner("Fetching Live Market Data & Executing Backtest..."):
            m_data = fetch_live_market_data(active_symbols)
            if not m_data:
                st.error("Market Data Feed Unavailable. Please verify connection or symbol configuration.")
            else:
                bt = Backtester(initial_capital=initial_cap, broker_model=selected_broker)
                bt_res = bt.run_portfolio(m_data, ema_crossover_strategy, product_type=prod_type)
                
                st.session_state['bt_engine'] = bt
                st.session_state['bt_results'] = bt_res
                st.session_state['market_data'] = m_data

    if 'bt_results' in st.session_state:
        res = st.session_state['bt_results']
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Final Equity", f"₹{res.get('final_equity', 0):,}", delta=f"₹{res.get('net_pnl', 0):,}")
        m2.metric("CAGR (%)", f"{res.get('cagr_pct', 0)}%")
        m3.metric("Sharpe Ratio", f"{res.get('sharpe_ratio', 0)}")
        m4.metric("Win Rate", f"{res.get('win_rate_pct', 0)}%")
        
        if 'equity_curve' in res and len(res['equity_curve']) > 0:
            fig = px.line(pd.DataFrame(res['equity_curve']), x='timestamp', y='equity', title="Equity Performance Curve", template="plotly_dark")
            st.plotly_chart(fig, width="stretch")

# ==========================================
# TAB 4: ROLLING WALK-FORWARD
# ==========================================
with tabs[3]:
    st.subheader("Rolling Walk-Forward Analytics")
    col_w1, col_w2, col_w3 = st.columns(3)
    train_w = col_w1.number_input("Train Window", value=100)
    test_w = col_w2.number_input("Test Window", value=30)
    run_wfa = col_w3.button("🔄 Run WFA", disabled=not has_permission("Trader"))

    if run_wfa:
        if 'market_data' not in st.session_state:
            st.warning("First run Backtest in Tab 3.")
        else:
            with st.spinner("Running Walk-Forward..."):
                bt = st.session_state.get('bt_engine', Backtester(initial_cap, selected_broker))
                wfa_res = bt.rolling_walk_forward(st.session_state['market_data'], ema_crossover_strategy, train_w, test_w)
                st.session_state['wfa_results'] = wfa_res

    if 'wfa_results' in st.session_state:
        st.dataframe(pd.DataFrame(st.session_state['wfa_results']), width="stretch")

# ==========================================
# TAB 5: MONTE CARLO STRESS TEST
# ==========================================
with tabs[4]:
    st.subheader("Monte Carlo Stress Testing")
    sims = st.slider("Simulations Count", 50, 500, 100)
    run_mc = st.button("🎲 Run Monte Carlo", disabled=not has_permission("Trader"))

    if run_mc:
        if 'bt_engine' not in st.session_state:
            st.warning("First run Backtest in Tab 3.")
        else:
            with st.spinner("Simulating Scenarios..."):
                bt = st.session_state['bt_engine']
                mc_res = bt.run_parameter_monte_carlo(runs=sims)
                st.session_state['mc_results'] = mc_res

    if 'mc_results' in st.session_state:
        mc = st.session_state['mc_results']
        c1, c2, c3 = st.columns(3)
        c1.metric("Simulations", mc.get("simulations_run", 0))
        c2.metric("Median Equity", f"₹{mc.get('median_final_equity', 0):,}")
        c3.metric("Confidence Score", f"{mc.get('confidence_score_pct', 0)}%")

# ==========================================
# TAB 6: SECTOR EXPOSURE
# ==========================================
with tabs[5]:
    st.subheader("Sector Exposure Allocation")
    if 'bt_engine' in st.session_state:
        bt = st.session_state['bt_engine']
        if hasattr(bt, 'sector_exposure_history') and bt.sector_exposure_history:
            st.plotly_chart(px.area(pd.DataFrame(bt.sector_exposure_history), title="Sector Allocation History", template="plotly_dark"), width="stretch")
        else:
            st.info("No Sector Allocation data available yet.")
    else:
        st.info("Execute Backtest to view Sector Exposure limits.")
