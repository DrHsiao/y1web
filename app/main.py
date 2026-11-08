"""
main.py — y1web 官網（韻譯助聽器 睿聲中正門市）。

公開網站。頁面由 Jinja2 在伺服器端產生（搜尋引擎讀得到、長輩手機不用跑大量 JS）。
會員（/member，app/member.py）：Google／LINE 登入；客戶資料只能透過 crm1.v_member_customer 讀稱呼（D6）。
預約表單寫進 crm1.booking_requests（只能 INSERT），店員在 y1crm「預約留言」頁處理。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, quote, urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import booking, content, db, member, progress, qr, settings
from app.icons import icon

log = logging.getLogger("y1web")
ROOT = settings.ROOT

# 頁首選單（版型比照 ear.com.tw，John 2026-10-21 指定）：中間五項＋右側藍字兩項＋金色方塊打開全部頁面
NAV = [("/products", "助聽器介紹"), ("/hearing-test", "聽力測驗"), ("/about", "關於韻譯"),
       ("/column", "聽力專欄"), ("/share", "好評分享")]
NAV_CTA = [("/booking", "線上預約"), ("/store", "服務據點")]
SITEMAP = [
    ("About us", [("/about", "關於韻譯"), ("/store", "服務據點"), ("/services", "服務項目"), ("/privacy", "隱私權政策")]),
    ("Product", [("/products", "助聽器介紹"), ("/products/accessories", "助聽器配件"), ("/care", "使用小教室"),
                 ("/booking", "線上預約")]),
    ("Share", [("/column", "聽力專欄"), ("/share", "好評分享"), ("/faq", "常見問題")]),
    ("Service", [("/home-visit", "到府服務"), ("/hearing-test", "線上聽力測驗"), ("/hearing-sim", "聽損模擬"),
                 ("/practice/", "每日聽練"), ("/subsidy", "補助小幫手"), ("/line", "LINE 官方帳號"), ("/member", "會員登入")]),
]
# 金色選單方塊的內容（SITEMAP）在電腦版左邊有空間時改成展開在左側；這是左側每一項的圖示
SITEMAP_ICONS = {"/about": "info", "/store": "pin", "/services": "list", "/privacy": "file",
                 "/products": "aid", "/products/accessories": "battery", "/booking": "calendar",
                 "/column": "book", "/share": "star", "/faq": "help", "/care": "tool",
                 "/home-visit": "home", "/hearing-test": "headphones", "/hearing-sim": "waves", "/practice/": "volume",
                 "/subsidy": "clipboard", "/line": "chat", "/member": "user"}
# 首頁「挑選一副適合的助聽器」：型錄的外型／電源 → 產品頁篩選
PRODUCT_TYPES = {"ric": ("耳掛型", "RIC 受話器外置", {"ric"}), "bte": ("耳掛型", "BTE", {"bte"}),
                 "custom": ("耳內型", "訂製", {"ite", "itc", "cic"}), "rechargeable": ("充電式", "免換電池", set())}
MAX_FORM_BYTES = 16 * 1024
MEMBER_FORM_BYTES = 4096
AUDIO_EXT = {".m4a", ".mp3", ".wav", ".ogg", ".webm"}

# frame-src：好評分享點開評論時的 Google 官方嵌入地圖（/maps?output=embed 會轉到 /maps/embed）、Facebook 官方嵌入貼文
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "frame-src https://www.google.com/maps https://www.google.com/maps/embed https://www.facebook.com/plugins/post.php; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")


def line_share_url(text: str) -> str:
    """用 LINE 傳給家人：開啟 LINE 選擇聊天對象，帶入文字（由使用者自己決定要不要送出）。"""
    return "https://line.me/R/share?text=" + quote(text, safe="")


def _asset_version() -> str:
    files = [p for p in (ROOT / "static").rglob("*") if p.is_file()]
    return str(int(max((p.stat().st_mtime for p in files), default=0)))


templates = Jinja2Templates(directory=str(ROOT / "templates"))
templates.env.globals.update(
    site_name=settings.SITE["name"], nav=NAV, nav_cta=NAV_CTA, sitemap=SITEMAP, sitemap_icons=SITEMAP_ICONS, v=_asset_version(), qr=qr.svg, icon=icon,
    line_add_url=booking.line_add_url(), hour_label=booking.hour_label, brand_slug=content.brand_slug,
    line_share=line_share_url)


def page(request: Request, name: str, status: int = 200, **ctx: Any) -> HTMLResponse:
    ctx.setdefault("title", "")
    return templates.TemplateResponse(
        request, name, {"store": content.store(), "path": request.url.path, "base_url": content.site_url(),
                        "year": date.today().year, **ctx}, status_code=status)


def _type_match(series: Dict[str, Any], forms: set) -> bool:
    """產品篩選：有指定外型就看外型；沒有（充電式）就看有沒有充電款。"""
    return bool(series["form_keys"] & forms) if forms else series["rechargeable"]


def _form_ctx() -> Dict[str, Any]:
    """預約表單要的可選日期、時段、表單簽章。"""
    return {"dates": booking.booking_dates(), "hours": booking.HOURS, "token": booking.form_token()}


def create_app() -> FastAPI:
    app = FastAPI(title="y1web", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
    # 商品照（scripts/sync_images.py 產生，檔名是內容雜湊，可以長期快取）
    app.mount("/media", StaticFiles(directory=str(ROOT / "media"), check_dir=False), name="media")
    # 每日聽練（測試版）：practice/ 是自成一格的單頁 app（相對路徑、不用官網樣板），暫時掛在官網下；
    # 整個資料夾搬走就能獨立架站或包成手機 app。測試期間不給搜尋引擎收錄（middleware 加 noindex）
    app.mount("/practice", StaticFiles(directory=str(ROOT / "practice"), html=True), name="practice")

    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # 麥克風只開給「聽損模擬」（錄自己的一句話來聽；錄音只留在瀏覽器）
        mic = "(self)" if request.url.path == "/hearing-sim" else "()"
        resp.headers["Permissions-Policy"] = f"camera=(), microphone={mic}, geolocation=()"
        if request.url.path.startswith("/practice"):
            resp.headers["X-Robots-Tag"] = "noindex"
            # 每日聽練的檔名沒有版本號：程式每次都要回來確認（Cloudflare 沒收到 Cache-Control 會自己快取 4 小時，
            # 改版後客人會拿到舊的 sw.js／practice.js）；錄音很少換，快取一天
            audio = request.url.path.startswith("/practice/audio/")
            resp.headers["Cache-Control"] = "public, max-age=86400" if audio else "no-cache"
        if request.url.path.startswith(("/member", "/api/member")):
            # 會員頁：個人資料，不給快取（Cloudflare、共用電腦的瀏覽器）、不給搜尋引擎收錄
            resp.headers["Cache-Control"] = "no-store"
            resp.headers["X-Robots-Tag"] = "noindex"
        if request.url.path.startswith("/static/") and request.query_params.get("v"):
            resp.headers["Cache-Control"] = "public, max-age=604800"
        elif request.url.path.startswith("/media/") and resp.status_code == 200:
            resp.headers["Cache-Control"] = "public, max-age=2592000, immutable"
        return resp

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return page(request, "error.html", 404, title="找不到這個頁面",
                        message="這個網址不存在，可能已經移除或打錯字。")
        return page(request, "error.html", exc.status_code, title="暫時無法處理",
                    message=str(exc.detail))

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc: Exception):
        log.exception("頁面錯誤 %s", request.url.path)
        return page(request, "error.html", 500, title="系統暫時有問題",
                    message="很抱歉，網站暫時有問題。需要預約或諮詢，請直接來電。")

    # ── 頁面 ─────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return page(request, "index.html", brands=content.brands(), showcase=content.showcase(8),
                    topics=content.articles()[:3], shares=content.shares()[:3], types=PRODUCT_TYPES,
                    share_map=content.share_map())

    @app.get("/about", response_class=HTMLResponse)
    def about(request: Request):
        return page(request, "about.html", title="關於韻譯", sections=content.about_sections())

    @app.get("/column", response_class=HTMLResponse)
    def column(request: Request, cat: str = ""):
        cat = cat if cat in content.COLUMN_CATEGORIES else ""
        return page(request, "column.html", title="聽力專欄", items=content.articles(cat or None), cat=cat,
                    cats=content.COLUMN_CATEGORIES)

    @app.get("/column/{slug}", response_class=HTMLResponse)
    def column_item(request: Request, slug: str):
        a = content.article(slug)
        if not a:
            raise HTTPException(404)
        more = [x for x in content.articles(a["category"]) if x["slug"] != slug][:3]
        return page(request, "article.html", title=a["title"], a=a, more=more)

    @app.get("/share", response_class=HTMLResponse)
    def share(request: Request):
        items = content.shares()
        return page(request, "share.html", title="好評分享", quotes=[a for a in items if a["format"] == "quote"],
                    items=[a for a in items if a["format"] == "story"], links=content.share_links(),
                    share_map=content.share_map())

    @app.get("/share/{slug}", response_class=HTMLResponse)
    def share_item(request: Request, slug: str):
        a = content.share(slug)
        if not a:
            raise HTTPException(404)
        return page(request, "share_item.html", title=a["title"], a=a)

    @app.get("/hearing-test", response_class=HTMLResponse)
    def hearing_test(request: Request):
        return page(request, "hearing_test.html", title="線上聽力測驗｜聽力不求人", brands=content.brands())

    @app.get("/hearing-sim", response_class=HTMLResponse)
    def hearing_sim(request: Request):
        # 門市錄的說話聲：放在 static/audio/sim/talk.m4a（或 mp3／wav），有放才出現「家人說話」
        talk = next((f"/static/audio/sim/{f.name}" for f in sorted((ROOT / "static" / "audio" / "sim").glob("talk.*"))
                     if f.suffix in AUDIO_EXT), None)
        return page(request, "hearing_sim.html", title="聽損模擬｜聽聽看家人聽到的世界", talk_url=talk)

    @app.get("/practice", include_in_schema=False)
    def practice():
        return RedirectResponse("/practice/", status_code=301)

    @app.get("/hearing-test/noise", response_class=HTMLResponse)
    def noise_test(request: Request, pilot: str = ""):
        """噪音中聽數字（測試版）：還沒有判讀標準，不放選單、不給搜尋引擎收錄。?pilot=1＝門市校正模式。"""
        # 數字錄音：static/audio/din/0–9（wav／m4a／mp3），十個都有才開放；levels.json＝每個數字的音量校正（dB）
        din = ROOT / "static" / "audio" / "din"
        found = {f.stem: f"/static/audio/din/{f.name}" for f in sorted(din.glob("*"))
                 if f.suffix in AUDIO_EXT and f.stem.isdigit() and len(f.stem) == 1}
        digits = [found[str(d)] for d in range(10)] if len(found) == 10 else None
        try:
            levels = json.loads((din / "levels.json").read_text())       # JS 會再檢查（10 個、±6 dB 內）
        except (OSError, ValueError):
            levels = None
        resp = page(request, "noise_test.html", title="噪音中聽數字（測試版）", digits=digits, levels=levels,
                    pilot=pilot == "1")
        resp.headers["X-Robots-Tag"] = "noindex"
        return resp

    @app.get("/care", response_class=HTMLResponse)
    def care_list(request: Request, type: str = ""):
        tag = type if type in content.CARE_TYPES else ""
        return page(request, "care.html", title="使用小教室", items=content.care_guides(tag or None),
                    tag=tag, types=content.CARE_TYPES)

    @app.get("/care/qr", response_class=HTMLResponse)
    def care_qr(request: Request):
        """門市列印 QR 貼紙用（貼在交給客人的助聽器盒上）。"""
        resp = page(request, "care_qr.html", title="使用小教室 QR 貼紙", items=content.care_guides())
        resp.headers["X-Robots-Tag"] = "noindex"
        return resp

    @app.get("/care/{slug}", response_class=HTMLResponse)
    def care_item(request: Request, slug: str):
        a = content.care(slug)
        if not a:
            raise HTTPException(404)
        more = [x for x in content.care_guides() if x["slug"] != slug][:6]
        return page(request, "care_item.html", title=a["title"], a=a, more=more)

    @app.get("/subsidy", response_class=HTMLResponse)
    def subsidy(request: Request):
        items = content.subsidies()
        programs = [p for p in items if p["when"]]
        return page(request, "subsidy.html", title="補助小幫手", programs=programs,
                    general=[p for p in items if not p["when"]], conditions=content.SUBSIDY_CONDITIONS,
                    used={w for p in programs for w in p["when"]})      # 只問有用到的條件

    @app.get("/services", response_class=HTMLResponse)
    def services(request: Request):
        return page(request, "services.html", title="服務項目")

    @app.get("/store", response_class=HTMLResponse)
    def store(request: Request):
        return page(request, "store.html", title="服務據點")

    @app.get("/faq", response_class=HTMLResponse)
    def faq(request: Request):
        return page(request, "faq.html", title="常見問題", groups=content.faq())

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy(request: Request):
        return page(request, "privacy.html", title="隱私權政策")

    @app.get("/products", response_class=HTMLResponse)
    def products(request: Request, type: str = ""):
        t = PRODUCT_TYPES.get(type)
        groups: Dict[str, list] = {}
        for s in content.series():
            if t and not _type_match(s, t[2]):
                continue
            groups.setdefault(s["brand"], []).append(s)
        return page(request, "products.html", title="助聽器介紹", groups=groups, types=PRODUCT_TYPES,
                    type=type if t else "")

    @app.get("/products/accessories", response_class=HTMLResponse)
    def accessories(request: Request):
        return page(request, "accessories.html", title="配件", groups=content.accessories())

    @app.get("/products/{slug}", response_class=HTMLResponse)
    def product(request: Request, slug: str):
        s = content.series_by_slug(slug)
        if not s:
            raise HTTPException(404)
        return page(request, "product.html", title=s["title"], s=s, care=content.care_for_series(s))

    @app.get("/line")
    def line(src: str = ""):
        """加 LINE 好友的統一出口（之後在這裡統計各頁帶來的好友數）。"""
        return RedirectResponse(booking.line_add_url(), status_code=302)

    # ── 預約（門市／到府）─────────────────────────────────────

    def _form(request: Request, kind: str, status: int = 200, values=None, errors=None, notice=""):
        return page(request, "booking.html", status, title=booking.KINDS[kind], kind=kind,
                    values=values or {}, errors=errors or {}, notice=notice, **_form_ctx())

    def _preset(request: Request) -> Dict[str, str]:
        """/booking?for=family：從「幫家人預約」的按鈕進來，先選好「幫家人預約」。"""
        return {"for_whom": "family"} if request.query_params.get("for") == "family" else {}

    @app.get("/booking", response_class=HTMLResponse)
    def booking_form(request: Request):
        return _form(request, "store", values=_preset(request))

    @app.get("/home-visit", response_class=HTMLResponse)
    def home_form(request: Request):
        return _form(request, "home", values=_preset(request))

    async def _submit(request: Request, kind: str):
        if int(request.headers.get("content-length") or 0) > MAX_FORM_BYTES:
            raise HTTPException(413, "資料太長，請精簡後再送出。")
        raw = (await request.body())[:MAX_FORM_BYTES]
        form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
        if form.get("website"):                     # 隱藏欄位有值＝機器人；假裝成功，不寫入
            log.info("擋下機器人表單 ip=%s", request.client.host if request.client else "")
            return _form(request, kind, notice="已收到。")
        clean, errors = booking.validate(form, kind)
        if not booking.check_token(form.get("t", "")):
            errors["form"] = "頁面停留太久或送出太快，請確認內容後再按一次送出。"
        if errors:
            return _form(request, kind, 422, values=form, errors=errors)
        ip = request.client.host if request.client else ""
        try:
            code, expires = booking.submit(clean, ip)
        except booking.Rejected as e:
            return _form(request, kind, 429, values=form, errors={"form": str(e)})
        log.info("官網%s %s 電話末3碼 %s %s %02d:00 預約碼 %s", booking.KINDS[kind],
                 clean["contact_name"][:1] + "○", clean["phone"][-3:], clean["req_date"],
                 clean["req_hour"], code)
        d = date.fromisoformat(clean["req_date"])
        resp = page(request, "booking_done.html", title="預約已送出", kind=kind, clean=clean,
                    when=f"{booking.roc(d)} {booking.hour_label(clean['req_hour'])}",
                    code=code, expires=f"{booking.roc(expires.date())}",
                    line_code_url=booking.line_code_url(code))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.post("/booking", response_class=HTMLResponse)
    async def booking_submit(request: Request):
        return await _submit(request, "store")

    @app.post("/home-visit", response_class=HTMLResponse)
    async def home_submit(request: Request):
        return await _submit(request, "home")

    # ── 會員（docs/會員系統.md）：正式開放前不放選單；頁面一律 no-store、noindex（見 headers）──────

    def _ip(request: Request) -> str:
        return request.client.host if request.client else ""

    def _https(request: Request) -> bool:
        return request.url.scheme == "https"

    def _sess(request: Request):
        tok = request.cookies.get(member.COOKIE)
        return tok, (member.session_member(tok) if tok else None)

    async def _member_form(request: Request) -> Dict[str, str]:
        raw = (await request.body())[:MEMBER_FORM_BYTES]
        return {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}

    def _login_page(request: Request, status: int = 200, error: str = "", next_path: str = "/member",
                    notice: str = ""):
        return page(request, "member_login.html", status, title="會員登入", providers=member.providers(),
                    labels=member.LABEL, next=member.safe_next(next_path), error=error, notice=notice,
                    dev=member.dev_allowed(_ip(request)))

    def _account(request: Request, tok: str, m: Dict[str, Any], status: int = 200, ok: str = "", error: str = ""):
        acc = member.account(m["id"])
        if not acc:
            return RedirectResponse("/member/login", status_code=303)
        return page(request, "member_account.html", status, title="會員帳號", acc=acc, csrf=member.csrf_token(tok),
                    ok=ok, error=error, providers=member.providers(), labels=member.LABEL, roc=booking.roc,
                    dev=member.dev_allowed(_ip(request)))

    def _signed_in(request: Request, member_id: int, next_path: Any) -> Response:
        """登入成功：發新的登入憑證（舊的作廢，免得別人先塞一個憑證給您），轉回原本的頁面。"""
        old = request.cookies.get(member.COOKIE)
        if old:
            member.end_session(old)
        tok = member.create_session(member_id, request.headers.get("user-agent", ""))
        nxt = member.safe_next(next_path)
        resp = RedirectResponse(nxt + ("?ok=hello" if nxt == "/member" else ""), status_code=303)
        resp.set_cookie(member.COOKIE, tok, max_age=member.SESSION_DAYS * 86400, httponly=True,
                        secure=_https(request), samesite="lax", path="/")
        return resp

    def _done(code: str, provider: str = "") -> RedirectResponse:
        q = f"?ok={code}" + (f"&p={provider}" if provider in member.LABEL else "")
        return RedirectResponse("/member" + q, status_code=303)

    @app.get("/member/login", response_class=HTMLResponse)
    def member_login(request: Request, next: str = "/member", bye: str = ""):
        _, m = _sess(request)
        if m:
            return RedirectResponse(member.safe_next(next), status_code=303)
        return _login_page(request, next_path=next, notice="會員已刪除。謝謝您使用。" if bye == "1" else "")

    @app.get("/member/auth/{provider}/start")
    def member_start(request: Request, provider: str, next: str = "/member", mode: str = "login", t: str = ""):
        if provider not in member.LABEL or not member.enabled(provider):
            raise HTTPException(404)
        site = content.site_url()
        if request.url.netloc != urlparse(site).netloc:
            # 從區網 IP 或舊網址進來：先換到正式網址，state cookie 才會和回呼在同一個網域
            return RedirectResponse(f"{site}{request.url.path}?{request.url.query}", status_code=302)
        uid = None
        if mode == "bind":
            tok, m = _sess(request)
            if not m or not member.check_csrf(tok, t):
                return RedirectResponse("/member", status_code=303)
            uid = m["id"]
        else:
            mode = "login"
        try:
            cookie, params = member.make_state(provider, mode, next, uid)
        except member.LoginError as e:
            return _login_page(request, 503, str(e), next)
        resp = RedirectResponse(member.authorize_url(provider, params), status_code=302)
        resp.set_cookie(member.STATE_COOKIE, cookie, max_age=member.STATE_SECS, httponly=True,
                        secure=_https(request), samesite="lax", path=member.STATE_PATH)
        return resp

    @app.get("/member/auth/{provider}/callback")
    def member_callback(request: Request, provider: str, code: str = "", state: str = "", error: str = ""):
        if provider not in member.LABEL:
            raise HTTPException(404)
        try:
            st = member.read_state(request.cookies.get(member.STATE_COOKIE), provider, state)
        except member.LoginError:
            st = None

        def finish(resp: Response) -> Response:
            resp.delete_cookie(member.STATE_COOKIE, path=member.STATE_PATH)
            return resp

        nxt = st["nx"] if st else "/member"
        if not st:
            return finish(_login_page(request, 400, "登入逾時，或是從別的視窗開始的。請重新按一次登入。", nxt))
        if error or not code:      # 客人在 Google／LINE 的畫面按了取消
            tok, m = _sess(request)
            if m and st["m"] == "bind":
                return finish(_account(request, tok, m, error="沒有完成綁定。需要的話請再按一次。"))
            return finish(_login_page(request, 200, "沒有完成登入。需要的話請再按一次。", nxt))
        tok, m = _sess(request)
        try:
            prof = member.exchange(provider, code, st)
            if st["m"] == "bind":
                if not m or m["id"] != st.get("u"):
                    return finish(_login_page(request, 400, "請先登入，再到帳號設定綁定。"))
                return finish(_done(member.bind(m["id"], provider, prof), provider))
            return finish(_signed_in(request, member.login(provider, prof), nxt))
        except member.LoginError as e:
            if m and st["m"] == "bind":
                return finish(_account(request, tok, m, 400, error=str(e)))
            return finish(_login_page(request, 400, str(e), nxt))

    @app.get("/member", response_class=HTMLResponse)
    def member_home(request: Request, ok: str = "", p: str = ""):
        tok, m = _sess(request)
        if not m:
            return RedirectResponse("/member/login", status_code=303)
        return _account(request, tok, m, ok=member.MESSAGES.get(ok, "").format(p=member.LABEL.get(p, "")))

    @app.post("/member/dev")
    async def member_dev(request: Request):
        """開發用登入（config [member] dev_login，只限區網）：用 dev-／Utest_ 開頭的假帳號走一樣的登入、綁定、合併。"""
        if not member.dev_allowed(_ip(request)):
            raise HTTPException(404)
        form = await _member_form(request)
        provider = form.get("provider", "")
        tok, m = _sess(request)
        try:
            prof = member.dev_profile(provider, form.get("suffix", ""), form.get("name", ""))
            if form.get("mode") == "bind":
                if not m or not member.check_csrf(tok, form.get("t")):
                    return RedirectResponse("/member", status_code=303)
                return _done(member.bind(m["id"], provider, prof), provider)
            return _signed_in(request, member.login(provider, prof), form.get("next"))
        except member.LoginError as e:
            if m and form.get("mode") == "bind":
                return _account(request, tok, m, 400, error=str(e))
            return _login_page(request, 400, str(e))

    @app.post("/member/{action}")
    async def member_action(request: Request, action: str):
        if action not in {"logout", "logout-others", "unbind", "link-line", "unlink", "delete"}:
            raise HTTPException(404)
        form = await _member_form(request)
        tok, m = _sess(request)
        if not m:
            return RedirectResponse("/member/login", status_code=303)
        if not member.check_csrf(tok, form.get("t")):
            return _account(request, tok, m, 403, error="頁面放太久了，請再按一次。")
        try:
            if action == "logout" or action == "delete":
                if action == "delete":
                    if form.get("confirm") != "1":
                        return _account(request, tok, m, 400, error="要刪除會員，請先勾選「我確定要刪除」。")
                    member.delete_member(m["id"])
                else:
                    member.end_session(tok)
                resp = RedirectResponse("/member/login?bye=1" if action == "delete" else "/", status_code=303)
                resp.delete_cookie(member.COOKIE, path="/")
                return resp
            if action == "logout-others":
                member.end_other_sessions(m["id"], tok)
                return _done("others")
            if action == "unbind":
                return _done(member.unbind(m["id"], form.get("provider", "")), form.get("provider", ""))
            if action == "link-line":
                if form.get("consent") != "1":
                    return _account(request, tok, m, 400, error="要連結門市資料，請先勾選同意。")
                return _done(member.link_by_line(m["id"]))
            return _done(member.unlink_customer(m["id"]))
        except member.LoginError as e:
            return _account(request, tok, m, 400, error=str(e))

    @app.get("/api/member/me")
    def member_me(request: Request):
        """給每日聽練等程式問「現在有沒有登入」。"""
        _, m = _sess(request)
        return {"member": _me(m) if m else None}

    def _me(m: Dict[str, Any]) -> Dict[str, Any]:
        return {"name": m["display_name"], "uid": member.public_id(m["id"]), "linked": bool(m["customer_id"])}

    @app.get("/api/member/progress/{name}")
    def progress_get(request: Request, name: str):
        """每日聽練打開時：有登入就回伺服器上的紀錄（前端再和瀏覽器的合併）；沒登入回 member=null。"""
        if name not in progress.APPS:
            raise HTTPException(404)
        _, m = _sess(request)
        if not m:
            return {"member": None}
        return {"member": _me(m), "data": progress.load(m["id"], name)}

    @app.put("/api/member/progress/{name}")
    async def progress_put(request: Request, name: str):
        """練完一回：把瀏覽器的紀錄併進伺服器（只會變多、不會覆蓋），回傳合併後的結果。"""
        if name not in progress.APPS:
            raise HTTPException(404)
        _, m = _sess(request)
        if not m:
            return JSONResponse({"member": None}, status_code=401)
        # 只收本站頁面送的（會員憑證是 SameSite=Lax，跨站本來就帶不過來；這裡再擋一次）
        origin = request.headers.get("origin")
        if origin and origin != content.site_url() and urlparse(origin).netloc != request.url.netloc:
            return JSONResponse({"error": "origin"}, status_code=403)
        if int(request.headers.get("content-length") or 0) > progress.MAX_BYTES:
            return JSONResponse({"error": "too_large"}, status_code=413)
        raw = await request.body()
        if len(raw) > progress.MAX_BYTES:
            return JSONResponse({"error": "too_large"}, status_code=413)
        try:
            incoming = json.loads(raw)
        except ValueError:
            return JSONResponse({"error": "json"}, status_code=400)
        if not isinstance(incoming, dict):
            return JSONResponse({"error": "json"}, status_code=400)
        return {"member": _me(m), "data": progress.save_merged(m["id"], name, incoming)}

    # ── SEO 與監控 ────────────────────────────────────────────

    @app.get("/robots.txt", response_class=PlainTextResponse)
    def robots():
        base = content.site_url()
        return f"User-agent: *\nDisallow: /static/\nSitemap: {base}/sitemap.xml\n"

    @app.get("/sitemap.xml")
    def sitemap():
        base = content.site_url()
        # 每日聽練還是測試版（頁面 noindex），正式上線再放進 sitemap；會員頁是個人頁面，一直 noindex
        paths = ["/"] + sorted({p for _, links in SITEMAP for p, _ in links if p not in ("/line", "/practice/", "/member")})
        paths += [f"/care/{a['slug']}" for a in content.care_guides()]
        paths += [f"/products/{s['slug']}" for s in content.series()]
        paths += [f"/column/{a['slug']}" for a in content.articles()]
        paths += [f"/share/{a['slug']}" for a in content.shares() if a["format"] == "story"]   # 評論轉貼沒有自己的網頁
        urls = "".join(f"<url><loc>{base}{p}</loc></url>" for p in paths)
        xml = ('<?xml version="1.0" encoding="UTF-8"?>'
               f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>')
        return Response(xml, media_type="application/xml")

    @app.get("/healthz")
    def healthz():
        t = time.monotonic()
        try:
            db.rows("SELECT 1 AS ok")
            ok = True
        except Exception:
            ok = False
        return {"ok": ok, "db_ms": round((time.monotonic() - t) * 1000, 1)}

    return app


app = create_app()
