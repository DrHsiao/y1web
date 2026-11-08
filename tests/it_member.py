"""會員整合實測：本機 127.0.0.1:18998 跑 y1web（開發用登入打開、Google 換碼用假的），打真的資料庫。
測試資料：dev-／Utest_ 帳號、「測試請忽略」客戶；跑完全部刪掉。
執行：cd /home/john/y1web && ./venv/bin/python -I tests/it_member.py（檔名不是 test_ 開頭：unittest discover 不會跑它，因為它會寫資料庫）"""
import http.cookiejar
import json
import re
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, "/home/john/y1web")
import pymysql  # noqa: E402
import uvicorn  # noqa: E402

from app import db, member, progress, settings  # noqa: E402

settings.MEMBER["dev_login"] = True
PORT = 18998
BASE = f"http://127.0.0.1:{PORT}"
FAKE = {}                                   # 假的 Google 換碼結果（callback 實測用）
member.exchange = lambda provider, code, st: dict(FAKE[code])

from app.main import app  # noqa: E402

ok = fail = 0


def check(cond, name):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print("❌", name)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Client:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), NoRedirect)

    def req(self, method, path, data=None, headers=None):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        r = urllib.request.Request(BASE + path, data=body, method=method, headers=headers or {})
        try:
            resp = self.op.open(r, timeout=10)
        except urllib.error.HTTPError as e:
            resp = e
        return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")

    def raw(self, method, path, body: bytes, headers=None):
        r = urllib.request.Request(BASE + path, data=body, method=method,
                                   headers={"Content-Type": "application/json", **(headers or {})})
        try:
            resp = self.op.open(r, timeout=10)
        except urllib.error.HTTPError as e:
            resp = e
        return resp.status, resp.read().decode("utf-8", "replace")

    def put(self, data, **kw):
        s, body = self.raw("PUT", "/api/member/progress/practice", json.dumps(data).encode(), **kw)
        return s, (json.loads(body) if body.startswith("{") else body)

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def post(self, path, data, **kw):
        return self.req("POST", path, data, **kw)

    def csrf(self):
        s, _, html = self.get("/member")
        m = re.search(r'name="t" value="([^"]+)"', html)
        return m.group(1) if m else ""

    def dev(self, provider, suffix, mode="login", nxt="/member"):
        d = {"provider": provider, "suffix": suffix, "name": f"測試請忽略 {suffix}", "mode": mode, "next": nxt}
        if mode == "bind":
            d["t"] = self.csrf()
        return self.post("/member/dev", d)

    def me(self):
        return json.loads(self.get("/api/member/me")[2])["member"]


def mid_of(subject):
    r = db.rows("SELECT member_id FROM y1member.member_identities WHERE subject=%s", (subject,))
    return r[0]["member_id"] if r else None


def admin():
    with open("/home/john/y1crm/config.toml", "rb") as f:
        d = tomllib.load(f)["db"]
    return pymysql.connect(host=d.get("host", "localhost"), user=d["user"], password=d["password"],
                           charset="utf8mb4", autocommit=True)


def cleanup(cust_ids):
    with db.connect() as con, con.cursor() as cur:
        cur.execute("SELECT DISTINCT member_id FROM y1member.member_identities "
                    "WHERE subject LIKE 'dev-%%' OR subject LIKE 'Utest\\_%%'")
        ids = [r["member_id"] for r in cur.fetchall()]
        if ids:
            cur.execute("DELETE FROM y1member.members WHERE id IN %s", (ids,))
        cur.execute("DELETE FROM y1member.members WHERE display_name LIKE '測試請忽略%%'")
    if cust_ids:
        with admin() as con, con.cursor() as cur:
            cur.execute("DELETE FROM crm1.customers WHERE id IN %s AND name LIKE '測試請忽略%%'", (cust_ids,))


