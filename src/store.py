from __future__ import annotations
import json, os, sqlite3
from datetime import datetime, timezone
from typing import Any
from .models import Position

class Store:
    def __init__(self, path: str) -> None:
        directory = os.path.dirname(path)
        if directory: os.makedirs(directory, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS snapshots(ts TEXT NOT NULL,symbol TEXT NOT NULL,bid REAL NOT NULL,ask REAL NOT NULL,mid REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_snapshots ON snapshots(symbol,ts);
        CREATE TABLE IF NOT EXISTS decisions(ts TEXT NOT NULL,symbol TEXT NOT NULL,action TEXT NOT NULL,confidence REAL NOT NULL,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS trades(ts TEXT NOT NULL,symbol TEXT NOT NULL,side TEXT NOT NULL,quote_amount REAL,quantity REAL,price REAL,order_id TEXT,state TEXT NOT NULL,mode TEXT NOT NULL,realized_pnl REAL,payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_trades ON trades(ts);
        CREATE TABLE IF NOT EXISTS positions(symbol TEXT PRIMARY KEY,quantity REAL NOT NULL,entry_price REAL NOT NULL,entry_time TEXT NOT NULL,high_water_price REAL NOT NULL);
        """)
        cols={r["name"] for r in self.conn.execute("PRAGMA table_info(trades)").fetchall()}
        if "realized_pnl" not in cols: self.conn.execute("ALTER TABLE trades ADD COLUMN realized_pnl REAL")
        self.conn.commit()

    def add_snapshot(self,symbol,bid,ask,mid,ts): self.conn.execute("INSERT INTO snapshots VALUES(?,?,?,?,?)",(ts.isoformat(),symbol,bid,ask,mid)); self.conn.commit()
    def prices(self,symbol,since): return [float(r["mid"]) for r in self.conn.execute("SELECT mid FROM snapshots WHERE symbol=? AND ts>=? ORDER BY ts",(symbol,since.isoformat())).fetchall()]
    def last_trade_time(self,symbol):
        r=self.conn.execute("SELECT ts FROM trades WHERE symbol=? ORDER BY ts DESC LIMIT 1",(symbol,)).fetchone()
        return datetime.fromisoformat(r["ts"]) if r else None
    def daily_spend(self,now):
        start=now.astimezone(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0).isoformat()
        r=self.conn.execute("SELECT COALESCE(SUM(quote_amount),0) x FROM trades WHERE side='buy' AND state='filled' AND ts>=?",(start,)).fetchone()
        return float(r["x"] or 0)
    def daily_realized_pnl(self,now):
        start=now.astimezone(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0).isoformat()
        r=self.conn.execute("SELECT COALESCE(SUM(realized_pnl),0) x FROM trades WHERE side='sell' AND state='filled' AND ts>=?",(start,)).fetchone()
        return float(r["x"] or 0)
    def record_decision(self,symbol,action,confidence,payload,ts):
        self.conn.execute("INSERT INTO decisions VALUES(?,?,?,?,?)",(ts.isoformat(),symbol,action,confidence,json.dumps(payload,separators=(",",":")))); self.conn.commit()
    def record_trade(self,*,ts,symbol,side,quote_amount,quantity,price,order_id,state,mode,realized_pnl=None,payload):
        self.conn.execute("INSERT INTO trades VALUES(?,?,?,?,?,?,?,?,?,?,?)",(ts.isoformat(),symbol,side,quote_amount,quantity,price,order_id,state,mode,realized_pnl,json.dumps(payload,separators=(",",":")))); self.conn.commit()
    def set_position(self,p: Position):
        self.conn.execute("INSERT INTO positions VALUES(?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET quantity=excluded.quantity,entry_price=excluded.entry_price,entry_time=excluded.entry_time,high_water_price=excluded.high_water_price",(p.symbol,p.quantity,p.entry_price,p.entry_time.isoformat(),p.high_water_price)); self.conn.commit()
    def delete_position(self,symbol): self.conn.execute("DELETE FROM positions WHERE symbol=?",(symbol,)); self.conn.commit()
    def positions(self):
        return [Position(symbol=r["symbol"],quantity=float(r["quantity"]),entry_price=float(r["entry_price"]),entry_time=datetime.fromisoformat(r["entry_time"]),high_water_price=float(r["high_water_price"])) for r in self.conn.execute("SELECT * FROM positions ORDER BY entry_time").fetchall()]
    def prune_snapshots(self,older_than): self.conn.execute("DELETE FROM snapshots WHERE ts<?",(older_than.isoformat(),)); self.conn.commit()
