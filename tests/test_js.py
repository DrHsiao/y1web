"""前端的純計算（聽損模擬的濾波器、噪音中聽數字的調整規則、每日聽練的打卡與紀錄）：用 node 跑，沒有 node 就略過。"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

JS = Path(__file__).resolve().parent.parent / "static" / "js"
PRACTICE = Path(__file__).resolve().parent.parent / "practice" / "practice.js"
NODE = shutil.which("node") or shutil.which("nodejs")


def run(code: str):
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


@unittest.skipUnless(NODE, "沒有 node")
class HearingSim(unittest.TestCase):
    def test_filter_matches_audiogram(self):
        r = run(f"""
const m = require({json.dumps(str(JS / "hearing-sim.js"))});
const out = {{}};
for (const [k, p] of Object.entries(m.PRESETS)) {{
  const h = m.designIR(p.hl, 48000);
  out[k] = m.FREQS.map((f, i) => [m.responseDb(h, 48000, f), -Math.min(80, p.hl[i])]);
}}
const flat = m.designIR([0, 0, 0, 0, 0, 0, 0], 48000);
out.flat = [100, 1000, 12000].map(f => [m.responseDb(flat, 48000, f), 0]);
console.log(JSON.stringify(out));""")
        for name, pts in r.items():
            for got, want in pts:
                self.assertAlmostEqual(got, want, delta=1.0, msg=name)     # 每個頻率誤差在 1 dB 內


@unittest.skipUnless(NODE, "沒有 node")
class NoiseTest(unittest.TestCase):
    def test_adaptive_rule(self):
        r = run(f"""
const m = require({json.dumps(str(JS / "noise-test.js"))});
let s = 1; const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
const est = [];
for (const t of [-17, -9]) for (let k = 0; k < 200; k++) {{
  let snr = 0; const snrs = [];
  for (let i = 0; i < m.TRIALS; i++) {{ const p = 1 / (1 + Math.exp(-(snr - t) * 0.8)); snrs.push(snr); snr = m.nextSnr(snr, rnd() < p, i); }}
  est.push([t, m.srt(snrs)]);
}}
const trip = Array.from({{length: 500}}, () => m.triplet(rnd));
const hp = Array.from({{length: 300}}, () => m.hpTrial(rnd));
console.log(JSON.stringify({{est,
  steps: [m.nextSnr(0, true, 0), m.nextSnr(0, false, 0), m.nextSnr(-8, true, 2), m.nextSnr(-8, true, 3), m.nextSnr(-26, true, 9), m.nextSnr(10, false, 9)],
  srtLast: m.srt([0, 0, 0, 0].concat(Array(19).fill(-10))),
  tripOk: trip.every(t => t.length === 3 && new Set(t).size === 3),
  hpOk: hp.every(h => h.kinds.slice().sort().join() === "anti,quiet,ref" && h.kinds[h.answer] === "quiet"),
  hpPos: [0, 1, 2].map(i => hp.filter(h => h.answer === i).length),
  levels: [m.levels([1, -2, 0, 0, 0, 0, 0, 0, 0, 3]), m.levels([9, 0, 0, 0, 0, 0, 0, 0, 0, 0]), m.levels(null), m.levels([1, 2])]}}));""")
        self.assertEqual(r["steps"], [-4, 2, -12, -10, -26, 10])          # 前 3 題答對降 4 dB，之後 ±2，上下限 −26／+10
        self.assertEqual(r["srtLast"], -10)                               # 只平均最後 19 題
        self.assertTrue(r["tripOk"])                                      # 三個數字都不同
        self.assertTrue(r["hpOk"])
        self.assertTrue(all(70 < n < 130 for n in r["hpPos"]))            # 最小聲的位置平均分配
        self.assertEqual(r["levels"][0], [1, -2, 0, 0, 0, 0, 0, 0, 0, 3])
        self.assertEqual(r["levels"][1:], [[0] * 10] * 3)                 # 超過 ±6 dB、格式不對＝不校正
        for t in (-17, -9):
            vals = [e for tt, e in r["est"] if tt == t]
            self.assertAlmostEqual(sum(vals) / len(vals), t, delta=0.6)  # 23 題估得到真正的 SRT


@unittest.skipUnless(NODE, "沒有 node")
class Practice(unittest.TestCase):
    def test_streak_and_week(self):
        r = run(f"""
