"""會員：登入流程的純函式（state cookie、授權網址、id_token 檢查、轉址防護、開發用登入）和練習紀錄合併（不連資料庫）。

資料庫那一段（登入、綁定、合併、連結客戶）另外用本機測試伺服器實測，見 docs/會員系統.md 第 7 節。
執行：cd /home/john/y1web && ./venv/bin/python -m unittest discover -s tests
"""
import json
import random
import shutil
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

from app import member, progress, settings

PRACTICE = Path(__file__).resolve().parent.parent / "practice" / "practice.js"
NODE = shutil.which("node") or shutil.which("nodejs")
CFG = {"google_client_id": "123-abc.apps.googleusercontent.com", "google_client_secret": "GOCSPX-x",
       "line_channel_id": "1234567890", "line_channel_secret": "f" * 32, "dev_login": False}


class Base(unittest.TestCase):
    def setUp(self):
        p = [mock.patch.dict(settings.MEMBER, CFG), mock.patch.dict(settings.SITE, {"secret": "test-secret"}),
             mock.patch("app.content.site_url", return_value="https://y1ear.com")]
        for x in p:
            x.start()
            self.addCleanup(x.stop)


class SafeNext(unittest.TestCase):
    def test_only_own_paths(self):
        self.assertEqual(member.safe_next("/practice/"), "/practice/")
        self.assertEqual(member.safe_next("/member?x=1"), "/member?x=1")
        for bad in ["https://evil.com", "//evil.com", "/\\evil.com", "/a\\b", "evil", "", None,
                    "/a b", "/x\n", "/" + "a" * 400]:
            self.assertEqual(member.safe_next(bad), "/member", bad)

    def test_mask_email(self):
        self.assertEqual(member.mask_email("abcdef@gmail.com"), "ab***@gmail.com")
        self.assertEqual(member.mask_email(None), "")
        self.assertEqual(member.mask_email("bad"), "")


