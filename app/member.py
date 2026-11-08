"""
member.py — 官網會員：Google／LINE 登入、登入憑證、綁第二種登入方式（必要時合併兩個會員）、連結門市客戶資料。

設計見 docs/會員系統.md（D1–D6 John 2026-11-01 照建議定案）。重點：
- 伺服器端 OAuth 授權碼流程＋PKCE＋state＋nonce；不載入 Google／LINE 的 JS，CSP 不用放行外部網域。
  state、nonce、PKCE verifier 放在簽章過的短效 cookie（10 分鐘、只送到 /member/auth/）。
- id_token 是伺服器直接向 Google／LINE 的 token 端點（HTTPS）拿的：依 OpenID Connect Core 3.1.3.7 第 6 點，
  用 TLS 代替簽章驗證，仍檢查 iss、aud、exp、nonce。LINE 另外交給官方的 /oauth2/v2.1/verify 驗。
- 登入憑證：32 bytes 亂數放 HttpOnly cookie，資料庫只存 SHA-256，180 天沒用就失效。
- 客戶資料只透過 crm1.v_member_customer（編號、稱呼、LINE userId 的雜湊），見 scripts/setup_db.py。
- log 只記會員編號和登入方式，不記 Google sub、LINE userId、Email、憑證。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import pymysql

from app import content, db, progress, settings

log = logging.getLogger("y1web.member")

COOKIE = "y1m"                       # 登入憑證
STATE_COOKIE = "y1m_oa"              # OAuth 進行中（state／nonce／verifier）
STATE_PATH = "/member/auth/"
STATE_SECS = 600
SESSION_DAYS = 180
MAX_SESSIONS = 20                    # 每個會員最多同時登入幾台裝置；超過就登出最舊的
LABEL = {"google": "Google", "line": "LINE"}
# 動作完成後轉回帳號頁 /member?ok=代碼&p=登入方式：頁面只顯示這裡的固定句子，不把網址上的文字照印
MESSAGES = {"already": "這個 {p} 帳號本來就綁在您的會員裡。", "bound": "綁定好了，之後用 {p} 也能登入。",
            "merged": "綁定好了。原本用 {p} 登入的紀錄也合併進來了。", "unbound": "已解除 {p} 登入。",
            "linked": "已連結門市客戶資料。", "unlinked": "已解除和門市客戶資料的連結。",
            "others": "其他裝置都已登出。", "hello": "登入好了。"}
DEV_PREFIX = {"google": "dev-", "line": "Utest_"}    # 開發用登入只能用這種假帳號（LINE 的跟測試資料慣例一樣）

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_ISS = {"https://accounts.google.com", "accounts.google.com"}
LINE_AUTH = "https://access.line.me/oauth2/v2.1/authorize"
LINE_TOKEN = "https://api.line.me/oauth2/v2.1/token"
LINE_VERIFY = "https://api.line.me/oauth2/v2.1/verify"
LINE_ISS = "https://access.line.me"


class LoginError(Exception):
    """登入／綁定失敗；訊息直接顯示給客人。"""


# ── 設定 ─────────────────────────────────────────────────────

def _cfg(key: str) -> str:
    return str(settings.MEMBER.get(key) or "").strip()


def client(provider: str) -> Tuple[str, str]:
    if provider == "google":
        return _cfg("google_client_id"), _cfg("google_client_secret")
    if provider == "line":
        return _cfg("line_channel_id"), _cfg("line_channel_secret")
    return "", ""


def enabled(provider: str) -> bool:
    cid, sec = client(provider)
    return bool(cid and sec)


def providers() -> Dict[str, bool]:
    return {p: enabled(p) for p in LABEL}


def redirect_uri(provider: str) -> str:
    """要和 Google Cloud Console、LINE Developers 登記的回呼網址一字不差。"""
    return f"{content.site_url()}/member/auth/{provider}/callback"


def dev_allowed(client_ip: Optional[str]) -> bool:
    """開發用登入：config 開了、而且是區網直接連進來（經 .26／Cloudflare 轉進來的拿到的是客人的外部 IP）。"""
    if not settings.MEMBER.get("dev_login") or not client_ip:
        return False
    proxies = {p.strip() for p in str(settings.SERVER.get("forwarded_allow_ips", "")).split(",")}
    if client_ip in proxies:
        return False
    try:
        ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def _key(purpose: str) -> bytes:
    secret = str(settings.SITE.get("secret") or "")
    if not secret or secret == "CHANGE_ME":
        raise LoginError("網站設定還沒完成，暫時不能登入。")
    return hmac.new(secret.encode(), f"y1member:{purpose}".encode(), hashlib.sha256).digest()


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def safe_next(path: Any, default: str = "/member") -> str:
    """登入後要回去的頁面：只接受本站路徑（擋 //evil.com、/\\evil.com、控制字元）。"""
    p = str(path or "")
    if (not p.startswith("/") or p.startswith("//") or p.startswith("/\\") or len(p) > 300
            or any(ord(c) < 33 or c == "\\" for c in p)):
        return default
    return p


