"""會員頁「集點紀錄」的純函式（app/points.py；不連資料庫）。

資料庫那一段（綁定碼、檢視表、會員頁顯示）用本機測試伺服器實測。
執行：cd /home/john/y1web && ./venv/bin/python -m unittest tests.test_points
"""
import hashlib
import unittest
from unittest import mock

import pymysql

from app import hearing_rec, points


class MessengerUrl(unittest.TestCase):
    def test_profile_id(self):
        self.assertEqual(points.messenger_url("https://www.facebook.com/profile.php?id=100086946412032"),
                         "https://m.me/100086946412032")

    def test_page_name(self):
        self.assertEqual(points.messenger_url("https://www.facebook.com/y1ear.shop/"), "https://m.me/y1ear.shop")
        self.assertEqual(points.messenger_url("https://m.facebook.com/y1ear.shop?ref=x"), "https://m.me/y1ear.shop")

    def test_rejects(self):
        for bad in [None, "", "http://www.facebook.com/abcde", "https://evil.com/profile.php?id=1",
                    "https://facebook.com.evil.com/abcde", "https://www.facebook.com/profile.php?id=abc",
                    "https://www.facebook.com/", "https://www.facebook.com/a<b>cde", "javascript:alert(1)"]:
            self.assertIsNone(points.messenger_url(bad), bad)


class Codes(unittest.TestCase):
    def test_hash_matches_crm(self):
        # y1crm api/fb_points.code_hash 用同一種算法：兩邊對不上就綁不起來
        self.assertEqual(points.code_hash("012345"), hashlib.sha256(b"012345").hexdigest())

    def test_new_code_format_and_retry(self):
        cur = mock.MagicMock()
        cur.execute.side_effect = [None, pymysql.err.IntegrityError(1062, "dup"), None]
        con = mock.MagicMock()
        con.__enter__.return_value = con
        con.cursor.return_value.__enter__.return_value = cur
        with mock.patch("app.db.connect", return_value=con):
            code = points.new_code(5)
        self.assertRegex(code, r"^\d{6}$")
        self.assertEqual(cur.execute.call_count, 3)          # 清舊碼、撞碼一次、成功
        sql, args = cur.execute.call_args_list[2][0]
        self.assertIn("INSERT INTO y1member.fb_link_codes", sql)
        self.assertEqual(args[1], points.code_hash(code))    # 資料庫只存雜湊


class Summary(unittest.TestCase):
    def _con(self, err):
        cur = mock.MagicMock()
        cur.execute.side_effect = err
        con = mock.MagicMock()
        con.__enter__.return_value = con
        con.cursor.return_value.__enter__.return_value = cur
        return con

    def test_not_set_up_yet(self):
        for code in (1142, 1146):
            with mock.patch("app.db.connect", return_value=self._con(pymysql.err.ProgrammingError(code, "x"))):
                self.assertFalse(points.summary(1)["ready"])

    def test_other_errors_raise(self):
        with mock.patch("app.db.connect", return_value=self._con(pymysql.err.OperationalError(2003, "down"))):
            with self.assertRaises(pymysql.err.OperationalError):
                points.summary(1)

    def test_staff_rows_shown_with_reason(self):
        # John 11-19：會員要查得到每次增加或減少的明細（店員加點、兌換扣點、原因）
        from datetime import datetime
        ledger = [
            {"source": "hearing_store", "ref": "s1", "points": 10, "note": "11/19 門市聽力檢查", "status": "counted",
             "created_at": datetime(2026, 11, 19, 10, 0)},
            {"source": "redeem", "ref": "s2", "points": -8, "note": "兌換 電池一盒", "status": "counted",
             "created_at": datetime(2026, 11, 19, 11, 0)},
            {"source": "adjust", "ref": "s3", "points": 3, "note": "推薦朋友", "status": "counted",
             "created_at": datetime(2026, 11, 19, 12, 0)},
            {"source": "adjust", "ref": "s4", "points": -2, "note": "打錯", "status": "void",
             "created_at": datetime(2026, 11, 19, 13, 0)},
        ]
        cur = mock.MagicMock()
        cur.fetchall.side_effect = [[], ledger]          # 沒綁 Facebook → 帳本
        con = mock.MagicMock()
        con.__enter__.return_value = con
        con.cursor.return_value.__enter__.return_value = cur
        with mock.patch("app.db.connect", return_value=con):
            s = points.summary(1)
        self.assertEqual((s["total"], s["earned"], s["used"]), (5, 13, 8))
        got = [(r["title"], r["sub"], r["points"], r["note"]) for r in s["rows"]]
        self.assertEqual(got, [("門市扣點", "打錯", 0, "不列入"), ("門市加點", "推薦朋友", 3, ""),
                               ("兌換贈品", "兌換 電池一盒", -8, ""), ("來店打卡", "11/19 門市聽力檢查", 10, "")])
        self.assertNotIn("created_by", cur.execute.call_args_list[1][0][0])   # 店員名字不給會員看



