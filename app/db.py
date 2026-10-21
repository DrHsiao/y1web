"""
db.py — 資料庫連線（帳號 y1web_app：y1web.* 唯讀，crm1.booking_requests 只能新增）。

官網流量小，每次請求開一條本機連線就夠，不用連線池。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

import pymysql
import pymysql.cursors

from app import settings


@contextmanager
def connect() -> Iterator[pymysql.connections.Connection]:
    con = pymysql.connect(**settings.DB, cursorclass=pymysql.cursors.DictCursor,
                          autocommit=True, connect_timeout=5, read_timeout=10, write_timeout=10)
    try:
        yield con
    finally:
        con.close()


def rows(sql: str, args: Optional[tuple] = None) -> List[Dict[str, Any]]:
    with connect() as con, con.cursor() as cur:
        cur.execute(sql, args)
        return list(cur.fetchall())
