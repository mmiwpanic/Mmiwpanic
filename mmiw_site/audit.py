from __future__ import annotations
from .db import connect, now_ts
def log(actor:str, action:str, details:str="")->None:
    conn=connect(); cur=conn.cursor()
    cur.execute("INSERT INTO audit_log (ts,actor,action,details) VALUES (?,?,?,?)",(now_ts(),actor,action,details))
    conn.commit(); conn.close()