class PracticeDays(unittest.TestCase):
    def test_only_today_and_yesterday_with_rounds(self):
        from datetime import date
        data = {"days": {"2026-11-19": {"n": 2}, "2026-11-18": {"n": 1}, "2026-11-17": {"n": 3},
                         "2026-11-20": {"n": 1}}}
        self.assertEqual(points.practice_days_to_award(data, date(2026, 11, 19)), ["2026-11-19", "2026-11-18"])

    def test_old_or_empty_days_not_awarded(self):
        from datetime import date
        self.assertEqual(points.practice_days_to_award({"days": {"2026-01-01": {"n": 5}}}, date(2026, 11, 19)), [])
        self.assertEqual(points.practice_days_to_award({"days": {"2026-11-19": {"n": 0}}}, date(2026, 11, 19)), [])
        self.assertEqual(points.practice_days_to_award({"days": "x"}, date(2026, 11, 19)), [])
        self.assertEqual(points.practice_days_to_award({}, date(2026, 11, 19)), [])


class Streak(unittest.TestCase):
    """11-19 晚 John：「每日聽力練習連續 7 天 +10 點」「斷掉就從頭算」"""

    def _days(self, start, n):
        from datetime import date, timedelta
        return {(date.fromisoformat(start) + timedelta(days=i)).isoformat() for i in range(n)}

    def test_len_and_status(self):
        from datetime import date
        days = self._days("2026-11-13", 7)                                   # 13～19 日
        self.assertEqual(points.streak_len(days, date(2026, 11, 19)), 7)
        self.assertEqual(points.streak_len(days, date(2026, 11, 20)), 0)
        st = points.streak_status(days, date(2026, 11, 19))
        self.assertEqual((st["today"], st["streak"], st["to_bonus"]), (True, 7, 7))      # 今天剛拿到 → 下一次再 7 天
        st = points.streak_status(days, date(2026, 11, 20))
        self.assertEqual((st["today"], st["streak"], st["to_bonus"]), (False, 7, 7))
        st = points.streak_status(self._days("2026-11-14", 6), date(2026, 11, 20))
        self.assertEqual((st["today"], st["streak"], st["to_bonus"]), (False, 6, 1))     # 今天練完就拿
        st = points.streak_status(self._days("2026-11-15", 6), date(2026, 11, 20))
        self.assertEqual((st["today"], st["streak"], st["to_bonus"]), (True, 6, 1))      # 明天再練就拿
        gap = self._days("2026-11-10", 3) | self._days("2026-11-15", 5)                  # 13、14 斷掉 → 從頭算
        self.assertEqual(points.streak_len(gap, date(2026, 11, 19)), 5)

    def _award(self, ledger_days, data, today, given=()):
        """假帳本：INSERT IGNORE 照唯一鍵；given＝之前已經拿過連續獎勵的日子。回 (結果, 新記的 (source, ref, points, note))"""
        rows = {("practice", d) for d in ledger_days} | {("streak", d) for d in given}
        new = []
        cur = mock.MagicMock()

        def execute(sql, args=()):
            cur.rowcount = 0
            if sql.startswith("INSERT IGNORE"):
                key = (args[1], args[2])
                if key not in rows:
                    rows.add(key)
                    new.append((args[1], args[2], args[3], args[4]))
                    cur.rowcount = 1
            elif "source='practice'" in sql:
                cur.fetchall.return_value = [{"ref": r} for s, r in rows if s == "practice"]
            elif "SUM" in sql:
                cur.fetchone.return_value = {"n": 0}
        cur.execute.side_effect = execute
        con = mock.MagicMock()
        con.__enter__.return_value = con
        con.cursor.return_value.__enter__.return_value = cur
        with mock.patch("app.db.connect", return_value=con):
            return points.award_practice(5, data, today), new

    def test_bonus_on_7th_and_14th_day_only(self):
        from datetime import date
        day = {"n": 1}
        r, new = self._award(self._days("2026-11-13", 6), {"days": {"2026-11-19": day}}, date(2026, 11, 19))
        self.assertEqual((r["day"], r["bonus"], r["gained"]), (1, 10, 11))
        self.assertIn(("streak", "2026-11-19", 10, "連續 7 天"), new)
        r, new = self._award(self._days("2026-11-13", 7), {"days": {"2026-11-20": day}}, date(2026, 11, 20),
                             given=["2026-11-19"])
        self.assertEqual((r["bonus"], r["streak"], r["to_bonus"]), (0, 8, 6))               # 第 8 天不給
        r, new = self._award(self._days("2026-11-13", 13), {"days": {"2026-11-26": day}}, date(2026, 11, 26),
                             given=["2026-11-19"])
        self.assertEqual(r["bonus"], 10)                                                   # 第 14 天再給
        self.assertIn(("streak", "2026-11-26", 10, "連續 14 天"), new)

    def test_late_yesterday_completes_today(self):
        # 今天先在 A 手機同步；昨天在 B 手機練的晚一點才傳上來 → 補上昨天後今天剛好滿 7 天
        from datetime import date
        ledger = self._days("2026-11-13", 5) | {"2026-11-19"}                              # 13～17、19（缺 18）
        r, new = self._award(ledger, {"days": {"2026-11-18": {"n": 1}, "2026-11-19": {"n": 1}}}, date(2026, 11, 19))
        self.assertEqual((r["day"], r["bonus"]), (1, 10))
        self.assertIn(("streak", "2026-11-19", 10, "連續 7 天"), new)

    def test_missed_bonus_yesterday_is_made_up(self):
        # 昨天滿 7 天但當時沒記到（例：上線前就在練）→ 今天同步時補給昨天那次
        from datetime import date
        r, new = self._award(self._days("2026-11-13", 7), {"days": {"2026-11-20": {"n": 1}}}, date(2026, 11, 20))
        self.assertEqual(new[-1], ("streak", "2026-11-19", 10, "連續 7 天"))

    def test_nothing_to_award_no_bonus_check(self):
        from datetime import date
        r, new = self._award(self._days("2026-11-13", 7), {"days": {}}, date(2026, 11, 19))
        self.assertEqual((r["gained"], new), (0, []))


