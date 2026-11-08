"""噪音中聽數字：用門市校正資料算「每個數字的音量校正」與門市常模。

同仁戴耳機開 /hearing-test/noise?pilot=1 做完測驗，結果頁「門市校正資料」整段複製，
每次一行存進同一個文字檔，再執行：
    ./venv/bin/python scripts/din_calibrate.py 校正資料.txt [--write]

做法（同 Polspoel et al. 2025 Sci Rep 的人耳校正）：每個數字在每一題答對與否 × 當時的訊噪比，
用共同斜率的 logistic 函數（猜對率 10%）估出每個數字的 SRT；校正值＝該數字 SRT − 平均 SRT
（越難的數字放越大聲），限制在 ±6 dB。--write 會寫進 static/audio/din/levels.json。
只用同一組錄音、同一份 levels 的資料（不同錄音的資料混在一起沒有意義）。
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

GUESS = 0.1          # 十選一，用猜的也有 10% 會對
MAX_LEVEL = 6.0
LEVELS_FILE = Path(__file__).resolve().parent.parent / "static" / "audio" / "din" / "levels.json"


def parse(lines: Sequence[str]) -> List[dict]:
    runs = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        r = json.loads(ln)
        if not isinstance(r.get("trials"), list) or not r["trials"]:
            raise ValueError("缺少 trials")
        runs.append(r)
    return runs


def observations(runs: Sequence[dict]) -> List[Tuple[int, float, int]]:
    """每個數字位置一筆：(數字, 相對訊噪比, 對=1／錯=0)。
    相對訊噪比＝題目訊噪比＋當時的校正值－這次測驗的 SRT：每個人整體聽力不同，先扣掉各自的 SRT，
    只比「同一個人聽各個數字」的難易；不扣的話混在一起估，數字間的差距會被放大（模擬時誤差到 2.5 dB，扣了 0.4 dB 內）。"""
    out = []
    for r in runs:
        lv = r.get("levels") or [0] * 10
        base = r["srt"] if isinstance(r.get("srt"), (int, float)) else 0.0
        for t in r["trials"]:
            d, a = str(t["d"]), str(t["a"])
            for i, ch in enumerate(d):
                n = int(ch)
                out.append((n, float(t["snr"]) + lv[n] - base, int(i < len(a) and a[i] == ch)))
    return out


def _p(x: float, srt: float, slope: float) -> float:
    return GUESS + (1 - GUESS) / (1 + math.exp(-slope * (x - srt)))


def _loglik(obs, th) -> float:
    b, s = th[10], 0.0
    for n, x, y in obs:
        p = min(max(GUESS + (1 - GUESS) / (1 + math.exp(-(b * x - th[n]))), 1e-9), 1 - 1e-9)
        s += math.log(p if y else 1 - p)
    return s


def _grad(obs, th) -> List[float]:
    """參數 th＝[c0…c9, b]，z＝b·snr − c_數字（SRT＝c/b）。"""
    g, b = [0.0] * 11, th[10]
    for n, x, y in obs:
        sig = 1 / (1 + math.exp(-(b * x - th[n])))
        p = min(max(GUESS + (1 - GUESS) * sig, 1e-9), 1 - 1e-9)
        w = (y - p) / (p * (1 - p)) * (1 - GUESS) * sig * (1 - sig)
        g[n] -= w
        g[10] += w * x
    return g


def _solve(a: List[List[float]], v: List[float]) -> List[float]:
    m = [row[:] + [vv] for row, vv in zip(a, v)]
    k = len(v)
    for i in range(k):
        piv = max(range(i, k), key=lambda r: abs(m[r][i]))
        m[i], m[piv] = m[piv], m[i]
        if abs(m[i][i]) < 1e-12:
            raise ArithmeticError("資料不足，無法估計")
        for r in range(k):
            if r != i:
                f = m[r][i] / m[i][i]
                m[r] = [a2 - f * b2 for a2, b2 in zip(m[r], m[i])]
    return [m[i][k] / m[i][i] for i in range(k)]


def fit(obs: Sequence[Tuple[int, float, int]], iters: int = 50) -> Tuple[List[float], float]:
    """最大概似（牛頓法＋回溯）：10 個數字各自的 SRT、共同斜率。回傳 (每個數字的 SRT, 斜率 1/dB)。"""
    b = 0.5
    th = [b * statistics.fmean(x for _, x, _ in obs)] * 10 + [b]
    ll = _loglik(obs, th)
    for _ in range(iters):
        g = _grad(obs, th)
        h = []
        for j in range(11):                                   # 數值 Hessian（梯度的中央差分）
            e = 1e-4
            up = th[:]; up[j] += e
            dn = th[:]; dn[j] -= e
            gu, gd = _grad(obs, up), _grad(obs, dn)
            h.append([(gu[i] - gd[i]) / (2 * e) for i in range(11)])
        step = _solve([[-h[i][j] for j in range(11)] for i in range(11)], g)
        t = 1.0
        while t > 1e-4:
            cand = [a + t * s for a, s in zip(th, step)]
            if cand[10] > 0.01:
                cll = _loglik(obs, cand)
                if cll >= ll - 1e-9:
                    break
            t /= 2
        else:
            break
        done = abs(cll - ll) < 1e-7
        th, ll = cand, cll
        if done:
            break
    b = th[10]
    return [c / b for c in th[:10]], b


def levels_from(srt: Sequence[float]) -> List[float]:
    mean = statistics.fmean(srt)
    return [round(max(-MAX_LEVEL, min(MAX_LEVEL, s - mean)), 1) for s in srt]


def main(argv: Sequence[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    runs = parse(Path(argv[0]).read_text(encoding="utf-8").splitlines())
    sets = {json.dumps(r.get("digits")) for r in runs}
    if len(sets) > 1:
        print("資料裡有不同的錄音，請分開算。")
        return 1
    obs = observations(runs)
    srt, slope = fit(obs)
    old = runs[0].get("levels") or [0] * 10
    # 新的校正＝舊校正＋這次量到的差距（資料已經是加過舊校正後的結果）
    new = levels_from([s for s in srt])
    combined = [round(max(-MAX_LEVEL, min(MAX_LEVEL, o + n)), 1) for o, n in zip(old, new)]
    srts = [r["srt"] for r in runs if isinstance(r.get("srt"), (int, float))]
    print(f"資料：{len(runs)} 次測驗、{len(obs)} 個數字")
    print(f"常模：SRT 平均 {statistics.fmean(srts):.1f} dB，標準差 {statistics.pstdev(srts):.1f} dB，範圍 {min(srts)} ～ {max(srts)}")
    print(f"共同斜率 {slope * 100 * (1 - GUESS) / 4:.1f} %/dB（50% 點）")
    for k in range(10):
        print(f"  數字 {k}：相對 SRT {srt[k]:+5.1f} dB　這次差距 {new[k]:+.1f}　新校正 {combined[k]:+.1f} dB（原 {old[k]:+.1f}）")
    if "--write" in argv:
        LEVELS_FILE.write_text(json.dumps(combined) + "\n", encoding="utf-8")
        print(f"已寫入 {LEVELS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
