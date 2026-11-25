"""按鈕點擊數（app/clicks.py，改善清單 D2）與流量統計的安全設定。"""
import unittest
from unittest import mock

from app import clicks, main


class CleanTest(unittest.TestCase):
    def test_kinds_and_pages(self):
        self.assertEqual(clicks.clean("tel", "/"), ("tel", "/"))
        self.assertEqual(clicks.clean("line", "/column/hearing-aid-101"), ("line", "/column/hearing-aid-101"))
        self.assertEqual(clicks.clean(" booking ", "/store"), ("booking", "/store"))
        for k, p in (("map", "/"), ("", "/"), ("tel", ""), ("tel", "about"), ("tel", "//evil.com"),
                     ("tel", "/a?b=1"), ("tel", "/中文"), ("tel", "/" + "a" * 80), ("tel", "/<script>")):
            self.assertIsNone(clicks.clean(k, p), (k, p))


class AllowTest(unittest.TestCase):
    def setUp(self):
        clicks._seen.clear()

    def test_per_ip_hour(self):
        t = 1_000_000.0
        for _ in range(clicks.PER_IP_HOUR):
            self.assertTrue(clicks.allow("1.2.3.4", t))
        self.assertFalse(clicks.allow("1.2.3.4", t + 1))
        self.assertTrue(clicks.allow("5.6.7.8", t + 1))              # 別的 IP 不受影響
        self.assertTrue(clicks.allow("1.2.3.4", t + 3601))           # 一小時後又可以

    def test_memory_cap(self):
        t = 1_000_000.0
        with mock.patch.object(clicks, "_MAX_IPS", 3):
            for i in range(5):
                clicks.allow(f"10.0.0.{i}", t)
            clicks.allow("10.0.1.1", t + 7200)                        # 超過上限：一小時前的先清掉
        self.assertEqual(list(clicks._seen), ["10.0.1.1"])


class RecordTest(unittest.TestCase):
    def test_db_error_is_swallowed(self):
        with mock.patch.object(clicks.db, "connect", side_effect=OSError("down")):
            self.assertFalse(clicks.record("tel", "/"))


class CspTest(unittest.TestCase):
    def test_cloudflare_analytics_allowed(self):
        self.assertIn("script-src 'self' https://static.cloudflareinsights.com;", main.CSP)
        self.assertIn("connect-src 'self' https://cloudflareinsights.com;", main.CSP)
        self.assertIn("style-src 'self';", main.CSP)                  # 不為了裁切位置放寬 style（D1 改用 class）


if __name__ == "__main__":
    unittest.main()