def main():
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)

    cust = []
    with admin() as con, con.cursor() as cur:
        for i, (lid, g) in enumerate([("Utest_mc1", "M"), ("Utest_mc2", "F"), (None, "M")], 1):
            cur.execute("INSERT INTO crm1.customers (customer_code, registration_date, name, gender, line_user_id) "
                        "VALUES (%s, CURDATE(), %s, %s, %s)", (f"UTEST-M{i}", f"測試請忽略{i}", g, lid))
            cust.append(cur.lastrowid)
    try:
        run(cust)
    finally:
        cleanup(cust)
        left = db.rows("SELECT COUNT(*) AS n FROM y1member.members m WHERE m.display_name LIKE '測試請忽略%%' OR EXISTS "
                       "(SELECT 1 FROM y1member.member_identities i WHERE i.member_id = m.id AND "
                       "(i.subject LIKE 'dev-%%' OR i.subject LIKE 'Utest\\_%%'))")[0]["n"]
        print(f"清除後剩 {left} 個測試會員（真的會員不動）")
        server.should_exit = True
    print(f"\n通過 {ok} 項，失敗 {fail} 項")


def run(cust):
    c1 = Client()
    s, h, _ = c1.get("/member")
    check(s == 303 and h["location"] == "/member/login", "沒登入 → 轉登入頁")
    s, h, html = c1.get("/member/login")
    check(s == 200 and "用 LINE 登入" in html and "使用 Google 帳戶登入" in html and "開發用登入" in html, "登入頁")
    check(h.get("cache-control") == "no-store" and h.get("x-robots-tag") == "noindex", "會員頁 no-store、noindex")
    check(c1.me() is None, "me：沒登入")

    # 1. 開發用登入（Google）→ 新會員
    s, h, _ = c1.dev("google", "itA", nxt="//evil.com")
    check(s == 303 and h["location"] == "/member?ok=hello", "登入後只轉回本站（擋 //evil.com）")
    check("httponly" in h.get("set-cookie", "").lower() and "samesite=lax" in h.get("set-cookie", "").lower(),
          "登入憑證 HttpOnly、SameSite=Lax")
    s, _, html = c1.get("/member?ok=hello")
    check(s == 200 and "測試請忽略 itA" in html and "登入好了" in html, "帳號頁")
    me = c1.me()
    check(me["name"] == "測試請忽略 itA" and me["linked"] is False and len(me["uid"]) == 16, "me：已登入")
    s, _, _ = c1.get("/member?ok=<script>")
    check(s == 200, "網址上的 ok 亂填不會出錯")
    s, _, html = c1.post("/member/unbind", {"provider": "google", "t": "wrong"})
    check(s == 403, "表單防偽：t 不對 403")
    s, _, html = c1.post("/member/unbind", {"provider": "google", "t": c1.csrf()})
    check(s == 400 and "至少要留一種" in html, "最後一種登入方式不能解除")

    # 2. 綁 LINE（新帳號）→ 兩種都能登入同一個會員
    a = mid_of("dev-itA")
    s, h, _ = c1.dev("line", "itB", mode="bind")
    check(s == 303 and "ok=bound" in h["location"], "綁 LINE")
    check(mid_of("Utest_itB") == a, "LINE 綁在同一個會員")
    c1b = Client()
    c1b.dev("line", "itB")
    check(c1b.me()["name"] == "測試請忽略 itA", "用 LINE 登入＝同一個會員")
    s, h, html = c1.dev("line", "itZ", mode="bind")
    check(s == 400 and "已經綁了另一個 LINE" in html, "同一種登入方式不能綁兩個")
    s, h, _ = c1.post("/member/unbind", {"provider": "google", "t": c1.csrf()})
    check(s == 303 and mid_of("dev-itA") is None, "解除 Google")
    s, _, _ = c1.post("/member/logout-others", {"t": c1.csrf()})
    check(c1b.me() is None and c1.me() is not None, "登出其他裝置")

    # 3. 合併：A（Google）綁 C（LINE，另一個會員）→ 練習紀錄合併、C 的裝置跟過來
    c2, c3 = Client(), Client()
    c2.dev("google", "itA2")
    c3.dev("line", "itC")
    ma, mc = mid_of("dev-itA2"), mid_of("Utest_itC")
    with db.connect() as con, con.cursor() as cur:
        cur.execute("INSERT INTO y1member.member_progress (member_id, app, data) VALUES (%s,'practice',%s),(%s,'practice',%s)",
                    (ma, json.dumps({"days": {"2026-10-01": {"n": 1, "xp": 30}}, "lv": {"digit": 3}, "best": 1}),
                     mc, json.dumps({"days": {"2026-10-01": {"n": 2, "xp": 10}, "2026-10-02": {"n": 1, "xp": 5}},
                                     "lv": {"digit": 2, "pitch": 6}, "best": 2})))
    s, h, _ = c2.dev("line", "itC", mode="bind")
    check(s == 303 and "ok=merged" in h["location"], "綁到已存在的 LINE 會員 → 合併")
    check(mid_of("Utest_itC") == ma and not db.rows("SELECT 1 FROM y1member.members WHERE id=%s", (mc,)),
          "C 併進 A、C 刪掉")
    pr = json.loads(db.rows("SELECT data FROM y1member.member_progress WHERE member_id=%s", (ma,))[0]["data"])
    check(pr["days"] == {"2026-10-01": {"n": 2, "xp": 30}, "2026-10-02": {"n": 1, "xp": 5}}
          and pr["lv"] == {"digit": 3, "pitch": 6, "count": 1} and pr["best"] == 2, "練習紀錄合併")
    check(c3.me() is not None and c3.me()["name"] == "測試請忽略 itA2", "C 登入中的裝置變成 A")

    # 4. 不能合併：對方也有 LINE（會變成兩個 LINE）
    c4, c5 = Client(), Client()
    c4.dev("google", "itD"); c4.dev("line", "itE", mode="bind")
    c5.dev("line", "itF")
    s, _, html = c5.dev("google", "itD", mode="bind")
    check(s == 400 and "沒辦法再合併" in html and mid_of("dev-itD") == mid_of("Utest_itE"), "對方也有 LINE → 不合併")

    # 5. LINE 自動連結門市客人（要勾同意）
    c6 = Client()
    c6.dev("line", "mc1")
    s, _, html = c6.get("/member")
    check("您的 LINE 在門市登記過" in html and "測先生" in html, "LINE 比對到門市客人（稱呼＝姓＋先生）")
    s, _, html = c6.post("/member/link-line", {"t": c6.csrf()})
    check(s == 400 and "請先勾選同意" in html, "沒勾同意不能連")
    s, h, _ = c6.post("/member/link-line", {"t": c6.csrf(), "consent": "1"})
    m6 = db.rows("SELECT customer_id, link_method, consent_at FROM y1member.members WHERE id=%s", (mid_of("Utest_mc1"),))[0]
    check(s == 303 and m6["customer_id"] == cust[0] and m6["link_method"] == "line" and m6["consent_at"], "連結成功、記同意時間")
    check(c6.me()["linked"] is True, "me：已連結")
    s, _, html = c6.get("/member")
    check("已連結：<b>測先生</b>" in html, "帳號頁顯示已連結")
    c7 = Client()
    c7.dev("line", "mc2")
    check("測女士" in c7.get("/member")[2], "女性稱呼＝女士")

    # 6. 不能合併：兩邊連到不同客人
    c8 = Client()
    c8.dev("google", "itJ")
    with db.connect() as con, con.cursor() as cur:
        cur.execute("UPDATE y1member.members SET customer_id=%s, link_method='code' WHERE id=%s", (cust[2], mid_of("dev-itJ")))
    s, _, html = c6.dev("google", "itJ", mode="bind")
    check(s == 400 and "不同的門市客戶資料" in html, "連到不同客人 → 不合併")

    # 7. 一位客人只能連一個會員：mc1 的客人已被連走，另一個會員綁了同樣條件也不會出現連結邀請
    with db.connect() as con, con.cursor() as cur:
        try:
            cur.execute("UPDATE y1member.members SET customer_id=%s WHERE id=%s", (cust[0], mid_of("dev-itJ")))
            check(False, "同一位客人連兩個會員應該被擋")
        except pymysql.err.IntegrityError:
            check(True, "")
    s, h, _ = c6.post("/member/unlink", {"t": c6.csrf()})
    check(db.rows("SELECT customer_id FROM y1member.members WHERE id=%s", (mid_of("Utest_mc1"),))[0]["customer_id"] is None,
          "解除連結")

    # 8. 真的 OAuth 流程（Google 換碼用假的）：start → callback
    c9 = Client()
    s, h, _ = c9.get("/member/auth/google/start?next=/practice/")
    check(s == 302 and h["location"].startswith("https://y1ear.com/member/auth/google/start"), "不是正式網址 → 先換網址")
    s, h, _ = c9.get("/member/auth/google/start?next=/practice/", headers={"Host": "y1ear.com"})
    loc = urllib.parse.urlparse(h.get("location", ""))
    q = {k: v[0] for k, v in urllib.parse.parse_qs(loc.query).items()}
    check(s == 302 and loc.netloc == "accounts.google.com" and q.get("code_challenge_method") == "S256", "轉到 Google")
    check("path=/member/auth/" in h.get("set-cookie", "").lower(), "state cookie 只送到 /member/auth/")
    s, _, html = c9.get(f"/member/auth/google/callback?code=X&state={q['state']}x")
    check(s == 400 and "登入逾時" in html, "state 不對 → 擋")
    s, h, _ = c9.get("/member/auth/google/start", headers={"Host": "y1ear.com"})     # 擋下時 state cookie 也清掉了，重來
    q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(h["location"]).query).items()}
    s, _, html = c9.get(f"/member/auth/google/callback?error=access_denied&state={q['state']}")
    check(s == 200 and "沒有完成登入" in html, "在 Google 按取消")
    s, h, _ = c9.get(f"/member/auth/google/start?next=/practice/", headers={"Host": "y1ear.com"})
    q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(h["location"]).query).items()}
    FAKE["CODE1"] = {"subject": "dev-cb1", "name": "測試請忽略 cb", "email": "cb@example.com"}
    s, h, _ = c9.get(f"/member/auth/google/callback?code=CODE1&state={q['state']}")
    check(s == 303 and h["location"] == "/practice/" and c9.me()["name"] == "測試請忽略 cb", "callback 登入、回到原本的頁面")
    check("cb***@example.com" in c9.get("/member")[2], "Email 遮罩顯示")
    s, _, html = c9.get(f"/member/auth/google/callback?code=CODE1&state={q['state']}")
    check(s == 400, "state cookie 用過就清掉（不能重放）")
    # 綁定流程：mode=bind 要 t
    s, h, _ = c9.get("/member/auth/line/start?mode=bind&t=bad", headers={"Host": "y1ear.com"})
    check(s == 303 and h["location"] == "/member", "綁定沒帶對的 t → 不開始")
    s, h, _ = c9.get(f"/member/auth/line/start?mode=bind&t={c9.csrf()}", headers={"Host": "y1ear.com"})
    q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(h["location"]).query).items()}
    FAKE["CODE2"] = {"subject": "Utest_cb2", "name": "LINE 名字", "email": None}
    s, h, _ = c9.get(f"/member/auth/line/callback?code=CODE2&state={q['state']}")
    check(s == 303 and "ok=bound" in h["location"] and mid_of("Utest_cb2") == mid_of("dev-cb1"), "callback 綁定 LINE")

    # 8b. 每日聽練同步 API
    c10, c11 = Client(), Client()
    s, _, body = c10.get("/api/member/progress/practice")
    check(s == 200 and json.loads(body) == {"member": None}, "同步：沒登入 member=null")
    check(c10.put({"days": {}})[0] == 401, "同步：沒登入不能送")
    check(c10.get("/api/member/progress/other")[0] == 404, "同步：沒有的程式 404")
    c10.dev("google", "pg1")
    c11.dev("google", "pg2")
    j = json.loads(c10.get("/api/member/progress/practice")[2])
    check(j["data"] is None and len(j["member"]["uid"]) == 16 and str(mid_of("dev-pg1")) not in j["member"]["uid"],
          "同步：還沒有紀錄、會員代號不是流水號")
    uid1 = j["member"]["uid"]
    check(json.loads(c11.get("/api/member/progress/practice")[2])["member"]["uid"] != uid1, "同步：每個會員代號不同")
    s, j = c10.put({"days": {"2026-10-01": {"n": 2, "xp": 40}}, "lv": {"digit": 4}, "best": 1, "evil": "x"})
    check(s == 200 and j["data"]["days"] == {"2026-10-01": {"n": 2, "xp": 40}} and "evil" not in j["data"], "同步：送上去（先整理）")
    s, j = c10.put({"days": {"2026-10-01": {"n": 1, "xp": 10}, "2026-10-03": {"n": 1, "xp": 30}}, "lv": {"digit": 1, "pitch": 3}})
    check(j["data"]["days"] == {"2026-10-01": {"n": 2, "xp": 40}, "2026-10-03": {"n": 1, "xp": 30}}
          and j["data"]["lv"] == {"digit": 4, "pitch": 3, "count": 1} and j["data"]["best"] == 1,
          "同步：只會變多，不會被別台的舊資料蓋掉")
    s, j = c10.put({"days": {}})
    check(len(j["data"]["days"]) == 2, "同步：送空的紀錄也不會清掉伺服器的")
    check(json.loads(c11.get("/api/member/progress/practice")[2])["data"] is None, "同步：別的會員看不到")
    check(c10.raw("PUT", "/api/member/progress/practice", b"not json")[0] == 400, "同步：不是 JSON 400")
    check(c10.raw("PUT", "/api/member/progress/practice", b"[1,2]")[0] == 400, "同步：不是物件 400")
    check(c10.raw("PUT", "/api/member/progress/practice", b"{" + b" " * (progress.MAX_BYTES + 1024) + b"}")[0] == 413, "同步：太大 413")
    check(c10.put({"days": {}}, headers={"Origin": "https://evil.example"})[0] == 403, "同步：別的網站送的 403")
    check(c10.put({"days": {}}, headers={"Origin": "https://y1ear.com"})[0] == 200, "同步：正式網址的頁面可以送")
    s, h, _ = c10.get("/api/member/progress/practice")
    check(h.get("cache-control") == "no-store", "同步：不給快取")

    # 9. 登出、刪除
    s, _, html = c9.post("/member/delete", {"t": c9.csrf()})
    check(s == 400 and "我確定要刪除" in html, "刪除要勾確定")
    mid = mid_of("dev-cb1")
    s, h, _ = c9.post("/member/delete", {"t": c9.csrf(), "confirm": "1"})
    gone = not db.rows("SELECT 1 FROM y1member.member_identities WHERE member_id=%s UNION ALL "
                       "SELECT 1 FROM y1member.member_sessions WHERE member_id=%s", (mid, mid))
    check(s == 303 and h["location"] == "/member/login?bye=1" and gone and c9.me() is None, "刪除會員（登入方式、憑證一起刪）")
    check("會員已刪除" in c9.get("/member/login?bye=1")[2], "刪除後的提示")
    s, h, _ = c2.post("/member/logout", {"t": c2.csrf()})
    check(s == 303 and c2.me() is None, "登出")
    s, _, _ = c2.post("/member/nope", {})
    check(s == 404, "沒有的動作 404")


if __name__ == "__main__":
    main()
