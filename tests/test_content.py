"""官網內容：Markdown 不放行 HTML、型錄整理成系列與機型（不連資料庫）。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import content


class Markdown(unittest.TestCase):
    def test_html_escaped(self):
        out = content.md("<script>alert(1)</script> **粗體**")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertIn("<strong>粗體</strong>", out)

    def test_js_link_dropped(self):
        out = content.md("[點我](javascript:alert(1))")
        self.assertNotIn('href="javascript:', out)

    def test_image_credit_figure(self):
        # 翻譯文章的圖：Markdown 圖片標題＝出處，做成 <figure>，標題變成圖下方的小字
        out = content.md('![助聽器<零件>](/media/uploads/0123456789abcdef.webp "圖片來源：A&B")\n\n![](/media/uploads/a.webp)')
        self.assertIn('<figure class="fig"><img src="/media/uploads/0123456789abcdef.webp" alt="助聽器&lt;零件&gt;" '
                      'loading="lazy"><figcaption>圖片來源：A&amp;B</figcaption></figure>', out)
        self.assertIn('<p><img src="/media/uploads/a.webp" alt=""', out)                  # 沒標出處的圖照舊
        self.assertNotIn('title="', out)


def item(model, form="ric", bat="rechargeable", ch=12, spec="等級4 / 12頻道", brand="ReSound", cat="hearing_aid"):
    return {"brand": brand, "model_name": model, "spec": spec, "category": cat, "ha_form": form,
            "ha_channels": ch, "ha_battery": bat}


class Series(unittest.TestCase):
    def test_match_prefix(self):
        extra = {"brand": "ReSound", "match": ["Nexia"]}
        self.assertTrue(content._matches(item("Nexia 61 等級4"), extra))
        self.assertTrue(content._matches(item("Nexia CIC-NW 等級5"), extra))
        self.assertTrue(content._matches(item("Nexia"), extra))
        self.assertFalse(content._matches(item("NexiaX 等級4"), extra))
        self.assertFalse(content._matches(item("Nexia 61", brand="Phonak"), extra))
        self.assertFalse(content._matches(item("Nexia TV", cat="tv_streamer"), extra))

    def test_facts(self):
        f = content._facts([
            item("Nexia 61 等級4", "ric", "312", 12, "等級4 / 12頻道"),
            item("Nexia 61 等級5", "ric", "rechargeable", 12, "等級5 / 12頻道"),
            item("Nexia CIC-NW 等級4", "cic", "10", 12, "等級4 / 12頻道"),
        ])
        self.assertEqual(f["forms"], ["耳掛型（受話器外置 RIC）", "深耳道型"])
        self.assertEqual(f["batteries"][0], "充電式")         # 充電式排第一
        self.assertEqual(f["levels"], 2)
        self.assertEqual(f["count"], 3)

    def test_levels_per_brand(self):
        self.assertEqual(content._level(item("Savi 61 等級2", spec="等級2 / 12頻道")), "2")
        self.assertEqual(content._level(item("Audéo Lumity L70-R", spec="12頻道")), "70")
        self.assertEqual(content._level(item("Moment RIC R D 330", spec="Moment330 / 6頻道")), "330")
        self.assertEqual(content._level(item("Terra BTE-M Basic", spec="8頻道")), "Basic")   # Terra 分 Basic/Plus


def hi(pid, model, form="ric", bat="rechargeable", bt=1, brand="ReSound"):
    return {"id": pid, "brand": brand, "model_name": model, "spec": "", "category": "hearing_aid",
            "ha_form": form, "ha_channels": 12, "ha_battery": bat, "ha_bluetooth": bt}


class Models(unittest.TestCase):
    def test_base_model(self):
        self.assertEqual(content.base_model("Nexia 61 等級4"), "Nexia 61")
        self.assertEqual(content.base_model("Key 61 充電 等級3"), "Key 61 充電")
        self.assertEqual(content.base_model("Audéo Lumity L30-R"), "Audéo Lumity L-R")
        self.assertEqual(content.base_model("Virto Paradise P90-NW CIC"), "Virto Paradise P-NW CIC")
        self.assertEqual(content.base_model("Moment RIC R D 330"), "Moment RIC R D")
        self.assertEqual(content.base_model("Terra BTE-M Plus"), "Terra BTE-M")

    def test_group_by_model(self):
        items = [hi(1, "Nexia 61 等級4", bat="312"), hi(2, "Nexia 61 等級5", bat="312"),
                 hi(3, "Nexia CIC-NW 等級4", form="cic", bat="10", bt=0), hi(4, "Nexia 60S 等級4")]
        with mock.patch.object(content, "images", return_value={2: "/media/catalog/a.webp"}):
            ms = content.models(items)
        self.assertEqual([m["name"] for m in ms], ["Nexia 60S", "Nexia 61", "Nexia CIC-NW"])  # RIC 先、充電式先
        n61 = ms[1]
        self.assertEqual(n61["image"], "/media/catalog/a.webp")   # 同機型任一等級有照片就用
        self.assertEqual(n61["levels"], 2)
        self.assertEqual(n61["battery"], "312 號電池")
        self.assertIsNone(ms[0]["image"])
        self.assertFalse(ms[2]["bluetooth"])

    def test_showcase_rotates_brands(self):
        ss = [{"brand": b, "cover": c, "slug": f"{b}{i}"} for i, (b, c) in enumerate(
            [("ReSound", "x"), ("ReSound", "x"), ("ReSound", "x"), ("Phonak", "x"), ("Widex", None), ("Widex", "x")])]
        with mock.patch.object(content, "series", return_value=ss):
            out = content.showcase(4)
        self.assertEqual([s["brand"] for s in out], ["ReSound", "Phonak", "Widex", "ReSound"])
        self.assertTrue(all(s["cover"] for s in out))



def post(pid, slug, kind="article", **kw):
    from datetime import datetime
    base = {"id": pid, "slug": slug, "title": slug, "summary": None, "body_md": "**內文**", "category": "聽力保健",
            "extra": {}, "cover": None, "cover_url": None, "publish_at": datetime(2026, 10, pid), "sort": 0,
            "start_date": None, "end_date": None}
    base.update(kw)
    return base


class Cms(unittest.TestCase):
    def test_cover_url_only_safe_names(self):
        self.assertEqual(content.cover_url("0123456789abcdef.webp"), "/media/uploads/0123456789abcdef.webp")
        for bad in ("../x.webp", "0123456789abcdef.png", "x", None, ""):
            self.assertIsNone(content.cover_url(bad))

    def test_articles_newest_first_and_filter(self):
        rows = [post(1, "a"), post(5, "b", category="補助申請"), post(3, "c")]
        with mock.patch.object(content, "_posts", return_value=rows):
            self.assertEqual([a["slug"] for a in content.articles()], ["b", "c", "a"])
            self.assertEqual([a["slug"] for a in content.articles("補助申請")], ["b"])
            self.assertEqual(content.articles()[0]["date"], "115/10/05")
            self.assertIn("<strong>內文</strong>", content.article("c")["body_html"])
            self.assertIsNone(content.article("nope"))

    def test_share_needs_consent(self):
        rows = [post(1, "ok", "share", extra={"person": "王先生", "consent": True}),
                post(2, "no", "share", extra={"person": "陳小姐", "consent": False})]
        with mock.patch.object(content, "_posts", return_value=rows):
            self.assertEqual([a["slug"] for a in content.shares()], ["ok"])
            self.assertIsNone(content.share("no"))
            self.assertEqual(content.share("ok")["person"], "王先生")

    def test_share_quotes(self):
        q = {"format": "quote", "platform": "google", "rating": 5, "posted": "2026-10-16", "person": "測試***",
             "url": "https://maps.app.goo.gl/x", "consent": True}
        rows = [post(1, "story", "share", extra={"person": "王先生", "consent": True}),
                post(2, "q", "share", extra=q),
                post(3, "bad", "share", extra={**q, "url": "javascript:alert(1)", "rating": 9, "platform": "亂填"})]
        with mock.patch.object(content, "_posts", return_value=rows):
            items = {a["slug"]: a for a in content.shares()}
            self.assertEqual(items["story"]["format"], "story")              # 舊資料沒有 format＝心得文章
            self.assertEqual((items["q"]["format"], items["q"]["platform"], items["q"]["rating"]), ("quote", "Google 地圖", 5))
            self.assertNotIn("month", items["q"])                            # 評論日期不顯示
            self.assertEqual((items["bad"]["url"], items["bad"]["rating"], items["bad"]["platform"]), ("", None, ""))
            self.assertEqual((items["q"]["logo"], items["bad"]["logo"]), ("/static/img/google-g.svg", "/static/img/quote.svg"))
            self.assertIsNone(items["q"]["thumb"])                           # 沒有圖：平台標誌當背景
            self.assertIsNone(content.share("q"))                             # 評論轉貼沒有自己的網頁
            self.assertIsNotNone(content.share("story"))

    def test_share_image_thumb(self):
        name = "0123456789abcdef.webp"
        with tempfile.TemporaryDirectory() as d, mock.patch.object(content, "UPLOADS", Path(d)):
            self.assertEqual(content.thumb_url(name), "/media/uploads/" + name)        # 舊圖沒有卡片圖：用原圖
            (Path(d) / "c").mkdir()
            (Path(d) / "c" / name).write_bytes(b"x")
            self.assertEqual(content.thumb_url(name), "/media/uploads/c/" + name)
            self.assertIsNone(content.thumb_url("../" + name))
            rows = [post(1, "q", "share", cover=name, extra={"format": "quote", "platform": "facebook", "consent": True})]
            with mock.patch.object(content, "_posts", return_value=rows):
                q = content.shares()[0]
            self.assertEqual((q["image"], q["thumb"], q["platform_key"]),
                             ("/media/uploads/" + name, "/media/uploads/c/" + name, "facebook"))

    def test_share_map(self):
        src = "https://www.google.com/maps/embed?pb=!1m18!1m12!3m2!1szh-TW!2stw"
        with mock.patch.object(content, "setting", return_value={"map": src}):
            self.assertEqual(content.share_map(), src)
        with mock.patch.object(content, "store", return_value={"name": "韻譯 門市"}):
            for bad in ({"map": "https://evil.example/maps/embed?pb=!1"}, {"map": 'x" onload="alert(1)'}, None):
                with mock.patch.object(content, "setting", return_value=bad):
                    self.assertEqual(content.share_map(),
                                     "https://www.google.com/maps?output=embed&hl=zh-TW&q=%E9%9F%BB%E8%AD%AF%20%E9%96%80%E5%B8%82")
            with mock.patch.object(content, "setting", side_effect=RuntimeError):
                self.assertTrue(content.share_map().startswith("https://www.google.com/maps?output=embed&hl=zh-TW&q="))

    def test_share_links(self):
        with mock.patch.object(content, "setting", return_value={"google": "https://maps.app.goo.gl/x",
                                                                 "facebook": "javascript:x", "evil": "https://x"}):
            self.assertEqual(content.share_links(), [{"label": "Google 地圖", "url": "https://maps.app.goo.gl/x"}])
        with mock.patch.object(content, "setting", side_effect=RuntimeError):
            self.assertEqual(content.share_links(), [])


class Seo(unittest.TestCase):
    ROWS = [post(1, "battery", title="電池怎麼換", extra={"keywords": ["助聽器電池", "台北助聽器"]}),
            post(2, "tinnitus", title="耳鳴怎麼辦", body_md="先找耳鼻喉科", extra={"keywords": ["耳鳴", "台北助聽器"]}),
            post(3, "winter", title="冬天保養", body_md="天冷 Battery 比較不耐用",
                 extra={"keywords": ["助聽器電池", "冬天"]})]

    def test_search(self):
        with mock.patch.object(content, "_posts", return_value=self.ROWS):
            self.assertEqual([a["slug"] for a in content.articles(q="電池")], ["winter", "battery"])   # 標題＋關鍵字
            self.assertEqual([a["slug"] for a in content.articles(q="耳鼻喉")], ["tinnitus"])          # 內文
            self.assertEqual([a["slug"] for a in content.articles(q="battery")], ["winter"])           # 不分大小寫
            self.assertEqual([a["slug"] for a in content.articles(q="電池 冬天")], ["winter"])         # 每個詞都要有
            self.assertEqual(content.articles(q="沒有這個"), [])
            self.assertEqual(content.articles()[0]["keywords"], ["助聽器電池", "冬天"])

    def test_top_keywords(self):
        with mock.patch.object(content, "_posts", return_value=self.ROWS):
            self.assertEqual(content.top_keywords(), ["助聽器電池", "冬天", "耳鳴"])   # 地區詞不列

    def test_keywords_shape(self):
        self.assertEqual(content.keywords({"extra": {"keywords": "不是清單"}}), [])
        self.assertEqual(content.keywords({"extra": {}}), [])
        self.assertEqual(len(content.keywords({"extra": {"keywords": [str(i) for i in range(20)]}})), 10)

    def test_article_ld(self):
        from datetime import datetime
        a = {**post(4, "x", title="標題", summary="摘要", cover_url="/media/uploads/0123456789abcdef.webp",
                    updated_at=datetime(2026, 11, 8, 10, 0)), "keywords": ["耳鳴", "冬天"]}
        with mock.patch.object(content, "store", return_value={"name": "測試門市"}):
            d = content.article_ld(a, "https://y1ear.com", "/column/x")
        self.assertEqual((d["@type"], d["headline"], d["description"]), ("Article", "標題", "摘要"))
        self.assertEqual(d["keywords"], "耳鳴, 冬天")
        self.assertEqual(d["image"], "https://y1ear.com/media/uploads/0123456789abcdef.webp")
        self.assertEqual(d["mainEntityOfPage"], "https://y1ear.com/column/x")
        self.assertEqual(d["datePublished"], "2026-10-04T00:00:00")
        self.assertEqual(d["dateModified"], "2026-11-08T10:00:00")
        self.assertEqual(d["publisher"]["name"], "測試門市")

    def test_cover_attrs(self):
        self.assertEqual(str(content.cover_attrs({"extra": {"cover_src": "ai"}})), 'alt="示意插圖（AI 生成）" data-ai')
        self.assertEqual(str(content.cover_attrs({"extra": {"cover_ai": True}})), 'alt="示意插圖（AI 生成）" data-ai')
        self.assertEqual(str(content.cover_attrs({"extra": {"cover_src": "upload"}})), 'alt=""')
        self.assertEqual(str(content.cover_attrs({"extra": None})), 'alt=""')

    def test_stock_cover(self):
        c = {"title": "鋅空氣電池", "author": "Laserlicht", "source": "Wikimedia Commons", "license": "CC BY-SA 4.0",
             "credit_required": True}
        a = {"extra": {"cover_src": "stock", "cover_credit": c}}
        self.assertEqual(content.cover_credit(a), "圖：Laserlicht／Wikimedia Commons（CC BY-SA 4.0），經裁切調色")
        self.assertIn('alt="鋅空氣電池"', str(content.cover_attrs(a)))
        self.assertIn('title="圖：Laserlicht', str(content.cover_attrs(a)))
        b = {"extra": {"cover_src": "stock", "cover_credit": {"title": "耳鏡<檢查>", "source": "Pexels"}}}
        self.assertEqual(content.cover_credit(b), "")                   # Pexels 不用標
        self.assertEqual(str(content.cover_attrs(b)), 'alt="耳鏡&lt;檢查&gt;"')

    def test_source(self):
        # 翻譯／轉載的文章：文章上方的出處、結構化資料 isBasedOn、原文圖片封面的小字
        x = {"source": {"url": "https://www.example.com/a", "title": "How hearing aids work", "site": "Example",
                        "mode": "translate"}, "cover_src": "source", "cover_credit": {"source": "Example"}}
        a = {**post(4, "x", title="助聽器怎麼運作"), "extra": x}
        self.assertEqual(content.source_of(a), {"url": "https://www.example.com/a", "title": "How hearing aids work",
                                                "site": "Example", "verb": "本文翻譯整理自"})
        self.assertEqual(content.source_of({"extra": {"source": {**x["source"], "mode": "repost"}}})["verb"], "本文轉載自")
        for bad in (None, "x", {"url": "javascript:alert(1)"}, {"url": "https://a.b/\"><script>"}):
            self.assertIsNone(content.source_of({"extra": {"source": bad}}), bad)
        self.assertEqual(content.cover_credit(a), "圖片來源：Example")
        self.assertEqual(str(content.cover_attrs(a)), 'alt="" title="圖片來源：Example"')
        with mock.patch.object(content, "store", return_value={"name": "測試門市"}):
            d = content.article_ld(a, "https://y1ear.com", "/column/x")
            plain = content.article_ld({**a, "extra": {}}, "https://y1ear.com", "/column/x")
        self.assertEqual(d["isBasedOn"], "https://www.example.com/a")
        self.assertNotIn("isBasedOn", plain)

    def test_video(self):
        a = {**post(4, "x", title="聽力變差的警訊", summary="摘要"),
             "extra": {"video": {"file": "videos/0123456789abcdef.mp4", "poster": "videos/0123456789abcdef.jpg",
                                 "sec": 62.7, "at": "2026-11-08 16:37"}}}
        v = content.video_of(a)
        self.assertEqual((v["src"], v["poster"]), ("/media/uploads/videos/0123456789abcdef.mp4",
                                                   "/media/uploads/videos/0123456789abcdef.jpg"))
        with mock.patch.object(content, "store", return_value={"name": "測試門市"}):
            d = content.article_ld(a, "https://y1ear.com", "/column/x")
        self.assertEqual((d["video"]["@type"], d["video"]["duration"]), ("VideoObject", "PT1M3S"))
        self.assertEqual(d["video"]["uploadDate"], "2026-11-08T16:37:00+08:00")
        for bad in ({"file": "../etc/passwd"}, {"file": "videos/0123456789abcdef.jpg"}, "x", None):
            self.assertIsNone(content.video_of({"extra": {"video": bad}}))
        self.assertIsNone(v["youtube"])
        self.assertNotIn("embedUrl", d["video"])

    def test_youtube(self):
        base = {"file": "videos/0123456789abcdef.mp4", "sec": 10}
        yt = lambda **k: content.video_of({"extra": {"video": {**base, "youtube": k}}})["youtube"]
        self.assertEqual(yt(id="dQw4w9WgXcQ", ok=True), "dQw4w9WgXcQ")
        self.assertIsNone(yt(id="dQw4w9WgXcQ", ok=False))              # 查過有問題（私人、不能嵌入）→ 播本站的
        self.assertIsNone(yt(id="dQw4w9WgXcQ"))
        self.assertIsNone(yt(id="dQw4w9WgXc\"><", ok=True))
        self.assertIsNone(yt(id="short", ok=True))
        self.assertIsNone(content.video_of({"extra": {"video": {**base, "youtube": "dQw4w9WgXcQ"}}})["youtube"])
        a = {**post(4, "x", title="t"), "extra": {"video": {**base, "youtube": {"id": "dQw4w9WgXcQ", "ok": True}}}}
        with mock.patch.object(content, "store", return_value={"name": "測試門市"}):
            d = content.article_ld(a, "https://y1ear.com", "/column/x")
        self.assertEqual(d["video"]["embedUrl"], "https://www.youtube.com/embed/dQw4w9WgXcQ")
        self.assertEqual(d["video"]["contentUrl"], "https://y1ear.com/media/uploads/videos/0123456789abcdef.mp4")

    def test_same_as_drops_reviews_tab(self):
        links = {"google": "https://maps.app.goo.gl/abc", "facebook": "https://www.facebook.com/profile.php?id=1&sk=reviews"}
        with mock.patch.object(content, "setting", return_value=links):
            self.assertEqual(content.same_as(), ["https://maps.app.goo.gl/abc", "https://www.facebook.com/profile.php?id=1"])
        with mock.patch.object(content, "setting", return_value={"facebook": "https://www.facebook.com/shop/reviews/"}):
            self.assertEqual(content.same_as(), ["https://www.facebook.com/shop"])

    def test_social_links(self):
        yt = "https://www.youtube.com/channel/UCNS7Tf0XQSBN_kiJXjj9GRg"
        cfg = {"share_links": {"google": "https://maps.app.goo.gl/abc", "facebook": "https://www.facebook.com/p?id=1&sk=reviews"},
               "youtube": {"url": yt}}
        with mock.patch.object(content, "setting", side_effect=lambda k, d=None: cfg.get(k, d)):
            links = content.social_links()
            same = content.same_as()
        self.assertEqual([x["key"] for x in links], ["line", "facebook", "google", "youtube"])
        self.assertEqual([x["url"] for x in links], ["/line?src=social", "https://www.facebook.com/p?id=1",
                                                     "https://maps.app.goo.gl/abc", yt])
        self.assertTrue(all(x["icon"].startswith("/static/img/") for x in links))
        self.assertEqual(same[-1], yt)
        for bad in ("https://evil.example/channel/UCNS7Tf0XQSBN_kiJXjj9GRg", "javascript:alert(1)", yt + "/x", None):
            cfg["youtube"] = {"url": bad}
            with mock.patch.object(content, "setting", side_effect=lambda k, d=None: cfg.get(k, d)):
                self.assertNotIn("youtube", [x["key"] for x in content.social_links()])
        with mock.patch.object(content, "setting", side_effect=RuntimeError("db")):
            self.assertEqual([x["key"] for x in content.social_links()], ["line"])     # 資料庫掛了照樣有 LINE


class CareAndSubsidy(unittest.TestCase):
    def test_care_filter(self):
        rows = [post(1, "all", "care"), post(2, "bat", "care", extra={"types": ["battery", "亂填"]}),
                post(3, "ric", "care", extra={"types": ["ric", "rechargeable"]})]
        with mock.patch.object(content, "_posts", return_value=rows):
            self.assertEqual([c["slug"] for c in content.care_guides()], ["all", "bat", "ric"])
            self.assertEqual([c["slug"] for c in content.care_guides("battery")], ["all", "bat"])  # 沒勾＝都適用
            self.assertEqual(content.care_guides()[1]["types"], ["battery"])                       # 不認得的代碼丟掉
            self.assertEqual(content.care("ric")["type_labels"], ["耳掛型 RIC", "充電式"])
            self.assertIsNone(content.care("nope"))
            s = {"form_keys": {"cic"}, "models": [{"battery": "10 號電池"}]}
            self.assertEqual(content.series_care_tags(s), {"custom", "battery"})
            self.assertEqual([c["slug"] for c in content.care_for_series(s)], ["bat"])         # 通用的不列在產品頁
            s = {"form_keys": {"ric"}, "models": [{"battery": "充電式"}, {"battery": ""}]}
            self.assertEqual(content.series_care_tags(s), {"ric", "rechargeable"})

    def test_subsidy_conditions(self):
        rows = [post(1, "a", "subsidy", extra={"when": ["disability", "亂填"], "agency": "區公所"}),
                post(2, "g", "subsidy", extra={})]
        with mock.patch.object(content, "_posts", return_value=rows):
            items = content.subsidies()
            self.assertEqual(items[0]["when"], ["disability"])
            self.assertEqual(items[0]["when_labels"], ["領有身心障礙證明"])
            self.assertEqual(items[1]["when"], [])                   # 一般資訊
            self.assertIn("<strong>內文</strong>", items[0]["body_html"])


class SiteUrl(unittest.TestCase):
    def test_setting_first_then_config(self):
        with mock.patch.object(content, "setting", return_value={"base_url": "https://ap888.duckdns.org:5177/"}):
            self.assertEqual(content.site_url(), "https://ap888.duckdns.org:5177")
        for bad in (None, {}, {"base_url": "javascript:alert(1)"}, {"base_url": "https://a.b/path"}):
            with mock.patch.object(content, "setting", return_value=bad):
                self.assertEqual(content.site_url(), content.settings.SITE["base_url"].rstrip("/"))
        with mock.patch.object(content, "setting", side_effect=RuntimeError("db down")):
            self.assertEqual(content.site_url(), content.settings.SITE["base_url"].rstrip("/"))


class StoreLd(unittest.TestCase):
    def test_default_store(self):
        ld = content.store_ld(content.DEFAULT_STORE, "https://y1ear.com", ["助聽器"], [])
        self.assertEqual(ld["address"], {"@type": "PostalAddress", "addressCountry": "TW", "addressRegion": "台北市",
                                         "addressLocality": "中正區", "streetAddress": "羅斯福路一段105號",
                                         "postalCode": "100"})
        self.assertEqual(ld["geo"]["latitude"], content.STORE_GEO["latitude"])
        self.assertEqual(ld["openingHoursSpecification"][0]["dayOfWeek"][-1], "Saturday")   # 週日公休
        self.assertTrue(ld["logo"].startswith("https://y1ear.com/static/"))
        self.assertEqual((ld["name"], ld["alternateName"]), ("韻譯助聽器 睿聲中正門市", "睿聲助聽器 - 中正門市"))  # 別名＝地圖上的名稱

    def test_moved_store_drops_geo(self):
        ld = content.store_ld({**content.DEFAULT_STORE, "address": "新北市板橋區文化路一段1號"}, "https://y1ear.com", [], [])
        self.assertEqual((ld["address"]["addressRegion"], ld["address"]["addressLocality"]), ("新北市", "板橋區"))
        self.assertNotIn("geo", ld)                                  # 地址改了，舊座標不能用
        self.assertNotIn("postalCode", ld["address"])
        ld = content.store_ld({**content.DEFAULT_STORE, "address": "羅斯福路一段105號"}, "https://y1ear.com", [], [])
        self.assertEqual(ld["address"]["streetAddress"], "羅斯福路一段105號")


if __name__ == "__main__":
    unittest.main()
