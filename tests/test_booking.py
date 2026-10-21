"""官網預約：驗證、表單簽章、預約碼、次數限制、LINE 連結（不連資料庫）。

執行：cd /home/john/y1web && ./venv/bin/python -m unittest discover -s tests
"""
import unittest
from datetime import date, datetime
from urllib.parse import unquote

from app import booking, settings

TODAY = date(2026, 10, 21)          # 週三


def form(**kw):
    base = {"contact_name": "王先生", "phone": "0912-345-678", "req_date": "2026-10-22",
            "req_hour": "10", "consent": "1"}
    base.update(kw)
    return base


class Dates(unittest.TestCase):
    def test_same_rule_as_line(self):
        ds = booking.booking_dates(TODAY)
        self.assertEqual(len(ds), 12)                       # 14 天扣掉 2 個週日
        self.assertEqual(ds[0]["v"], "2026-10-22")          # 明天起
        self.assertEqual(ds[-1]["v"], "2026-11-04")         # 14 天
        self.assertNotIn("2026-10-25", [d["v"] for d in ds])  # 週日公休
        self.assertTrue(all(d["wd"] != "日" for d in ds))
        self.assertEqual(ds[0]["label"], "115/10/22（四）")
        self.assertEqual(booking.HOURS, list(range(9, 19)))


class Validate(unittest.TestCase):
    def test_ok_store(self):
        clean, err = booking.validate(form(), "store", TODAY)
        self.assertEqual(err, {})
        self.assertEqual(clean["phone"], "0912-345-678")
        self.assertEqual(clean["req_hour"], 10)
        self.assertIsNone(clean["address"])

    def test_sunday_rejected(self):
        _, err = booking.validate(form(req_date="2026-10-25"), "store", TODAY)
        self.assertIn("req_date", err)

    def test_home_needs_address(self):
        _, err = booking.validate(form(), "home", TODAY)
        self.assertIn("address", err)
        clean, err = booking.validate(form(address="台北市中正區羅斯福路一段1號"), "home", TODAY)
        self.assertEqual(err, {})
        self.assertEqual(clean["kind"], "home")

    def test_store_ignores_address(self):
        clean, _ = booking.validate(form(address="不該存"), "store", TODAY)
        self.assertIsNone(clean["address"])

    def test_rejects(self):
        cases = {
            "contact_name": form(contact_name="  "),
            "phone": form(phone="12345"),
            "req_date": form(req_date="2026-10-21"),         # 今天不行
            "req_hour": form(req_hour="8"),
            "consent": form(consent=""),
        }
        for field, f in cases.items():
            _, err = booking.validate(f, "store", TODAY)
            self.assertIn(field, err, field)
        _, err = booking.validate(form(req_date="2026-11-05"), "store", TODAY)   # 第 15 天
        self.assertIn("req_date", err)
        _, err = booking.validate(form(req_hour="abc"), "store", TODAY)
        self.assertIn("req_hour", err)
        _, err = booking.validate(form(), "shop", TODAY)
        self.assertIn("kind", err)

    def test_lengths_capped(self):
        clean, _ = booking.validate(form(contact_name="名" * 80, note="字" * 900,
                                         phone="09" + "1" * 40), "store", TODAY)
        self.assertEqual(len(clean["contact_name"]), 50)
        self.assertEqual(len(clean["note"]), 500)
        self.assertEqual(len(clean["phone"]), 20)


class Token(unittest.TestCase):
    def setUp(self):
        self._secret = settings.SITE.get("secret")
        settings.SITE["secret"] = "unit-test-secret"

    def tearDown(self):
        settings.SITE["secret"] = self._secret

    def test_timing(self):
        t = booking.form_token(now=1000)
        self.assertFalse(booking.check_token(t, now=1001))          # 太快
        self.assertTrue(booking.check_token(t, now=1005))
        self.assertFalse(booking.check_token(t, now=1000 + 3 * 3600))   # 太久

    def test_forged(self):
        t = booking.form_token(now=1000)
        ts, sig = t.split(".")
        self.assertFalse(booking.check_token(f"999.{sig}", now=1005))
        self.assertFalse(booking.check_token(f"{ts}.{'0' * len(sig)}", now=1005))
        self.assertFalse(booking.check_token("", now=1005))
        self.assertFalse(booking.check_token("abc", now=1005))


class Code(unittest.TestCase):
    def test_alphabet(self):
        for _ in range(200):
            c = booking.new_code()
            self.assertEqual(len(c), 6)
            self.assertFalse(set(c) & set("01OI"))

    def test_expires(self):
        now = datetime(2026, 10, 21, 15, 0)
        self.assertEqual(booking.code_expires("2026-10-23", now), datetime(2026, 10, 23, 23, 59, 59))
        self.assertEqual(booking.code_expires("2026-11-04", now), datetime(2026, 10, 28, 15, 0))

    def test_line_urls(self):
        self.assertEqual(booking.line_add_url(), "https://line.me/R/ti/p/%40021tpjrz")
        url = booking.line_code_url("K7Q2XM")
        self.assertTrue(url.startswith("https://line.me/R/oaMessage/%40021tpjrz/?"))
        self.assertEqual(unquote(url.split("?", 1)[1]), "預約碼 K7Q2XM")


class Limits(unittest.TestCase):
    def test_ip_window(self):
        lim = booking.Limiter()
        n, window = booking.IP_LIMIT
        for i in range(n):
            self.assertTrue(lim.allow("ip:x", booking.IP_LIMIT, 100 + i))
            lim.hit("ip:x", 100 + i)
        self.assertFalse(lim.allow("ip:x", booking.IP_LIMIT, 200))
        self.assertTrue(lim.allow("ip:x", booking.IP_LIMIT, 100 + window + n))   # 視窗過了

    def test_duplicate_returns_same_code(self):
        lim = booking.Limiter()
        key = ("0912345678", "2026-10-22", 10, "store")
        exp = datetime(2026, 10, 22, 23, 59, 59)
        lim.remember(key, "ABCDEF", exp, 100)
        self.assertEqual(lim.recent(key, 100 + 60), ("ABCDEF", exp))
        self.assertIsNone(lim.recent(key, 100 + booking.DUP_SECS + 1))


if __name__ == "__main__":
    unittest.main()
