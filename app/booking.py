"""
booking.py — 官網預約（門市預約、到府服務）：驗證、防灌水、寫入 crm1.booking_requests、LINE 預約碼。

規則和 y1crm LINE 預約相同（John 2026-10-21 定案「一樣」）：
  日期＝明天起 14 天，週日公休不列（y1crm api/line_bot.py `_booking_dates`；週日排除是 John 2026-10-21 定案）
  時段＝09–18 整點（同檔 `_HOURS`）
  電話＝去掉數字和 - 以外的字元，至少 7 碼
兩邊規則要一起改。

寫入只用 INSERT（資料庫帳號讀不到 booking_requests），所以重複送出、次數限制都在這裡用記憶體判斷。
預約碼：客人在 LINE 傳「預約碼 XXXXXX」，y1crm webhook 就把這筆連上他的 LINE，之後沿用前一天提醒。
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from typing import Any, Deque, Dict, List, Optional, Tuple
from urllib.parse import quote

import pymysql

from app import db, settings

HOURS = list(range(9, 19))
DAYS_AHEAD = 14
CLOSED_WEEKDAYS = {6}        # 週日公休
WD = "一二三四五六日"
KINDS = {"store": "門市預約", "home": "到府服務"}
CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"     # 不用 0/O、1/I，長輩唸給店員聽也不會混淆
CODE_LEN = 6

MIN_FILL_SECS = 3            # 打開頁面不到 3 秒就送出＝機器人
MAX_FILL_SECS = 2 * 3600     # 頁面放超過 2 小時要重新整理
IP_LIMIT = (5, 3600)         # 同一 IP 每小時 5 筆
PHONE_LIMIT = (3, 86400)     # 同一支電話每天 3 筆
DUP_SECS = 1800              # 30 分鐘內同電話、同日期時段、同類型＝重複送出，回傳同一個預約碼


def roc(d: date) -> str:
    return f"{d.year - 1911}/{d.month:02d}/{d.day:02d}（{WD[d.weekday()]}）"


def booking_dates(today: Optional[date] = None) -> List[Dict[str, str]]:
    today = today or date.today()
    out = []
    for i in range(1, DAYS_AHEAD + 1):
        d = today + timedelta(days=i)
        if d.weekday() in CLOSED_WEEKDAYS:
            continue
        out.append({"v": d.isoformat(), "md": f"{d.month}/{d.day}", "wd": WD[d.weekday()],
                    "label": roc(d)})
    return out


def hour_label(h: int) -> str:
    return f"{h:02d}:00"


# ── 表單簽章（防機器人：要先打開頁面、停留幾秒才送得出）────────────

def _sig(ts: str) -> str:
    key = (settings.SITE.get("secret") or "").encode()
    return hmac.new(key, ts.encode(), hashlib.sha256).hexdigest()[:24]


def form_token(now: Optional[float] = None) -> str:
    ts = str(int(now if now is not None else time.time()))
    return f"{ts}.{_sig(ts)}"


def check_token(tok: str, now: Optional[float] = None) -> bool:
    ts, _, sig = (tok or "").partition(".")
    if not ts.isdigit() or not hmac.compare_digest(sig, _sig(ts)):
        return False
    age = (now if now is not None else time.time()) - int(ts)
    return MIN_FILL_SECS <= age <= MAX_FILL_SECS


# ── 驗證 ─────────────────────────────────────────────────────

def clean_phone(s: Any) -> str:
    return re.sub(r"[^\d\-]", "", str(s or ""))[:20]


def validate(form: Dict[str, Any], kind: str, today: Optional[date] = None) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """回傳 (清理後的資料, 錯誤訊息{欄位: 文字})；錯誤是空的才算通過。"""
    err: Dict[str, str] = {}
    name = str(form.get("contact_name") or "").strip()[:50]
    phone = clean_phone(form.get("phone"))
    note = str(form.get("note") or "").strip()[:500]
    address = str(form.get("address") or "").strip()[:200]
    req_date = str(form.get("req_date") or "")
    if kind not in KINDS:
        err["kind"] = "預約類型不正確。"
    if not name:
        err["contact_name"] = "請填寫稱呼。"
    if len(re.sub(r"\D", "", phone)) < 7:
        err["phone"] = "請填寫有效的聯絡電話。"
    if kind == "home" and len(address) < 5:
        err["address"] = "請填寫到府服務的地址。"
    if req_date not in {d["v"] for d in booking_dates(today)}:
        err["req_date"] = "請選擇日期（明天起兩週內）。"
    try:
        req_hour = int(form.get("req_hour"))
    except (TypeError, ValueError):
        req_hour = -1
    if req_hour not in HOURS:
        err["req_hour"] = "請選擇時段。"
    if not form.get("consent"):
        err["consent"] = "請勾選同意，我們才能使用您的資料聯絡您。"
    clean = {"contact_name": name, "phone": phone, "note": note or None,
             "address": (address or None) if kind == "home" else None,
             "req_date": req_date, "req_hour": req_hour, "kind": kind}
    return clean, err


# ── 次數限制與重複送出（單一行程，記憶體即可）────────────────────

class Limiter:
    def __init__(self) -> None:
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._recent: Dict[tuple, Tuple[float, str, datetime]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: Tuple[int, int], now: float) -> bool:
        n, window = limit
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window:
                q.popleft()
            return len(q) < n

    def hit(self, key: str, now: float) -> None:
        with self._lock:
            self._hits[key].append(now)

    def recent(self, key: tuple, now: float) -> Optional[Tuple[str, datetime]]:
        with self._lock:
            for k in [k for k, v in self._recent.items() if now - v[0] > DUP_SECS]:
                del self._recent[k]
            v = self._recent.get(key)
            return (v[1], v[2]) if v else None

    def remember(self, key: tuple, code: str, expires: datetime, now: float) -> None:
        with self._lock:
            self._recent[key] = (now, code, expires)


limiter = Limiter()


# ── 預約碼與 LINE 連結 ─────────────────────────────────────────

def new_code() -> str:
    return "".join(secrets.choice(CODE_CHARS) for _ in range(CODE_LEN))


def code_expires(req_date: str, now: Optional[datetime] = None) -> datetime:
    """有效到「今天起 code_days 天」或「預約當天結束」，取較早者。"""
    now = now or datetime.now()
    last = datetime.combine(date.fromisoformat(req_date), datetime.max.time()).replace(microsecond=0)
    return min(now + timedelta(days=int(settings.BOOKING.get("code_days", 7))), last)


def line_add_url() -> str:
    return "https://line.me/R/ti/p/" + quote(settings.LINE["basic_id"], safe="")


def line_code_url(code: str) -> str:
    """開啟官方帳號聊天室並帶入「預約碼 XXXXXX」，客人按傳送即可。"""
    return ("https://line.me/R/oaMessage/" + quote(settings.LINE["basic_id"], safe="")
            + "/?" + quote(f"預約碼 {code}", safe=""))


# ── 送出 ─────────────────────────────────────────────────────

class Rejected(Exception):
    """要顯示給客人的拒絕原因。"""


def submit(clean: Dict[str, Any], ip: str, now: Optional[float] = None) -> Tuple[str, datetime]:
    """寫入 crm1.booking_requests（source=web），回傳 (預約碼, 期限)。"""
    now = now if now is not None else time.time()
    digits = re.sub(r"\D", "", clean["phone"])
    dup_key = (digits, clean["req_date"], clean["req_hour"], clean["kind"])
    prev = limiter.recent(dup_key, now)
    if prev:
        return prev
    if not limiter.allow(f"ip:{ip}", IP_LIMIT, now):
        raise Rejected("送出次數太多，請稍後再試，或直接來電預約。")
    if not limiter.allow(f"ph:{digits}", PHONE_LIMIT, now):
        raise Rejected("這支電話今天已經預約過幾次了，如需更改請直接來電。")
    expires = code_expires(clean["req_date"], datetime.fromtimestamp(now))
    with db.connect() as con, con.cursor() as cur:
        for _ in range(5):
            code = new_code()
            try:
                cur.execute(
                    "INSERT INTO crm1.booking_requests (contact_name, phone, req_date, req_hour, "
                    "note, source, kind, address, link_code, link_expires) "
                    "VALUES (%s,%s,%s,%s,%s,'web',%s,%s,%s,%s)",
                    (clean["contact_name"], clean["phone"], clean["req_date"], clean["req_hour"],
                     clean["note"], clean["kind"], clean["address"], code, expires))
                break
            except pymysql.err.IntegrityError as e:
                if e.args[0] != 1062:       # 1062＝預約碼撞號，換一個再試
                    raise
        else:
            raise RuntimeError("預約碼連續撞號")
    limiter.hit(f"ip:{ip}", now)
    limiter.hit(f"ph:{digits}", now)
    limiter.remember(dup_key, code, expires, now)
    return code, expires
