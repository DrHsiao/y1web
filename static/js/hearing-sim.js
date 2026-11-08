// 聽損模擬：聽聽看家人聽到的世界（Web Audio）。
//
// 做法：依聽力圖（各頻率 dB HL）把每個頻率的聲音調小同樣的 dB，模擬「聽不到的部分」。
// 濾波器是線性相位 FIR（頻率取樣法＋Blackman 窗），交給 ConvolverNode 做；左右耳各一組，所以兩耳不同也模擬得出來。
// 原音那一路加上同樣的延遲（濾波器的群延遲），切換「原本的／聽力變差時」才不會前後錯開。
// 這是簡化的示意：只模擬「變小聲」，沒有模擬耳鳴、聲音失真等其他感受，每個人的實際感受也不同。
//
// 聲音來源：門鈴、鳥叫、電器嗶聲、手機鈴聲、餐廳背景聲都在瀏覽器裡即時合成（不用音檔）；
// 說話聲用門市錄的檔案（有放才出現）、使用者自己選的錄音檔，或在 https 下用麥克風錄一句。
// 錄音和選的檔案只在這台裝置的記憶體裡，不會上傳；離開頁面就沒了。
// 「用我的測驗結果」：線上聽力測驗結果頁按「用我的結果聽聽看」時，才把各頻率數值放進 sessionStorage（y1web-ht-sim）。
(function () {
  "use strict";

  var FREQS = [250, 500, 1000, 2000, 4000, 6000, 8000];
  // 年長者常見的「高頻下降」型（dB HL，左右相同）
  var PRESETS = {
    mild: { label: "輕度", hl: [15, 15, 20, 30, 45, 50, 55],
      desc: "安靜時面對面說話大致聽得到，但小聲說話、離得遠、環境吵的時候開始聽不清楚；鳥叫、電器嗶聲這類高音變小。" },
    moderate: { label: "中度", hl: [25, 30, 40, 50, 60, 65, 70],
      desc: "一般音量說話常要對方重複；ㄙ、ㄘ、ㄈ、ㄒ 這類聲音容易聽錯；電視常開得比家人習慣的大聲。" },
    modsev: { label: "中重度", hl: [40, 45, 55, 65, 75, 80, 85],
      desc: "一般對話很吃力，常常聽得到有人在說話、卻聽不出在說什麼；門鈴、電話響可能沒注意到。" },
    severe: { label: "重度", hl: [60, 65, 75, 85, 90, 95, 95],
      desc: "一般說話的音量幾乎聽不到，只聽得到很大的聲音。" }
  };
  var MAX_ATTEN = 80;            // dB：再往下也聽不出差別，濾波器也做不到
  var IR_LEN = 2049;             // 奇數長度的對稱 FIR（48 kHz 約 43 ms，延遲約 21 ms）
  var SIM_KEY = "y1web-ht-sim";
  var REC_SECS = 8, FILE_MAX = 15 * 1024 * 1024, FILE_SECS = 30;

  // ── 純計算（node 也能跑，tests 用）─────────────────────────
  // 某頻率要調小幾 dB：聽力圖各點之間用對數頻率內插，兩端外延
  function attenAt(hl, f) {
    var a;
    if (f <= FREQS[0]) a = hl[0];
    else if (f >= FREQS[FREQS.length - 1]) a = hl[hl.length - 1];
    else {
      for (var i = 0; i < FREQS.length - 1; i++) {
        if (f <= FREQS[i + 1]) {
          var t = (Math.log(f) - Math.log(FREQS[i])) / (Math.log(FREQS[i + 1]) - Math.log(FREQS[i]));
          a = hl[i] + t * (hl[i + 1] - hl[i]);
          break;
        }
      }
    }
    return Math.max(0, Math.min(MAX_ATTEN, a));
  }

  // 線性相位 FIR：h[n] = (1/N)·[A0 + 2·Σ A_k·cos(2πk(n−c)/N)]，c＝(N−1)/2，再乘 Blackman 窗
  function designIR(hl, sr, n) {
    n = n || IR_LEN;
    var half = (n - 1) / 2, A = new Float64Array(half + 1), h = new Float32Array(n);
    for (var k = 0; k <= half; k++) A[k] = Math.pow(10, -attenAt(hl, k * sr / n) / 20);
    for (var i = 0; i < n; i++) {
      var m = i - half, s = A[0];
      for (var j = 1; j <= half; j++) s += 2 * A[j] * Math.cos(2 * Math.PI * j * m / n);
      var w = 0.42 - 0.5 * Math.cos(2 * Math.PI * i / (n - 1)) + 0.08 * Math.cos(4 * Math.PI * i / (n - 1));
      h[i] = s / n * w;
    }
    return h;
  }

  // FIR 在某頻率的增益（dB），測試用
  function responseDb(h, sr, f) {
    var re = 0, im = 0;
    for (var i = 0; i < h.length; i++) { re += h[i] * Math.cos(2 * Math.PI * f * i / sr); im -= h[i] * Math.sin(2 * Math.PI * f * i / sr); }
    return 20 * Math.log10(Math.sqrt(re * re + im * im));
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { FREQS: FREQS, PRESETS: PRESETS, attenAt: attenAt, designIR: designIR, responseDb: responseDb };
  }
  if (typeof document === "undefined") return;

  // ── 合成聲音（全部在瀏覽器裡產生）──────────────────────────
  function buffer(ctx, secs, fill) {
    var b = ctx.createBuffer(1, Math.round(secs * ctx.sampleRate), ctx.sampleRate), d = b.getChannelData(0);
    fill(d, ctx.sampleRate);
    var peak = 0;
    for (var i = 0; i < d.length; i++) peak = Math.max(peak, Math.abs(d[i]));
    if (peak > 0) for (var j = 0; j < d.length; j++) d[j] *= 0.5 / peak;
    return b;
  }
  function tone(d, sr, t0, dur, f, amp, decay) {      // 單一音：快速起音、指數衰減
    var s0 = Math.round(t0 * sr), n = Math.round(dur * sr);
    for (var i = 0; i < n && s0 + i < d.length; i++) {
      var t = i / sr, env = Math.min(1, t / 0.005) * Math.exp(-t / decay);
      d[s0 + i] += amp * env * Math.sin(2 * Math.PI * f * t);
    }
  }
  var SYNTH = {
    doorbell: function (ctx) {                        // 叮咚
      return buffer(ctx, 3, function (d, sr) {
        [[0, 659.25], [0.65, 523.25]].forEach(function (nt) {
          [[1, 1], [2, 0.45], [3, 0.22], [4.2, 0.12]].forEach(function (p) { tone(d, sr, nt[0], 2.2, nt[1] * p[0], p[1], 0.7 / p[0] + 0.15); });
        });
      });
    },
    birds: function (ctx) {                           // 鳥叫：3–6 kHz 的短促滑音
      return buffer(ctx, 3.2, function (d, sr) {
        var phrases = [[0.1, 5, 1], [1.0, 3, -1], [1.7, 6, 1], [2.6, 2, -1]];
        phrases.forEach(function (ph) {
          for (var c = 0; c < ph[1]; c++) {
            var t0 = ph[0] + c * 0.11, dur = 0.08, s0 = Math.round(t0 * sr), n = Math.round(dur * sr), phase = 0;
            for (var i = 0; i < n && s0 + i < d.length; i++) {
              var x = i / n, f = ph[2] > 0 ? 3600 + 2200 * x : 5600 - 2000 * x;
              f += 250 * Math.sin(2 * Math.PI * 40 * i / sr);
              phase += 2 * Math.PI * f / sr;
              d[s0 + i] += Math.sin(Math.PI * x) * Math.sin(phase);
            }
          }
        });
      });
    },
    beep: function (ctx) {                            // 電器嗶聲（微波爐、電鍋）：約 3 kHz
      return buffer(ctx, 3, function (d, sr) {
        for (var b = 0; b < 3; b++) {
          var s0 = Math.round((0.15 + b * 0.5) * sr), n = Math.round(0.25 * sr);
          for (var i = 0; i < n; i++) {
            var t = i / sr, env = Math.min(1, i / (0.004 * sr), (n - i) / (0.004 * sr));
            d[s0 + i] += env * (Math.sin(2 * Math.PI * 2900 * t) + 0.2 * Math.sin(2 * Math.PI * 8700 * t));
          }
        }
      });
    },
    phone: function (ctx) {                           // 手機鈴聲：1–2 kHz 的短旋律
      return buffer(ctx, 3, function (d, sr) {
        var notes = [1318.5, 1568, 1760, 1568, 1318.5, 1174.7, 1318.5, 1568];
        for (var r = 0; r < 2; r++) notes.forEach(function (f, i) {
          var t0 = 0.05 + r * 1.45 + i * 0.16;
          tone(d, sr, t0, 0.3, f, 1, 0.09); tone(d, sr, t0, 0.3, f * 2, 0.35, 0.05); tone(d, sr, t0, 0.3, f * 3, 0.15, 0.03);
        });
      });
    },
    cafe: function (ctx) {                            // 餐廳背景聲：起伏的人聲般噪音＋偶爾的碗盤聲
      return buffer(ctx, 6, function (d, sr) {
        var b0 = 0, b1 = 0, b2 = 0, lp = 0, hp = 0, prev = 0, seed = 7;
        function rnd() { seed = (seed * 16807) % 2147483647; return seed / 2147483647 * 2 - 1; }
        var mods = [[2.1, 0.3], [3.7, 1.1], [5.3, 2.0], [1.3, 4.2]];
        for (var i = 0; i < d.length; i++) {
          var w = rnd();                                // 粉紅雜訊（Paul Kellet 簡化版）
          b0 = 0.99765 * b0 + w * 0.0990460; b1 = 0.96300 * b1 + w * 0.2965164; b2 = 0.57000 * b2 + w * 1.0526913;
          var pink = b0 + b1 + b2 + w * 0.1848;
          lp += 0.18 * (pink - lp);                     // 約 1.5 kHz 以下（人聲的主要範圍）
          hp = 0.98 * (hp + lp - prev); prev = lp;      // 去掉很低的轟轟聲
          var t = i / sr, m = 0;
          mods.forEach(function (md) { m += Math.sin(2 * Math.PI * md[0] * t + md[1]); });
          d[i] = hp * (0.65 + 0.12 * m);
        }
        [[0.8, 3800], [2.3, 4600], [3.9, 3300], [5.1, 5200]].forEach(function (c) {
          tone(d, sr, c[0], 0.4, c[1], 0.9, 0.06); tone(d, sr, c[0], 0.4, c[1] * 1.47, 0.5, 0.04);
        });
      });
    }
  };

  // 錄音／選的檔案：去掉頭尾的安靜、限制長度、統一音量、尾巴留一點空白（循環播放時好分辨）
  function tidy(ctx, src, maxSecs) {
    var sr = src.sampleRate, ch = src.numberOfChannels, len = src.length, mono = new Float32Array(len);
    for (var c = 0; c < ch; c++) { var x = src.getChannelData(c); for (var i = 0; i < len; i++) mono[i] += x[i] / ch; }
    var peak = 0;
    for (var j = 0; j < len; j++) peak = Math.max(peak, Math.abs(mono[j]));
    if (peak < 0.002) return null;                   // 幾乎沒有聲音
    var th = peak * 0.04, a = 0, z = len - 1;
    while (a < len && Math.abs(mono[a]) < th) a++;
    while (z > a && Math.abs(mono[z]) < th) z--;
    a = Math.max(0, a - Math.round(0.08 * sr));
    z = Math.min(len - 1, z + Math.round(0.15 * sr), a + Math.round(maxSecs * sr));
    var pad = Math.round(0.7 * sr), out = ctx.createBuffer(1, z - a + 1 + pad, sr), d = out.getChannelData(0);
    for (var k = a; k <= z; k++) d[k - a] = mono[k] * 0.5 / peak;
    return out;
  }

  function decode(ctx, data) {
    return new Promise(function (ok, fail) {
      var p = ctx.decodeAudioData(data, ok, fail);   // 舊版 Safari 只有 callback 版
      if (p && p.then) p.then(ok, fail);
    });
  }

  // ── 頁面 ───────────────────────────────────────────────
  function $(id) { return document.getElementById(id); }

  function init() {
    var root = $("sim-root");
    if (!root) return;
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) { $("sim-unsupported").hidden = false; root.querySelector(".sim-body").hidden = true; return; }

    var ctx = null, nodes = null, playing = false;
    var state = { sound: null, level: "moderate", sim: true, cafe: false, loud: false };
    var buffers = {}, cafeBuf = null, mine = null;

    function audio() {
      if (!ctx) {
        ctx = new AC();
        try { if (navigator.audioSession) navigator.audioSession.type = "playback"; } catch (e) {}
      }
      if (ctx.state === "suspended") ctx.resume();
      return ctx;
    }

    function audiogram() {
      if (state.level === "mine" && mine) return { right: mine.right, left: mine.left };
      var p = PRESETS[state.level] || PRESETS.moderate;
      return { right: p.hl, left: p.hl };
    }

    function irBuffer(c) {
      var ag = audiogram(), b = c.createBuffer(2, IR_LEN, c.sampleRate);
      b.getChannelData(0).set(designIR(ag.left, c.sampleRate));     // 聲道 0＝左耳
      b.getChannelData(1).set(designIR(ag.right, c.sampleRate));
      return b;
    }

    function setMix() {
      if (!nodes) return;
      var t = ctx.currentTime, k = 0.04;
      nodes.dry.gain.setTargetAtTime(state.sim ? 0 : 1, t, k);
      nodes.wet.gain.setTargetAtTime(state.sim ? 1 : 0, t, k);
      nodes.loud.gain.setTargetAtTime(state.loud ? Math.pow(10, 20 / 20) : 1, t, k);
      nodes.cafe.gain.setTargetAtTime(state.cafe ? 0.35 : 0, t, k);
    }

    function stop() {
      if (nodes) {
        try { nodes.src.stop(); } catch (e) {}
        try { nodes.noise.stop(); } catch (e) {}
        nodes.master.disconnect();
        nodes = null;
      }
      playing = false;
      syncUI();
    }

    function play() {
      var c = audio(), buf = buffers[state.sound];
      if (!buf) return;
      stop();
      if (!cafeBuf) cafeBuf = SYNTH.cafe(c);
      var mix = c.createGain();
      mix.channelCount = 1; mix.channelCountMode = "explicit"; mix.channelInterpretation = "speakers";
      var src = c.createBufferSource(); src.buffer = buf; src.loop = true;
      var noise = c.createBufferSource(); noise.buffer = cafeBuf; noise.loop = true;
      var cafe = c.createGain(); cafe.gain.value = 0;
      src.connect(mix); noise.connect(cafe); cafe.connect(mix);
      var delay = c.createDelay(1); delay.delayTime.value = (IR_LEN - 1) / 2 / c.sampleRate;
      var dry = c.createGain(), wet = c.createGain(), loud = c.createGain();
      var conv = c.createConvolver(); conv.normalize = false; conv.buffer = irBuffer(c);
      var master = c.createDynamicsCompressor();       // 只當限幅器：「開大聲」時不爆音
      master.threshold.value = -3; master.knee.value = 0; master.ratio.value = 20;
      master.attack.value = 0.003; master.release.value = 0.25;
      mix.connect(delay); delay.connect(dry); dry.connect(master);
      mix.connect(conv); conv.connect(loud); loud.connect(wet); wet.connect(master);
      master.connect(c.destination);
      nodes = { src: src, noise: noise, cafe: cafe, mix: mix, dry: dry, wet: wet, loud: loud, conv: conv, master: master };
      dry.gain.value = state.sim ? 0 : 1;               // 起始值直接設好，開頭不會兩路一起響
      wet.gain.value = state.sim ? 1 : 0;
      loud.gain.value = state.loud ? Math.pow(10, 20 / 20) : 1;
      cafe.gain.value = state.cafe ? 0.35 : 0;
      src.start(); noise.start();
      playing = true;
      syncUI();
    }

    function rebuildFilter() {                        // 換聽力程度：換一組濾波器，不用重新播放
      if (!nodes) return;
      var conv = ctx.createConvolver(); conv.normalize = false; conv.buffer = irBuffer(ctx);
      nodes.mix.connect(conv); conv.connect(nodes.loud);
      nodes.mix.disconnect(nodes.conv); nodes.conv.disconnect();
      nodes.conv = conv;
    }

    // ── 畫面 ─────────────────────────────
    function press(container, value, attr) {
      var bs = container.querySelectorAll("button[" + attr + "]");
      for (var i = 0; i < bs.length; i++) bs[i].setAttribute("aria-pressed", bs[i].getAttribute(attr) === value ? "true" : "false");
    }

    function syncUI() {
      press($("sim-sounds"), state.sound, "data-sound");
      press($("sim-levels"), state.level, "data-level");
      press($("sim-ab"), state.sim ? "sim" : "orig", "data-ab");
      var pb = $("sim-play");
      pb.disabled = !buffers[state.sound];
      pb.classList.toggle("is-playing", playing);
      pb.querySelector("span").textContent = playing ? "停止" : "播放";
      $("sim-cafe").checked = state.cafe;
      $("sim-loud").checked = state.loud;
      var lv = state.level === "mine" ? "您的測驗結果" : (PRESETS[state.level] || PRESETS.moderate).label + "聽力損失";
      var now = $("sim-now");
      now.textContent = !buffers[state.sound] ? "請先選一個聲音" :
        (playing ? "正在播放：" : "準備播放：") + (state.sim ? lv + "時聽到的聲音（示意）" : "原本的聲音");
      now.className = "sim-now " + (state.sim ? "is-sim" : "is-orig");
      $("sim-loud-tip").hidden = !(state.loud && state.sim);
      var p = PRESETS[state.level];
      $("sim-level-desc").textContent = state.level === "mine"
        ? "用您在線上聽力測驗的結果（右耳、左耳分開）。可以讓家人戴上耳機，聽聽看您聽到的聲音。"
        : p.desc;
      drawChart();
    }

    function pickSound(name) {
      state.sound = name;
      var c = audio();
      if (!buffers[name] && SYNTH[name]) buffers[name] = SYNTH[name](c);
      if (buffers[name]) play(); else { stop(); }
      syncUI();
    }

    var soundBox = $("sim-sounds");
    soundBox.addEventListener("click", function (e) {
      var b = e.target.closest("button[data-sound]");
      if (!b) return;
      var name = b.getAttribute("data-sound");
      if (name === "talk" && !buffers.talk) { loadTalk(b); return; }
      if (name === "rec") { record(b); return; }
      if (name === "file" ) { if (buffers.file) pickSound("file"); else $("sim-file").click(); return; }
      pickSound(name);
    });

    // 門市錄的說話聲（有放檔案才有這顆按鈕）
    function loadTalk(btn) {
      var url = btn.getAttribute("data-src");
      btn.disabled = true;
      fetch(url).then(function (r) { if (!r.ok) throw new Error(r.status); return r.arrayBuffer(); })
        .then(function (data) { return decode(audio(), data); })
        .then(function (b) { buffers.talk = tidy(ctx, b, FILE_SECS); btn.disabled = false; pickSound("talk"); })
        .catch(function () { btn.disabled = false; msg("說話聲的檔案讀不到，請先試試其他聲音。"); });
    }

    // 自己選的錄音檔（手機的「語音備忘錄」錄好再選）
    $("sim-file").addEventListener("change", function (e) {
      var f = e.target.files[0];
      e.target.value = "";
      if (!f) return;
      if (f.size > FILE_MAX) { msg("檔案太大了，請選 30 秒以內的錄音。"); return; }
      var rd = new FileReader();
      rd.onload = function () {
        decode(audio(), rd.result).then(function (b) {
          var t = tidy(ctx, b, FILE_SECS);
          if (!t) { msg("這個檔案聽起來沒有聲音，請換一個。"); return; }
          buffers.file = t;
          msg("");
          pickSound("file");
        }).catch(function () { msg("這個檔案無法播放，請換成 m4a、mp3 或 wav 錄音檔。"); });
      };
      rd.readAsArrayBuffer(f);
    });

    // 用麥克風錄一句（瀏覽器規定要 https 才能用麥克風；不支援就不顯示這顆按鈕）
    var rec = null;
    var canRec = window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder;
    var recBtn = soundBox.querySelector('[data-sound="rec"]');
    if (canRec && recBtn) recBtn.hidden = false;
    function record(btn) {
      if (rec) { rec.stop(); return; }
      if (buffers.rec && !window.confirm("要重新錄一句嗎？（按「取消」播放剛剛錄的）")) { pickSound("rec"); return; }
      stop();
      audio();
      navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false } }).then(function (stream) {
        var chunks = [], mr = new MediaRecorder(stream), timer = null, left = REC_SECS;
        var label = btn.querySelector("span");
        rec = mr;
        mr.ondataavailable = function (ev) { if (ev.data && ev.data.size) chunks.push(ev.data); };
        mr.onstop = function () {
          clearInterval(timer);
          stream.getTracks().forEach(function (t) { t.stop(); });
          rec = null;
          btn.classList.remove("is-rec");
          label.textContent = "自己錄一句";
          new Blob(chunks).arrayBuffer().then(function (data) { return decode(ctx, data); }).then(function (b) {
            var t = tidy(ctx, b, REC_SECS);
            if (!t) { msg("沒有錄到聲音，請靠近麥克風再錄一次。"); return; }
            buffers.rec = t;
            msg("");
            pickSound("rec");
          }).catch(function () { msg("錄音無法播放，請改用「選錄音檔」。"); });
        };
        mr.start();
        btn.classList.add("is-rec");
        label.textContent = "錄音中… 按這裡停止（" + left + "）";
        timer = setInterval(function () {
          left--;
          label.textContent = "錄音中… 按這裡停止（" + left + "）";
          if (left <= 0) mr.stop();
        }, 1000);
        msg("請唸一句家裡常說的話，例如：「爸，吃飯了，今天有你喜歡的魚。」");
      }).catch(function () { msg("沒有取得麥克風權限。可以改用「選錄音檔」，或在瀏覽器設定裡允許使用麥克風。"); });
    }

    function msg(t) { var m = $("sim-msg"); m.textContent = t; m.hidden = !t; }

    $("sim-levels").addEventListener("click", function (e) {
      var b = e.target.closest("button[data-level]");
      if (!b) return;
      state.level = b.getAttribute("data-level");
      rebuildFilter();
      syncUI();
    });
    $("sim-ab").addEventListener("click", function (e) {
      var b = e.target.closest("button[data-ab]");
      if (!b) return;
      state.sim = b.getAttribute("data-ab") === "sim";
      setMix();
      syncUI();
    });
    $("sim-play").addEventListener("click", function () { if (playing) stop(); else play(); });
    $("sim-cafe").addEventListener("change", function (e) { state.cafe = e.target.checked; setMix(); syncUI(); });
    $("sim-loud").addEventListener("change", function (e) { state.loud = e.target.checked; setMix(); syncUI(); });
    document.addEventListener("visibilitychange", function () { if (document.hidden && playing) stop(); });

    // 線上聽力測驗的結果（有才顯示「我的測驗結果」）
    try {
      var raw = sessionStorage.getItem(SIM_KEY);
      if (raw) {
        var r = JSON.parse(raw), ok = true, pick = function (ear) {
          return FREQS.map(function (f) { var v = Number(r[ear] && r[ear][f]); if (!isFinite(v)) ok = false; return v; });
        };
        mine = { right: pick("right"), left: pick("left") };
        if (!ok) mine = null;
      }
    } catch (e) { mine = null; }
    var mineBtn = $("sim-levels").querySelector('[data-level="mine"]');
    if (mine && mineBtn) {
      mineBtn.hidden = false;
      if (/[?&]mine=1/.test(location.search)) state.level = "mine";
    }

    // ── 小聽力圖 ─────────────────────────
    var NS = "http://www.w3.org/2000/svg";
    function el(name, attrs, text) {
      var e = document.createElementNS(NS, name);
      for (var k in attrs) e.setAttribute(k, attrs[k]);
      if (text != null) e.textContent = text;
      return e;
    }
    function drawChart() {
      var svg = $("sim-chart");
      while (svg.firstChild) svg.removeChild(svg.firstChild);
      var W = 360, H = 230, L = 40, R = 12, T = 12, B = 30, pw = W - L - R, ph = H - T - B;
      var lo = Math.log2(200), hi = Math.log2(10000);
      var x = function (f) { return L + (Math.log2(f) - lo) / (hi - lo) * pw; };
      var y = function (d) { return T + (d + 10) / 110 * ph; };
      [[-10, 25, "#dcfce7"], [25, 40, "#fef9c3"], [40, 55, "#ffedd5"], [55, 70, "#fee2e2"], [70, 90, "#fecaca"], [90, 100, "#f3e8ff"]].forEach(function (b) {
        svg.appendChild(el("rect", { x: L, y: y(b[0]), width: pw, height: y(b[1]) - y(b[0]), fill: b[2] }));
      });
      for (var d = 0; d <= 100; d += 20) {
        svg.appendChild(el("line", { x1: L, x2: L + pw, y1: y(d), y2: y(d), stroke: "#cbd5e1", "stroke-width": 0.7 }));
        svg.appendChild(el("text", { x: L - 6, y: y(d) + 4, "font-size": 11, fill: "#475569", "text-anchor": "end" }, d));
      }
      FREQS.forEach(function (f) {
        svg.appendChild(el("text", { x: x(f), y: H - 10, "font-size": 11, fill: "#475569", "text-anchor": "middle" }, f >= 1000 ? f / 1000 + "k" : f));
      });
      svg.appendChild(el("rect", { x: L, y: T, width: pw, height: ph, fill: "none", stroke: "#334155" }));
      var ag = audiogram(), same = ag.right.join() === ag.left.join();
      var lines = same ? [[ag.right, "#0853a5", ""]] : [[ag.right, "#c52020", ""], [ag.left, "#1d4ed8", "5 4"]];
      lines.forEach(function (ln) {
        svg.appendChild(el("polyline", { points: FREQS.map(function (f, i) { return x(f) + "," + y(ln[0][i]); }).join(" "),
          fill: "none", stroke: ln[1], "stroke-width": 2.5, "stroke-dasharray": ln[2] }));
        FREQS.forEach(function (f, i) { svg.appendChild(el("circle", { cx: x(f), cy: y(ln[0][i]), r: 4, fill: "#fff", stroke: ln[1], "stroke-width": 2 })); });
      });
      $("sim-chart-cap").textContent = same ? "聽力圖：越往下，代表那個頻率要越大聲才聽得到（dB HL）。"
        : "聽力圖：紅色實線＝右耳、藍色虛線＝左耳；越往下，代表要越大聲才聽得到。";
    }

    syncUI();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
