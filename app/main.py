"""
main.py — y1web 官網（韻譯助聽器 睿聲中正門市）。

公開網站：沒有登入、不放客戶資料。頁面由 Jinja2 在伺服器端產生（搜尋引擎讀得到、長輩手機不用跑大量 JS）。
預約表單寫進 crm1.booking_requests（只能 INSERT），店員在 y1crm「預約留言」頁處理。
"""
from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import booking, content, db, qr, settings
from app.icons import icon

log = logging.getLogger("y1web")
ROOT = settings.ROOT

# 頁首選單（版型比照 ear.com.tw，John 2026-10-21 指定）：中間五項＋右側藍字兩項＋金色方塊打開全部頁面
NAV = [("/products", "助聽器介紹"), ("/hearing-test", "聽力測驗"), ("/about", "關於韻譯"),
       ("/column", "聽力專欄"), ("/share", "好評分享")]
NAV_CTA = [("/booking", "線上預約"), ("/store", "服務據點")]
SITEMAP = [
    ("About us", [("/about", "關於韻譯"), ("/store", "服務據點"), ("/services", "服務項目"), ("/privacy", "隱私權政策")]),
    ("Product", [("/products", "助聽器介紹"), ("/products/accessories", "助聽器配件"), ("/booking", "線上預約")]),
    ("Share", [("/column", "聽力專欄"), ("/share", "好評分享"), ("/faq", "常見問題")]),
    ("Service", [("/home-visit", "到府服務"), ("/hearing-test", "線上聽力測驗"), ("/line", "LINE 官方帳號")]),
]
# 首頁「挑選一副適合的助聽器」：型錄的外型／電源 → 產品頁篩選
PRODUCT_TYPES = {"ric": ("耳掛型", "RIC 受話器外置", {"ric"}), "bte": ("耳掛型", "BTE", {"bte"}),
                 "custom": ("耳內型", "訂製", {"ite", "itc", "cic"}), "rechargeable": ("充電式", "免換電池", set())}
MAX_FORM_BYTES = 16 * 1024

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")


def _asset_version() -> str:
    files = [p for p in (ROOT / "static").rglob("*") if p.is_file()]
    return str(int(max((p.stat().st_mtime for p in files), default=0)))


templates = Jinja2Templates(directory=str(ROOT / "templates"))
templates.env.globals.update(
    site_name=settings.SITE["name"], nav=NAV, nav_cta=NAV_CTA, sitemap=SITEMAP, v=_asset_version(), qr=qr.svg, icon=icon,
    line_add_url=booking.line_add_url(), hour_label=booking.hour_label, brand_slug=content.brand_slug)


def page(request: Request, name: str, status: int = 200, **ctx: Any) -> HTMLResponse:
    ctx.setdefault("title", "")
    return templates.TemplateResponse(
        request, name, {"store": content.store(), "path": request.url.path,
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

    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
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
                    topics=content.articles()[:3], shares=content.shares()[:3], types=PRODUCT_TYPES)

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
        return page(request, "share.html", title="好評分享", items=content.shares())

    @app.get("/share/{slug}", response_class=HTMLResponse)
    def share_item(request: Request, slug: str):
        a = content.share(slug)
        if not a:
            raise HTTPException(404)
        return page(request, "share_item.html", title=a["title"], a=a)

    @app.get("/hearing-test", response_class=HTMLResponse)
    def hearing_test(request: Request):
        return page(request, "hearing_test.html", title="線上聽力測驗｜聽力不求人", brands=content.brands())

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
        return page(request, "product.html", title=s["title"], s=s)

    @app.get("/line")
    def line(src: str = ""):
        """加 LINE 好友的統一出口（之後在這裡統計各頁帶來的好友數）。"""
        return RedirectResponse(booking.line_add_url(), status_code=302)

    # ── 預約（門市／到府）─────────────────────────────────────

    def _form(request: Request, kind: str, status: int = 200, values=None, errors=None, notice=""):
        return page(request, "booking.html", status, title=booking.KINDS[kind], kind=kind,
                    values=values or {}, errors=errors or {}, notice=notice, **_form_ctx())

    @app.get("/booking", response_class=HTMLResponse)
    def booking_form(request: Request):
        return _form(request, "store")

    @app.get("/home-visit", response_class=HTMLResponse)
    def home_form(request: Request):
        return _form(request, "home")

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

    # ── SEO 與監控 ────────────────────────────────────────────

    @app.get("/robots.txt", response_class=PlainTextResponse)
    def robots():
        base = settings.SITE["base_url"].rstrip("/")
        return f"User-agent: *\nDisallow: /static/\nSitemap: {base}/sitemap.xml\n"

    @app.get("/sitemap.xml")
    def sitemap():
        base = settings.SITE["base_url"].rstrip("/")
        paths = ["/"] + sorted({p for _, links in SITEMAP for p, _ in links if p != "/line"})
        paths += [f"/products/{s['slug']}" for s in content.series()]
        paths += [f"/column/{a['slug']}" for a in content.articles()]
        paths += [f"/share/{a['slug']}" for a in content.shares()]
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
