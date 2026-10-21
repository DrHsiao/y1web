#!/usr/bin/env python3
"""
seed_content.py — 第一版官網內容：門市設定、常見問題、產品系列介紹（scripts/products.toml）、
關於韻譯／聽力專欄／好評分享的預設內容（scripts/cms_defaults.toml，只新增不覆蓋）。

可重跑：已經存在的內容不覆蓋（店員在後台改過的會保留）；要用這裡的版本蓋掉請加 --force。
y1web_app 帳號是唯讀的，所以和 setup_db.py 一樣用 y1crm 的管理者連線執行：
  sudo /home/john/y1web/venv/bin/python /home/john/y1web/scripts/seed_content.py [--force]
"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_db import _admin_conn  # noqa: E402

HERE = Path(__file__).resolve().parent
ACTOR = "官網種子"

STORE = {
    "name": "韻譯助聽器 睿聲中正門市",       # 寫在一行裡用；兩行顯示用 brand／branch
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

# (分類, 問題, 答案, 放進 LINE)。前四題沿用 LINE 機器人現有的常見問題文字。
FAQ = [
    ("使用與保養", "助聽器沒聲音或變小聲，怎麼辦？",
     "先更換新電池（充電式請確認已充飽），並檢查出聲孔有沒有耳垢。仍然沒有改善，請帶回門市檢查。", 1),
    ("使用與保養", "助聽器可以戴著洗澡、游泳嗎？",
     "不行，請先取下。不小心受潮時，請盡快擦乾，並帶到門市除濕保養。", 1),
    ("使用與保養", "剛開始戴，覺得聲音很吵、很不習慣，正常嗎？",
     "很常見。戴上助聽器後，會重新聽到很多原本聽不到的聲音，自己說話的聲音聽起來也會不一樣，需要一段時間適應。"
     "建議先在家裡安靜的環境戴，再慢慢增加時間與場合。覺得不舒服或某些聲音特別刺耳，請記下來，回門市時告訴我們調整。", 0),
    ("使用與保養", "助聽器發出嗶嗶聲或尖銳的叫聲？",
     "常見原因是沒有戴好、耳垢比較多，或耳塞尺寸不合。請先取下再重新戴好；如果經常發生，請回門市檢查。", 0),
    ("使用與保養", "平常要怎麼保養？",
     "- 每天用乾的軟布擦拭，不要用水或酒精\n- 晚上取下後放進乾燥盒；充電式放回充電座\n"
     "- 避免放在高溫、潮濕的地方，例如浴室、車內\n- 定期回門市清潔、除濕、更換耳塞與濾網", 0),
    ("電池與充電", "電池一顆可以用多久？",
     "依型號不同，大約 5–10 天。建議隨身攜帶備用電池。", 1),
    ("電池與充電", "充電式助聽器要怎麼充電？",
     "每天晚上取下後放回充電座即可，各品牌的指示燈意義不同，交機時門市人員會示範。外出旅行請記得帶充電座。", 0),
    ("預約與服務", "需要先預約嗎？",
     "建議先預約，到店不用等，我們也能先準備好需要的機型。可以[線上預約](/booking)、用 LINE 預約，或直接來電。", 0),
    ("預約與服務", "到府服務怎麼預約？",
     "請到[到府服務](/home-visit)頁面填表，或直接來電。門市人員會打電話和您確認時間與地址。", 0),
    ("預約與服務", "第一次去門市要準備什麼？",
     "如果有以前的聽力檢查報告，或正在使用的助聽器，請一起帶來。建議家人陪同，一起了解使用與保養方式。", 0),
    ("預約與服務", "保固和保養怎麼算？",
     "保固依購買機型的保固卡為準，門市提供基本清潔保養服務。", 1),
    ("預約與服務", "有政府補助嗎？",
     "符合資格的民眾可以申請助聽器補助。我們可以協助您了解申請資格與需要準備的文件，詳情請來電或到門市詢問。", 0),
    ("預約與服務", "助聽器多少錢？",
     "價格依品牌、外型與技術等級不同，也要看哪一種適合您的聽力狀況與生活需求。歡迎預約到門市，我們會依您的情況說明。", 0),
]


def main() -> None:
    force = "--force" in sys.argv
    con = _admin_conn()
    cur = con.cursor()
    cur.execute("USE y1web")
    verb = "REPLACE" if force else "INSERT IGNORE"

    cur.execute(f"{verb} INTO web_settings (k, v, updated_by) VALUES ('store', %s, %s)",
                (json.dumps(STORE, ensure_ascii=False), ACTOR))
    for i, (cat, q, a, line) in enumerate(FAQ):
        cur.execute(
            "INSERT INTO web_faq (category, question, answer_md, sort, status, show_on_line, use_for_ai, updated_by) "
            "VALUES (%s,%s,%s,%s,'published',%s,0,%s)"
            + (" ON DUPLICATE KEY UPDATE category=VALUES(category), answer_md=VALUES(answer_md), "
               "sort=VALUES(sort), show_on_line=VALUES(show_on_line)" if force else
               " ON DUPLICATE KEY UPDATE id=id"),
            (cat, q, a, (i + 1) * 10, line, ACTOR))

    products = HERE / "products.toml"
    if products.exists():
        with open(products, "rb") as f:
            items = tomllib.load(f).get("series", [])
        for i, s in enumerate(items):
            extra = {"brand": s["brand"], "match": s["match"], "sources": s.get("sources", [])}
            cur.execute(
                "INSERT INTO web_posts (kind, slug, title, summary, body_md, category, status, sort, extra, updated_by) "
                "VALUES ('product',%s,%s,%s,%s,%s,'published',%s,%s,%s)"
                + (" ON DUPLICATE KEY UPDATE title=VALUES(title), summary=VALUES(summary), body_md=VALUES(body_md), "
                   "category=VALUES(category), sort=VALUES(sort), extra=VALUES(extra)" if force else
                   " ON DUPLICATE KEY UPDATE id=id"),
                (s["slug"], s["title"], s.get("summary"), s.get("body", "").strip(), s["brand"],
                 (i + 1) * 10, json.dumps(extra, ensure_ascii=False), ACTOR))
        print(f"產品系列 {len(items)} 筆")
    cms = HERE / "cms_defaults.toml"
    if cms.exists():
        with open(cms, "rb") as f:
            data = tomllib.load(f)
        n = 0
        for kind in ("about", "article", "share"):
            for i, p in enumerate(data.get(kind, [])):
                extra = {"about": {"en": p.get("en", "")},
                         "article": {},
                         "share": {"person": p.get("person", ""), "model": p.get("model", ""),
                                   "hearing": p.get("hearing", ""), "consent": False}}[kind]
                publish_at = f"{p['date']} 09:00:00" if p.get("date") else None
                # 店員在後台改過的內容一律保留：這裡只新增，不加 --force 也不覆蓋
                cur.execute(
                    "INSERT INTO web_posts (kind, slug, title, summary, body_md, category, status, publish_at, sort, "
                    "extra, updated_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE id=id",
                    (kind, p["slug"], p["title"], p.get("summary"), p.get("body", "").strip(), p.get("category"),
                     "draft" if kind == "share" else p.get("status", "published"), publish_at,
                     p.get("sort", (i + 1) * 10), json.dumps(extra, ensure_ascii=False), ACTOR))
                n += cur.rowcount == 1
        print(f"關於韻譯／聽力專欄／好評分享：新增 {n} 筆")
    con.commit()
    cur.execute("SELECT (SELECT COUNT(*) FROM web_faq), (SELECT COUNT(*) FROM web_posts), (SELECT COUNT(*) FROM web_settings)")
    print("FAQ／文章與產品／設定：", cur.fetchone())
    con.close()


if __name__ == "__main__":
    main()
