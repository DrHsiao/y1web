// 每日聽練（測試版）：每天 1–3 分鐘的聽覺小練習，練完打卡、累積連續天數（Duolingo 式）。
//
// - 一回 15 題：聽數字、高低音、數聲音各 5 題（順序每回打亂）。每種練習各有等級 Lv1–10：
//   連續答對 2 題升一級、答錯降一級，練完把等級存起來，下次從那裡開始。
// - 這是練習，不是檢查：不判讀、不宣稱效果（醫療器材廣告的限制）；說明頁有就醫提醒。
// - 紀錄：每天練了幾回（n）、經驗值（xp）；2026-11-02 起每天另記練了幾秒（t）、各項練習答了幾題／答對幾題／練完的等級（s），
//   給門市人員在客戶資料裡看（John 要「盡可能看見所有練習細節」）。不記每一題的題目和答案。
// - 紀錄存在這台裝置的瀏覽器（localStorage，KEY）。換手機可以用「備份碼」合併（merge）；
//   登入官網會員的話，打開時、練完時和伺服器合併（../api/member/progress/practice，伺服器用一樣的 merge：
//   app/progress.py，tests 隨機比對兩邊結果一樣）。沒登入、沒網路、或整個資料夾搬到沒有這個 API 的地方，都照舊只存在瀏覽器。
// - 自成一個資料夾、路徑一律相對，不用到官網其他檔案：整個搬走就能獨立，或包成 app。
// - 數字錄音和雜音做法同官網 /static/js/noise-test.js（Google cmn-TW-Wavenet-A，雜音＝數字錯開疊加 1000 層）。
//   這裡不用反相、不用耳機檢查：聲音兩耳相同，用喇叭也能練。
(function () {
  "use strict";

  var KEY = "y1-practice";
  var MAX_LV = 10, UP_RUN = 2;           // 連續答對 2 題升一級、答錯降一級
  var PER_SKILL = 5;                     // 每回每種 5 題，共 15 題，約 2 分鐘
  var XP_RIGHT = 10, XP_DONE = 20;       // 答對一題 10、練完一回再加 20
  var MILESTONES = [3, 7, 14, 30, 50, 100, 200, 365];

  // 難度表（Lv1–10）
  var DIGIT_SNR = [12, 8, 4, 1, -2, -4, -6, -8, -10, -12];               // 數字比雜音大幾 dB
  var PITCH_PCT = [40, 25, 16, 10, 7, 5, 3.5, 2.5, 1.8, 1.2];             // 兩個音的高低差（%）
  var COUNT_IOI = [450, 380, 320, 270, 230, 200, 175, 155, 140, 125];     // 嗶聲間隔（毫秒）
  var COUNT_MIN = 2, COUNT_MAX = 6;

  var SKILLS = {
    digit: { name: "聽數字", ask: "雜音裡說了哪個數字？", desc: "在雜音裡聽一個數字" },
    pitch: { name: "高低音", ask: "哪一個音比較高？", desc: "兩個音，分辨哪個比較高" },
    count: { name: "數聲音", ask: "一共響了幾聲？", desc: "數數嗶聲響了幾下" }
  };
  var ORDER = ["digit", "pitch", "count"];

  // ── 純計算（node 也能跑，tests/test_js.py）────────────────
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function ymd(d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
  function parseYmd(s) { var p = s.split("-"); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function addDays(s, n) { var d = parseYmd(s); d.setDate(d.getDate() + n); return ymd(d); }

  // 連續打卡天數：今天練了就從今天往回數，還沒練就從昨天數（今天還有機會接上）
  function streak(days, today) {
    var d = days[today] ? today : addDays(today, -1), n = 0;
    while (days[d]) { n++; d = addDays(d, -1); }
    return n;
  }
  // 這一週（週一到週日）每天有沒有練
  function week(days, today) {
    var dow = (parseYmd(today).getDay() + 6) % 7, out = [];
    for (var i = 0; i < 7; i++) {
      var d = addDays(today, i - dow);
      out.push({ d: d, done: !!days[d], today: d === today, future: d > today });
    }
    return out;
  }
  function totalXp(s) {
    var t = 0;
    for (var k in s.days) if (Object.prototype.hasOwnProperty.call(s.days, k)) t += s.days[k].xp;
    return t;
  }

  function fresh() { return { v: 1, days: {}, lv: { digit: 1, pitch: 1, count: 1 }, best: 0, vol: "" }; }

  var DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
  function int(v, lo, hi) { v = Math.round(Number(v)); return isFinite(v) ? Math.max(lo, Math.min(hi, v)) : lo; }
  // 一天的紀錄：{ n, xp, t?, s? }。t、s 是 11-02 才加的，舊紀錄沒有；沒練到的項目不放（s 裡只有 q ≥ 1 的）
  function cleanDay(x) {
    var d = { n: int(x.n, 1, 99), xp: int(x.xp, 0, 99999) }, t = int(x.t, 0, 86400), s = {}, any = false;
    if (t > 0) d.t = t;
    var sk = x.s && typeof x.s === "object" ? x.s : {};
    ORDER.forEach(function (k) {
      var o = sk[k];
      if (!o || typeof o !== "object") return;
      var q = int(o.q, 0, 9999);
      if (q > 0) { s[k] = { q: q, r: Math.min(q, int(o.r, 0, 9999)), lv: int(o.lv, 1, MAX_LV) }; any = true; }
    });
    if (any) d.s = s;
    return d;
  }
  // 同一天兩份（兩台裝置都練了）：每一項都取較多的，和整份的 merge 一樣不管順序、重複合併都不變
  function mergeDay(x, y) {
    var d = { n: Math.max(x.n, y.n), xp: Math.max(x.xp, y.xp), t: Math.max(x.t || 0, y.t || 0), s: {} };
    ORDER.forEach(function (k) {
      var a = x.s && x.s[k], b = y.s && y.s[k];
      if (a && b) d.s[k] = { q: Math.max(a.q, b.q), r: Math.max(a.r, b.r), lv: Math.max(a.lv, b.lv) };
      else if (a || b) d.s[k] = a || b;
    });
    return cleanDay(d);
  }
  // 練完一回加進當天的紀錄。r＝{ xp, sec 這回花了幾秒, sk: { digit: { q, r }, … } 這回各項的題數／答對, lv: 練完的等級 }
  // 一回最多算 10 分鐘（畫面開著沒練、隔天才按完，不會灌進好幾個鐘頭）
  function addRound(day, r) {
    day = day ? cleanDay(day) : { n: 0, xp: 0 };
    var d = { n: day.n + 1, xp: day.xp + r.xp, t: (day.t || 0) + Math.min(600, r.sec), s: {} };
    ORDER.forEach(function (k) {
      var o = (day.s && day.s[k]) || { q: 0, r: 0 }, now = r.sk[k] || { q: 0, r: 0 };
      d.s[k] = { q: o.q + now.q, r: o.r + now.r, lv: r.lv[k] };
    });
    return cleanDay(d);
  }
  // 讀進來的資料（localStorage 或使用者貼的備份碼）一律重新整理，認不得的欄位丟掉
  function clean(o) {
    var s = fresh();
    if (!o || typeof o !== "object") return s;
    var days = o.days && typeof o.days === "object" ? o.days : {};
    Object.keys(days).filter(function (k) { return DATE_RE.test(k); }).sort().slice(-4000).forEach(function (k) {
      var x = days[k];
      if (x && typeof x === "object") s.days[k] = cleanDay(x);
    });
    var lv = o.lv && typeof o.lv === "object" ? o.lv : {};
    ORDER.forEach(function (k) { if (lv[k] != null) s.lv[k] = int(lv[k], 1, MAX_LV); });
    s.best = int(o.best, 0, 99999);
    if (typeof o.vol === "string" && DATE_RE.test(o.vol)) s.vol = o.vol;
    return s;
  }
  // 兩份紀錄合併（備份碼、會員同步）：每天取較多的、等級取較高的；不管合併幾次、誰先誰後，結果都一樣
  function merge(a, b) {
    a = clean(a); b = clean(b);
    var out = clean(a);
    Object.keys(b.days).forEach(function (k) {
      var x = out.days[k], y = b.days[k];
      out.days[k] = x ? mergeDay(x, y) : y;
    });
    ORDER.forEach(function (k) { out.lv[k] = Math.max(a.lv[k], b.lv[k]); });
    out.best = Math.max(a.best, b.best);
    out.vol = a.vol > b.vol ? a.vol : b.vol;
    return clean(out);                   // 日期重新排序：誰先誰後合併，內容和順序都一樣
  }

  // 登入會員後怎麼處理這台裝置上的紀錄：上次同步的是同一位（或從沒同步過）→ 合併；
  // 換了一個人登入 → 改用這個人的紀錄（前一位的已經在他自己的會員裡，不併進別人的）。push＝伺服器還少東西，要送上去
  function adopt(local, server, owner, uid) {
    var s = owner && owner !== uid ? clean(server) : merge(local, server);
    return { state: s, push: JSON.stringify(s) !== JSON.stringify(clean(server)) };
  }

  // 備份碼：Y1P1- 加上 JSON 的 base64（內容只有日期和數字，都是 ASCII）
  var CODE_PREFIX = "Y1P1-";
  function encode(s) { return CODE_PREFIX + btoa(JSON.stringify(clean(s))); }
  function decode(code) {
    code = String(code || "").replace(/\s+/g, "");
    if (code.indexOf(CODE_PREFIX) !== 0 || code.length > 1200000) return null;
    try { return clean(JSON.parse(atob(code.slice(CODE_PREFIX.length)))); } catch (e) { return null; }
  }

  // 一題答完後的等級：st＝{ lv, run（連續答對幾題）}
  function step(st, ok) {
    if (!ok) return { lv: Math.max(1, st.lv - 1), run: 0 };
    return st.run + 1 >= UP_RUN ? { lv: Math.min(MAX_LV, st.lv + 1), run: 0 } : { lv: st.lv, run: st.run + 1 };
  }

  function shuffle(a, rand) {
    for (var i = a.length - 1; i > 0; i--) { var j = Math.floor(rand() * (i + 1)), t = a[i]; a[i] = a[j]; a[j] = t; }
    return a;
  }
  // 一回的題目順序：三種練習各 PER_SKILL 題，同種的排在一起（少換來換去），種類順序打亂
  function plan(rand) {
    var out = [];
    shuffle(ORDER.slice(), rand || Math.random).forEach(function (k) {
      for (var i = 0; i < PER_SKILL; i++) out.push(k);
    });
    return out;
  }

  // 出一題。prev＝同種練習上一題的答案（數字、聲數不連續重複）
  function makeQ(skill, lv, rand, prev) {
    rand = rand || Math.random;
    if (skill === "digit") {
      var d;
      do { d = Math.floor(rand() * 10); } while (d === prev);
      return { skill: skill, lv: lv, answer: d, snr: DIGIT_SNR[lv - 1] };
    }
    if (skill === "pitch") {
      var base = 300 * Math.pow(3, rand()), hi = rand() < 0.5 ? 0 : 1, high = base * (1 + PITCH_PCT[lv - 1] / 100);
      return { skill: skill, lv: lv, answer: hi, freqs: hi ? [base, high] : [high, base] };   // 300–900 Hz，老人家聽得到的範圍
    }
    var n;
    do { n = COUNT_MIN + Math.floor(rand() * (COUNT_MAX - COUNT_MIN + 1)); } while (n === prev);
    return { skill: skill, lv: lv, answer: n, ioi: COUNT_IOI[lv - 1] };
  }

  function mmss(ms) { var s = Math.round(ms / 1000); return Math.floor(s / 60) + ":" + pad(s % 60); }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { ymd: ymd, addDays: addDays, streak: streak, week: week, totalXp: totalXp, fresh: fresh,
      clean: clean, merge: merge, addRound: addRound, adopt: adopt, encode: encode, decode: decode, step: step, plan: plan, makeQ: makeQ, mmss: mmss,
      MAX_LV: MAX_LV, PER_SKILL: PER_SKILL, ORDER: ORDER, DIGIT_SNR: DIGIT_SNR, PITCH_PCT: PITCH_PCT, COUNT_IOI: COUNT_IOI };
  }
  if (typeof document === "undefined") return;

  // ── 畫面 ──────────────────────────────────────────────
  // 圖示用內嵌 SVG，不用表情符號：舊手機、電腦的表情符號字型不一定有，也不會每台長得一樣
  function svg(body, cls) { return '<svg class="i ' + cls + '" viewBox="0 0 24 24" aria-hidden="true" focusable="false">' + body + "</svg>"; }
  var ICON = {
    flame: svg('<path fill="currentColor" d="M12 2c.7 3.3 3.6 5.2 4.8 7.9 1.7 3.8-.6 9.1-4.8 9.1S5.4 15.6 6.8 11.6c.6-1.7 1.8-2.7 2.6-3.7.2 1.6.9 2.8 1.9 3.4C11 8.2 11.4 5 12 2z"/>' +
      '<path fill="#ffd43b" d="M12 12.5c.4 1.5 1.9 2.4 1.9 4a1.9 1.9 0 0 1-3.8 0c0-1.4 1.3-2.4 1.9-4z"/>', "i-flame"),
    star: svg('<path fill="currentColor" d="M12 2.8l2.8 5.8 6.3.9-4.6 4.4 1.1 6.3L12 17.3l-5.6 2.9 1.1-6.3-4.6-4.4 6.3-.9z"/>', "i-star"),
    speaker: svg('<path fill="currentColor" d="M3.5 9h4l5.5-4.5v15L7.5 15h-4z"/>' +
      '<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M16.5 8.5a5 5 0 0 1 0 7M19.2 5.8a8.8 8.8 0 0 1 0 12.4"/>', "i-speaker"),
    digit: svg('<text x="12" y="16.2" text-anchor="middle" font-size="10.5" font-weight="800" fill="currentColor" font-family="system-ui,sans-serif">123</text>', "i-digit"),
    pitch: svg('<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M9 17.5V6l10-2v11.5"/>' +
      '<circle cx="6.5" cy="17.5" r="2.7" fill="currentColor"/><circle cx="16.5" cy="15.5" r="2.7" fill="currentColor"/>', "i-pitch"),
    count: svg('<path fill="currentColor" d="M12 3a6 6 0 0 0-6 6v4.5L4 17h16l-2-3.5V9a6 6 0 0 0-6-6z"/><path fill="currentColor" d="M9.5 18.5h5a2.5 2.5 0 0 1-5 0z"/>', "i-count")
  };
  function badge(k) { return '<span class="badge b-' + k + '">' + ICON[k] + "</span>"; }

  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function today() { return ymd(new Date()); }

  var state = fresh(), canStore = true;
  try {
    localStorage.setItem(KEY + "-t", "1"); localStorage.removeItem(KEY + "-t");
    state = clean(JSON.parse(localStorage.getItem(KEY) || "null"));
  } catch (e) { canStore = false; }
  function save() {
    if (!canStore) return false;
    try { localStorage.setItem(KEY, JSON.stringify(state)); return true; } catch (e) { return false; }
  }

  // ── 會員同步 ─────────────────────────────────────────────
  var API = "../api/member/progress/practice";
  var OWNER = KEY + "-owner", PENDING = KEY + "-pending";   // 這台裝置上次同步的會員代號；有沒有還沒送上去的
  var who = null, hasApi = false;                           // who＝登入中的會員 {name, uid}
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) {} }
  function api(method, body) {
    var opt = { method: method, credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } };
    if (body) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    return fetch(API, opt).then(function (r) {
      if (r.status === 404) { hasApi = false; return null; }   // 搬到沒有會員功能的地方
      hasApi = true;
      return r.status === 401 ? { member: null } : r.ok ? r.json() : Promise.reject(r.status);
    });
  }
  function push() {
    if (!who || !canStore) return;
    lsSet(PENDING, "1");
    api("PUT", state).then(function (j) {
      if (!j || !j.member) { who = null; renderSync(); return; }
      state = merge(state, j.data); save(); lsSet(PENDING, null); renderSync();
    }, function () { renderSync(); });                     // 沒網路：留著 PENDING，下次打開或連上網路再送
  }
  function sync() {
    if (!window.fetch || !canStore) return;
    api("GET").then(function (j) {
      who = j && j.member ? j.member : null;
      if (who) {
        var r = adopt(state, j.data, lsGet(OWNER), who.uid);
        state = r.state; save(); lsSet(OWNER, who.uid);
        if (r.push || lsGet(PENDING)) push();
        if (!$("home").hidden) renderHome();
      }
      renderSync();
    }, function () { renderSync(); });
  }
  function renderSync() {
    var msg = who ? "已登入會員：" + who.name + "。紀錄會自動保存，換手機也不會不見。" +
      (lsGet(PENDING) ? "（還有紀錄沒送上去，連上網路會自動補上）" : "") : "";
    $("h-sync").textContent = msg; $("h-sync").hidden = !who;
    $("h-login").hidden = who || !hasApi || !canStore;
    $("b-member").textContent = msg; $("b-member").hidden = !who;
  }
  window.addEventListener("online", function () { if (who && lsGet(PENDING)) push(); else if (!who) sync(); });

  var timers = [];
  function later(fn, ms) { timers.push(setTimeout(fn, ms)); }
  function clearTimers() { timers.forEach(clearTimeout); timers = []; }

  function show(id) {
    Array.prototype.forEach.call(document.querySelectorAll(".scr"), function (s) { s.hidden = s.id !== id; });
    window.scrollTo(0, 0);
    if (id === "home") renderHome();
    if (id === "backup") { $("b-code").value = encode(state); $("b-msg").textContent = ""; }
  }
  // 離開首頁時多一筆瀏覽紀錄：手機的「返回」會回到首頁，而不是直接離開
  function go(id) {
    var pushed = history.state && history.state.p;
    if (id === "home") { if (pushed) history.back(); else show("home"); return; }
    if (!pushed) history.pushState({ p: 1 }, "");
    show(id);
  }
  window.addEventListener("popstate", function () { stopLesson(); show("home"); });
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-go]");
    if (b) { stopLesson(); go(b.getAttribute("data-go")); }
  });

  function weekHtml(days) {
    var names = "一二三四五六日";
    return week(days, today()).map(function (w, i) {
      var cls = (w.done ? " done" : "") + (w.today ? " today" : "") + (w.future ? " future" : "");
      return '<li class="' + cls.trim() + '"><span class="dn">' + names[i] + '</span><span class="dot">' +
        (w.done ? "✓" : "") + "</span></li>";
    }).join("");
  }

  function renderHome() {
    var t = today(), s = streak(state.days, t), done = !!state.days[t];
    $("h-streak").textContent = s;
    $("h-fire").classList.toggle("cold", !done);
    $("h-xp").textContent = totalXp(state);
    $("h-nostore").hidden = canStore;
    $("h-title").textContent = done ? "今天已經打卡 ✓" : s ? "已經連續 " + s + " 天" : "今天還沒練習";
    $("h-sub").textContent = done ? "想多練也可以，經驗值會再累積。"
      : s ? "今天練完就是連續 " + (s + 1) + " 天。大約 2 分鐘。" : "大約 2 分鐘，練完就打卡。";
    $("h-start").textContent = done ? "再練一回" : "開始今天的練習";
    $("h-week").innerHTML = weekHtml(state.days);
    $("h-skills").innerHTML = ORDER.map(function (k) {
      var lv = state.lv[k];
      return '<li class="skill">' + badge(k) +
        '<span class="s-main"><b>' + SKILLS[k].name + '</b><small>' + SKILLS[k].desc + "</small>" +
        '<span class="lvbar" aria-hidden="true"><span class="lv' + lv + '"></span></span></span>' +
        '<span class="lv">Lv ' + lv + "</span></li>";
    }).join("");
  }

  // ── 聲音 ──────────────────────────────────────────────
  var NOISE_GAIN = 0.05;               // 雜音 RMS；數字＝雜音 × 10^(訊噪比/20)，Lv1 的 +12 dB 時峰值約 0.8
  var NOISE_LAYERS = 1000, LEAD = 0.5, TAIL = 0.4;
  var TONE_GAIN = 0.12, TONE_DUR = 0.4, TONE_GAP = 0.35, BEEP_DUR = 0.06, BEEP_FREQ = 700;
  var AC = window.AudioContext || window.webkitAudioContext;
  var ctx = null, out = null, wave = null, digits = null, noise = null, loading = null;

  function audio() {
    if (!ctx) {
      ctx = new AC();
      try { if (navigator.audioSession) navigator.audioSession.type = "playback"; } catch (e) {}   // iPhone 靜音開關
      out = ctx.createDynamicsCompressor();          // 只當限幅器，避免偶爾的峰值爆音
      out.threshold.value = -3; out.knee.value = 0; out.ratio.value = 20;
      out.attack.value = 0.003; out.release.value = 0.25;
      out.connect(ctx.destination);
    }
    if (ctx.state === "suspended") ctx.resume();
    return ctx;
  }

  function decodeAudio(c, data) {
    return new Promise(function (ok, fail) {
      var p = c.decodeAudioData(data, ok, fail);
      if (p && p.then) p.then(ok, fail);
    });
  }
  // 錄音：轉單聲道、去掉頭尾安靜、前後 5 ms 淡入淡出、RMS 統一為 1（同 noise-test.js）
  function prep(c, b) {
    var ch = b.numberOfChannels, n = b.length, m = new Float32Array(n);
    for (var k = 0; k < ch; k++) { var x = b.getChannelData(k); for (var i = 0; i < n; i++) m[i] += x[i] / ch; }
    var peak = 0;
    for (var j = 0; j < n; j++) peak = Math.max(peak, Math.abs(m[j]));
    var th = peak * 0.05, a = 0, z = n - 1;
    while (a < n && Math.abs(m[a]) < th) a++;
    while (z > a && Math.abs(m[z]) < th) z--;
    a = Math.max(0, a - Math.round(0.02 * b.sampleRate));
    z = Math.min(n - 1, z + Math.round(0.05 * b.sampleRate));
    var o = c.createBuffer(1, z - a + 1, b.sampleRate), d = o.getChannelData(0), ss = 0;
    for (var q = a; q <= z; q++) { d[q - a] = m[q]; ss += m[q] * m[q]; }
    var rms = Math.sqrt(ss / d.length) || 1, fade = Math.min(Math.round(0.005 * b.sampleRate), d.length >> 2);
    for (var r = 0; r < d.length; r++) {
      var w = r < fade ? r / fade : r >= d.length - fade ? (d.length - 1 - r) / fade : 1;
      d[r] = d[r] / rms * w;
    }
    return o;
  }
  // 雜音：數字錄音隨機錯開疊加，頻譜和說話聲一樣、但聽不出字（RMS 統一為 1）
  function makeNoise(c, bs) {
    var sr = c.sampleRate, len = Math.round(4 * sr), buf = c.createBuffer(1, len, sr), d = buf.getChannelData(0);
    for (var r = 0; r < NOISE_LAYERS; r++) {
      var src = bs[r % 10].getChannelData(0), off = Math.floor(Math.random() * len);
      for (var i = 0; i < src.length; i++) d[(off + i) % len] += src[i];
    }
    var ss = 0;
    for (var j = 0; j < len; j++) ss += d[j] * d[j];
    var rms = Math.sqrt(ss / len) || 1;
    for (var k = 0; k < len; k++) d[k] /= rms;
    return buf;
  }
  function loadAudio() {
    if (loading) return loading;
    var c = audio();
    loading = Promise.all([0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map(function (n) {
      return fetch("audio/digits/" + n + ".wav").then(function (r) { if (!r.ok) throw new Error(n); return r.arrayBuffer(); })
        .then(function (data) { return decodeAudio(c, data); }).then(function (b) { return prep(c, b); });
    })).then(function (bs) { digits = bs; noise = makeNoise(c, bs); });
    loading.catch(function () { loading = null; });
    return loading;
  }

  // 數字（noisy＝加雜音）；回傳從現在起到聲音結束的秒數
  function playDigit(n, snr, noisy) {
    var c = audio(), t0 = c.currentTime + 0.08, t = noisy ? t0 + LEAD : t0;
    var s = c.createBufferSource(), g = c.createGain();
    s.buffer = digits[n]; g.gain.value = NOISE_GAIN * Math.pow(10, snr / 20);
    s.connect(g); g.connect(out); s.start(t);
    var end = t + digits[n].duration + (noisy ? TAIL : 0);
    if (noisy) {
      var ns = c.createBufferSource(), env = c.createGain();
      ns.buffer = noise; ns.loop = true;
      env.gain.setValueAtTime(0, t0);
      env.gain.linearRampToValueAtTime(NOISE_GAIN, t0 + 0.05);
      env.gain.setValueAtTime(NOISE_GAIN, end - 0.05);
      env.gain.linearRampToValueAtTime(0, end);
      ns.connect(env); env.connect(out);
      ns.start(t0, Math.random() * (noise.duration - 1));
      ns.stop(end + 0.05);
    }
    return end - c.currentTime;
  }
  // 帶泛音的音（比純音好聽，聽損的人也比較聽得到）
  function tone(t, f, dur, gain) {
    var c = audio(), o = c.createOscillator(), g = c.createGain(), ramp = Math.min(0.02, dur / 6);
    if (!wave) wave = c.createPeriodicWave(new Float32Array([0, 0, 0, 0, 0]), new Float32Array([0, 1, 0.5, 0.3, 0.2]));
    o.setPeriodicWave(wave); o.frequency.value = f;
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(gain, t + ramp);
    g.gain.setValueAtTime(gain, t + dur - ramp);
    g.gain.linearRampToValueAtTime(0, t + dur);
    o.connect(g); g.connect(out);
    o.start(t); o.stop(t + dur + 0.02);
  }
  // 兩個音；onTone(i, 毫秒後) 讓畫面跟著亮
  function playPitch(freqs, onTone) {
    var c = audio(), t = c.currentTime + 0.08;
    freqs.forEach(function (f, i) {
      var at = t + i * (TONE_DUR + TONE_GAP);
      tone(at, f, TONE_DUR, TONE_GAIN);
      if (onTone) onTone(i, (at - c.currentTime) * 1000);
    });
    return t + 2 * TONE_DUR + TONE_GAP - c.currentTime;
  }
  function playCount(n, ioi, onBeep) {
    var c = audio(), t = c.currentTime + 0.08;
    for (var i = 0; i < n; i++) {
      var at = t + i * ioi / 1000;
      tone(at, BEEP_FREQ, BEEP_DUR, TONE_GAIN);
      if (onBeep) onBeep(i, (at - c.currentTime) * 1000);
    }
    return t + (n - 1) * ioi / 1000 + BEEP_DUR - c.currentTime;
  }

  // ── 練習 ──────────────────────────────────────────────
  var L = null;          // 進行中的一回：{ plan, i, st, from, right, sk（各項題數／答對）, t0, prev, q, answered }
  var busy = false;      // 正在播放：選項先鎖住，避免沒聽完就按

  function stopLesson() { clearTimers(); L = null; busy = false; }

  function startFlow() {
    var btn = $("h-start");
    $("h-err").hidden = true;
    if (!AC) { $("h-err").textContent = "這個瀏覽器不能播放練習的聲音，請改用 Chrome 或 Safari。"; $("h-err").hidden = false; return; }
    audio();                                   // 一定要在按鈕的點擊裡建立／喚醒，手機才會出聲
    btn.disabled = true; btn.textContent = "準備中…";
    loadAudio().then(function () {
      btn.disabled = false;
      go(state.vol === today() ? "lesson" : "volume");
      if (state.vol === today()) begin();
    }).catch(function () {
      btn.disabled = false; renderHome();
      $("h-err").textContent = "聲音檔讀取失敗，請確認網路後再試一次。"; $("h-err").hidden = false;
    });
  }

  function begin() {
    var st = {}, from = {}, sk = {};
    ORDER.forEach(function (k) { st[k] = { lv: state.lv[k], run: 0 }; from[k] = state.lv[k]; sk[k] = { q: 0, r: 0 }; });
    L = { plan: plan(), i: 0, st: st, from: from, right: 0, sk: sk, t0: Date.now(), prev: {} };
    show("lesson");
    $("l-prog").setAttribute("aria-valuemax", L.plan.length);
    nextQ();
  }

  function choicesHtml(k) {
    var vals = k === "digit" ? [1, 2, 3, 4, 5, 6, 7, 8, 9, 0] : k === "pitch" ? [0, 1] : [2, 3, 4, 5, 6];
    return vals.map(function (v) {
      var label = k === "pitch" ? "第 " + (v + 1) + " 個" : v;
      return '<button type="button" class="ch' + (k === "digit" && v === 0 ? " zero" : "") + '" data-v="' + v + '">' + label + "</button>";
    }).join("");
  }

  function setBusy(on) {
    busy = on;
    $("l-play").classList.toggle("playing", on);
    $("l-play").disabled = on;
    Array.prototype.forEach.call($("l-choices").querySelectorAll(".ch"), function (b) { b.disabled = on || (L && L.answered); });
  }

  function nextQ() {
    clearTimers();
    if (L.i >= L.plan.length) { finish(); return; }
    var k = L.plan[L.i], q = makeQ(k, L.st[k].lv, null, L.prev[k]);
    L.q = q; L.prev[k] = q.answer; L.answered = false;
    $("l-skill").innerHTML = badge(k) + esc(SKILLS[k].name + "・Lv " + q.lv);
    $("l-ask").textContent = SKILLS[k].ask;
    $("l-choices").className = "choices " + k;
    $("l-choices").innerHTML = choicesHtml(k);
    $("l-cues").innerHTML = "";
    $("l-sheet").hidden = true;
    $("lesson").classList.remove("sheet-open");
    $("l-fill").style.width = (L.i / L.plan.length * 100) + "%";
    $("l-prog").setAttribute("aria-valuenow", L.i);
    setBusy(true);
    later(play, 450);
  }

  // 高低音：按鈕跟著聲音亮，看得出哪個是第 1 個、第 2 個
  function lightChoice(i, ms) {
    later(function () {
      var b = $("l-choices").querySelector('[data-v="' + i + '"]');
      if (!b) return;
      b.classList.add("lit");
      later(function () { b.classList.remove("lit"); }, TONE_DUR * 1000);
    }, ms);
  }

  function play() {
    if (!L || !L.q) return;
    var q = L.q, dur;
    setBusy(true);
    if (q.skill === "digit") dur = playDigit(q.answer, q.snr, true);
    else if (q.skill === "pitch") dur = playPitch(q.freqs, lightChoice);
    else dur = playCount(q.answer, q.ioi);
    later(function () { setBusy(false); }, dur * 1000 + 150);
  }

  // 答錯時「聽正確的」：數字不加雜音；高低音重播並亮出答案；數聲音放慢、一聲一個點
  function hearRight() {
    if (!L || !L.q || busy) return;
    var q = L.q, dur;
    setBusy(true);
    if (q.skill === "digit") dur = playDigit(q.answer, 6, false);
    else if (q.skill === "pitch") dur = playPitch(q.freqs, lightChoice);
    else {
      $("l-cues").innerHTML = "";
      dur = playCount(q.answer, 600, function (i, ms) {
        later(function () {
          var s = document.createElement("span"); s.textContent = i + 1; $("l-cues").appendChild(s);
        }, ms);
      });
    }
    later(function () { setBusy(false); }, dur * 1000 + 150);
  }

  function answer(v) {
    if (!L || L.answered || busy) return;
    L.answered = true;
    var q = L.q, ok = v === q.answer, k = q.skill;
    L.st[k] = step(L.st[k], ok);
    if (ok) L.right++;
    L.sk[k].q++; if (ok) L.sk[k].r++;
    L.i++;
    Array.prototype.forEach.call($("l-choices").querySelectorAll(".ch"), function (b) {
      var bv = +b.getAttribute("data-v");
      b.disabled = true;
      if (bv === q.answer) b.classList.add("right");
      else if (bv === v) b.classList.add("wrong");
    });
    var sheet = $("l-sheet");
    sheet.className = "sheet " + (ok ? "ok" : "bad");
    $("l-msg").textContent = ok ? "答對了！ +" + XP_RIGHT
      : k === "digit" ? "正確答案是 " + q.answer
      : k === "pitch" ? "比較高的是第 " + (q.answer + 1) + " 個"
      : "一共響了 " + q.answer + " 聲";
    $("l-hear").hidden = ok;
    sheet.hidden = false;
    $("lesson").classList.add("sheet-open");
    $("l-fill").style.width = (L.i / L.plan.length * 100) + "%";
    if (ok) later(nextQ, 900); else $("l-next").focus();
  }

  function finish() {
    var t = today(), gained = L.right * XP_RIGHT + XP_DONE, first = !state.days[t];
    ORDER.forEach(function (k) { state.lv[k] = L.st[k].lv; });
    state.days[t] = addRound(state.days[t], { xp: gained, sec: Math.round((Date.now() - L.t0) / 1000), sk: L.sk, lv: state.lv });
    var s = streak(state.days, t);
    state.best = Math.max(state.best, s);
    var saved = save();
    push();

    $("d-streak").textContent = s;
    $("d-title").textContent = first ? "今天打卡完成！" : "多練一回，好棒！";
    $("d-sub").textContent = !first ? "今天已經打過卡，多練的經驗值也算進去。"
      : MILESTONES.indexOf(s) >= 0 ? "連續 " + s + " 天！給自己拍拍手。"
      : s === 1 ? "明天再來練，就是連續 2 天。" : "連續 " + s + " 天，明天繼續！";
    $("d-right").textContent = L.right + "/" + L.plan.length;
    $("d-xp").textContent = "+" + gained;
    $("d-time").textContent = mmss(Date.now() - L.t0);
    $("d-levels").innerHTML = ORDER.map(function (k) {
      var a = L.from[k], b = state.lv[k];
      var change = b > a ? "Lv " + a + " → " + b + ' <span class="up">升級</span>'
        : b < a ? "Lv " + a + " → " + b + ' <span class="down">下次簡單一點</span>' : "Lv " + b;
      return "<li>" + badge(k) + esc(SKILLS[k].name) + "<b>" + change + "</b></li>";
    }).join("");
    $("d-week").innerHTML = weekHtml(state.days);
    $("d-nosave").hidden = saved;
    L = null; busy = false; clearTimers();
    show("done");
    confetti();
  }

  function confetti() {
    var box = $("d-confetti");
    box.innerHTML = "";
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    for (var i = 0; i < 28; i++) {
      var p = document.createElement("i");
      p.className = "c" + (i % 5);
      p.style.left = (Math.random() * 100) + "%";
      p.style.animationDelay = (Math.random() * 0.6) + "s";
      p.style.animationDuration = (1.6 + Math.random() * 1.2) + "s";
      box.appendChild(p);
    }
  }

  // ── 事件 ──────────────────────────────────────────────
  $("h-start").addEventListener("click", startFlow);
  $("v-play").addEventListener("click", function () {
    if (busy || !digits) return;
    busy = true;
    var dur = playDigit(Math.floor(Math.random() * 10), DIGIT_SNR[0], true);   // 用最大聲的一級調：練習時不會比這更大聲
    later(function () { busy = false; }, dur * 1000);
  });
  $("v-ok").addEventListener("click", function () {
    clearTimers(); busy = false;
    state.vol = today(); save();
    begin();
  });
  $("l-play").addEventListener("click", function () { if (!busy) play(); });
  $("l-choices").addEventListener("click", function (e) {
    var b = e.target.closest(".ch");
    if (b && !b.disabled) answer(+b.getAttribute("data-v"));
  });
  $("l-next").addEventListener("click", function () { if (L) nextQ(); });
  $("l-hear").addEventListener("click", hearRight);
  $("l-quit").addEventListener("click", function () {
    if (L && L.i > 0 && !confirm("現在離開，這一回不會記錄。確定要離開嗎？")) return;
    stopLesson(); go("home");
  });
  $("d-again").addEventListener("click", function () { begin(); });
  document.addEventListener("keydown", function (e) {
    if ($("lesson").hidden || !L || e.ctrlKey || e.metaKey || e.altKey) return;
    if (!$("l-sheet").hidden && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); $("l-next").click(); return; }
    if (e.key === "r" || e.key === "R") { if (!busy) play(); return; }
    if (/^[0-9]$/.test(e.key)) {
      var v = L.q.skill === "pitch" ? +e.key - 1 : +e.key;
      var b = $("l-choices").querySelector('[data-v="' + v + '"]');
      if (b && !b.disabled) answer(v);
    }
  });

  $("b-copy").addEventListener("click", function () {
    var ta = $("b-code"), done = function () { $("b-msg").textContent = "已複製。"; };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(ta.value).then(done, function () { ta.select(); });
    } else {
      ta.select();
      try { if (document.execCommand("copy")) done(); } catch (e) {}
    }
  });
  $("b-import").addEventListener("click", function () {
    var got = decode($("b-in").value);
    if (!got) { $("b-msg").textContent = "備份碼不正確，請確認整串都有貼上。"; return; }
    state = merge(state, got);
    var ok = save();
    push();
    $("b-in").value = "";
    $("b-code").value = encode(state);
    $("b-msg").textContent = ok ? "已合併，現在共有 " + Object.keys(state.days).length + " 天的紀錄。" : "合併了，但這個瀏覽器不能保存紀錄。";
  });
  $("b-reset").addEventListener("click", function () {
    if (!confirm("確定要清除這台裝置上的所有練習紀錄嗎？清除後無法復原。")) return;
    state = fresh(); save(); lsSet(PENDING, null);
    $("b-code").value = encode(state);
    // 會員裡的紀錄沒有刪：下次打開會再合併回來（要整個刪掉，到官網「會員帳號」刪除會員）
    $("b-msg").textContent = who ? "已清除這台裝置的紀錄。會員裡的紀錄還在，下次打開會再下載回來。" : "已清除。";
  });

  Array.prototype.forEach.call(document.querySelectorAll("[data-icon]"), function (el) {
    el.innerHTML = ICON[el.getAttribute("data-icon")] || "";
  });
  show("home");
  sync();
  // 加到主畫面、離線也能練（只在 https 下有效；區網 http 測試時略過）
  if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("sw.js", { updateViaCache: "none" }).catch(function () {});
})();