class HearingOnline(unittest.TestCase):
    def _con(self, rowcount):
        cur = mock.MagicMock()
        cur.rowcount = rowcount
        con = mock.MagicMock()
        con.__enter__.return_value = con
        con.cursor.return_value.__enter__.return_value = cur
        return con, cur

    def test_first_in_month_gets_10(self):
        from datetime import datetime
        con, cur = self._con(1)
        with mock.patch("app.db.connect", return_value=con):
            r = points.award_hearing_online(7, datetime(2026, 12, 31, 23, 0))
        self.assertEqual((r["awarded"], r["points"], r["next"].isoformat()), (True, 10, "2027-01-01"))
        args = cur.execute.call_args[0][1]
        self.assertEqual(args, (7, "hearing_online", "2026-12", 10, None))      # 一個月一筆：ref＝年月

    def test_same_month_again(self):
        from datetime import datetime
        con, cur = self._con(0)
        with mock.patch("app.db.connect", return_value=con):
            r = points.award_hearing_online(7, datetime(2026, 11, 19, 10, 0))
        self.assertEqual((r["awarded"], r["points"], r["next"].isoformat()), (False, 0, "2026-12-01"))


class HearingRec(unittest.TestCase):
    def _raw(self, **over):
        ear = {str(f): {"hl": 20, "nr": False} for f in hearing_rec.FREQS}
        raw = {"right": dict(ear), "left": dict(ear), "fh": 1}
        raw.update(over)
        return raw

    def test_clean_and_pta(self):
        raw = self._raw()
        raw["right"]["4000"] = {"hl": 999, "nr": True}     # 超出範圍夾住
        raw["right"]["9999"] = {"hl": 5}                     # 認不得的頻率丟掉
        d = hearing_rec.clean(raw)
        self.assertEqual(d["right"]["4000"], {"hl": hearing_rec.MAX_HL, "nr": True})
        self.assertNotIn("9999", d["right"])
        self.assertEqual(hearing_rec.pta(d["left"]), {"v": 20, "nr": False})
        self.assertEqual(hearing_rec.pta(d["right"])["v"], round((20 * 3 + 135) / 4))   # 沒聽到 +5
        self.assertEqual(hearing_rec.grade(25), "正常範圍")
        self.assertEqual(hearing_rec.grade(26), "輕度範圍")
        self.assertEqual(hearing_rec.grade(91), "極重度範圍")

    def test_rejects(self):
        bad = self._raw()
        del bad["left"]["8000"]
        for raw in (None, [], {"right": {}}, bad, self._raw(left="x")):
            with self.assertRaises(hearing_rec.RecError):
                hearing_rec.clean(raw)
        b2 = self._raw()
        b2["right"]["500"] = {"hl": "20"}                    # 字串不收
        with self.assertRaises(hearing_rec.RecError):
            hearing_rec.clean(b2)
        b3 = self._raw()
        b3["right"]["500"] = {"hl": True}                    # 布林不收
        with self.assertRaises(hearing_rec.RecError):
            hearing_rec.clean(b3)

    def test_fh(self):
        self.assertEqual(hearing_rec.clean(self._raw(fh=500))["fh"], 99)
        self.assertEqual(hearing_rec.clean(self._raw(fh="3"))["fh"], 0)


