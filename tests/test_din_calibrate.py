"""噪音中聽數字的門市校正：模擬聽者（已知每個數字的難易）跑完測驗，看估不估得回來。"""
import json
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import din_calibrate as dc  # noqa: E402

TRUE = [1.5, -1.0, 0.5, -2.0, 2.5, 0.0, -0.5, 1.0, -1.5, -0.5]      # 每個數字比平均難幾 dB


def next_snr(snr, ok, trial):                                      # 同 noise-test.js nextSnr
    return max(-26, min(10, snr + (-(4 if trial < 3 else 2) if ok else 2)))


def simulate(rng, base, levels):
    snr, trials, snrs = 0, [], []
    for i in range(23):
        nums = rng.sample(range(10), 3)
        ans = ""
        for n in nums:
            p = dc._p(snr + levels[n], base + TRUE[n], 0.9)
            ans += str(n) if rng.random() < p else str((n + 1) % 10)
        ok = ans == "".join(map(str, nums))
        trials.append({"d": "".join(map(str, nums)), "a": ans, "snr": snr})
        snrs.append(snr)
        snr = next_snr(snr, ok, i)
    return {"srt": round(sum(snrs[-19:]) / 19, 1), "levels": levels, "digits": ["x"], "trials": trials}


class Calibrate(unittest.TestCase):
    def test_recovers_digit_offsets(self):
        rng = random.Random(3)
        runs = [simulate(rng, rng.gauss(-17, 1.5), [0] * 10) for _ in range(40)]
        srt, slope = dc.fit(dc.observations(dc.parse([json.dumps(r) for r in runs])))
        got = dc.levels_from(srt)
        for g, t in zip(got, TRUE):
            self.assertAlmostEqual(g, t - sum(TRUE) / 10, delta=0.8)     # 40 次（約 20 人各 2 次）誤差在 1 dB 內
        self.assertTrue(-4 < sum(srt) / 10 < 0)       # 單一數字答對一半的點比「三個全對」一半的點（各人 SRT）低

    def test_levels_clamped_and_parse_checks(self):
        self.assertEqual(dc.levels_from([0, 0, 0, 0, 0, 0, 0, 0, 0, 90])[9], 6.0)
        with self.assertRaises(ValueError):
            dc.parse(['{"srt": -10}'])
        obs = dc.observations([{"srt": -12, "levels": [2] + [0] * 9, "trials": [{"d": "012", "a": "013", "snr": -10}]}])
        self.assertEqual(obs, [(0, 4.0, 1), (1, 2.0, 1), (2, 2.0, 0)])          # 含校正值、扣掉這次的 SRT


if __name__ == "__main__":
    unittest.main()
