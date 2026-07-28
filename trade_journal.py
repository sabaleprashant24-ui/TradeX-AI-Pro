# ==========================================
# trade_journal.py
# TradeX AI Pro v3.1
# ==========================================

import sqlite3
from datetime import datetime


class TradeJournal:

    def __init__(self):

        self.db = "tradex.db"
        self.create_table()

    # ---------------------------------
    # Create Table
    # ---------------------------------

    def create_table(self):

        conn = sqlite3.connect(self.db)
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS trades(

            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            side TEXT,
            qty INTEGER,
            entry REAL,
            sl REAL,
            target REAL,
            exit REAL,
            pnl REAL,
            status TEXT,
            trade_time TEXT

        )
        """)

        conn.commit()
        conn.close()

    # ---------------------------------
    # Save Trade
    # ---------------------------------

    def save(
        self,
        symbol,
        side,
        qty,
        entry,
        sl,
        target,
        status
    ):

        conn = sqlite3.connect(self.db)
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO trades
            (
                symbol,
                side,
                qty,
                entry,
                sl,
                target,
                exit,
                pnl,
                status,
                trade_time
            )
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                symbol,
                side,
                qty,
                entry,
                sl,
                target,
                0,
                0,
                status,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
        )

        conn.commit()
        conn.close()

    # ---------------------------------
    # Close Last Trade
    # ---------------------------------

    def close(self, exit_price, pnl):

        conn = sqlite3.connect(self.db)
        cur = conn.cursor()

        cur.execute(
            """
            UPDATE trades
            SET
                exit=?,
                pnl=?,
                status='CLOSED'
            WHERE id=(
                SELECT MAX(id)
                FROM trades
            )
            """,
            (
                exit_price,
                pnl
            )
        )

        conn.commit()
        conn.close()

    # ---------------------------------
    # Statistics
    # ---------------------------------

    def stats(self):

        conn = sqlite3.connect(self.db)
        cur = conn.cursor()

        cur.execute("""
        SELECT
            COUNT(*),
            COALESCE(SUM(pnl),0),
            COALESCE(AVG(pnl),0)
        FROM trades
        WHERE status='CLOSED'
        """)

        total, total_pnl, avg_pnl = cur.fetchone()

        conn.close()

        return {
            "total_trades": total,
            "total_pnl": round(total_pnl, 2),
            "average_pnl": round(avg_pnl, 2)
        }


journal = TradeJournal()