if __name__ == "__main__":
    unittest.main()


class FbFab(unittest.TestCase):
    """官網右側「綁定 Facebook」圓鈕：沒登入、或登入了還沒綁的才顯示。"""

    def _req(self, path="/", cookie=None):
        from app import member
        r = mock.MagicMock()
        r.url.path = path
        r.cookies = {member.COOKIE: cookie} if cookie else {}
        return r

    def test_visitor_no_db(self):
        from app import main
        with mock.patch("app.member.session_member") as sm:
            self.assertTrue(main.fb_fab(self._req()))
        sm.assert_not_called()                                # 沒有登入憑證不查資料庫

    def test_member_bound_or_not(self):
        from app import main
        with mock.patch("app.member.session_member", return_value={"id": 7}), \
                mock.patch("app.points.fb_bound", side_effect=[False, True]) as fb:
            self.assertTrue(main.fb_fab(self._req(cookie="t")))
            self.assertFalse(main.fb_fab(self._req(cookie="t")))
        fb.assert_called_with(7)

    def test_expired_session_counts_as_visitor(self):
        from app import main
        with mock.patch("app.member.session_member", return_value=None):
            self.assertTrue(main.fb_fab(self._req(cookie="old")))

    def test_member_pages_and_db_error(self):
        from app import main
        self.assertFalse(main.fb_fab(self._req("/member")))
        self.assertFalse(main.fb_fab(self._req("/member/login")))
        with mock.patch("app.member.session_member", side_effect=pymysql.err.OperationalError(2003, "down")):
            self.assertFalse(main.fb_fab(self._req(cookie="t")))   # 錯誤頁照樣出得來

    def test_fb_bound_sql(self):
        cur = mock.MagicMock()
        cur.fetchone.side_effect = [None, (1,)]
        con = mock.MagicMock()
        con.__enter__.return_value = con
        con.cursor.return_value.__enter__.return_value = cur
        with mock.patch("app.db.connect", return_value=con):
            self.assertFalse(points.fb_bound(3))
            self.assertTrue(points.fb_bound(3))
        self.assertIn("y1member.member_fb", cur.execute.call_args[0][0])


class CodeState(unittest.TestCase):
    """會員頁綁定碼：頁面自己問綁好了沒（Messenger 回覆不一定送得到）。"""

    def _run(self, row):
        from datetime import datetime, timedelta
        cur = mock.MagicMock()
        cur.fetchone.return_value = row(datetime.now(), timedelta) if callable(row) else row
        con = mock.MagicMock()
        con.__enter__.return_value = con
        con.cursor.return_value.__enter__.return_value = cur
        with mock.patch("app.db.connect", return_value=con):
            st = points.code_state(9)
        self.assertIn("ORDER BY id DESC", cur.execute.call_args[0][0])
        return st

    def test_states(self):
        self.assertEqual(self._run(None), "none")
        self.assertEqual(self._run(lambda n, td: {"used_at": n, "expires_at": n + td(minutes=20)}), "used")
        self.assertEqual(self._run(lambda n, td: {"used_at": None, "expires_at": n + td(minutes=20)}), "waiting")
        self.assertEqual(self._run(lambda n, td: {"used_at": None, "expires_at": n - td(minutes=1)}), "expired")
