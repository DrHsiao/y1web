"""
clicks.py — 官網「打電話／LINE／預約」按鈕的點擊數（改善清單 D2，2026-11-25 John：「修 D2」）。

為什麼要自己記：Cloudflare Web Analytics 看得到瀏覽量、哪一頁、從哪裡來，但看不到按了哪個按鈕——
打電話是 tel: 連結、LINE 是轉址（/line → line.me），都不是網頁，統計程式不會跑。

做法：site.js 在按下這些按鈕時用 navigator.sendBeacon 送 POST /api/click（k＝種類、p＝在哪一頁按的），
不擋住原本的動作。這裡只記「哪天、哪種、哪一頁、幾次」（y1web.web_clicks），
不記 IP、不記是誰、不用 Cookie。y1crm「官網管理」讀出來看。

擋灌票：同一個 IP 一小時最多記 PER_IP_HOUR 次（記憶體計數；服務重啟歸零沒關係）。
資料庫帳號 y1web_app 對 web_clicks 只能新增、把次數加一（scripts/setup_db.py 的 CLICK_GRANT）。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Optional, Tuple

from app import db

log = logging.getLogger("y1web.clicks")

KINDS = {"tel": "打電話", "line": "LINE", "booking": "預約"}
PAGE_RE = re.compile(r"/[A-Za-z0-9/_.-]{0,79}")
PER_IP_HOUR = 30
_MAX_IPS = 5000                     # 記憶體上限：超過就先清掉一小時前的

_seen: Dict[str, List[float]] = {}


def clean(kind: str, page: str) -> Optional[Tuple[str, str]]:
    """種類只收 KINDS；頁面只收站內路徑（/ 開頭、80 字內、英數與 /_.-），其他一律不記。"""
    kind = (kind or "").strip()
    page = (page or "").strip()
    if kind not in KINDS or not PAGE_RE.fullmatch(page) or page.startswith("//"):
        return None
    return kind, page


def allow(ip: str, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    if len(_seen) > _MAX_IPS:
        for k in [k for k, v in _seen.items() if not v or v[-1] < now - 3600]:
            del _seen[k]
    hits = [t for t in _seen.get(ip, []) if t > now - 3600]
    if len(hits) >= PER_IP_HOUR:
        _seen[ip] = hits
        return False
    hits.append(now)
    _seen[ip] = hits
    return True


def record(kind: str, page: str) -> bool:
    """今天這一頁這種按鈕加一。資料庫連不上就算了（不影響客人），回 False。"""
    try:
        with db.connect() as con, con.cursor() as cur:
            cur.execute("INSERT INTO y1web.web_clicks (day, kind, page, n) VALUES (CURDATE(), %s, %s, 1) "
                        "ON DUPLICATE KEY UPDATE n = n + 1", (kind, page))
        return True
    except Exception as e:          # noqa: BLE001 — 計數失敗不能讓頁面出錯
        log.warning("點擊數沒記到：%s", e)
        return False
