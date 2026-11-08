"""
content.py — 官網內容：門市設定、常見問題、產品系列、聽力專欄、關於韻譯、好評分享、使用小教室、補助小幫手、
商品型錄（唯讀檢視 v_catalog）。

內容由店員在 y1crm「官網管理」編輯（M2），這裡只讀。快取 60 秒，改完最多一分鐘就生效。
型錄只有公開欄位：v_catalog 本身就不含售價、進價、供應商與備註。
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

from markdown_it import MarkdownIt

from app import db, settings

TTL = 60

# 和 crm1.codes 的顯示文字一致（ha_form／battery_type／product_category），官網用比較口語的寫法
FORM_LABEL = {"bte": "耳掛型", "ric": "耳掛型（受話器外置 RIC）", "ite": "耳內型", "itc": "耳道型",
              "cic": "深耳道型", "pocket": "口袋型"}
FORM_ORDER = ["ric", "bte", "ite", "itc", "cic", "pocket"]
BATTERY_LABEL = {"rechargeable": "充電式", "10": "10 號電池", "13": "13 號電池",
                 "312": "312 號電池", "675": "675 號電池"}
ACCESSORY_LABEL = OrderedDict([("charger", "充電座"), ("tv_streamer", "電視音訊串流器"),
                               ("remote_mic", "遠端麥克風"), ("dryer", "乾燥除濕"), ("other", "其他配件")])

# 預設門市資訊：資料庫還沒設定或暫時讀不到時使用。
# 名稱分兩行顯示（John 2026-10-21）：brand＝第一行、branch＝第二行；name 是寫在一行裡（標題、句子）用的
DEFAULT_STORE = {
    "name": "韻譯助聽器 睿聲中正門市",
    "brand": "韻譯助聽器",
    "branch": "睿聲中正門市",
    "address": "台北市中正區羅斯福路一段105號",
    "phone": "02-2341-5968",
    "hours": [{"days": "週一至週六", "time": "09:00–18:00"}, {"days": "週日", "time": "公休"}],
    "map_url": "https://www.google.com/maps/search/?api=1&query=%E5%8F%B0%E5%8C%97%E5%B8%82%E4%B8%AD%E6%AD%A3%E5%8D%80%E7%BE%85%E6%96%AF%E7%A6%8F%E8%B7%AF%E4%B8%80%E6%AE%B5105%E8%99%9F",
    # 交通：lines 是捷運線代碼（顯示該線顏色的圓點），text 照原文顯示
    "transit": [{"lines": ["red"], "text": "捷運紅線 - 中正紀念堂站 3號出口"},
                {"lines": ["orange", "green"], "text": "捷運橘線、綠線 - 古亭站 6號出口"}],
}

_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).enable("table")


def md(text: Optional[str]) -> str:
    """Markdown → HTML；原始 HTML 一律不轉（店員打的 <script> 只會變成文字）。"""
    return _md.render(text or "")


_cache: Dict[str, tuple] = {}
_lock = threading.Lock()


def _cached(key: str, loader: Callable[[], Any]) -> Any:
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < TTL:
            return hit[1]
    try:
        val = loader()
    except Exception:
        if hit:                 # 資料庫暫時連不上：先用舊資料撐著
            return hit[1]
        raise
    with _lock:
        _cache[key] = (now, val)
    return val


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _json(v: Any) -> Any:
    return json.loads(v) if isinstance(v, (str, bytes)) else v


# ── 門市設定 ─────────────────────────────────────────────────

def store() -> Dict[str, Any]:
    def load():
        r = db.rows("SELECT v FROM web_settings WHERE k='store'")
        return {**DEFAULT_STORE, **(_json(r[0]["v"]) if r else {})}
    try:
        return _cached("store", load)
    except Exception:
        return dict(DEFAULT_STORE)


def setting(key: str, default: Any = None) -> Any:
    r = _cached(f"setting:{key}", lambda: db.rows("SELECT v FROM web_settings WHERE k=%s", (key,)))
    return _json(r[0]["v"]) if r else default


# 官網對外網址（John 2026-10-22：存一個地方，換網域時一次改好）。
# 在 y1crm「官網管理 → 官網網址」修改，存在 web_settings k='site'；分享連結、QR 貼紙、sitemap、
# y1crm 的「在官網查看」與保養提醒訊息都讀這一個。config.toml 的 base_url 只在讀不到時備用。
SITE_URL_RE = re.compile(r"^https?://[a-z0-9.-]+(:\d{1,5})?$")


def site_url() -> str:
    try:
        v = str((setting("site") or {}).get("base_url") or "").rstrip("/")
    except Exception:
        v = ""
    return v if SITE_URL_RE.match(v) else settings.SITE["base_url"].rstrip("/")


# ── 常見問題 ─────────────────────────────────────────────────

def faq() -> "OrderedDict[str, List[Dict[str, Any]]]":
    """已發佈的常見問題，依分類分組（分類順序＝該分類最小的 sort）。"""
    rows = _cached("faq", lambda: db.rows(
        "SELECT id, category, question, answer_md FROM web_faq WHERE status='published' "
        "ORDER BY sort, id"))
    out: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for r in rows:
        out.setdefault(r["category"], []).append({**r, "answer_html": md(r["answer_md"])})
    return out


def faq_top(n: int = 4) -> List[Dict[str, Any]]:
    return [q for qs in faq().values() for q in qs][:n]


# ── 產品系列（web_posts kind=product）＋型錄 ─────────────────────────

def _posts(kind: str) -> List[Dict[str, Any]]:
    rows = _cached(f"posts:{kind}", lambda: db.rows(
        "SELECT id, slug, title, summary, body_md, category, extra, cover, publish_at, start_date, end_date "
        "FROM web_posts WHERE kind=%s AND status='published' "
        "AND (publish_at IS NULL OR publish_at <= NOW()) ORDER BY sort, id", (kind,)))
    return [{**r, "extra": _json(r["extra"]) or {}, "cover_url": cover_url(r["cover"])} for r in rows]


def cover_url(name: Optional[str]) -> Optional[str]:
    """官網管理上傳的圖（media/uploads）；檔名只允許 y1crm 產生的格式，避免路徑穿越。"""
    return f"/media/uploads/{name}" if name and re.fullmatch(r"[0-9a-f]{16}\.webp", name) else None


UPLOADS = Path(__file__).resolve().parent.parent / "media" / "uploads"


def thumb_url(name: Optional[str]) -> Optional[str]:
    """封面圖的卡片圖（y1crm 上傳封面時另做一張中等大小的放在 media/uploads/c/，同檔名）；舊圖沒有就用原圖。"""
    url = cover_url(name)
    return f"/media/uploads/c/{name}" if url and (UPLOADS / "c" / name).is_file() else url


def roc_date(d: Any) -> str:
    return f"{d.year - 1911}/{d.month:02d}/{d.day:02d}" if d else ""


# ── 關於韻譯、聽力專欄、好評分享（y1crm「官網管理」編輯）────────────

# 聽力專欄分類（y1crm 官網管理的下拉選單用同一份，見 api/web_admin.py）
COLUMN_CATEGORIES = ["助聽器知識", "聽力保健", "聽能復健", "補助申請", "生活樂活"]


def about_sections() -> List[Dict[str, Any]]:
    return [{**p, "body_html": md(p["body_md"]), "en": p["extra"].get("en", "")} for p in _posts("about")]


def articles(category: Optional[str] = None) -> List[Dict[str, Any]]:
    """已發佈的專欄文章，新的在前（沒填發佈日的照排序）。"""
    items = sorted(_posts("article"), key=lambda p: (p["publish_at"].timestamp() if p["publish_at"] else 0, p["id"]),
                   reverse=True)
    out = [{**p, "date": roc_date(p["publish_at"])} for p in items]
    return [p for p in out if p["category"] == category] if category else out


def article(slug: str) -> Optional[Dict[str, Any]]:
    for p in articles():
        if p["slug"] == slug:
            return {**p, "body_html": md(p["body_md"])}
    return None


# 好評分享的評論轉貼來自哪個平台；代碼、名稱和 y1crm api/web_admin.py 的 SHARE_PLATFORMS 相同，要一起改
SHARE_PLATFORMS = {"google": "Google 地圖", "facebook": "Facebook", "store": "門市心得卡", "other": "其他"}
SHARE_URL_RE = re.compile(r"^https://[^\s<>\"']+$")
# 評論卡片的來源平台標誌（標題旁的小圖示；沒有圖片的卡片另外當淡淡的背景）
SHARE_LOGOS = {"google": "/static/img/google-g.svg", "facebook": "/static/img/facebook.svg",
               "store": "/static/img/logo-mark.svg", "other": "/static/img/quote.svg"}
# 點開評論時視窗裡的 Google 地圖：Google 的評論頁不准別的網站嵌入（X-Frame-Options），只有官方嵌入地圖可以。
# y1crm 可貼「分享 → 嵌入地圖」的網址（和 y1crm api/web_admin.py MAP_EMBED_RE 同規則）；沒貼就用門市名稱搜尋
SHARE_MAP_RE = re.compile(r"^https://www\.google\.com/maps/embed\?pb=[A-Za-z0-9!._%:+*-]+$")


def roc_month(iso: str) -> str:
    """2026-10-16 → 115 年 10 月（評論日期是從「3 週前」推回去的，只顯示年月）。"""
    m = re.match(r"^(\d{4})-(\d{2})", iso or "")
    return f"{int(m.group(1)) - 1911} 年 {int(m.group(2))} 月" if m else ""


def shares() -> List[Dict[str, Any]]:
    """好評分享：只顯示已發佈、而且有勾「已取得當事人同意」的。
    format＝story 心得文章（有自己的網頁；舊資料沒有 format 也算）、quote 評論轉貼（只在列表顯示）。"""
    out = []
    for p in _posts("share"):
        x = p["extra"]
        if not x.get("consent"):
            continue
        item = {**p, "date": roc_date(p["publish_at"]), "person": x.get("person", ""),
                "format": "quote" if x.get("format") == "quote" else "story"}
        if item["format"] == "quote":
            url = str(x.get("url") or "")
            rating = x.get("rating")
            key = x.get("platform") if x.get("platform") in SHARE_PLATFORMS else "other"
            item.update(platform=SHARE_PLATFORMS.get(x.get("platform"), ""), month=roc_month(x.get("posted", "")),
                        url=url if SHARE_URL_RE.match(url) else "",
                        rating=rating if isinstance(rating, int) and 1 <= rating <= 5 else None,
                        platform_key=key, logo=SHARE_LOGOS[key],
                        image=cover_url(p.get("cover")), thumb=thumb_url(p.get("cover")))
        else:
            item.update(model=x.get("model", ""), hearing=x.get("hearing", ""))
        out.append(item)
    return sorted(out, key=lambda p: (p["publish_at"].timestamp() if p["publish_at"] else 0, p["id"]), reverse=True)


def share(slug: str) -> Optional[Dict[str, Any]]:
    """心得文章的網頁；評論轉貼沒有自己的網頁。"""
    for p in shares():
        if p["slug"] == slug and p["format"] == "story":
            return {**p, "body_html": md(p["body_md"])}
    return None


def share_links() -> List[Dict[str, str]]:
    """好評分享頁最下面「到 Google 地圖看全部評論」：y1crm 官網管理設定，存在 web_settings k='share_links'。"""
    try:
        v = setting("share_links") or {}
    except Exception:
        v = {}
    return [{"label": SHARE_PLATFORMS[k], "url": v[k]} for k in SHARE_PLATFORMS
            if isinstance(v.get(k), str) and SHARE_URL_RE.match(v[k])]


def share_map() -> str:
    """評論彈窗裡的 Google 地圖（門市位置＋Google 上的總評分）。"""
    try:
        v = (setting("share_links") or {}).get("map")
    except Exception:
        v = None
    if isinstance(v, str) and SHARE_MAP_RE.match(v):
        return v
    return "https://www.google.com/maps?output=embed&hl=zh-TW&q=" + quote(store()["name"])


# ── 使用小教室、補助小幫手（y1crm「官網管理」編輯）────────────────
# 代碼和 y1crm api/web_admin.py 的 CARE_TYPES／SUBSIDY_CONDITIONS 相同，要一起改

CARE_TYPES = OrderedDict([("battery", "電池式"), ("rechargeable", "充電式"), ("ric", "耳掛型 RIC"),
                          ("bte", "耳掛型 BTE"), ("custom", "耳內型")])
SUBSIDY_CONDITIONS = OrderedDict([("taipei", "設籍臺北市"), ("disability", "領有身心障礙證明"),
                                  ("age65", "65 歲以上"), ("low_income", "低收入戶或中低收入戶"),
                                  ("work", "聽力損失和工作有關（職業傷病）"), ("veteran", "榮民或榮眷")])
_CUSTOM_FORMS = {"ite", "itc", "cic"}


def care_guides(tag: Optional[str] = None) -> List[Dict[str, Any]]:
    """已發佈的使用小教室（依排序）。tag＝只要適用這種助聽器的（沒勾適用類型的＝每一種都適用）。"""
    out = [{**p, "types": [t for t in p["extra"].get("types", []) if t in CARE_TYPES],
            "video": p["extra"].get("video", "")} for p in _posts("care")]
    for p in out:
        p["type_labels"] = [CARE_TYPES[t] for t in p["types"]]
    return [p for p in out if not p["types"] or tag in p["types"]] if tag else out


def care(slug: str) -> Optional[Dict[str, Any]]:
    for p in care_guides():
        if p["slug"] == slug:
            return {**p, "body_html": md(p["body_md"])}
    return None


def series_care_tags(s: Dict[str, Any]) -> set:
    """產品系列 → 使用小教室的適用代碼（外型＋電源）。"""
    tags = {f for f in s["form_keys"] if f in ("ric", "bte")}
    if s["form_keys"] & _CUSTOM_FORMS:
        tags.add("custom")
    for m in s["models"]:
        tags.add("rechargeable" if m["battery"] == "充電式" else "battery" if m["battery"] else "")
    tags.discard("")
    return tags


def care_for_series(s: Dict[str, Any]) -> List[Dict[str, Any]]:
    """產品頁下方「使用小教室」：只列有指定適用類型、而且和這個系列有關的（通用教學在小教室首頁看）。"""
    tags = series_care_tags(s)
    return [p for p in care_guides() if p["types"] and tags & set(p["types"])]


def subsidies() -> List[Dict[str, Any]]:
    """已發佈的補助項目（依排序）。when＝適用條件（全部符合才算），空的＝一般資訊。"""
    out = []
    for p in _posts("subsidy"):
        when = [w for w in p["extra"].get("when", []) if w in SUBSIDY_CONDITIONS]
        out.append({**p, "when": when, "when_labels": [SUBSIDY_CONDITIONS[w] for w in when],
                    "agency": p["extra"].get("agency", ""), "link": p["extra"].get("link", ""),
                    "body_html": md(p["body_md"])})
    return out


def catalog() -> List[Dict[str, Any]]:
    return _cached("catalog", lambda: db.rows(
        "SELECT id, brand, model_name, spec, category, ha_form, ha_channels, ha_battery, "
        "ha_bluetooth, compatible_models FROM v_catalog ORDER BY brand, model_name"))


def _matches(item: Dict[str, Any], extra: Dict[str, Any]) -> bool:
    if item["brand"] != extra.get("brand") or item["category"] != "hearing_aid":
        return False
    name = item["model_name"]
    return any(name == p or name.startswith(p + " ") or name.startswith(p + "-")
               for p in extra.get("match") or [])


# 技術等級的寫法各品牌不同：ReSound／Interton／Audibel「等級4」、Phonak 型號裡的 L30/I90/P50、
# Phonak Terra 的 Basic/Plus、Widex 110–440
_LEVEL_RE = [re.compile(r"等級\s*(\d+)"), re.compile(r"\b[A-Z](30|50|70|90)\b"),
             re.compile(r"\b(110|220|330|440)\b"), re.compile(r"\b(Basic|Plus|Premium)\b")]
# 去掉等級，剩下的就是機型（同一機型各等級外觀相同，共用一張照片）
_BASE_RE = [(re.compile(r"\s*等級\s*\d+"), ""), (re.compile(r"\b([A-Z])(30|50|70|90)\b"), r"\1"),
            (re.compile(r"\s+(110|220|330|440)$"), ""), (re.compile(r"\s+(Basic|Plus|Premium)$"), "")]


def _level(item: Dict[str, Any]) -> Optional[str]:
    text = f"{item['model_name']} {item['spec'] or ''}"
    for rx in _LEVEL_RE:
        m = rx.search(text)
        if m:
            return m.group(1)
    return None


def images() -> Dict[int, str]:
    """商品 id → 官網照片網址（scripts/sync_images.py 從型錄照裁好的 media/catalog/*.webp）。"""
    def load():
        return {r["product_id"]: f"/media/catalog/{r['file']}"
                for r in db.rows("SELECT product_id, file FROM web_catalog_images")}
    try:
        return _cached("images", load)
    except Exception:           # 還沒跑過 sync_images.py：沒有照片也能顯示
        return {}


def base_model(name: str) -> str:
    for rx, rep in _BASE_RE:
        name = rx.sub(rep, name)
    return name.strip()


def models(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """型錄品項依機型分組：一張卡＝一個機型，列出外型、電源、有幾種等級（不列價格）。"""
    imgs = images()
    out: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    for i in items:
        m = out.setdefault(base_model(i["model_name"]), {"items": []})
        m["items"].append(i)
    cards = []
    for name, m in out.items():
        its = m["items"]
        form = next((i["ha_form"] for i in its if i["ha_form"]), None)
        bat = next((i["ha_battery"] for i in its if i["ha_battery"]), None)
        cards.append({
            "name": name,
            "image": next((imgs[i["id"]] for i in its if i["id"] in imgs), None),
            "form": FORM_LABEL.get(form, form) if form else "",
            "form_key": form or "",
            "battery": BATTERY_LABEL.get(bat, bat) if bat else "",
            "bluetooth": any(i["ha_bluetooth"] for i in its),
            "levels": len({lv for lv in (_level(i) for i in its) if lv}),
        })
    cards.sort(key=lambda c: (FORM_ORDER.index(c["form_key"]) if c["form_key"] in FORM_ORDER else 99,
                              c["battery"] != "充電式", c["name"]))
    return cards


def _facts(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """型錄整理成「有哪些外型、電源、技術等級」，不列單品（單品差在等級和價格）。"""
    forms = sorted({i["ha_form"] for i in items if i["ha_form"]},
                   key=lambda f: FORM_ORDER.index(f) if f in FORM_ORDER else 99)
    bats = sorted({i["ha_battery"] for i in items if i["ha_battery"]},
                  key=lambda b: (b != "rechargeable", b))
    chans = sorted({i["ha_channels"] for i in items if i["ha_channels"]})
    levels = {lv for lv in (_level(i) for i in items) if lv}
    return {
        "forms": [FORM_LABEL.get(f, f) for f in forms],
        "batteries": [BATTERY_LABEL.get(b, b) for b in bats],
        "channels": f"{chans[0]}–{chans[-1]}" if len(chans) > 1 else (str(chans[0]) if chans else ""),
        "levels": len(levels),
        "count": len(items),
    }


def series() -> List[Dict[str, Any]]:
    """產品系列卡片：介紹文字（web_posts）＋從型錄算出的外型／電源。型錄裡已經沒有的系列不顯示。"""
    cat = catalog()
    out = []
    for p in _posts("product"):
        items = [i for i in cat if _matches(i, p["extra"])]
        if items:
            ms = models(items)
            out.append({**p, "brand": p["extra"].get("brand", ""), "facts": _facts(items),
                        "models": ms, "cover": next((m["image"] for m in ms if m["image"]), None),
                        "form_keys": {i["ha_form"] for i in items if i["ha_form"]},
                        "rechargeable": any(i["ha_battery"] == "rechargeable" for i in items)})
    return out


def series_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    for s in series():
        if s["slug"] == slug:
            return {**s, "body_html": md(s["body_md"])}
    return None


def brands() -> List[str]:
    seen: List[str] = []
    for s in series():
        if s["brand"] not in seen:
            seen.append(s["brand"])
    return seen


def showcase(n: int = 8) -> List[Dict[str, Any]]:
    """首頁展示用：有照片的系列，先每個品牌輪流挑一個，避免整排都是同一品牌。"""
    by_brand: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for s in series():
        if s["cover"]:
            by_brand.setdefault(s["brand"], []).append(s)
    out: List[Dict[str, Any]] = []
    while len(out) < n and any(by_brand.values()):
        for items in by_brand.values():
            if items and len(out) < n:
                out.append(items.pop(0))
    return out


def brand_slug(brand: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", brand.lower()).strip("-")


def accessories() -> "OrderedDict[str, OrderedDict[str, List[Dict[str, Any]]]]":
    """配件：分類 → 品牌 → [{name, image}]。"""
    imgs = images()
    out: "OrderedDict[str, OrderedDict[str, List[Dict[str, Any]]]]" = OrderedDict()
    for key, label in ACCESSORY_LABEL.items():
        by_brand: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
        for i in catalog():
            if i["category"] == key:
                by_brand.setdefault(i["brand"], []).append(
                    {"name": i["model_name"], "image": imgs.get(i["id"])})
        if by_brand:
            out[label] = by_brand
    return out
