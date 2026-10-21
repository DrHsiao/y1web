"""官網內容：Markdown 不放行 HTML、型錄整理成系列與機型（不連資料庫）。"""
import unittest
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


if __name__ == "__main__":
    unittest.main()