def mask_email(email: Optional[str]) -> str:
    if not email or "@" not in email:
        return ""
    user, _, dom = email.partition("@")
    return f"{user[:2]}***@{dom}"


# ── OAuth：state cookie、授權網址、id_token 檢查 ───────────────────

def make_state(provider: str, mode: str, next_path: str, member_id: Optional[int] = None,
               now: Optional[float] = None) -> Tuple[str, Dict[str, str]]:
    """回傳 (cookie 值, 授權網址要的 state／nonce／code_challenge)。綁定時記下發起的會員，回來時要是同一位。"""
    st = {"p": provider, "m": mode, "nx": safe_next(next_path), "s": _b64u(secrets.token_bytes(24)),
          "n": _b64u(secrets.token_bytes(24)), "cv": _b64u(secrets.token_bytes(48)),
          "u": member_id, "t": int(now if now is not None else time.time())}
    body = _b64u(json.dumps(st, separators=(",", ":")).encode())
    sig = _b64u(hmac.new(_key("state"), body.encode(), hashlib.sha256).digest())
    challenge = _b64u(hashlib.sha256(st["cv"].encode()).digest())
    return f"{body}.{sig}", {"state": st["s"], "nonce": st["n"], "challenge": challenge}


def read_state(cookie: Optional[str], provider: str, state: str,
               now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    body, _, sig = (cookie or "").partition(".")
    if not body or not sig:
        return None
    want = _b64u(hmac.new(_key("state"), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, want):
        return None
    try:
        st = json.loads(_unb64u(body))
    except ValueError:
        return None
    age = (now if now is not None else time.time()) - int(st.get("t") or 0)
    if st.get("p") != provider or not 0 <= age <= STATE_SECS:
        return None
    if not state or not hmac.compare_digest(str(st.get("s")), state):
        return None
    return st


def authorize_url(provider: str, params: Dict[str, str]) -> str:
    cid, _ = client(provider)
    q = {"response_type": "code", "client_id": cid, "redirect_uri": redirect_uri(provider),
         "state": params["state"], "nonce": params["nonce"],
         "code_challenge": params["challenge"], "code_challenge_method": "S256"}
    if provider == "google":
        # select_account：家裡共用的手機也能選要用哪個帳號
        q.update(scope="openid email profile", prompt="select_account")
        return GOOGLE_AUTH + "?" + urllib.parse.urlencode(q)
    # bot_prompt=normal：同意畫面上順便問要不要加官方帳號好友（LINE Login channel 要連結官方帳號才有）
    q.update(scope="openid profile", bot_prompt="normal")
    return LINE_AUTH + "?" + urllib.parse.urlencode(q)


def jwt_payload(token: str) -> Dict[str, Any]:
    try:
        return json.loads(_unb64u(token.split(".")[1]))
    except (IndexError, ValueError):
        raise LoginError("登入資料讀不懂，請再試一次。") from None


def check_claims(claims: Dict[str, Any], provider: str, nonce: str, now: Optional[float] = None) -> None:
    cid, _ = client(provider)
    iss_ok = claims.get("iss") in GOOGLE_ISS if provider == "google" else claims.get("iss") == LINE_ISS
    aud = claims.get("aud")
    aud_ok = aud == cid or (isinstance(aud, list) and cid in aud)
    exp = claims.get("exp")
    now = now if now is not None else time.time()
    if not (iss_ok and aud_ok and isinstance(exp, (int, float)) and exp > now - 60):
        raise LoginError("登入資料驗證不過，請再試一次。")
    if not nonce or not hmac.compare_digest(str(claims.get("nonce") or ""), nonce):
        raise LoginError("登入資料驗證不過，請再試一次。")
    if not str(claims.get("sub") or ""):
        raise LoginError("登入資料不完整，請再試一次。")


def _post(url: str, data: Dict[str, str]) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read() or b"{}")
        except ValueError:
            err = {}
        # 不記 code、權杖；只記錯誤代碼（invalid_grant＝碼用過或過期、invalid_client＝ID／密碼不對）
        log.warning("會員登入 %s 失敗：HTTP %s %s", urllib.parse.urlparse(url).netloc, e.code,
                    err.get("error") or "")
        raise LoginError("登入沒有成功，請再試一次。") from None
    except Exception as e:  # noqa: BLE001
        log.warning("會員登入連線失敗 %s：%s", urllib.parse.urlparse(url).netloc, type(e).__name__)
        raise LoginError("暫時連不上登入服務，請稍後再試。") from None


def exchange(provider: str, code: str, st: Dict[str, Any]) -> Dict[str, Any]:
    """授權碼 → 帳號資料 {subject, name, email}。"""
    cid, sec = client(provider)
    tok = _post(GOOGLE_TOKEN if provider == "google" else LINE_TOKEN, {
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(provider),
        "client_id": cid, "client_secret": sec, "code_verifier": st["cv"]})
    idt = str(tok.get("id_token") or "")
    if not idt:
        raise LoginError("登入資料不完整，請再試一次。")
    if provider == "google":
        claims = jwt_payload(idt)
    else:
        claims = _post(LINE_VERIFY, {"id_token": idt, "client_id": cid, "nonce": st["n"]})
    check_claims(claims, provider, st["n"])
    email = claims.get("email") if provider == "google" and claims.get("email_verified") else None
    return {"subject": str(claims["sub"]), "name": str(claims.get("name") or "")[:50],
            "email": str(email)[:254] if email else None}


# ── 登入憑證 ─────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(member_id: int, device: str = "") -> str:
    token = _b64u(secrets.token_bytes(32))
    now = _now()
    with db.connect() as con, con.cursor() as cur:
        cur.execute("DELETE FROM y1member.member_sessions WHERE expires_at < %s", (now,))
        cur.execute("INSERT INTO y1member.member_sessions (token_hash, member_id, expires_at, last_seen_at, device) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (_hash(token), member_id, now + timedelta(days=SESSION_DAYS), now, (device or "")[:120] or None))
        cur.execute("SELECT token_hash FROM y1member.member_sessions WHERE member_id=%s "
                    "ORDER BY last_seen_at DESC, created_at DESC LIMIT 1000 OFFSET %s", (member_id, MAX_SESSIONS))
        old = [r["token_hash"] for r in cur.fetchall()]
        if old:
            cur.execute("DELETE FROM y1member.member_sessions WHERE token_hash IN %s", (old,))
    return token


def session_member(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """憑證 → 會員（停用或過期＝None）。一小時內沒用過才更新最後使用時間，順便往後延 180 天。"""
    if not token or len(token) > 100:
        return None
    th, now = _hash(token), _now()
    with db.connect() as con, con.cursor() as cur:
        cur.execute("SELECT m.id, m.display_name, m.customer_id, s.last_seen_at "
                    "FROM y1member.member_sessions s JOIN y1member.members m ON m.id = s.member_id "
                    "WHERE s.token_hash=%s AND s.expires_at > %s AND m.disabled = 0", (th, now))
        r = cur.fetchone()
        if r and r["last_seen_at"] < now - timedelta(hours=1):
            cur.execute("UPDATE y1member.member_sessions SET last_seen_at=%s, expires_at=%s WHERE token_hash=%s",
                        (now, now + timedelta(days=SESSION_DAYS), th))
    return r


def end_session(token: Optional[str]) -> None:
    if token:
        with db.connect() as con, con.cursor() as cur:
            cur.execute("DELETE FROM y1member.member_sessions WHERE token_hash=%s", (_hash(token),))


def public_id(member_id: int) -> str:
    """給前端的會員代號（每日聽練記「這台裝置上次同步的是誰」用）：不直接給流水號。"""
    return _b64u(hmac.new(_key("uid"), str(member_id).encode(), hashlib.sha256).digest())[:16]


def csrf_token(session_token: str) -> str:
    """表單防偽：跟著登入憑證走（登出就失效），不用另外存。"""
    return _b64u(hmac.new(_key("csrf"), _hash(session_token).encode(), hashlib.sha256).digest())[:32]


def check_csrf(session_token: Optional[str], value: Any) -> bool:
    return bool(session_token) and hmac.compare_digest(csrf_token(session_token), str(value or ""))


# ── 登入、綁定、合併 ─────────────────────────────────────────────

def login(provider: str, prof: Dict[str, Any]) -> int:
    """用 Google／LINE 登入：認得這個帳號就回原本的會員，不認得就開一個新會員。"""
    now = _now()
    with db.connect() as con, con.cursor() as cur:
        for _ in range(2):            # 同一個帳號同時第一次登入兩次：撞到唯一鍵就重查一次
            cur.execute("SELECT i.member_id, m.disabled FROM y1member.member_identities i "
                        "JOIN y1member.members m ON m.id = i.member_id WHERE i.provider=%s AND i.subject=%s",
                        (provider, prof["subject"]))
            r = cur.fetchone()
            if r:
                if r["disabled"]:
                    raise LoginError("這個會員帳號已經停用，請洽門市。")
                cur.execute("UPDATE y1member.member_identities SET last_login_at=%s, email=COALESCE(%s, email) "
                            "WHERE provider=%s AND subject=%s", (now, prof.get("email"), provider, prof["subject"]))
                cur.execute("UPDATE y1member.members SET last_login_at=%s WHERE id=%s", (now, r["member_id"]))
                return int(r["member_id"])
            con.begin()
            try:
                cur.execute("INSERT INTO y1member.members (display_name, terms_at, last_login_at) VALUES (%s, %s, %s)",
                            (prof.get("name") or "", now, now))
                mid = cur.lastrowid
                cur.execute("INSERT INTO y1member.member_identities (member_id, provider, subject, email, last_login_at) "
                            "VALUES (%s, %s, %s, %s, %s)", (mid, provider, prof["subject"], prof.get("email"), now))
                con.commit()
                log.info("新會員 #%s（%s）", mid, LABEL[provider])
                return int(mid)
            except pymysql.err.IntegrityError:
                con.rollback()
        raise LoginError("登入沒有成功，請再試一次。")


def _merge_into(cur, keep: Dict[str, Any], drop: Dict[str, Any]) -> None:
    """把 drop 會員整個併進 keep（在呼叫端的交易裡）：登入方式、練習紀錄、客戶連結、登入中的裝置、申請都搬過來。"""
    k, d = keep["id"], drop["id"]
    cur.execute("SELECT app, data FROM y1member.member_progress WHERE member_id=%s", (d,))
    for row in cur.fetchall():
        cur.execute("SELECT data FROM y1member.member_progress WHERE member_id=%s AND app=%s FOR UPDATE",
                    (k, row["app"]))
        mine = cur.fetchone()
        if mine:
            merged = progress.merge(json.loads(mine["data"]), json.loads(row["data"]))
            cur.execute("UPDATE y1member.member_progress SET data=%s WHERE member_id=%s AND app=%s",
                        (json.dumps(merged, separators=(",", ":")), k, row["app"]))
            cur.execute("DELETE FROM y1member.member_progress WHERE member_id=%s AND app=%s", (d, row["app"]))
        else:
            cur.execute("UPDATE y1member.member_progress SET member_id=%s WHERE member_id=%s AND app=%s",
                        (k, d, row["app"]))
    cur.execute("UPDATE y1member.member_identities SET member_id=%s WHERE member_id=%s", (k, d))
    cur.execute("UPDATE y1member.member_sessions SET member_id=%s WHERE member_id=%s", (k, d))
    cur.execute("UPDATE y1member.link_requests SET member_id=%s WHERE member_id=%s", (k, d))
    cur.execute("UPDATE y1member.link_codes SET used_by=%s WHERE used_by=%s", (k, d))
    if drop["customer_id"] and not keep["customer_id"]:
        cur.execute("UPDATE y1member.members SET customer_id=NULL WHERE id=%s", (d,))    # 唯一鍵：先放掉再接上
        cur.execute("UPDATE y1member.members SET customer_id=%s, link_method=%s, linked_at=%s, consent_at=%s "
                    "WHERE id=%s", (drop["customer_id"], drop["link_method"], drop["linked_at"],
                                    drop["consent_at"], k))
    if not keep["display_name"] and drop["display_name"]:
        cur.execute("UPDATE y1member.members SET display_name=%s WHERE id=%s", (drop["display_name"], k))
    cur.execute("DELETE FROM y1member.members WHERE id=%s", (d,))


def bind(member_id: int, provider: str, prof: Dict[str, Any]) -> str:
    """已登入的會員再綁一種登入方式。那個帳號如果已經是另一個會員，就合併（規則見 docs/會員系統.md 2.3）。
    回傳 MESSAGES 的代碼；不能綁丟 LoginError。"""
    label = LABEL[provider]
    now = _now()
    with db.connect() as con, con.cursor() as cur:
        con.begin()
        try:
            cur.execute("SELECT * FROM y1member.members WHERE id=%s FOR UPDATE", (member_id,))
            me = cur.fetchone()
            if not me or me["disabled"]:
                raise LoginError("請重新登入。")
            cur.execute("SELECT provider, subject FROM y1member.member_identities WHERE member_id=%s", (member_id,))
            mine = {r["provider"]: r["subject"] for r in cur.fetchall()}
            cur.execute("SELECT member_id FROM y1member.member_identities WHERE provider=%s AND subject=%s FOR UPDATE",
                        (provider, prof["subject"]))
            r = cur.fetchone()
            other = int(r["member_id"]) if r else None
            if other == member_id:
                con.rollback()
                return "already"
            if provider in mine:
                raise LoginError(f"您已經綁了另一個 {label} 帳號。要換的話，請先解除原本的。")
            if other is None:
                cur.execute("INSERT INTO y1member.member_identities (member_id, provider, subject, email, last_login_at) "
                            "VALUES (%s, %s, %s, %s, %s)", (member_id, provider, prof["subject"], prof.get("email"), now))
                con.commit()
                log.info("會員 #%s 綁定 %s", member_id, label)
                return "bound"
            cur.execute("SELECT * FROM y1member.members WHERE id=%s FOR UPDATE", (other,))
            drop = cur.fetchone()
            cur.execute("SELECT provider FROM y1member.member_identities WHERE member_id=%s", (other,))
            theirs = {x["provider"] for x in cur.fetchall()}
            if (theirs - {provider}) & set(mine):
                raise LoginError(f"這個 {label} 帳號已經和別的帳號一起使用，沒辦法再合併，請洽門市協助。")
            if me["customer_id"] and drop["customer_id"] and me["customer_id"] != drop["customer_id"]:
                raise LoginError("兩個帳號連到不同的門市客戶資料，沒辦法合併，請洽門市協助。")
            if drop["disabled"]:
                raise LoginError(f"這個 {label} 帳號所在的會員已經停用，請洽門市。")
            _merge_into(cur, me, drop)
            con.commit()
            log.info("會員 #%s 綁定 %s：併入會員 #%s", member_id, label, other)
            return "merged"
        except Exception:
            con.rollback()
            raise


def unbind(member_id: int, provider: str) -> str:
    if provider not in LABEL:
        raise LoginError("沒有這種登入方式。")
    with db.connect() as con, con.cursor() as cur:
        con.begin()
        cur.execute("SELECT provider FROM y1member.member_identities WHERE member_id=%s FOR UPDATE", (member_id,))
        have = {r["provider"] for r in cur.fetchall()}
        if provider not in have:
            con.rollback()
            return "unbound"
        if len(have) < 2:
            con.rollback()
            raise LoginError("至少要留一種登入方式。不想用了可以刪除會員。")
        cur.execute("DELETE FROM y1member.member_identities WHERE member_id=%s AND provider=%s", (member_id, provider))
        con.commit()
    log.info("會員 #%s 解除 %s", member_id, LABEL[provider])
    return "unbound"


def delete_member(member_id: int) -> None:
    """刪除會員：登入方式、練習紀錄、登入憑證、申請一起刪（外鍵 CASCADE）；CRM 的客戶資料照原本規則保存（D5）。"""
    with db.connect() as con, con.cursor() as cur:
        cur.execute("DELETE FROM y1member.members WHERE id=%s", (member_id,))
    log.info("會員 #%s 已刪除", member_id)


# ── 連結門市客戶資料 ───────────────────────────────────────────

def line_match(member_id: int) -> Optional[Dict[str, Any]]:
    """還沒連結、有綁 LINE、而且那個 LINE 在 CRM 已經綁在某位客人身上（那位客人也還沒被別的會員連走）。"""
    with db.connect() as con, con.cursor() as cur:
        cur.execute("SELECT m.customer_id, i.subject FROM y1member.members m "
                    "LEFT JOIN y1member.member_identities i ON i.member_id = m.id AND i.provider = 'line' "
                    "WHERE m.id=%s", (member_id,))
        r = cur.fetchone()
        if not r or r["customer_id"] or not r["subject"]:
            return None
        cur.execute("SELECT customer_id, salutation FROM crm1.v_member_customer WHERE line_hash=%s",
                    (hashlib.sha256(r["subject"].encode()).hexdigest(),))
        rows = cur.fetchall()
        if len(rows) != 1:            # 0＝不是門市客人；2 以上＝CRM 有重複，不猜，交給店員
            return None
        cur.execute("SELECT 1 FROM y1member.members WHERE customer_id=%s", (rows[0]["customer_id"],))
        return None if cur.fetchone() else rows[0]


def link_by_line(member_id: int) -> str:
    match = line_match(member_id)
    if not match:
        raise LoginError("找不到可以連結的門市資料。")
    now = _now()
    try:
        with db.connect() as con, con.cursor() as cur:
            cur.execute("UPDATE y1member.members SET customer_id=%s, link_method='line', linked_at=%s, consent_at=%s "
                        "WHERE id=%s AND customer_id IS NULL", (match["customer_id"], now, now, member_id))
            if cur.rowcount != 1:
                raise LoginError("找不到可以連結的門市資料。")
    except pymysql.err.IntegrityError:
        raise LoginError("這份門市資料已經連到別的會員，請洽門市。") from None
    log.info("會員 #%s 用 LINE 連上客戶 #%s", member_id, match["customer_id"])
    return "linked"


def unlink_customer(member_id: int) -> str:
    with db.connect() as con, con.cursor() as cur:
        cur.execute("UPDATE y1member.members SET customer_id=NULL, link_method=NULL, linked_at=NULL, consent_at=NULL "
                    "WHERE id=%s", (member_id,))
    log.info("會員 #%s 解除客戶連結", member_id)
    return "unlinked"


def account(member_id: int) -> Optional[Dict[str, Any]]:
    """帳號設定頁要的資料。"""
    with db.connect() as con, con.cursor() as cur:
        cur.execute("SELECT id, display_name, customer_id, link_method, linked_at, created_at "
                    "FROM y1member.members WHERE id=%s AND disabled=0", (member_id,))
        m = cur.fetchone()
        if not m:
            return None
        cur.execute("SELECT provider, email, created_at FROM y1member.member_identities WHERE member_id=%s",
                    (member_id,))
        m["identities"] = {r["provider"]: {**r, "email": mask_email(r["email"])} for r in cur.fetchall()}
        m["salutation"] = None
        if m["customer_id"]:
            cur.execute("SELECT salutation FROM crm1.v_member_customer WHERE customer_id=%s", (m["customer_id"],))
            r = cur.fetchone()
            m["salutation"] = r["salutation"] if r else None
        cur.execute("SELECT device, last_seen_at FROM y1member.member_sessions WHERE member_id=%s AND expires_at > %s "
                    "ORDER BY last_seen_at DESC", (member_id, _now()))
        m["sessions"] = list(cur.fetchall())
    m["line_offer"] = line_match(member_id)
    return m


def end_other_sessions(member_id: int, keep_token: str) -> None:
    with db.connect() as con, con.cursor() as cur:
        cur.execute("DELETE FROM y1member.member_sessions WHERE member_id=%s AND token_hash<>%s",
                    (member_id, _hash(keep_token)))


def dev_profile(provider: str, suffix: str, name: str) -> Dict[str, Any]:
    """開發用登入的假帳號：只能是 dev-／Utest_ 開頭，不可能撞到真的 Google sub 或 LINE userId。"""
    s = "".join(c for c in str(suffix or "") if c.isascii() and (c.isalnum() or c in "_-"))[:30]
    if provider not in LABEL or not s:
        raise LoginError("開發用登入：請填帳號代號（英數字）。")
    return {"subject": DEV_PREFIX[provider] + s, "name": (str(name or "").strip() or f"測試請忽略 {s}")[:50],
            "email": f"{s}@example.com" if provider == "google" else None}
