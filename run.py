#!/usr/bin/env python3
"""
run.py — y1web 入口（韻譯助聽器 睿聲中正門市官網）。

連接埠 8006（8004＝y1crm、8005＝TWIII 會員站）。
正式執行由 systemd 的 y1web.service 以專用帳號 y1web 啟動（見 deploy/y1web.service）。
手動啟動：/home/john/y1web/venv/bin/python run.py
"""
from __future__ import annotations

import logging

import uvicorn

from app import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

if __name__ == "__main__":
    host, port = settings.SERVER["host"], int(settings.SERVER["port"])
    logging.getLogger("y1web").info("啟動 y1web → http://%s:%s", host, port)
    uvicorn.run("app.main:app", host=host, port=port, log_level="warning",
                proxy_headers=True, forwarded_allow_ips=settings.SERVER["forwarded_allow_ips"])
