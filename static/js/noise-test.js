// 噪音中聽數字（測試版）：在雜音裡聽三個數字、輸入聽到的數字（Digits-in-Noise）。
//
// 做法比照 WHO hearWHO 中文版（Gu et al. 2025, Int J Audiol；De Sousa et al. 2020）：
// - 反相（antiphasic, NoSπ）：雜音兩耳相同、數字右耳反相。聽力正常時大腦會把數字從雜音裡「拉出來」（約好 5 dB），
//   對感音性聽損更敏感。一定要戴耳機（兩耳都戴）：用喇叭或手機開「單聲道音訊」時數字會互相抵消，所以先做耳機檢查。
// - 23 題、每題三個不同的數字；從 0 dB 開始，前 3 題答對降 4 dB、答錯升 2 dB，之後一律 ±2 dB；
//   結果（SRT＝三個數字答對一半時的訊噪比）＝最後 19 題訊噪比的平均。正式開始前有 2 題練習（不計分）。
// - 雜音固定音量、數字變大小聲；雜音用同一批數字錄音錯開疊加上千次而成，頻譜和說話聲一樣、但聽不出字。
// - 每個數字天生難易不同（研究中差到 ±3 dB），用「數字音量校正」（levels.json，dB）拉齊；校正值要跟著錄音一起換。
//
// 判讀標準要用門市自己的錄音、找聽力正常的人實測後由聽力師訂定，訂好之前 CUTOFFS 保持 null，結果頁只顯示數字。
// 錄音放在 static/audio/din/0–9（wav／m4a／mp3），十個都有才會開放測驗（app/main.py 判斷）。
// 結果只在記憶體；按「帶著結果預約」才把摘要放進 sessionStorage，交給預約表單的備註欄（site.js）。
// 網址加 ?pilot=1 是門市校正模式：結果頁多一份逐題紀錄，讓同仁複製給系統管理者算校正值（不會上傳）。
(function () {
  "use strict";

  var TRIALS = 23, SCORED = 19, START_SNR = 0, BIG_STEPS = 3, BIG_DOWN = 4, STEP = 2, MIN_SNR = -26, MAX_SNR = 10;
  var PRACTICE = 2, PRACTICE_SNR = 4;
  var CUTOFFS = null;          // 例如 { normal: -15.8, refer: -14.2 }：聽力師用門市錄音的常模訂好前不要填
  var GAP = 0.35, LEAD = 0.5, TAIL = 0.5;           // 秒：數字間隔、雜音先響、雜音延後結束（同 hearWHO 500 ms）
  // 雜音的 RMS（使用者用這個音量調到舒服）。說話聲峰值比 RMS 高 11–16 dB，訊噪比 +10 時數字峰值約 0.06×3.2×6≈1.2，
  // 所以輸出再接限幅器（同聽損模擬）：只壓偶爾超過的峰值，不會在訊噪比高時爆音；0.12 時從約 +3 dB 起就會削波
  var NOISE_GAIN = 0.06;
  // 雜音疊加層數：40 層時同一時間只有約 3 個數字在念，雜音裡聽得出別的數字、還有安靜空檔（起伏約 40 dB），
  // 聽力正常也聽不懂；1000 層約 74 個重疊、起伏約 1.5 dB，和隨機相位的同頻譜雜音（1.4 dB）相當
  var NOISE_LAYERS = 1000;
  // 耳機檢查（Woods et al. 2017, Atten Percept Psychophys）：三個 200 Hz 音，一個小 6 dB、一個左右反相，
  // 問哪一個最小聲。戴耳機時反相音聽起來一樣大；用喇叭時反相音會抵消變小聲而答錯。6 題對 5 題算通過
  var HP_TRIALS = 6, HP_PASS = 5, HP_FREQ = 200, HP_DUR = 1.0, HP_ISI = 0.5, HP_GAIN = 0.1;

  // ── 純計算（node 也能跑）────────────────────────────
  // trial＝剛答完的是第幾題（從 0 算）
  function nextSnr(snr, correct, trial) {
    var d = correct ? -(trial < BIG_STEPS ? BIG_DOWN : STEP) : STEP;
    return Math.max(MIN_SNR, Math.min(MAX_SNR, snr + d));
  }
  // snrs＝每一題用的訊噪比（依序）；取最後 SCORED 題平均
  function srt(snrs) {
    var use = snrs.slice(-SCORED), s = 0;
    for (var i = 0; i < use.length; i++) s += use[i];
    return Math.round(s / use.length * 10) / 10;
  }
  function triplet(rand) {
    rand = rand || Math.random;
    var out = [];
    while (out.length < 3) {
      var d = Math.floor(rand() * 10);
      if (out.indexOf(d) < 0) out.push(d);
    }
    return out;
  }
  // 耳機檢查一題：三個音的種類（ref／quiet／anti）隨機排，答案＝quiet 的位置（0–2）
  function hpTrial(rand) {
    rand = rand || Math.random;
    var kinds = ["ref", "quiet", "anti"];
    for (var i = kinds.length - 1; i > 0; i--) {
      var j = Math.floor(rand() * (i + 1)), t = kinds[i]; kinds[i] = kinds[j]; kinds[j] = t;
    }
    return { kinds: kinds, answer: kinds.indexOf("quiet") };
  }
  // 數字音量校正：只接受 10 個 ±6 dB 內的數字，其他一律 0
  function levels(list) {
    var ok = Array.isArray(list) && list.length === 10 && list.every(function (v) { return typeof v === "number" && isFinite(v) && Math.abs(v) <= 6; });
    return ok ? list.slice() : [0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { nextSnr: nextSnr, srt: srt, triplet: triplet, hpTrial: hpTrial, levels: levels,
      TRIALS: TRIALS, SCORED: SCORED, HP_TRIALS: HP_TRIALS, HP_PASS: HP_PASS };
  }
  if (typeof document === "undefined") return;

  function $(id) { return document.getElementById(id); }

  function decode(ctx, data) {
    return new Promise(function (ok, fail) {
      var p = ctx.decodeAudioData(data, ok, fail);
      if (p && p.then) p.then(ok, fail);
    });
  }

  // 錄音：轉單聲道、去掉頭尾安靜、前後各 5 ms 淡入淡出（避免喀聲）、音量統一成同樣的 RMS
  function prep(ctx, b) {
    var ch = b.numberOfChannels, n = b.length, m = new Float32Array(n);
    for (var c = 0; c < ch; c++) { var x = b.getChannelData(c); for (var i = 0; i < n; i++) m[i] += x[i] / ch; }
    var peak = 0;
    for (var j = 0; j < n; j++) peak = Math.max(peak, Math.abs(m[j]));
    var th = peak * 0.05, a = 0, z = n - 1;
    while (a < n && Math.abs(m[a]) < th) a++;
    while (z > a && Math.abs(m[z]) < th) z--;
    a = Math.max(0, a - Math.round(0.02 * b.sampleRate));
    z = Math.min(n - 1, z + Math.round(0.05 * b.sampleRate));
    var out = ctx.createBuffer(1, z - a + 1, b.sampleRate), d = out.getChannelData(0), ss = 0;
    for (var k = a; k <= z; k++) { d[k - a] = m[k]; ss += m[k] * m[k]; }
    var rms = Math.sqrt(ss / d.length) || 1, fade = Math.min(Math.round(0.005 * b.sampleRate), d.length >> 2);
    for (var q = 0; q < d.length; q++) {
      var w = q < fade ? q / fade : q >= d.length - fade ? (d.length - 1 - q) / fade : 1;
      d[q] = d[q] / rms * w;                                  // RMS＝1，播放時再乘上音量
    }
    return out;
  }

  // 雜音：把所有數字隨機錯開疊加 NOISE_LAYERS 次，得到和說話聲同頻譜的穩定雜音（RMS 統一為 1）
  function makeNoise(ctx, digits) {
    var sr = ctx.sampleRate, len = Math.round(4 * sr), buf = ctx.createBuffer(1, len, sr), d = buf.getChannelData(0);
    for (var r = 0; r < NOISE_LAYERS; r++) {
      var src = digits[r % 10].getChannelData(0), off = Math.floor(Math.random() * len);
      for (var i = 0; i < src.length; i++) d[(off + i) % len] += src[i];
    }
    var ss = 0;
    for (var j = 0; j < len; j++) ss += d[j] * d[j];
    var rms = Math.sqrt(ss / len) || 1;
    for (var k = 0; k < len; k++) d[k] /= rms;
    return buf;
  }

  function init() {
    var root = $("din-root");
    if (!root) return;
    var urls = JSON.parse(root.getAttribute("data-digits") || "null");
    var level = levels(JSON.parse(root.getAttribute("data-levels") || "null"));
    var pilot = root.hasAttribute("data-pilot");
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!urls || !AC) return;

    var ctx = null, master = null, digits = null, noise = null;
    var trial = 0, snr = START_SNR, snrs = [], log = [], answer = [], current = null, accepting = false;
    var practice = 0, finalSrt = null, running = false, timers = [];

    function audio() {
      if (!ctx) {
        ctx = new AC();
        try { if (navigator.audioSession) navigator.audioSession.type = "playback"; } catch (e) {}
        master = ctx.createDynamicsCompressor();         // 只當限幅器
        master.threshold.value = -3; master.knee.value = 0; master.ratio.value = 20;
        master.attack.value = 0.003; master.release.value = 0.25;
        master.connect(ctx.destination);
      }
      if (ctx.state === "suspended") ctx.resume();
      return ctx;
    }
    function later(fn, ms) { timers.push(setTimeout(fn, ms)); }
    function clearTimers() { timers.forEach(clearTimeout); timers = []; }

    function show(id) {
      var s = root.querySelectorAll(".ht-screen");
      for (var i = 0; i < s.length; i++) s[i].classList.toggle("is-on", s[i].id === id);
    }

    function load() {
      var c = audio();
      return Promise.all(urls.map(function (u) {
        return fetch(u).then(function (r) { if (!r.ok) throw new Error(u); return r.arrayBuffer(); })
          .then(function (data) { return decode(c, data); }).then(function (b) { return prep(c, b); });
      })).then(function (bs) { digits = bs; noise = makeNoise(c, bs); });
    }

    // 左右聲道各一路接到 merger：gainL 給左耳、gainR 給右耳（負的＝反相）
    function stereo(c, node, gainL, gainR, merger) {
      var gl = c.createGain(), gr = c.createGain();
      gl.gain.value = gainL; gr.gain.value = gainR;
      node.connect(gl); node.connect(gr);
      gl.connect(merger, 0, 0); gr.connect(merger, 0, 1);
    }

    // 播一題：雜音先響、三個數字依序（右耳反相）、雜音延後結束；回傳整段長度（秒）
    function present(nums, snrDb) {
      var c = audio(), t0 = c.currentTime + 0.1, t = t0 + LEAD;
      var merger = c.createChannelMerger(2); merger.connect(master);
      nums.forEach(function (n) {
        var s = c.createBufferSource(), g = NOISE_GAIN * Math.pow(10, (snrDb + level[n]) / 20);
        s.buffer = digits[n];
        stereo(c, s, g, -g, merger);
        s.start(t);
        t += digits[n].duration + GAP;
      });
      var end = t - GAP + TAIL, ns = c.createBufferSource(), env = c.createGain();
      ns.buffer = noise; ns.loop = true;
      env.gain.setValueAtTime(0, t0);
      env.gain.linearRampToValueAtTime(1, t0 + 0.05);
      env.gain.setValueAtTime(1, end - 0.05);
      env.gain.linearRampToValueAtTime(0, end);
      ns.connect(env);
      stereo(c, env, NOISE_GAIN, NOISE_GAIN, merger);
      ns.start(t0, Math.random() * (noise.duration - 1));
      ns.stop(end + 0.05);
      return end - c.currentTime;
    }

    // ── 開始前 ───────────────────────────
    var agree = $("din-agree"), start = $("din-start");
    agree.addEventListener("change", function () { start.disabled = !agree.checked; });
    start.addEventListener("click", function () {
      start.disabled = true;
      $("din-loading").hidden = false;
      $("din-loading").textContent = "錄音載入中…";
      load().then(function () { $("din-loading").hidden = true; hpBegin(); })
        .catch(function () { $("din-loading").textContent = "錄音檔讀取失敗，請稍後再試。"; });
    });

    // ── 耳機檢查 ─────────────────────────
    var hp = { n: 0, ok: 0, cur: null, ready: false };
    function hpBegin() {
      hp.n = 0; hp.ok = 0;
      $("din-hp-fail").hidden = true;
      $("din-hp-ask").hidden = false;
      show("din-hp");
      hpNext();
    }
    function hpNext() {
      hp.cur = hpTrial();
      hp.ready = false;
      $("din-hp-prog").textContent = (hp.n + 1) + " / " + HP_TRIALS;
      $("din-hp-status").textContent = "按「播放」，仔細聽三個聲音。";
      $("din-hp-play").disabled = false;
      setHpButtons(false);
    }
    function setHpButtons(on) {
      var bs = root.querySelectorAll("[data-hp]");
      for (var i = 0; i < bs.length; i++) bs[i].disabled = !on;
    }
    $("din-hp-play").addEventListener("click", function () {
      var c = audio(), t = c.currentTime + 0.15, merger = c.createChannelMerger(2);
      merger.connect(master);
      this.disabled = true;
      setHpButtons(false);
      $("din-hp-status").textContent = "播放中…";
      hp.cur.kinds.forEach(function (k) {
        var o = c.createOscillator(), env = c.createGain(), g = k === "quiet" ? HP_GAIN / 2 : HP_GAIN;
        o.frequency.value = HP_FREQ;
        env.gain.setValueAtTime(0, t);
        env.gain.linearRampToValueAtTime(1, t + 0.1);
        env.gain.setValueAtTime(1, t + HP_DUR - 0.1);
        env.gain.linearRampToValueAtTime(0, t + HP_DUR);
        o.connect(env);
        stereo(c, env, g, k === "anti" ? -g : g, merger);
        o.start(t); o.stop(t + HP_DUR + 0.02);
        t += HP_DUR + HP_ISI;
      });
      later(function () {
        $("din-hp-status").textContent = "哪一個聲音最小聲？";
        $("din-hp-play").disabled = false;
        setHpButtons(true);
        hp.ready = true;
      }, (t - c.currentTime - HP_ISI) * 1000);
    });
    root.querySelector(".din-hp-pick").addEventListener("click", function (e) {
      var b = e.target.closest("[data-hp]");
      if (!b || !hp.ready) return;
      hp.ready = false;
      if (Number(b.getAttribute("data-hp")) === hp.cur.answer) hp.ok++;
      hp.n++;
      if (hp.n < HP_TRIALS) { hpNext(); return; }
      if (hp.ok >= HP_PASS) { show("din-calib"); return; }
      $("din-hp-ask").hidden = true;
      $("din-hp-fail").hidden = false;
    });
    $("din-hp-retry").addEventListener("click", hpBegin);

    // ── 調音量 ───────────────────────────
    $("din-sample").addEventListener("click", function () { present(triplet(), 0); });
    $("din-go").addEventListener("click", function () {
      trial = 0; snr = START_SNR; snrs = []; log = []; finalSrt = null; practice = PRACTICE; running = true;
      show("din-test");
      next();
    });

    // ── 測試 ─────────────────────────────
    function render() {
      var boxes = root.querySelectorAll(".din-box");
      for (var i = 0; i < boxes.length; i++) boxes[i].textContent = answer[i] != null ? answer[i] : "";
      var done = practice ? 0 : trial;
      $("din-prog-fill").style.width = (done / TRIALS * 100) + "%";
      $("din-prog-text").textContent = practice ? "練習 " + (PRACTICE - practice + 1) + " / " + PRACTICE
        : Math.min(trial + 1, TRIALS) + " / " + TRIALS;
    }
    function next() {
      if (!running) return;
      if (!practice && trial >= TRIALS) { running = false; finish(); return; }
      answer = [];
      current = triplet();
      accepting = false;
      render();
      $("din-status").textContent = practice ? "練習題（不計分），請注意聽…" : "請注意聽…";
      later(function () {
        var secs = present(current, practice ? PRACTICE_SNR : snr);
        later(function () {
          if (!running) return;
          accepting = true;
          $("din-status").textContent = "請輸入聽到的三個數字（聽不清楚就猜一個）";
        }, Math.max(0, secs * 1000 - 800));
      }, 500);
    }
    function key(d) {
      if (!accepting || answer.length >= 3) return;
      answer.push(d);
      render();
      if (answer.length < 3) return;
      accepting = false;
      var ok = answer[0] === current[0] && answer[1] === current[1] && answer[2] === current[2];
      if (practice) {
        practice--;
        $("din-status").textContent = ok ? "答對了！" : "正確答案是 " + current.join(" ") + "。";
        if (!practice) later(function () { $("din-status").textContent = "練習結束，正式開始。"; }, 1200);
        later(next, practice ? 1600 : 2600);
        return;
      }
      snrs.push(snr);
      log.push({ d: current.join(""), a: answer.join(""), snr: snr });
      snr = nextSnr(snr, ok, trial);
      trial++;
      later(next, 400);
    }
    root.querySelector(".din-pad").addEventListener("click", function (e) {
      var b = e.target.closest("button");
      if (!b) return;
      if (b.hasAttribute("data-del")) { if (accepting && answer.length) { answer.pop(); render(); } return; }
      key(Number(b.getAttribute("data-d")));
    });
    document.addEventListener("keydown", function (e) {
      if (!$("din-test").classList.contains("is-on")) return;
      if (/^[0-9]$/.test(e.key)) { e.preventDefault(); key(Number(e.key)); }
      else if (e.key === "Backspace" && accepting && answer.length) { e.preventDefault(); answer.pop(); render(); }
    });
    function reset() {
      clearTimers();
      accepting = false; running = false;
      agree.checked = false; start.disabled = true;
      show("din-welcome");
    }
    $("din-quit").addEventListener("click", function () {
      if (window.confirm("確定要放棄這次測試嗎？")) reset();
    });

    // ── 結果 ─────────────────────────────
    function finish() {
      finalSrt = srt(snrs);
      $("din-srt").textContent = (finalSrt > 0 ? "+" : "") + finalSrt + " dB";
      var v = $("din-verdict");
      if (CUTOFFS) {
        var ok = finalSrt <= CUTOFFS.normal, refer = finalSrt > CUTOFFS.refer;
        v.className = "ht-verdict " + (ok ? "is-ok" : "is-warn");
        v.textContent = ok ? "在吵雜環境中聽數字的表現在一般範圍" : refer ? "在吵雜環境中聽話可能比較吃力，建議做專業聽力檢查" : "介於中間，建議過一陣子再測一次，或預約門市了解";
      } else {
        v.className = "ht-verdict is-warn";
        v.textContent = "測試版：判讀標準還在建立中，這個數字先提供門市參考。";
      }
      if (pilot) {
        $("din-pilot").hidden = false;
        $("din-pilot-log").value = JSON.stringify({ srt: finalSrt, levels: level, digits: urls, ua: navigator.userAgent, trials: log });
      }
      $("din-prog-fill").style.width = "100%";
      show("din-result");
    }
    if (pilot) {
      $("din-pilot-copy").addEventListener("click", function () {
        var t = $("din-pilot-log");
        t.select();
        try { navigator.clipboard.writeText(t.value); } catch (e) { document.execCommand("copy"); }
        this.textContent = "已複製";
      });
    }
    $("din-book").addEventListener("click", function () {
      var d = new Date(), roc = (d.getFullYear() - 1911) + "/" + (d.getMonth() + 1) + "/" + d.getDate();
      try { sessionStorage.setItem("y1web-ht", "【噪音中聽數字 測試版 " + roc + "】SRT 約 " + finalSrt + " dB SNR（耳機、反相；僅供參考）。"); } catch (e) {}
    });
    $("din-again").addEventListener("click", reset);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
