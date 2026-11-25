"""
hearing_rec.py — 會員保存的線上聽力測驗結果（John 2026-11-19：「會員可以留聽檢記錄」）。

- 只有會員自己在結果頁按「存到會員紀錄」（或「登入並保存」）才會上傳；沒按就跟以前一樣只在瀏覽器裡。
- 只有會員本人在會員頁看得到、可以刪；門市後台看不到（要開放給門市看，另外問 John＋改同意文字）。
- 瀏覽器送來的資料不可信：只收固定的頻率、數值夾在範圍內，平均值（PTA）伺服器自己算。
- 存一筆會由 points.award_hearing_online() 記點（30 天一次）；刪掉紀錄點數不收回。
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

from app import db

log = logging.getLogger("y1web.hearing_rec")

FREQS = (250, 500, 1000, 2000, 4000, 6000, 8000)       # 和 static/js/hearing-test.js 的 FREQS 一樣
PTA_FREQS = (500, 1000, 2000, 4000)
EARS = ("right", "left")
MIN_HL, MAX_HL = -10, 130
MAX_RECORDS = 100              # 每位會員最多存幾筆
MAX_PER_DAY = 10               # 一天最多存幾筆（擋程式亂送）


class RecError(ValueError):
    """資料不對或超過上限；訊息直接給客人看。"""


def _hl(v: Any) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise RecError("測驗結果的格式不對，請重新測一次。")
    return max(MIN_HL, min(MAX_HL, int(round(v))))


def clean(raw: Any) -> Dict[str, Any]:
    """{right:{"250":{hl,nr},…}, left:{…}, fh} → 只留認得的欄位；少一個頻率就整筆不收。"""
    if not isinstance(raw, dict):
        raise RecError("測驗結果的格式不對，請重新測一次。")
    out: Dict[str, Any] = {}
    for ear in EARS:
        src = raw.get(ear)
        if not isinstance(src, dict):
            raise RecError("要兩耳都測完才能保存。")
        e = {}
        for f in FREQS:
            r = src.get(str(f))
            if not isinstance(r, dict):
                raise RecError("要兩耳都測完才能保存。")
            e[str(f)] = {"hl": _hl(r.get("hl")), "nr": r.get("nr") is True}
        out[ear] = e
    fh = raw.get("fh")
    out["fh"] = max(0, min(99, int(fh))) if isinstance(fh, int) and not isinstance(fh, bool) else 0
    return out


def pta(ear: Dict[str, Any]) -> Dict[str, Any]:
    """和 hearing-test.js 的 pta() 一樣：沒聽到的頻率加 5 dB 算；回 {v, nr}。"""
    s, nr = 0, False
    for f in PTA_FREQS:
        r = ear[str(f)]
        s += r["hl"] + 5 if r["nr"] else r["hl"]
        nr = nr or r["nr"]
    return {"v": int(math.floor(s / 4 + 0.5)), "nr": nr}


def grade(v: int) -> str:
    """和 hearing-test.js 的 grade() 同一套分級文字。"""
    if v <= 25:
        return "正常範圍"
    if v <= 40:
        return "輕度範圍"
    if v <= 55:
        return "中度範圍"
    if v <= 70:
        return "中重度範圍"
    if v <= 90:
        return "重度範圍"
    return "極重度範圍"


def save(member_id: int, raw: Any) -> int:
    data = clean(raw)
    pr, pl = pta(data["right"]), pta(data["left"])
    now = datetime.now().replace(microsecond=0)
    with db.connect() as con, con.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n, SUM(tested_at >= %s) AS today FROM y1member.member_hearing "
                    "WHERE member_id=%s", (now.replace(hour=0, minute=0, second=0), member_id))
        r = cur.fetchone()
        if int(r["n"] or 0) >= MAX_RECORDS:
            raise RecError(f"會員紀錄最多存 {MAX_RECORDS} 筆，請先到會員頁刪掉舊的。")
        if int(r["today"] or 0) >= MAX_PER_DAY:
            raise RecError("今天已經存很多筆了，請明天再存。")
        cur.execute("INSERT INTO y1member.member_hearing (member_id, tested_at, data, pta_r, pta_l) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (member_id, now, json.dumps(data, separators=(",", ":")), pr["v"], pl["v"]))
        rid = cur.lastrowid
    log.info("會員 #%s 保存線上聽力測驗結果", member_id)
    return rid


def records(member_id: int) -> List[Dict[str, Any]]:
    """會員頁用：新到舊，附兩耳平均與分級。表還沒建（1142／1146）回空。"""
    try:
        rows = db.rows("SELECT id, tested_at, data FROM y1member.member_hearing WHERE member_id=%s "
                       "ORDER BY tested_at DESC, id DESC", (member_id,))
    except Exception as e:  # noqa: BLE001
        if getattr(e, "args", None) and e.args[0] in (1142, 1146):
            return []
        raise
    out = []
    for r in rows:
        d = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
        ears = {}
        for ear in EARS:
            p = pta(d[ear])
            ears[ear] = {**p, "grade": grade(p["v"])}
        out.append({"id": r["id"], "tested_at": r["tested_at"], "right": ears["right"], "left": ears["left"],
                    "fh": d.get("fh", 0)})
    return out


def delete(member_id: int, rid: Any) -> str:
    try:
        rid = int(rid)
    except (TypeError, ValueError):
        raise RecError("找不到這筆紀錄。") from None
    with db.connect() as con, con.cursor() as cur:
        cur.execute("DELETE FROM y1member.member_hearing WHERE id=%s AND member_id=%s", (rid, member_id))
        if cur.rowcount != 1:
            raise RecError("找不到這筆紀錄。")
    log.info("會員 #%s 刪除一筆線上聽力測驗紀錄", member_id)
    return "hearing_deleted"


def merge(cur, keep: int, drop: int) -> None:
    """合併會員時搬過去（在呼叫端的交易裡）。"""
    cur.execute("UPDATE y1member.member_hearing SET member_id=%s WHERE member_id=%s", (keep, drop))
