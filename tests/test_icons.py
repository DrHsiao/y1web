"""模板用到的圖示都要存在（刪改 app/icons.py 時避免頁面出錯）。"""
import re
import unittest
from pathlib import Path

from app import icons

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


class Icons(unittest.TestCase):
    def test_used_icons_exist(self):
        used = set()
        for p in TEMPLATES.glob("*.html"):
            used |= set(re.findall(r'icon\("([a-z-]+)"', p.read_text("utf-8")))
        self.assertIn("moon", used)                       # 深淺色開關
        self.assertIn("text-size", used)                  # 字放大開關
        self.assertEqual(sorted(used - set(icons._PATHS)), [])

    def test_side_menu_icons_exist(self):
        from app import main
        self.assertEqual(sorted(set(main.SITEMAP_ICONS.values()) - set(icons._PATHS)), [])
        hrefs = {h for _, links in main.SITEMAP for h, _ in links}
        self.assertEqual(sorted(hrefs - set(main.SITEMAP_ICONS)), [])   # 左側每一項都有圖示
        about = {h for h, _, _ in main.ABOUT_LINKS}
        self.assertEqual(sorted(about - set(main.SITEMAP_ICONS)), [])   # 關於我們頁的按鈕也用同一組圖示

    def test_about_pages_still_reachable(self):
        """左側選單 About us 在最下面、全部展開（11-25）；頁尾列的頁面跟左側一樣；最新消息、補助小幫手在頁首選單。"""
        from app import main
        side = {h for _, links in main.SITEMAP for h, _ in links}
        footer = {h for _, links in main.FOOTER_SITEMAP for h, _ in links}
        cols = [g for col in main.SIDE_COLUMNS for g in col]          # 電腦版左側兩欄：同樣四組、一組不少
        self.assertEqual(sorted(cols), sorted(main.SITEMAP))
        self.assertEqual([[g for g, _ in col] for col in main.SIDE_COLUMNS], [["Product", "About us"], ["Share", "Service"]])
        group, links = main.SITEMAP[-1]
        self.assertEqual(group, "About us")
        self.assertEqual([h for h, _ in links], ["/about"] + [h for h, _, _ in main.ABOUT_LINKS])
        self.assertEqual(footer, side)
        self.assertIn(("/news", "最新消息"), main.NAV)
        self.assertIn(("/subsidy", "補助小幫手"), main.NAV)

    def test_markup(self):
        svg = str(icons.icon("sun", "x"))
        self.assertTrue(svg.startswith('<svg class="ico x"'))
        self.assertIn('aria-hidden="true"', svg)


if __name__ == "__main__":
    unittest.main()
