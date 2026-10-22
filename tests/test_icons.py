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

    def test_markup(self):
        svg = str(icons.icon("sun", "x"))
        self.assertTrue(svg.startswith('<svg class="ico x"'))
        self.assertIn('aria-hidden="true"', svg)


if __name__ == "__main__":
    unittest.main()
