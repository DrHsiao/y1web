"""
progress.py — 會員的練習紀錄（member_progress）：整理與合併。

每日聽練的紀錄格式由 practice/practice.js 決定，這裡的 clean()／merge() 要和那邊的同名函式一樣：
  clean：認不得的欄位丟掉、數字夾在範圍內；伺服器收到的資料一律先過這一關。
  merge：每天取較多的、等級取較高的；不管合併幾次、誰先誰後，結果都一樣。
  一天的紀錄 {n, xp, t?, s?}：t（練了幾秒）、s（各項的題數 q／答對 r／練完的等級 lv）是 2026-11-02 加的，舊紀錄沒有。
兩邊要一起改。用在：瀏覽器和伺服器同步、兩個會員合併（Google 和 LINE 原本是兩個會員）。

伺服器只做「合併後存起來」，沒有「覆蓋」：哪一台裝置送來什麼，都只會讓紀錄變多，不會把別台練的蓋掉。
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, Optional

import pymysql

from app import db

APPS = {"practice"}
MAX_BYTES = 800 * 1024          # 一份紀錄的上限：4000 天每天都有細項約 600KB（.26 nginx 預設上傳上限 1MB）

MAX_LV = 10
SKILLS = ("digit", "pitch", "count")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _int(v: Any, lo: int, hi: int) -> int:
    """同 JS 的 int()：Math.round 後夾在範圍內；不是數字＝lo。"""
    if isinstance(v, bool):
        v = int(v)
    if isinstance(v, str):
        try:
            v = float(v.strip() or 0)
        except ValueError:
            return lo
    if v is None:
        v = 0
    if not isinstance(v, (int, float)) or not math.isfinite(v):
        return lo
    return max(lo, min(hi, math.floor(v + 0.5)))


def fresh() -> Dict[str, Any]:
    return {"v": 1, "days": {}, "lv": {k: 1 for k in SKILLS}, "best": 0, "vol": ""}


def _clean_day(x: Dict[str, Any]) -> Dict[str, Any]:
    d: Dict[str, Any] = {"n": _int(x.get("n"), 1, 99), "xp": _int(x.get("xp"), 0, 99999)}
    t = _int(x.get("t"), 0, 86400)
    if t > 0:
        d["t"] = t
    sk = x.get("s") if isinstance(x.get("s"), dict) else {}
    s = {}
    for k in SKILLS:
        o = sk.get(k)
        if not isinstance(o, dict):
            continue
        q = _int(o.get("q"), 0, 9999)
        if q > 0:
            s[k] = {"q": q, "r": min(q, _int(o.get("r"), 0, 9999)), "lv": _int(o.get("lv"), 1, MAX_LV)}
    if s:
        d["s"] = s
    return d


def _merge_day(x: Dict[str, Any], y: Dict[str, Any]) -> Dict[str, Any]:
    d = {"n": max(x["n"], y["n"]), "xp": max(x["xp"], y["xp"]), "t": max(x.get("t", 0), y.get("t", 0)), "s": {}}
    xs, ys = x.get("s", {}), y.get("s", {})
    for k in SKILLS:
        a, b = xs.get(k), ys.get(k)
        if a and b:
            d["s"][k] = {"q": max(a["q"], b["q"]), "r": max(a["r"], b["r"]), "lv": max(a["lv"], b["lv"])}
        elif a or b:
            d["s"][k] = a or b
    return _clean_day(d)


def clean(o: Any) -> Dict[str, Any]:
    s = fresh()
    if not isinstance(o, dict):
        return s
    days = o.get("days") if isinstance(o.get("days"), dict) else {}
    for k in sorted(k for k in days if isinstance(k, str) and _DATE_RE.match(k))[-4000:]:
        x = days[k]
        if isinstance(x, dict):
            s["days"][k] = _clean_day(x)
    lv = o.get("lv") if isinstance(o.get("lv"), dict) else {}
    for k in SKILLS:
        if lv.get(k) is not None:
            s["lv"][k] = _int(lv[k], 1, MAX_LV)
    s["best"] = _int(o.get("best"), 0, 99999)
    if isinstance(o.get("vol"), str) and _DATE_RE.match(o["vol"]):
        s["vol"] = o["vol"]
    return s


def merge(a: Any, b: Any) -> Dict[str, Any]:
    a, b = clean(a), clean(b)
    out = clean(a)
    for k, y in b["days"].items():
        x = out["days"].get(k)
        out["days"][k] = _merge_day(x, y) if x else y
    for k in SKILLS:
        out["lv"][k] = max(a["lv"][k], b["lv"][k])
    out["best"] = max(a["best"], b["best"])
    out["vol"] = a["vol"] if a["vol"] > b["vol"] else b["vol"]
    return clean(out)


def load(member_id: int, app: str) -> Optional[Dict[str, Any]]:
    r = db.rows("SELECT data FROM y1member.member_progress WHERE member_id=%s AND app=%s", (member_id, app))
    return clean(json.loads(r[0]["data"])) if r else None


def save_merged(member_id: int, app: str, incoming: Any) -> Dict[str, Any]:
    """把送來的紀錄併進伺服器上的，存起來並回傳合併後的結果。兩台裝置同時送：鎖住那一列，第一次寫入撞到就重試一次。"""
    for attempt in range(2):
        with db.connect() as con, con.cursor() as cur:
            con.begin()
            try:
                cur.execute("SELECT data FROM y1member.member_progress WHERE member_id=%s AND app=%s FOR UPDATE",
                            (member_id, app))
                r = cur.fetchone()
                merged = merge(json.loads(r["data"]) if r else None, incoming)
                body = json.dumps(merged, separators=(",", ":"))
                if r:
                    cur.execute("UPDATE y1member.member_progress SET data=%s WHERE member_id=%s AND app=%s",
                                (body, member_id, app))
                else:
                    cur.execute("INSERT INTO y1member.member_progress (member_id, app, data) VALUES (%s, %s, %s)",
                                (member_id, app, body))
                con.commit()
                return merged
            except (pymysql.err.IntegrityError, pymysql.err.OperationalError):
                con.rollback()
                if attempt:
                    raise
    raise RuntimeError("unreachable")
