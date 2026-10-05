import sqlite3, os
from pathlib import Path
DB=Path(os.getenv("WH_DB","data/market.db"))
DB.parent.mkdir(parents=True,exist_ok=True)
def conn():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c
def init():
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS snapshots(
        coin_id TEXT NOT NULL,symbol TEXT NOT NULL,name TEXT NOT NULL,ts INTEGER NOT NULL,
        price REAL,market_cap REAL,volume REAL,h1 REAL,h24 REAL,h7 REAL,
        PRIMARY KEY(coin_id,ts))""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_snap_coin_ts ON snapshots(coin_id,ts)")
def insert_many(rows):
    with conn() as c:
        c.executemany("""INSERT OR REPLACE INTO snapshots
        (coin_id,symbol,name,ts,price,market_cap,volume,h1,h24,h7)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",rows); c.commit()
def history(coin_id,limit=100):
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM snapshots WHERE coin_id=? ORDER BY ts DESC LIMIT ?",(coin_id,limit)).fetchall()]