class State(Base):
    def test_round_trip(self):
        cookie, p = member.make_state("google", "login", "/practice/", now=1000)
        st = member.read_state(cookie, "google", p["state"], now=1100)
        self.assertEqual((st["p"], st["m"], st["nx"]), ("google", "login", "/practice/"))
        self.assertEqual(st["n"], p["nonce"])
        # PKCE：challenge＝verifier 的 SHA-256（S256）
        import base64, hashlib
        want = base64.urlsafe_b64encode(hashlib.sha256(st["cv"].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(p["challenge"], want)
        self.assertGreaterEqual(len(st["cv"]), 43)       # RFC 7636：43–128 字

    def test_rejects(self):
        cookie, p = member.make_state("line", "bind", "/member", member_id=7, now=1000)
        self.assertEqual(member.read_state(cookie, "line", p["state"], now=1000)["u"], 7)
        body, sig = cookie.split(".")
        cases = {
            "過期": (cookie, "line", p["state"], 1000 + member.STATE_SECS + 1),
            "時間倒流": (cookie, "line", p["state"], 900),
            "換登入方式": (cookie, "google", p["state"], 1000),
            "state 不同": (cookie, "line", p["state"] + "x", 1000),
            "state 空白": (cookie, "line", "", 1000),
            "簽章被改": (body + "." + sig[:-2] + "AA", "line", p["state"], 1000),
            "內容被改": (body[:-2] + "AA." + sig, "line", p["state"], 1000),
            "沒有 cookie": (None, "line", p["state"], 1000),
            "亂碼": ("abc", "line", p["state"], 1000),
        }
        for name, (c, prov, s, now) in cases.items():
            self.assertIsNone(member.read_state(c, prov, s, now=now), name)

    def test_other_secret(self):
        cookie, p = member.make_state("google", "login", "/member", now=1000)
        with mock.patch.dict(settings.SITE, {"secret": "another"}):
            self.assertIsNone(member.read_state(cookie, "google", p["state"], now=1000))

    def test_no_secret(self):
        with mock.patch.dict(settings.SITE, {"secret": "CHANGE_ME"}):
            with self.assertRaises(member.LoginError):
                member.make_state("google", "login", "/member")


class AuthorizeUrl(Base):
    def test_google(self):
        u = urlparse(member.authorize_url("google", {"state": "S", "nonce": "N", "challenge": "C"}))
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.assertEqual(u.netloc, "accounts.google.com")
        self.assertEqual(q["redirect_uri"], "https://y1ear.com/member/auth/google/callback")
        self.assertEqual(q["client_id"], CFG["google_client_id"])
        self.assertEqual(q["scope"], "openid email profile")
        self.assertEqual((q["state"], q["nonce"], q["code_challenge"], q["code_challenge_method"]),
                         ("S", "N", "C", "S256"))

    def test_line(self):
        u = urlparse(member.authorize_url("line", {"state": "S", "nonce": "N", "challenge": "C"}))
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.assertEqual(u.netloc, "access.line.me")
        self.assertEqual(q["redirect_uri"], "https://y1ear.com/member/auth/line/callback")
        self.assertEqual(q["scope"], "openid profile")           # 不要 email（要另外申請、用不到）
        self.assertEqual(q["bot_prompt"], "normal")

    def test_enabled(self):
        self.assertEqual(member.providers(), {"google": True, "line": True})
        with mock.patch.dict(settings.MEMBER, {"line_channel_secret": ""}):
            self.assertFalse(member.enabled("line"))
        self.assertFalse(member.enabled("facebook"))


class Claims(Base):
    def good(self, provider="google", **kw):
        c = {"iss": "https://accounts.google.com" if provider == "google" else "https://access.line.me",
             "aud": CFG["google_client_id"] if provider == "google" else CFG["line_channel_id"],
             "exp": 2000, "nonce": "N", "sub": "123"}
        c.update(kw)
        return c

    def test_ok(self):
        member.check_claims(self.good(), "google", "N", now=1000)
        member.check_claims(self.good(iss="accounts.google.com"), "google", "N", now=1000)
        member.check_claims(self.good("line"), "line", "N", now=1000)

    def test_bad(self):
        cases = {"iss": self.good(iss="https://evil.com"), "aud": self.good(aud="other"),
                 "過期": self.good(exp=900), "exp 不是數字": self.good(exp="2000"), "nonce": self.good(nonce="X"),
                 "沒 nonce": self.good(nonce=None), "沒 sub": self.good(sub=""),
                 "LINE 的 iss 拿去 Google": self.good(iss="https://access.line.me")}
        for name, c in cases.items():
            with self.assertRaises(member.LoginError, msg=name):
                member.check_claims(c, "google", "N", now=1000)
        with self.assertRaises(member.LoginError):
            member.check_claims(self.good("line"), "line", "", now=1000)      # 我們這邊的 nonce 空白也不行

    def test_jwt_payload(self):
        import base64
        body = base64.urlsafe_b64encode(json.dumps({"sub": "1"}).encode()).rstrip(b"=").decode()
        self.assertEqual(member.jwt_payload(f"h.{body}.s"), {"sub": "1"})
        for bad in ["", "abc", "a.!!!.c"]:
            with self.assertRaises(member.LoginError):
                member.jwt_payload(bad)

    def test_exchange_google(self):
        import base64
        claims = self.good(exp=time.time() + 600, email="a@b.com", email_verified=True, name="王小明")
        idt = "h." + base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode() + ".s"
        calls = []
        with mock.patch.object(member, "_post", side_effect=lambda url, data: calls.append((url, data)) or {"id_token": idt}):
            prof = member.exchange("google", "CODE", {"cv": "VERIFIER", "n": "N"})
        self.assertEqual(prof, {"subject": "123", "name": "王小明", "email": "a@b.com"})
        url, data = calls[0]
        self.assertEqual(url, member.GOOGLE_TOKEN)
        self.assertEqual((data["code"], data["code_verifier"], data["grant_type"]),
                         ("CODE", "VERIFIER", "authorization_code"))
        self.assertEqual(data["redirect_uri"], "https://y1ear.com/member/auth/google/callback")

    def test_exchange_google_unverified_email(self):
        import base64
        claims = self.good(exp=time.time() + 600, email="a@b.com", email_verified=False)
        idt = "h." + base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode() + ".s"
        with mock.patch.object(member, "_post", return_value={"id_token": idt}):
            self.assertIsNone(member.exchange("google", "C", {"cv": "V", "n": "N"})["email"])

    def test_exchange_line_uses_verify(self):
        claims = self.good("line", exp=time.time() + 600, name="阿明")
        calls = []

        def post(url, data):
            calls.append((url, data))
            return {"id_token": "x.y.z"} if url == member.LINE_TOKEN else claims
        with mock.patch.object(member, "_post", side_effect=post):
            prof = member.exchange("line", "C", {"cv": "V", "n": "N"})
        self.assertEqual(prof, {"subject": "123", "name": "阿明", "email": None})
        self.assertEqual(calls[1][0], member.LINE_VERIFY)
        self.assertEqual((calls[1][1]["client_id"], calls[1][1]["nonce"]), (CFG["line_channel_id"], "N"))

    def test_exchange_no_id_token(self):
        with mock.patch.object(member, "_post", return_value={}):
            with self.assertRaises(member.LoginError):
                member.exchange("google", "C", {"cv": "V", "n": "N"})


class Dev(Base):
    def test_profile_prefix(self):
        self.assertEqual(member.dev_profile("google", "a1", "")["subject"], "dev-a1")
        p = member.dev_profile("line", "x<y>z 9", "")
        self.assertEqual(p["subject"], "Utest_xyz9")                 # 只留英數字，不可能是真的 LINE userId
        self.assertTrue(p["name"].startswith("測試請忽略"))
        for prov, s in [("google", ""), ("google", "！！"), ("facebook", "a")]:
            with self.assertRaises(member.LoginError):
                member.dev_profile(prov, s, "")

    def test_allowed(self):
        self.assertFalse(member.dev_allowed("127.0.0.1"))            # config 沒開
        with mock.patch.dict(settings.MEMBER, {"dev_login": True}), \
                mock.patch.dict(settings.SERVER, {"forwarded_allow_ips": "192.168.1.26, 173.245.48.0/20"}):
            self.assertTrue(member.dev_allowed("127.0.0.1"))
            self.assertTrue(member.dev_allowed("192.168.1.50"))
            self.assertFalse(member.dev_allowed("192.168.1.26"))     # .26 轉進來卻沒認出客人＝不算區網
            self.assertFalse(member.dev_allowed("1.2.3.4"))
            self.assertFalse(member.dev_allowed(""))
            self.assertFalse(member.dev_allowed("testclient"))

    def test_csrf(self):
        t = member.csrf_token("tok")
        self.assertTrue(member.check_csrf("tok", t))
        self.assertFalse(member.check_csrf("tok2", t))
        self.assertFalse(member.check_csrf(None, t))
        self.assertFalse(member.check_csrf("tok", ""))


class Progress(unittest.TestCase):
    A = {"v": 1, "days": {"2026-10-02": {"n": 1, "xp": 50}, "2026-10-01": {"n": 2, "xp": 90}},
         "lv": {"digit": 3, "pitch": 1, "count": 5}, "best": 3, "vol": "2026-10-01"}
    B = {"days": {"2026-10-02": {"n": 3, "xp": 20}, "2026-10-05": {"n": 1, "xp": 10}},
         "lv": {"digit": 2, "pitch": 4}, "best": 5, "vol": "2026-10-05"}

    def test_merge_rules(self):
        m = progress.merge(self.A, self.B)
        self.assertEqual(list(m["days"]), ["2026-10-01", "2026-10-02", "2026-10-05"])
        self.assertEqual(m["days"]["2026-10-02"], {"n": 3, "xp": 50})
        self.assertEqual(m["lv"], {"digit": 3, "pitch": 4, "count": 5})
        self.assertEqual((m["best"], m["vol"]), (5, "2026-10-05"))
        self.assertEqual(json.dumps(m), json.dumps(progress.merge(self.B, self.A)))     # 誰先誰後都一樣
        self.assertEqual(m, progress.merge(m, self.B))                                  # 重複合併不變

    def test_day_detail(self):
        """11-02 起每天多記的細項：同一天兩台都練了，各項取較多的；沒練到的項目不放；答對不會比題數多。"""
        x = {"n": 1, "xp": 120, "t": 130, "s": {"digit": {"q": 5, "r": 4, "lv": 3}, "pitch": {"q": 5, "r": 5, "lv": 4}}}
        y = {"n": 2, "xp": 90, "s": {"digit": {"q": 10, "r": 3, "lv": 2}, "count": {"q": 5, "r": 2, "lv": 1}}}
        m = progress.merge({"days": {"2026-11-02": x}}, {"days": {"2026-11-02": y, "2026-11-01": {"n": 1, "xp": 5}}})
        self.assertEqual(m["days"]["2026-11-02"], {"n": 2, "xp": 120, "t": 130, "s": {
            "digit": {"q": 10, "r": 4, "lv": 3}, "pitch": {"q": 5, "r": 5, "lv": 4}, "count": {"q": 5, "r": 2, "lv": 1}}})
        self.assertEqual(m["days"]["2026-11-01"], {"n": 1, "xp": 5})          # 舊紀錄沒有細項，照舊
        c = progress.clean({"days": {"2026-11-02": {"n": 1, "xp": 0, "t": 0, "s": {"digit": {"q": 3, "r": 9}, "pitch": {"q": 0}}}}})
        self.assertEqual(c["days"]["2026-11-02"], {"n": 1, "xp": 0, "s": {"digit": {"q": 3, "r": 3, "lv": 1}}})

    def test_clean(self):
        c = progress.clean({"days": {"<x>": {"n": 1}, "2026-10-01": "x", "2026-10-02": {"n": -5, "xp": 1e9},
                                     "2026-10-03": {"n": "2.5", "xp": None}},
                            "lv": {"digit": 99, "pitch": "abc"}, "best": float("nan"), "vol": "x", "evil": 1})
        self.assertEqual(c["days"], {"2026-10-02": {"n": 1, "xp": 99999}, "2026-10-03": {"n": 3, "xp": 0}})
        self.assertEqual(c["lv"], {"digit": 10, "pitch": 1, "count": 1})
        self.assertEqual((c["best"], c["vol"]), (0, ""))
        self.assertNotIn("evil", c)
        self.assertEqual(progress.clean(None), progress.fresh())
        self.assertEqual(progress.clean([1, 2]), progress.fresh())

    @unittest.skipUnless(NODE, "沒有 node")
    def test_same_as_practice_js(self):
        """隨機產生 200 組紀錄，Python 和 practice.js 的 clean／merge 結果要一模一樣。"""
        rnd = random.Random(7)

        def gen():
            days = {}
            for _ in range(rnd.randint(0, 8)):
                k = f"2026-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"
                days[k] = {"n": rnd.choice([0, 1, 2, 5, 120, 2.5, 3.49, "4", None]),
                           "xp": rnd.choice([0, 10, 99, 123456, -3, 7.5])}
                if rnd.random() < .6:
                    days[k]["t"] = rnd.choice([0, 1, 95, 600.4, 99999, -5, "30", None])
                if rnd.random() < .7:
                    days[k]["s"] = rnd.choice([None, [], "x", {
                        sk: rnd.choice([None, 3, {"q": 0, "r": 1}, {
                            "q": rnd.choice([1, 5, 15, 12000, "7", 2.5]), "r": rnd.choice([0, 3, 15, 20, -1, None]),
                            "lv": rnd.choice([0, 1, 6, 10, 11, None])}])
                        for sk in ("digit", "pitch", "count", "evil") if rnd.random() < .8}])
            if rnd.random() < .2:
                days["bad"] = {"n": 1}
            lv = {k: rnd.choice([0, 1, 4, 10, 11, 2.5, None]) for k in ("digit", "pitch", "count") if rnd.random() < .8}
            return {"days": days, "lv": lv, "best": rnd.choice([0, 3, 7, -1, 1e6]),
                    "vol": rnd.choice(["", "2026-01-01", "2026-12-31", "bad"])}
        pairs = [[gen(), gen()] for _ in range(200)]
        code = (f"const m = require({json.dumps(str(PRACTICE))});"
                "const pairs = JSON.parse(require('fs').readFileSync(0, 'utf8'));"     # 資料從 stdin 給：太長放不進命令列
                "console.log(JSON.stringify(pairs.map(([a, b]) => [m.clean(a), m.merge(a, b)])));")
        out = subprocess.run([NODE, "-e", code], input=json.dumps(pairs), capture_output=True, text=True,
                             timeout=60, check=True)
        js = json.loads(out.stdout)
        for (a, b), (jc, jm) in zip(pairs, js):
            self.assertEqual(json.dumps(progress.clean(a)), json.dumps(jc))
            self.assertEqual(json.dumps(progress.merge(a, b)), json.dumps(jm))


if __name__ == "__main__":
    unittest.main()