const m = require({json.dumps(str(PRACTICE))});
const d = {{"2026-10-26": {{n: 1, xp: 90}}, "2026-10-27": {{n: 2, xp: 300}}, "2026-10-28": {{n: 1, xp: 170}}}};
console.log(JSON.stringify({{
  done: m.streak(d, "2026-10-28"), notYet: m.streak(d, "2026-10-29"), broken: m.streak(d, "2026-10-30"), none: m.streak({{}}, "2026-10-29"),
  month: m.streak({{"2026-02-28": {{n: 1, xp: 0}}, "2026-03-01": {{n: 1, xp: 0}}}}, "2026-03-01"),
  week: m.week(d, "2026-10-29"), sunday: m.week(d, "2026-11-01").map(w => w.d),
  xp: m.totalXp({{days: d}}), time: [m.mmss(0), m.mmss(95400)]}}));""")
        self.assertEqual((r["done"], r["notYet"], r["broken"], r["none"]), (3, 3, 0, 0))   # 今天還沒練：從昨天往回算
        self.assertEqual(r["month"], 2)                                                  # 跨月
        self.assertEqual([w["d"] for w in r["week"]][0], "2026-10-26")                   # 週一開始
        self.assertEqual([w["done"] for w in r["week"]], [True, True, True, False, False, False, False])
        self.assertTrue(r["week"][3]["today"] and r["week"][4]["future"])
        self.assertEqual(r["sunday"][0], "2026-10-26")                                   # 週日還是同一週
        self.assertEqual(r["xp"], 560)
        self.assertEqual(r["time"], ["0:00", "1:35"])

    def test_adopt_member_sync(self):
        """登入會員後：同一位（或沒同步過）合併；換人就改用新的人的紀錄；伺服器缺東西才送上去。"""
        r = run(f"""
const m = require({json.dumps(str(PRACTICE))});
const local = {{days: {{"2026-10-28": {{n: 1, xp: 50}}}}, lv: {{digit: 3}}}};
const server = {{days: {{"2026-10-29": {{n: 1, xp: 70}}}}, lv: {{pitch: 4}}}};
console.log(JSON.stringify({{
  first: m.adopt(local, server, null, "u1"), same: m.adopt(local, server, "u1", "u1"),
  other: m.adopt(local, server, "u0", "u1"), noServer: m.adopt(local, null, null, "u1"),
  inSync: m.adopt(m.merge(local, server), m.merge(local, server), "u1", "u1"),
  freshBoth: m.adopt(null, null, null, "u1")}}));""")
        self.assertEqual(sorted(r["first"]["state"]["days"]), ["2026-10-28", "2026-10-29"])   # 第一次登入：瀏覽器的併進會員
        self.assertTrue(r["first"]["push"])
        self.assertEqual(r["same"], r["first"])
        self.assertEqual(list(r["other"]["state"]["days"]), ["2026-10-29"])                   # 換人：不把前一位的併進來
        self.assertFalse(r["other"]["push"])
        self.assertTrue(r["noServer"]["push"])                                                 # 會員裡還沒有紀錄
        self.assertFalse(r["inSync"]["push"])                                                  # 一樣就不用送
        self.assertFalse(r["freshBoth"]["push"])

    def test_clean_merge_backup(self):
        r = run(f"""
const m = require({json.dumps(str(PRACTICE))});
const a = {{v: 1, days: {{"2026-10-27": {{n: 1, xp: 100}}, "2026-10-28": {{n: 1, xp: 50}}}}, lv: {{digit: 4, pitch: 2, count: 1}}, best: 2, vol: "2026-10-28"}};
const b = {{v: 1, days: {{"2026-10-28": {{n: 2, xp: 40}}, "2026-10-29": {{n: 1, xp: 120}}}}, lv: {{digit: 3, pitch: 5, count: 2}}, best: 1, vol: ""}};
const ab = m.merge(a, b), ba = m.merge(b, a);
const junk = m.clean({{days: {{"<script>": {{n: 1}}, "2026-10-01": "x", "2026-10-02": {{n: -5, xp: 1e9}}, "__proto__": {{n: 1}}}},
                      lv: {{digit: 99, pitch: "a", count: 0}}, best: -1, vol: "昨天", extra: 1}});
console.log(JSON.stringify({{ab, same: JSON.stringify(ab) === JSON.stringify(ba), again: JSON.stringify(m.merge(ab, b)) === JSON.stringify(ab),
  junk, round: m.decode(m.encode(ab)), spaced: m.decode(" " + m.encode(ab).replace(/(.{{10}})/g, "$1\\n")) !== null,
  bad: [m.decode(""), m.decode("abc"), m.decode("Y1P1-!!!"), m.decode(null)], fresh: m.clean(null)}}));""")
        ab = r["ab"]
        self.assertEqual(ab["days"]["2026-10-28"], {"n": 2, "xp": 50})          # 同一天取較多的
        self.assertEqual(sorted(ab["days"]), ["2026-10-27", "2026-10-28", "2026-10-29"])
        self.assertEqual(ab["lv"], {"digit": 4, "pitch": 5, "count": 2})        # 等級取較高的
        self.assertEqual((ab["best"], ab["vol"]), (2, "2026-10-28"))
        self.assertTrue(r["same"] and r["again"])                               # 順序無關、重複合併不變
        self.assertEqual(r["junk"]["days"], {"2026-10-02": {"n": 1, "xp": 99999}})   # 壞日期、壞資料丟掉，數字夾在範圍內
        self.assertEqual(r["junk"]["lv"], {"digit": 10, "pitch": 1, "count": 1})
        self.assertEqual((r["junk"]["best"], r["junk"]["vol"]), (0, ""))
        self.assertNotIn("extra", r["junk"])
        self.assertEqual(r["round"], ab)
        self.assertTrue(r["spaced"])                                            # 貼上時多了空白、換行也認得
        self.assertEqual(r["bad"], [None] * 4)
        self.assertEqual(r["fresh"]["lv"], {"digit": 1, "pitch": 1, "count": 1})

    def test_add_round(self):
        r = run(f"""
