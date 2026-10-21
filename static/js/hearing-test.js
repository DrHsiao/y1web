// 聽力不求人：線上聽力自我察覺（Web Audio）。
//
// 方法：簡化的 Hughson-Westlake 上升法。先從 30 dB 開始，沒聽到每次加 10 dB 找到第一次聽到；
// 之後「聽到降 10 dB、沒聽到升 5 dB」，同一個音量在上升時聽到 2 次就當作這個頻率的閾值。
// 測試音是 3 短聲（脈衝音比連續音容易分辨，有耳鳴的人也比較不會混淆），間隔隨機，避免抓節奏亂按。
//
// 音量對應：參考音（1 kHz、gain 0.08）＝ 60 dB HL，請使用者把它調到「像平常說話」的音量。
// 其他頻率加上耳機基準等效閾值和 1 kHz 的差（TDH-39，ANSI S3.6），低頻和高頻的 0 dB HL 才會接近一般人剛好聽到的程度。
// 耳機沒有校正過，只能估算，所以畫面上一律標示「僅供參考」。
//
// 結果只留在記憶體裡；使用者按「帶著結果預約」才把摘要放進 sessionStorage，交給預約表單的備註欄（site.js）。
(function () {
  "use strict";

  var ORDER = [1000, 2000, 4000, 6000, 8000, 500, 250];    // 先測 1 kHz（最好聽），再往高頻，最後低頻
  var FREQS = [250, 500, 1000, 2000, 4000, 6000, 8000];    // 畫圖、表格的順序
  var EARS = ["right", "left"];
  var EAR_LABEL = { right: "右耳", left: "左耳" };
  var OFFSET = { 250: 18.5, 500: 4.5, 1000: 0, 2000: 2, 4000: 2.5, 6000: 8.5, 8000: 6 };
  var REF_GAIN = 0.08, REF_HL = 60, MAX_GAIN = 0.9;
  var MIN_HL = -10, START_HL = 30, MAX_TRIALS = 16;
  var PULSES = 3, PULSE_ON = 0.25, PULSE_GAP = 0.15;         // 秒
  var TONE_SECS = PULSES * (PULSE_ON + PULSE_GAP);
  var ANSWER_MS = 1300;                                      // 聲音結束後還算「有聽到」的時間
  var HT_KEY = "y1web-ht";

  function $(id) { return document.getElementById(id); }

  function init() {
    var root = $("ht-root");
    if (!root) return;
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) { show("ht-unsupported"); return; }

    var ctx = null, tone = null, refTone = null;
    var timer = null, answerTimer = null, answerOpen = false, paused = false, running = false;
    var step = 0, st = null, results = { right: {}, left: {} }, falseHits = 0;

    function audio() {
      if (!ctx) {
        ctx = new AC();
        // iPhone：讓網頁聲音不受側邊靜音開關影響（Safari 17 以後才有）
        try { if (navigator.audioSession) navigator.audioSession.type = "playback"; } catch (e) {}
      }
      if (ctx.state === "suspended") ctx.resume();
      return ctx;
    }

    function maxHL(f) { return Math.floor((REF_HL + 20 * Math.log10(MAX_GAIN / REF_GAIN) - OFFSET[f]) / 5) * 5; }
    function gainFor(hl, f) { return Math.min(MAX_GAIN, REF_GAIN * Math.pow(10, (hl - REF_HL + OFFSET[f]) / 20)); }

    function stopTone() {
      if (tone) {
        try { tone.amp.gain.cancelScheduledValues(ctx.currentTime); tone.amp.gain.setValueAtTime(0, ctx.currentTime); tone.osc.stop(ctx.currentTime + 0.02); } catch (e) {}
        tone = null;
      }
    }

    // 送到單邊耳朵：左＝聲道 0、右＝聲道 1
    function pulses(ear, f, g) {
      stopTone();
      var c = audio(), t0 = c.currentTime + 0.05;
      var osc = c.createOscillator(), amp = c.createGain(), merger = c.createChannelMerger(2);
      osc.type = "sine";
      osc.frequency.value = f;
      amp.gain.setValueAtTime(0, t0);
      for (var i = 0; i < PULSES; i++) {
        var s = t0 + i * (PULSE_ON + PULSE_GAP);
        amp.gain.setValueAtTime(0, s);
        amp.gain.linearRampToValueAtTime(g, s + 0.025);
        amp.gain.setValueAtTime(g, s + PULSE_ON - 0.025);
        amp.gain.linearRampToValueAtTime(0, s + PULSE_ON);
      }
      osc.connect(amp);
      amp.connect(merger, 0, ear === "left" ? 0 : 1);
      merger.connect(c.destination);
      osc.start(t0);
      osc.stop(t0 + TONE_SECS + 0.05);
      tone = { osc: osc, amp: amp };
    }

    // ── 畫面切換 ─────────────────────────────
    function show(id) {
      var screens = root.querySelectorAll(".ht-screen");
      for (var i = 0; i < screens.length; i++) screens[i].classList.toggle("is-on", screens[i].id === id);
      var app = $("ht-app");                              // 換畫面時把測試區捲回畫面上方
      var top = app.getBoundingClientRect().top + window.pageYOffset - 80;
      if (Math.abs(window.pageYOffset - top) > 40) window.scrollTo({ top: top, behavior: "smooth" });
    }

    // ── 開始前：同意、參考音、左右確認 ─────────────
    var agree = $("ht-agree"), start = $("ht-start");
    agree.addEventListener("change", function () { start.disabled = !agree.checked; });
    start.addEventListener("click", function () { audio(); show("ht-calib"); });

    var refBtn = $("ht-ref"), refStop = null;
    function stopRef() {
      if (refTone) {
        try { refTone.amp.gain.setTargetAtTime(0, ctx.currentTime, 0.02); refTone.osc.stop(ctx.currentTime + 0.15); } catch (e) {}
        refTone = null;
      }
      clearTimeout(refStop);
      refBtn.querySelector("span").textContent = "播放參考音";
      refBtn.classList.remove("is-playing");
    }
    refBtn.addEventListener("click", function () {
      if (refTone) { stopRef(); return; }
      var c = audio(), osc = c.createOscillator(), amp = c.createGain();
      osc.frequency.value = 1000;
      amp.gain.setValueAtTime(0, c.currentTime);
      amp.gain.linearRampToValueAtTime(REF_GAIN, c.currentTime + 0.08);
      osc.connect(amp);
      amp.connect(c.destination);
      osc.start();
      refTone = { osc: osc, amp: amp };
      refBtn.querySelector("span").textContent = "停止播放";
      refBtn.classList.add("is-playing");
      refStop = setTimeout(stopRef, 20000);              // 忘了關也只響 20 秒
    });
    $("ht-try-r").addEventListener("click", function () { stopRef(); pulses("right", 1000, REF_GAIN); });
    $("ht-try-l").addEventListener("click", function () { stopRef(); pulses("left", 1000, REF_GAIN); });
    $("ht-go").addEventListener("click", function () {
      stopRef();
      results = { right: {}, left: {} };
      falseHits = 0;
      step = 0;
      running = true;
      show("ht-test");
      beginFreq();
    });

    // ── 測試流程 ─────────────────────────────
    function total() { return ORDER.length * EARS.length; }
    function current() { return { ear: EARS[Math.floor(step / ORDER.length)], f: ORDER[step % ORDER.length] }; }

    function beginFreq() {
      if (step >= total()) { finish(); return; }
      var c = current();
      st = { ear: c.ear, f: c.f, level: START_HL, phase: "find", trials: 0, lastMiss: false, asc: {}, heardAt: [] };
      $("ht-prog-fill").style.width = (step / total() * 100) + "%";
      $("ht-prog-text").textContent = (step + 1) + " / " + total();
      var earEl = $("ht-ear");
      earEl.textContent = EAR_LABEL[c.ear] + "測試中";
      earEl.className = "ht-ear ear-" + (c.ear === "right" ? "r" : "l");
      $("ht-freq").textContent = c.f;
      var gap = (step === ORDER.length) ? 2500 : 0;     // 換耳朵前多停一下
      if (gap) earEl.textContent = "換左耳測試";
      schedule(gap);
    }

    function schedule(extra) {
      clearTimeout(timer);
      if (paused) return;
      timer = setTimeout(present, (extra || 0) + 600 + Math.random() * 1000);
    }

    function present() {
      if (paused || !running) return;
      if (step === ORDER.length && st.trials === 0) $("ht-ear").textContent = "左耳測試中";
      st.trials++;
      pulses(st.ear, st.f, gainFor(st.level, st.f));
      answerOpen = true;
      clearTimeout(answerTimer);
      answerTimer = setTimeout(function () { answer(false); }, TONE_SECS * 1000 + 50 + ANSWER_MS);
    }

    function answer(heard) {
      if (!answerOpen) return;
      answerOpen = false;
      clearTimeout(answerTimer);
      if (heard) stopTone();
      var max = maxHL(st.f), done = null;
      if (heard) st.heardAt.push(st.level);
      if (st.phase === "find") {
        if (heard) { st.phase = "track"; st.lastMiss = false; st.level -= 10; }
        else { st.level += 10; }
      } else if (heard) {
        if (st.lastMiss) {
          st.asc[st.level] = (st.asc[st.level] || 0) + 1;
          if (st.asc[st.level] >= 2) done = { hl: st.level, nr: false };
        }
        st.lastMiss = false;
        st.level -= 10;
      } else {
        st.lastMiss = true;
        st.level += 5;
      }
      if (!done && st.level < MIN_HL) {
        if (heard && st.level + 10 === MIN_HL) done = { hl: MIN_HL, nr: false };   // 最小聲也聽得到
        st.level = MIN_HL;
      }
      if (!done && st.level > max) {
        var ascLv = Object.keys(st.asc).map(Number);
        if (ascLv.length) done = { hl: Math.min.apply(null, ascLv), nr: false };
        else if (st.heardAt.length) done = { hl: Math.min(max, Math.min.apply(null, st.heardAt) + 5), nr: false };
        else done = { hl: max, nr: true };                 // 最大聲仍沒聽到
      }
      if (!done && st.trials >= MAX_TRIALS) {               // 一直沒收斂：取上升時聽到的中間值
        var asc = [];
        Object.keys(st.asc).forEach(function (k) { for (var i = 0; i < st.asc[k]; i++) asc.push(Number(k)); });
        asc.sort(function (a, b) { return a - b; });
        if (asc.length) done = { hl: asc[Math.floor(asc.length / 2)], nr: false };
        else if (st.heardAt.length) done = { hl: Math.min.apply(null, st.heardAt), nr: false };
        else done = { hl: max, nr: true };
      }
      if (done) {
        results[st.ear][st.f] = done;
        step++;
        beginFreq();
      } else {
        schedule(0);
      }
    }

    var heardBtn = $("ht-heard");
    function press() {
      heardBtn.classList.add("is-pressed");
      setTimeout(function () { heardBtn.classList.remove("is-pressed"); }, 160);
      if (!running || paused) return;
      if (answerOpen) answer(true);
      else falseHits++;                                    // 沒有聲音時按下：記下來，結果頁提醒
    }
    heardBtn.addEventListener("click", press);
    document.addEventListener("keydown", function (e) {
      if (e.code !== "Space" || e.repeat || !running || !$("ht-test").classList.contains("is-on")) return;
      if (document.activeElement === heardBtn) return;    // 焦點在按鈕上：瀏覽器本來就會觸發 click
      e.preventDefault();
      press();
    });

    var pauseBtn = $("ht-pause");
    function setPaused(on) {
      paused = on;
      pauseBtn.querySelector("span").textContent = on ? "繼續" : "暫停";
      heardBtn.disabled = on;
      if (on) {
        clearTimeout(timer); clearTimeout(answerTimer);
        if (answerOpen) { answerOpen = false; st.trials--; }  // 這一次不算
        stopTone();
      } else {
        schedule(500);
      }
    }
    pauseBtn.addEventListener("click", function () { setPaused(!paused); });
    document.addEventListener("visibilitychange", function () {
      if (document.hidden && running && !paused) setPaused(true);   // 切到別的 App 就先暫停
    });
    $("ht-quit").addEventListener("click", function () {
      if (!window.confirm("確定要放棄這次測試嗎？")) return;
      reset();
    });

    function reset() {
      running = false;
      clearTimeout(timer); clearTimeout(answerTimer);
      answerOpen = false;
      stopTone();
      if (paused) setPaused(false);
      clearTimeout(timer);
      agree.checked = false;
      start.disabled = true;
      show("ht-welcome");
    }
    $("ht-again").addEventListener("click", reset);
    $("ht-print").addEventListener("click", function () { window.print(); });

    // ── 結果 ─────────────────────────────────
    function pta(ear) {
      var sum = 0, nr = false;
      [500, 1000, 2000, 4000].forEach(function (f) {
        var r = results[ear][f];
        sum += r.nr ? r.hl + 5 : r.hl;
        nr = nr || r.nr;
      });
      return { v: Math.round(sum / 4), nr: nr };
    }
    function grade(v) {
      if (v <= 25) return { t: "正常範圍", c: "g0" };
      if (v <= 40) return { t: "輕度範圍", c: "g1" };
      if (v <= 55) return { t: "中度範圍", c: "g2" };
      if (v <= 70) return { t: "中重度範圍", c: "g3" };
      if (v <= 90) return { t: "重度範圍", c: "g4" };
      return { t: "極重度範圍", c: "g5" };
    }
    function fmt(r) { return r.nr ? "≥" + r.hl : String(r.hl); }
    function fLabel(f) { return f >= 1000 ? (f / 1000) + "k" : String(f); }

    function finish() {
      running = false;
      stopTone();
      var p = { right: pta("right"), left: pta("left") };
      ["right", "left"].forEach(function (ear) {
        var k = ear === "right" ? "r" : "l", g = grade(p[ear].v);
        $("ht-pta-" + k).textContent = (p[ear].nr ? "≥ " : "約 ") + p[ear].v + " dB";
        var ge = $("ht-grade-" + k);
        ge.textContent = "估算落在「" + g.t + "」";
        ge.className = "ht-grade " + g.c;
      });
      var worst = 0;
      FREQS.forEach(function (f) { worst = Math.max(worst, results.right[f].hl, results.left[f].hl); });
      var ok = p.right.v <= 25 && p.left.v <= 25 && worst <= 35;
      var v = $("ht-verdict");
      v.className = "ht-verdict " + (ok ? "is-ok" : "is-warn");
      v.textContent = "";
      var b = document.createElement("b");
      b.textContent = ok ? "目前估算在正常範圍" : "建議進一步做專業聽力檢查";
      var s = document.createElement("span");
      s.textContent = ok ? "生活中如果還是常聽不清楚，或家人覺得您電視開得很大聲，仍歡迎預約門市。"
                         : "您的聽力可能有些狀況，建議預約門市，由專業人員為您了解。";
      v.appendChild(b); v.appendChild(s);

      var notes = [];
      var diffs = FREQS.filter(function (f) { return Math.abs(results.right[f].hl - results.left[f].hl) >= 20; }).length;
      if (Math.abs(p.right.v - p.left.v) >= 15 || diffs >= 2)
        notes.push("兩耳差距比較大。單耳聽力變差可能和耳朵的疾病有關，建議盡快到耳鼻喉科或門市做專業檢查。");
      if (falseHits >= 4)
        notes.push("測試中有 " + falseHits + " 次在沒有播放聲音時按下按鈕（耳鳴或環境聲音都可能造成），結果可能比實際好，可以換個更安靜的地方重新測一次。");
      var floor = 0;
      FREQS.forEach(function (f) { if (results.right[f].hl <= MIN_HL) floor++; if (results.left[f].hl <= MIN_HL) floor++; });
      if (floor >= 6) notes.push("很多頻率在最小聲時也聽得到，可能音量調得比較大，結果僅供參考。");
      var n = $("ht-notes");
      n.hidden = !notes.length;
      n.textContent = notes.join(" ");

      var tb = $("ht-table").tBodies[0];
      tb.textContent = "";
      FREQS.forEach(function (f) {
        var tr = tb.insertRow();
        [f + " Hz", fmt(results.right[f]), fmt(results.left[f])].forEach(function (t, i) {
          var cell = i ? tr.insertCell() : document.createElement("th");
          if (!i) { cell.scope = "row"; tr.appendChild(cell); }
          cell.textContent = t;
        });
      });
      drawAudiogram();
      $("ht-prog-fill").style.width = "100%";
      show("ht-result");
    }

    function summary() {
      var d = new Date(), roc = (d.getFullYear() - 1911) + "/" + (d.getMonth() + 1) + "/" + d.getDate();
      var p = { right: pta("right"), left: pta("left") };
      var line = function (ear) {
        return EAR_LABEL[ear] + " " + FREQS.map(function (f) { return fLabel(f) + ":" + fmt(results[ear][f]); }).join(" ");
      };
      return "【聽力不求人 線上自我察覺 " + roc + "】右耳平均約 " + (p.right.nr ? "≥" : "") + p.right.v + " dB、左耳約 " +
        (p.left.nr ? "≥" : "") + p.left.v + " dB（僅供參考）。" + line("right") + "；" + line("left") + "。";
    }
    ["ht-book", "ht-book-home"].forEach(function (id) {
      $(id).addEventListener("click", function () {
        try { sessionStorage.setItem(HT_KEY, summary()); } catch (e) {}
      });
    });

    // ── 聽力圖（SVG，不用 innerHTML）────────────────
    var NS = "http://www.w3.org/2000/svg";
    function el(name, attrs, text) {
      var e = document.createElementNS(NS, name);
      for (var k in attrs) e.setAttribute(k, attrs[k]);
      if (text != null) e.textContent = text;
      return e;
    }
    function drawAudiogram() {
      var svg = $("ht-audiogram");
      while (svg.firstChild) svg.removeChild(svg.firstChild);
      var W = 700, H = 470, L = 64, R = 70, T = 24, B = 56, pw = W - L - R, ph = H - T - B;
      var lo = Math.log2(180), hi = Math.log2(11000), dMin = -10, dMax = 100;
      var x = function (f) { return L + (Math.log2(f) - lo) / (hi - lo) * pw; };
      var y = function (d) { return T + (d - dMin) / (dMax - dMin) * ph; };
      [[-10, 25, "#dcfce7", "正常"], [25, 40, "#fef9c3", "輕度"], [40, 55, "#ffedd5", "中度"],
       [55, 70, "#fee2e2", "中重度"], [70, 90, "#fecaca", "重度"], [90, 100, "#f3e8ff", "極重度"]].forEach(function (b) {
        svg.appendChild(el("rect", { x: L, y: y(b[0]), width: pw, height: y(b[1]) - y(b[0]), fill: b[2] }));
        svg.appendChild(el("text", { x: L + pw + 8, y: (y(b[0]) + y(b[1])) / 2 + 5, "font-size": 14, fill: "#666" }, b[3]));
      });
      for (var d = -10; d <= 100; d += 10) {
        svg.appendChild(el("line", { x1: L, x2: L + pw, y1: y(d), y2: y(d), stroke: "#cbd5e1", "stroke-width": d === 0 ? 1.5 : 0.7 }));
        svg.appendChild(el("text", { x: L - 10, y: y(d) + 5, "font-size": 14, fill: "#475569", "text-anchor": "end" }, d));
      }
      FREQS.forEach(function (f) {
        svg.appendChild(el("line", { x1: x(f), x2: x(f), y1: T, y2: T + ph, stroke: "#cbd5e1", "stroke-width": 0.7 }));
        svg.appendChild(el("text", { x: x(f), y: T + ph + 22, "font-size": 14, fill: "#475569", "text-anchor": "middle" }, fLabel(f)));
      });
      svg.appendChild(el("text", { x: L + pw / 2, y: H - 8, "font-size": 15, fill: "#334155", "text-anchor": "middle", "font-weight": 700 }, "頻率（Hz）"));
      svg.appendChild(el("text", { x: 18, y: T + ph / 2, "font-size": 15, fill: "#334155", "text-anchor": "middle", "font-weight": 700,
        transform: "rotate(-90 18 " + (T + ph / 2) + ")" }, "聽力（dB HL）"));
      svg.appendChild(el("rect", { x: L, y: T, width: pw, height: ph, fill: "none", stroke: "#334155", "stroke-width": 1.5 }));
      [["right", "#c52020", ""], ["left", "#1d4ed8", "6 5"]].forEach(function (cfg) {
        var ear = cfg[0], color = cfg[1];
        var pts = FREQS.map(function (f) { return [x(f), y(results[ear][f].hl), results[ear][f].nr]; });
        svg.appendChild(el("polyline", { points: pts.map(function (p) { return p[0] + "," + p[1]; }).join(" "),
          fill: "none", stroke: color, "stroke-width": 2.5, "stroke-dasharray": cfg[2] }));
        pts.forEach(function (p) {
          if (ear === "right") svg.appendChild(el("circle", { cx: p[0], cy: p[1], r: 8, fill: "#fff", stroke: color, "stroke-width": 3 }));
          else {
            svg.appendChild(el("line", { x1: p[0] - 7, y1: p[1] - 7, x2: p[0] + 7, y2: p[1] + 7, stroke: color, "stroke-width": 3 }));
            svg.appendChild(el("line", { x1: p[0] - 7, y1: p[1] + 7, x2: p[0] + 7, y2: p[1] - 7, stroke: color, "stroke-width": 3 }));
          }
          if (p[2]) {                                       // 沒有反應：符號下方加箭頭
            var dx = ear === "right" ? -9 : 9;
            svg.appendChild(el("polyline", { points: (p[0]) + "," + (p[1] + 10) + " " + (p[0] + dx) + "," + (p[1] + 22) +
              " " + (p[0] + dx * 0.3) + "," + (p[1] + 20), fill: "none", stroke: color, "stroke-width": 2.5 }));
          }
        });
      });
      var date = $("ht-date");
      if (date) { var d2 = new Date(); date.textContent = (d2.getFullYear() - 1911) + "/" + (d2.getMonth() + 1) + "/" + d2.getDate(); }
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
