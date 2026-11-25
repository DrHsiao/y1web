"""
points.py — 會員集點：會員頁的「集點紀錄」與官網這邊的記點。

點數來源（John 2026-11-19）：
| 來源 | 規則 | 誰記 |
| 粉專留言 | 同一人同一則貼文 1 點 | y1crm api/fb_points.py（crm1.fb_comment_points，官網經 v_member_fb_points 讀雜湊） |
| 每日聽練 | 一天練完至少 1 回 1 點；只記「今天、昨天」同步上來的（舊日期不補，防灌點） | 這裡 award_practice() |
| 聽練連續 7 天 | 帳本裡的聽練日子連續滿 7、14、21…天各 +10；斷掉就從頭算（John 11-19「斷掉就從頭算，細部規則以後再加」） | 同上 |
| 線上聽力測驗 | 會員保存結果，每個月（月曆月）第一次 10 點（John 11-19「每個月做一次的話，10分」） | 這裡 award_hearing_online() |
| 來店打卡 | 客人 Facebook 粉專打卡給店員看，店員在 y1crm「會員集點」一鍵加點（每月第一次 10 點；11-20 取代門市聽檢，來源沿用 hearing_store） | y1crm api/member_points.adjust() |
| 兌換贈品／點數調整 | 店員在 y1crm 扣點（或其他加減點），要寫原因 | 同上，扣點是負的 |

John 11-19：「…會員登入後，也要能查到每次增加或減少的明細。」→ summary() 每筆都列出來，店員寫的原因照實顯示
（登記的店員名字不給會員看）。
- 帳本都在 y1member.member_points（作廢由 y1crm 經理以上做；作廢的會員看到「不列入」）。
- 官網讀不到留言者的 Facebook 編號、也讀不到聽檢結果。
- Facebook 綁定碼：會員按「取得綁定碼」→ 6 位數（只存 SHA-256，30 分鐘、一次）→ 客人用 Messenger 傳給粉專 →
  y1crm 用掉並寫 member_fb。碼只在產生的那一頁顯示一次。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import pymysql

from app import content, db

log = logging.getLogger("y1web.points")

CODE_MINUTES = 30
RECENT = 100                      # 會員頁列出最近幾筆
PRACTICE_POINTS = 1
STREAK_DAYS = 7                  # 聽練連續幾天給一次獎勵
STREAK_POINTS = 10
HEARING_ONLINE_POINTS = 10       # 每個月第一次（來店打卡由店員登記，預設點數在 y1crm member_points.STORE_POINTS）
SOURCE_LABEL = {"fb": "粉專活動", "practice": "每日聽練", "streak": "聽練連續 7 天", "hearing_online": "線上聽力測驗",
                "hearing_store": "來店打卡", "redeem": "兌換贈品", "adjust": "點數調整"}
FB_NOTE = {"counted": "", "repeat": "同一則貼文已累積過", "removed": "留言已刪除", "void": "不列入"}
_FB_NAME_RE = re.compile(r"^[A-Za-z0-9.]{5,50}$")
_NOT_READY = (1142, 1146)         # 沒權限、表不存在：setup_db.py 還沒跑


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def messenger_url(fb_url: Optional[str]) -> Optional[str]:
    """粉專網址 → Messenger 連結（m.me）。profile.php?id=… 用編號，facebook.com/名稱 用名稱；認不出來回 None。"""
    try:
        u = urlparse(fb_url or "")
    except ValueError:
        return None
    if u.scheme != "https" or not (u.netloc == "facebook.com" or u.netloc.endswith(".facebook.com")):
        return None
    path = u.path.strip("/")
    if path == "profile.php":
        pid = (parse_qs(u.query).get("id") or [""])[0]
        return f"https://m.me/{pid}" if pid.isdigit() else None
    name = path.split("/")[0] if path else ""
    return f"https://m.me/{name}" if _FB_NAME_RE.match(name) else None


def page_messenger() -> Optional[str]:
    for x in content.social_links():
        if x["key"] == "facebook":
            return messenger_url(x["url"])
    return None


# ── 記點 ───────────────────────────────────────────────────

def practice_days_to_award(data: Dict[str, Any], today: date) -> List[str]:
    """這次同步後可以記點的聽練日期：今天、昨天，而且那天至少練完 1 回。"""
    days = data.get("days") if isinstance(data.get("days"), dict) else {}
    out = []
    for d in (today, today - timedelta(days=1)):
        k = d.isoformat()
        day = days.get(k)
        if isinstance(day, dict) and int(day.get("n") or 0) >= 1:
            out.append(k)
    return out


def _insert(cur, member_id: int, source: str, ref: str, pts: int, note: Optional[str] = None) -> bool:
    cur.execute("INSERT IGNORE INTO y1member.member_points (member_id, source, ref, points, note) VALUES (%s,%s,%s,%s,%s)",
                (member_id, source, ref, pts, note))
    return cur.rowcount == 1


def streak_len(days: set, end: date) -> int:
    """從 end 往回數，連續幾天都在 days（YYYY-MM-DD）裡。"""
    n = 0
    while (end - timedelta(days=n)).isoformat() in days:
        n += 1
    return n


def _practice_days(cur, member_id: int) -> set:
    """帳本裡記過點（沒作廢）的聽練日子：連續天數只看這個（伺服器只收今天、昨天的，假的舊日期進不來）。"""
    cur.execute("SELECT ref FROM y1member.member_points WHERE member_id=%s AND source='practice' AND status='counted'",
                (member_id,))
    return {r["ref"] for r in cur.fetchall()}


def streak_status(days: set, today: date) -> Dict[str, Any]:
    """聽練 app 顯示用：today＝今天記過點了沒、streak＝集點的連續天數（今天還沒練就算到昨天）、
    to_bonus＝再練幾天拿連續獎勵（今天剛好滿 7 天＝已經拿到，下一次還要 7 天）。"""
    done = today.isoformat() in days
    s = streak_len(days, today if done else today - timedelta(days=1))
    return {"today": done, "streak": s, "to_bonus": STREAK_DAYS - s % STREAK_DAYS,
            "bonus_days": STREAK_DAYS, "bonus_points": STREAK_POINTS}


def balance(cur, member_id: int) -> int:
    """目前點數（加點減扣點＋粉專留言）；聽練 app 顯示用。"""
    cur.execute("SELECT COALESCE(SUM(points), 0) AS n FROM y1member.member_points WHERE member_id=%s AND status='counted'",
                (member_id,))
    n = int(cur.fetchone()["n"])
    try:
        cur.execute("SELECT COALESCE(SUM(v.points), 0) AS n FROM crm1.v_member_fb_points v "
                    "JOIN y1member.member_fb f ON f.person_hash = v.person_hash WHERE f.member_id=%s", (member_id,))
        n += int(cur.fetchone()["n"])
    except pymysql.err.MySQLError as e:
        if not (e.args and e.args[0] in _NOT_READY):
            raise
    return n


def award_practice(member_id: int, data: Dict[str, Any], today: Optional[date] = None) -> Dict[str, Any]:
    """每日聽練同步完呼叫：今天／昨天有練就記 1 點，連續滿 7 天再 +10。
    回 {gained 這次新記的點數, day, bonus, streak, to_bonus, total 目前點數…}；表還沒建就回 gained=0（聽練照常）。"""
    today = today or date.today()
    keys = practice_days_to_award(data, today)
    out: Dict[str, Any] = {"gained": 0, "day": 0, "bonus": 0}
    try:
        with db.connect() as con, con.cursor() as cur:
            for k in keys:
                out["day"] += PRACTICE_POINTS if _insert(cur, member_id, "practice", k, PRACTICE_POINTS) else 0
            days = _practice_days(cur, member_id)
            # 昨天、今天各看一次：昨天晚補上來的，可能讓昨天或今天剛好滿 7 天（INSERT IGNORE：拿過的不會重複）
            for d in (today - timedelta(days=1), today):
                s = streak_len(days, d) if keys else 0
                if s and s % STREAK_DAYS == 0 and _insert(cur, member_id, "streak", d.isoformat(), STREAK_POINTS,
                                                          f"連續 {s} 天"):
                    out["bonus"] += STREAK_POINTS
            out.update(streak_status(days, today))
            out["total"] = balance(cur, member_id)
    except pymysql.err.MySQLError as e:
        if e.args and e.args[0] in _NOT_READY:
            return out
        raise
    out["gained"] = out["day"] + out["bonus"]
    if out["gained"]:
        log.info("會員 #%s 每日聽練 +%s（連續獎勵 %s）", member_id, out["gained"], out["bonus"])
    return out


def practice_status(member_id: int, today: Optional[date] = None) -> Optional[Dict[str, Any]]:
    """聽練 app 打開時顯示集點狀態；表還沒建回 None。"""
    today = today or date.today()
    try:
        with db.connect() as con, con.cursor() as cur:
            out = streak_status(_practice_days(cur, member_id), today)
            out["total"] = balance(cur, member_id)
            return out
    except pymysql.err.MySQLError as e:
        if e.args and e.args[0] in _NOT_READY:
            return None
        raise


def next_month(d: date) -> date:
    return date(d.year + d.month // 12, d.month % 12 + 1, 1)


def award_hearing_online(member_id: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    """保存線上聽力測驗結果後呼叫：這個月（月曆月）還沒記過才記。回 {awarded, points, next（下次可記點的日期）}。
    帳本的 ref＝年月（YYYY-MM），唯一鍵本身就保證一個月一次；被作廢的那個月也不會再記。"""
    now = now or datetime.now()
    try:
        with db.connect() as con, con.cursor() as cur:
            ok = _insert(cur, member_id, "hearing_online", now.strftime("%Y-%m"), HEARING_ONLINE_POINTS)
    except pymysql.err.MySQLError as e:
        if e.args and e.args[0] in _NOT_READY:
            return {"awarded": False, "points": 0, "next": None}
        raise
    if ok:
        log.info("會員 #%s 線上聽力測驗 +%s", member_id, HEARING_ONLINE_POINTS)
    return {"awarded": ok, "points": HEARING_ONLINE_POINTS if ok else 0, "next": next_month(now.date())}


def merge(cur, keep: int, drop: int) -> None:
    """合併會員（在呼叫端的交易裡）：點數搬過去；同一天兩邊都有的（聽練）留一筆。Facebook 綁定也搬。"""
    cur.execute("UPDATE IGNORE y1member.member_points SET member_id=%s WHERE member_id=%s", (keep, drop))
    cur.execute("DELETE FROM y1member.member_points WHERE member_id=%s", (drop,))
    cur.execute("UPDATE y1member.member_fb SET member_id=%s WHERE member_id=%s", (keep, drop))


# ── 會員頁 ─────────────────────────────────────────────────

def _practice_rounds(cur, member_id: int) -> Dict[str, int]:
    cur.execute("SELECT data FROM y1member.member_progress WHERE member_id=%s AND app='practice'", (member_id,))
    r = cur.fetchone()
    if not r:
        return {}
    d = json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
    days = d.get("days") if isinstance(d, dict) and isinstance(d.get("days"), dict) else {}
    return {k: int(v.get("n") or 0) for k, v in days.items() if isinstance(v, dict)}


def summary(member_id: int) -> Dict[str, Any]:
    """會員頁要的：目前點數（加點減扣點）、累積、用掉、各來源點數、每一筆明細、綁了哪些 Facebook。
    集點的表還沒建就回 ready=False。"""
    out: Dict[str, Any] = {"ready": True, "links": [], "total": 0, "earned": 0, "used": 0, "rows": [],
                           "by": {k: 0 for k in SOURCE_LABEL}}
    rows: List[Dict[str, Any]] = []
    try:
        with db.connect() as con, con.cursor() as cur:
            cur.execute("SELECT fb_name, linked_at FROM y1member.member_fb WHERE member_id=%s ORDER BY linked_at",
                        (member_id,))
            out["links"] = list(cur.fetchall())
            if out["links"]:
                cur.execute("SELECT v.created_at, v.post_title, v.excerpt, v.status, v.points "
                            "FROM crm1.v_member_fb_points v JOIN y1member.member_fb f ON f.person_hash = v.person_hash "
                            "WHERE f.member_id=%s", (member_id,))
                for r in cur.fetchall():
                    detail = r["post_title"] or "粉專貼文"
                    rows.append({"at": r["created_at"], "source": "fb", "title": detail,
                                 "sub": f"您的留言：{r['excerpt']}" if r["excerpt"] and r["status"] != "removed" else "",
                                 "points": int(r["points"]), "note": FB_NOTE.get(r["status"], "")})
            cur.execute("SELECT source, ref, points, note, status, created_at FROM y1member.member_points "
                        "WHERE member_id=%s", (member_id,))
            ledger = list(cur.fetchall())
            rounds = _practice_rounds(cur, member_id) if any(r["source"] == "practice" for r in ledger) else {}
            for r in ledger:
                pts = int(r["points"]) if r["status"] == "counted" else 0
                sub = ""
                if r["source"] == "practice" and rounds.get(r["ref"]):
                    sub = f"練完 {rounds[r['ref']]} 回"
                at = datetime.fromisoformat(r["ref"]) if r["source"] in ("practice", "streak") else r["created_at"]
                if r["source"] == "hearing_online":
                    sub = f"{int(r['ref'][5:7])} 月"
                elif r["note"]:
                    sub = r["note"]              # 店員寫的加減點原因；連續獎勵＝「連續 7 天」
                title = SOURCE_LABEL[r["source"]]
                if r["source"] == "adjust":
                    title = "門市加點" if int(r["points"]) > 0 else "門市扣點"
                rows.append({"at": at, "source": r["source"], "title": title, "sub": sub,
                             "points": pts, "note": "" if r["status"] == "counted" else "不列入"})
    except pymysql.err.MySQLError as e:
        if e.args and e.args[0] in _NOT_READY:
            return {"ready": False, "links": [], "total": 0, "rows": [], "by": {}}
        raise
    for r in rows:
        out["by"][r["source"]] += r["points"]
    out["total"] = sum(out["by"].values())
    out["earned"] = sum(r["points"] for r in rows if r["points"] > 0)
    out["used"] = -sum(r["points"] for r in rows if r["points"] < 0)
    rows.sort(key=lambda r: r["at"], reverse=True)
    out["rows"] = rows[:RECENT]
    out["more"] = len(rows) > RECENT
    return out


# ── Facebook 綁定碼 ─────────────────────────────────────────

def new_code(member_id: int) -> str:
    """產生新的綁定碼（同一位會員之前沒用的碼作廢）。回 6 位數字。"""
    now = datetime.now().replace(microsecond=0)
    with db.connect() as con, con.cursor() as cur:
        cur.execute("DELETE FROM y1member.fb_link_codes WHERE expires_at < %s OR (member_id=%s AND used_at IS NULL)",
                    (now, member_id))
        for _ in range(8):
            code = f"{secrets.randbelow(1_000_000):06d}"
            try:
                cur.execute("INSERT INTO y1member.fb_link_codes (member_id, code_hash, expires_at) VALUES (%s,%s,%s)",
                            (member_id, code_hash(code), now + timedelta(minutes=CODE_MINUTES)))
            except pymysql.err.IntegrityError:
                continue                  # 剛好撞到別人還在用的碼
            log.info("會員 #%s 產生 Facebook 綁定碼", member_id)
            return code
    raise RuntimeError("綁定碼產生失敗")


def code_state(member_id: int) -> str:
    """會員頁拿到綁定碼之後一直來問：最新那組碼 used（y1crm 收到 Messenger 綁好了）／waiting／expired／none。
    Messenger 的「綁定好了」回覆要等 Meta 審過 pages_messaging 才送得到一般客人，所以會員頁自己確認。"""
    with db.connect() as con, con.cursor() as cur:
        cur.execute("SELECT used_at, expires_at FROM y1member.fb_link_codes WHERE member_id=%s ORDER BY id DESC LIMIT 1",
                    (member_id,))
        r = cur.fetchone()
    if not r:
        return "none"
    if r["used_at"]:
        return "used"
    return "waiting" if r["expires_at"] > datetime.now() else "expired"


def fb_bound(member_id: int) -> bool:
    """有沒有綁粉專集點用的 Facebook（官網右側「綁定 Facebook」圓鈕看這個）。"""
    with db.connect() as con, con.cursor() as cur:
        cur.execute("SELECT 1 FROM y1member.member_fb WHERE member_id=%s LIMIT 1", (member_id,))
        return cur.fetchone() is not None


def unlink(member_id: int) -> str:
    with db.connect() as con, con.cursor() as cur:
        cur.execute("DELETE FROM y1member.member_fb WHERE member_id=%s", (member_id,))
    log.info("會員 #%s 解除 Facebook 綁定", member_id)
    return "fb_unlinked"