const m = require({json.dumps(str(PRACTICE))});
const lv = {{digit: 3, pitch: 2, count: 4}}, sk = {{digit: {{q: 5, r: 4}}, pitch: {{q: 5, r: 5}}, count: {{q: 5, r: 1}}}};
const one = m.addRound(undefined, {{xp: 120, sec: 140, sk, lv}});
const two = m.addRound(one, {{xp: 80, sec: 99999, sk: {{digit: {{q: 5, r: 5}}, pitch: {{q: 0, r: 0}}, count: {{q: 5, r: 3}}}}, lv: {{digit: 4, pitch: 2, count: 3}}}});
const old = m.addRound({{n: 2, xp: 50}}, {{xp: 20, sec: 60, sk: {{digit: {{q: 5, r: 0}}}}, lv}});
console.log(JSON.stringify({{one, two, old}}));""")
        self.assertEqual(r["one"], {"n": 1, "xp": 120, "t": 140, "s": {
            "digit": {"q": 5, "r": 4, "lv": 3}, "pitch": {"q": 5, "r": 5, "lv": 2}, "count": {"q": 5, "r": 1, "lv": 4}}})
        self.assertEqual(r["two"]["t"], 740)                                     # 一回最多算 10 分鐘
        self.assertEqual(r["two"]["s"]["digit"], {"q": 10, "r": 9, "lv": 4})     # 等級是練完最後一回的
        self.assertEqual(r["two"]["s"]["count"], {"q": 10, "r": 4, "lv": 3})
        self.assertEqual((r["two"]["n"], r["two"]["xp"]), (2, 200))
        self.assertEqual(r["old"], {"n": 3, "xp": 70, "t": 60, "s": {"digit": {"q": 5, "r": 0, "lv": 3}}})   # 沒練到的項目不放

    def test_levels_and_questions(self):
        r = run(f"""
const m = require({json.dumps(str(PRACTICE))});
let s = 7; const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
let st = {{lv: 1, run: 0}}; const path = [];
for (const ok of [true, true, true, true, false, true, false, false]) {{ st = m.step(st, ok); path.push(st.lv); }}
const top = m.step(m.step({{lv: 10, run: 0}}, true), true), bottom = m.step({{lv: 1, run: 1}}, false);
const qs = {{}};
for (const k of m.ORDER) for (let lv = 1; lv <= 10; lv++) {{
  let prev; for (let i = 0; i < 40; i++) {{ const q = m.makeQ(k, lv, rnd, prev); (qs[k] = qs[k] || []).push([q, prev]); prev = q.answer; }}
}}
const plans = Array.from({{length: 60}}, () => m.plan(rnd));
console.log(JSON.stringify({{path, top, bottom,
  digitOk: qs.digit.every(([q, p]) => q.answer >= 0 && q.answer <= 9 && q.answer !== p && q.snr === m.DIGIT_SNR[q.lv - 1]),
  digitsSeen: new Set(qs.digit.map(([q]) => q.answer)).size,
  pitchOk: qs.pitch.every(([q]) => {{ const [a, b] = q.freqs, hi = q.answer ? b : a, lo = q.answer ? a : b;
    return Math.abs(hi / lo - 1 - m.PITCH_PCT[q.lv - 1] / 100) < 1e-9 && lo >= 300 && lo <= 900; }}),
  pitchFirst: qs.pitch.filter(([q]) => q.answer === 0).length,
  countOk: qs.count.every(([q, p]) => q.answer >= 2 && q.answer <= 6 && q.answer !== p && q.ioi === m.COUNT_IOI[q.lv - 1]),
  plansOk: plans.every(p => p.length === 15 && m.ORDER.every(k => p.filter(x => x === k).length === 5) &&
    p.every((x, i) => i % 5 === 0 || x === p[i - 1])),
  firsts: new Set(plans.map(p => p[0])).size,
  tables: [m.DIGIT_SNR, m.PITCH_PCT, m.COUNT_IOI].map(t => t.length)}}));""")
        self.assertEqual(r["path"], [1, 2, 2, 3, 2, 2, 1, 1])     # 連對 2 題升級、答錯降級，不低於 1
        self.assertEqual(r["top"]["lv"], 10)
        self.assertEqual(r["bottom"], {"lv": 1, "run": 0})
        self.assertTrue(r["digitOk"] and r["pitchOk"] and r["countOk"])
        self.assertEqual(r["digitsSeen"], 10)
        self.assertTrue(150 < r["pitchFirst"] < 250)              # 高音在前、在後各半
        self.assertTrue(r["plansOk"])                              # 三種各 5 題、同種連在一起
        self.assertEqual(r["firsts"], 3)                           # 先練哪一種會換
        self.assertEqual(r["tables"], [10, 10, 10])

if __name__ == "__main__":
    unittest.main()
