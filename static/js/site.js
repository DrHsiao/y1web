// y1web 共用：字放大、深淺色、捲動淡入、預約表單「幫家人預約」、聽力測驗結果帶進預約表單。
// 儲存（localStorage／sessionStorage）一律包 try：無痕模式或被封鎖時照樣能用，只是不記住。
(function () {
  var root = document.documentElement;
  var BIG = "y1web-big", THEME = "y1web-theme", HT = "y1web-ht";
  // 字放大、深淺色的初始狀態已由 theme-init.js 在 <head> 套用

  document.addEventListener("DOMContentLoaded", function () {
    // 頁首兩顆切換鈕：圖示和說明文字由 CSS 依 html.big／html.dark 切換，這裡只改狀態、記住選擇
    var toggles = [
      { sel: ".font-toggle", cls: "big", key: BIG, on: "1", off: "0" },
      { sel: ".theme-toggle", cls: "dark", key: THEME, on: "dark", off: "light" }
    ];
    toggles.forEach(function (t) {
      var btns = document.querySelectorAll(t.sel);
      var sync = function () {
        var on = root.classList.contains(t.cls);
        for (var i = 0; i < btns.length; i++) btns[i].setAttribute("aria-pressed", on ? "true" : "false");
      };
      for (var i = 0; i < btns.length; i++) {
        btns[i].addEventListener("click", function () {
          root.classList.toggle(t.cls);
          try { localStorage.setItem(t.key, root.classList.contains(t.cls) ? t.on : t.off); } catch (e) {}
          sync();
        });
      }
      sync();
    });
    // 沒選過深淺色的人：手機或電腦切換深色模式時跟著變
    if (window.matchMedia) {
      var mq = window.matchMedia("(prefers-color-scheme: dark)");
      var follow = function (e) {
        var saved = null;
        try { saved = localStorage.getItem(THEME); } catch (err) {}
        if (saved) return;
        root.classList.toggle("dark", e.matches);
        var b = document.querySelectorAll(".theme-toggle");
        for (var i = 0; i < b.length; i++) b[i].setAttribute("aria-pressed", e.matches ? "true" : "false");
      };
      if (mq.addEventListener) mq.addEventListener("change", follow);
      else if (mq.addListener) mq.addListener(follow);
    }

    // 捲動到才淡入（不支援或使用者關閉動態效果時直接顯示）
    var items = document.querySelectorAll("[data-reveal]");
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!("IntersectionObserver" in window) || reduce) {
      for (var j = 0; j < items.length; j++) items[j].classList.add("in");
    } else {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
        });
      }, { rootMargin: "0px 0px -8% 0px" });
      for (var k = 0; k < items.length; k++) io.observe(items[k]);
    }

    // 首頁、子頁面輪播：慢慢淡入淡出（長輩看滑動容易暈）；滑鼠移上去、手指碰到、鍵盤停在上面、切到別的分頁時暫停；
    // 使用者關閉動態效果時不自動換，只能按圓點換
    var car = document.querySelector("[data-slides]");
    if (car) {
      var slides = car.querySelectorAll(".hero-slide"), dots = car.querySelectorAll(".hero-dot");
      var cur = 0, timer = null, hold = false, wait = parseInt(car.getAttribute("data-interval"), 10) || 6500;
      for (var c0 = 0; c0 < slides.length; c0++) if (slides[c0].classList.contains("on")) cur = c0;   // 子頁面從隨機一張開始
      var show = function (n) {
        cur = (n + slides.length) % slides.length;
        for (var s = 0; s < slides.length; s++) {
          var on = s === cur;
          slides[s].classList.toggle("on", on);
          if (on) slides[s].removeAttribute("aria-hidden"); else slides[s].setAttribute("aria-hidden", "true");
          if (dots[s]) { dots[s].classList.toggle("on", on); if (on) dots[s].setAttribute("aria-current", "true"); else dots[s].removeAttribute("aria-current"); }
        }
        // 11-23 活動投影片：換左邊文案（沒有活動文案的照片＝平常的網站標題）
        var copies = car.parentNode.querySelectorAll("[data-copy]"), hit = false;
        for (var k = 0; k < copies.length; k++) if (copies[k].getAttribute("data-copy") === String(cur)) hit = true;
        for (var k2 = 0; k2 < copies.length; k2++) {
          var dc = copies[k2].getAttribute("data-copy");
          copies[k2].hidden = hit ? dc !== String(cur) : dc !== "-1";
        }
      };
      var stop = function () { if (timer) { clearInterval(timer); timer = null; } };
      var start = function () {
        stop();
        if (!reduce && !hold && slides.length > 1 && !document.hidden) timer = setInterval(function () { show(cur + 1); }, wait);
      };
      for (var d = 0; d < dots.length; d++) {
        (function (n) { dots[n].addEventListener("click", function () { show(n); start(); }); })(d);
      }
      var hero = car.parentNode;
      hero.addEventListener("mouseenter", function () { hold = true; stop(); });
      hero.addEventListener("mouseleave", function () { hold = false; start(); });
      hero.addEventListener("focusin", function () { hold = true; stop(); });
      hero.addEventListener("focusout", function () { hold = false; start(); });
      hero.addEventListener("touchstart", function () { stop(); setTimeout(start, wait * 2); }, { passive: true });
      document.addEventListener("visibilitychange", start);
      start();
    }

    // 好評分享的評論卡片：點卡片開全螢幕彈窗（_share_dialog.html）。左邊放「原文」：Facebook 官方嵌入貼文、
    // Google 門市地圖（Google 評論頁不准嵌入，原文用置中的彈出視窗開）或大圖；右邊放文字。
    // 嵌入的內容點開才載入、關掉就移除。手機按返回鍵＝關彈窗。不支援 <dialog> 的舊瀏覽器不攔，「看原文」照原本的連結開
    // 用 show()＋蓋滿畫面，焦點跑出去就拉回來、Esc 關閉；不用 showModal()：
    // 2026-11-07 測試時 Facebook 嵌入貼文在 showModal() 的視窗裡一直沒顯示，一般視窗才出現
    var dlg = document.getElementById("qdialog");
    if (dlg && typeof dlg.show === "function") {
      document.body.appendChild(dlg);                // 移到 body 底下，不受 main 的版面規則影響
      var $q = function (sel) { return dlg.querySelector(sel); };
      var lastFocus = null;
      var clear = function (el) { while (el.firstChild) el.removeChild(el.firstChild); };
      var frame = function (cls, src, title) {
        var f = document.createElement("iframe");
        f.className = cls;
        f.title = title;
        f.setAttribute("sandbox", "allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox");
        f.src = src;
        return f;
      };
      var fbPost = function (url) {             // 只有單則貼文能嵌入；粉專評論頁這類網址只給「看原文」按鈕
        try {
          var u = new URL(url);
          return /(^|\.)facebook\.com$/.test(u.hostname) &&
            /\/(posts|videos|permalink|photos?)(\/|\.php|$)|story_fbid=|[?&]fbid=/.test(u.pathname + u.search);
        } catch (e) { return false; }
      };
      var pushed = false;
      var qOpen = function (card) {
        var key = card.getAttribute("data-platform"), full = card.getAttribute("data-full"), url = card.getAttribute("data-url");
        var ai = card.hasAttribute("data-ai");     // AI 畫的：只寫在圖片替代文字，畫面上不標（John 2026-11-08）
        var src = card.querySelector(".qsrc"), who = card.querySelector("figcaption span"), logo = card.querySelector(".qlogo");
        var label = src ? src.textContent : "", person = who ? who.textContent : "";
        // 標題：Google 地圖評論、Facebook 評論；門市心得卡就叫門市心得卡
        $q(".qd-title span").textContent = key === "store" && label ? label
          : label + (/[A-Za-z0-9]$/.test(label) ? " " : "") + "評論";
        $q(".qd-title img").src = logo ? logo.getAttribute("src") : "";
        var text = $q(".qd-text"), visual = $q(".qd-visual"), extra = $q(".qd-extra"), note = $q(".qd-note");
        clear(text); clear(visual); clear(extra);
        [".qhead", "blockquote", "figcaption"].forEach(function (sel) {
          var el = card.querySelector(sel);
          if (el) text.appendChild(el.cloneNode(true));
        });
        var tail = text.querySelector("figcaption [data-qopen]");
        if (tail) tail.parentNode.removeChild(tail);
        var mapSrc = key === "google" ? dlg.getAttribute("data-map") : "";
        var fb = key === "facebook" && url && fbPost(url);
        if (fb) {
          var w = Math.max(350, Math.min(560, window.innerWidth < 900 ? window.innerWidth - 32 : 560));   // 和 CSS 版面同寬
          visual.appendChild(frame("qd-fb", "https://www.facebook.com/plugins/post.php?show_text=true&width=" + w +
            "&href=" + encodeURIComponent(url), "Facebook 原文"));
        } else if (full) {
          var img = document.createElement("img");
          img.className = "qd-img";
          img.src = full;
          img.alt = ai ? "示意插圖（AI 生成）" : person + " 的評論附圖";
          visual.appendChild(img);
        }
        if (mapSrc) (visual.firstChild ? extra : visual).appendChild(frame("qd-map", mapSrc, "Google 地圖：門市位置與評分"));
        $q(".qd-inner").classList.toggle("solo", !visual.firstChild);
        if (fb && full) {                          // Facebook 原文在左邊，圖片放右邊文字下面
          var pic = document.createElement("img");
          pic.className = "qd-img";
          pic.src = full;
          pic.alt = ai ? "示意插圖（AI 生成）" : person + " 的評論附圖";
          extra.appendChild(pic);
        }
        var orig = $q(".qd-orig");
        orig.hidden = !url;
        if (url) {
          orig.href = url;
          orig.textContent = (key === "google" ? "開啟 " : "到 ") + label + (/[A-Za-z0-9]$/.test(label) ? " " : "") +
            (key === "google" ? "原文" : "看原文");
        }
        note.hidden = !(key === "google" || fb);
        note.textContent = fb ? "原文由 Facebook 提供；如果沒有顯示，可能是貼文不是公開的，請按上面的按鈕到 Facebook 看。"
          : key === "google" ? "地圖由 Google 提供。Google 不允許其他網站嵌入評論，原文會在另一個視窗開啟。" : "";
        lastFocus = document.activeElement;
        dlg.show();
        $q(".qd-close").focus();
        $q(".qd-main").scrollTop = 0;
        if (!pushed) { try { history.pushState({ qd: 1 }, ""); pushed = true; } catch (e) {} }
      };
      // Google 原文：電腦上開一個置中的小視窗；手機照連結開（新分頁或 Google 地圖 App）
      $q(".qd-orig").addEventListener("click", function (e) {
        if (!(window.matchMedia && window.matchMedia("(min-width: 900px) and (pointer: fine)").matches)) return;
        if (!/maps\.app\.goo\.gl|google\./.test(this.hostname)) return;
        var sw = screen.availWidth, sh = screen.availHeight;
        var w = Math.min(560, sw - 40), h = Math.min(900, sh - 60);
        var x = (screen.availLeft || 0) + Math.round((sw - w) / 2), y = (screen.availTop || 0) + Math.round((sh - h) / 2);
        var win = window.open(this.href, "y1web-review", "popup=yes,width=" + w + ",height=" + h + ",left=" + x + ",top=" + y);
        if (win) { try { win.opener = null; } catch (err) {} win.focus(); e.preventDefault(); }
      });
      var cards = document.querySelectorAll(".qcard[data-q]");
      for (var c = 0; c < cards.length; c++) {
        var more = cards[c].querySelector("button[data-qopen]");
        if (more) more.hidden = false;
        cards[c].addEventListener("click", function (e) {
          var a = e.target.closest ? e.target.closest("a") : null;
          if (a && !a.hasAttribute("data-qopen")) return;
          if (!a && window.getSelection && String(window.getSelection())) return;    // 在選字，不要開
          e.preventDefault();
          qOpen(this);
        });
      }
      dlg.addEventListener("click", function (e) {
        if (e.target.closest && e.target.closest("[data-qclose]")) dlg.close();
      });
      document.addEventListener("keydown", function (e) {
        if (dlg.open && (e.key === "Escape" || e.key === "Esc")) { e.preventDefault(); dlg.close(); }
      });
      document.addEventListener("focusin", function (e) {
        if (dlg.open && !dlg.contains(e.target)) $q(".qd-close").focus();
      });
      dlg.addEventListener("close", function () {
        clear($q(".qd-visual")); clear($q(".qd-extra"));      // 嵌入的貼文、地圖不留在背景
        if (lastFocus && lastFocus.focus) lastFocus.focus();
        if (pushed) { pushed = false; history.back(); }
      });
      window.addEventListener("popstate", function () {
        if (dlg.open) { pushed = false; dlg.close(); }
      });
    }

    // 「列印」按鈕（CSP 不允許 onclick，統一在這裡接）
    var prints = document.querySelectorAll("[data-print]");
    for (var q = 0; q < prints.length; q++) prints[q].addEventListener("click", function () { window.print(); });

    // 預約表單「這次是幫誰預約」：選「幫家人預約」才顯示家人欄位（伺服器端也依送出的值決定顯示，沒有 JS 也能填）
    var whom = document.querySelectorAll('input[name="for_whom"]');
    if (whom.length) {
      var syncWhom = function () {
        var picked = document.querySelector('input[name="for_whom"]:checked');
        var fam = !!picked && picked.value === "family";
        var a = document.querySelectorAll("[data-fam]"), b = document.querySelectorAll("[data-self]");
        for (var i = 0; i < a.length; i++) a[i].hidden = !fam;
        for (var j = 0; j < b.length; j++) b[j].hidden = fam;
        var pt = document.getElementById("f-patient");
        if (pt) pt.required = fam;
      };
      for (var w = 0; w < whom.length; w++) whom[w].addEventListener("change", syncWhom);
      syncWhom();
    }

    // 專欄文章的 YouTube 影片：先放本站的封面，按了才換成 YouTube 播放器（隱私加強模式，沒按之前不連 YouTube）；
    // 「改用本站播放」換回自己的 MP4（YouTube 被擋或播放不了時）
    var figs = document.querySelectorAll(".article-video.yt");
    for (var f = 0; f < figs.length; f++) (function (fig) {
      var a = fig.querySelector(".yt-facade"), own = fig.querySelector("template.yt-own"), fb = fig.querySelector(".yt-fallback");
      if (a) a.addEventListener("click", function (e) {
        var id = a.getAttribute("data-yt") || "";
        if (!/^[A-Za-z0-9_-]{11}$/.test(id)) return;          // 不認得就照連結開 YouTube
        e.preventDefault();
        var ifr = document.createElement("iframe");
        ifr.src = "https://www.youtube-nocookie.com/embed/" + id + "?autoplay=1&rel=0&playsinline=1";
        ifr.title = a.getAttribute("data-title") || "影片";
        ifr.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
        ifr.allowFullscreen = true;
        ifr.referrerPolicy = "strict-origin-when-cross-origin";  // YouTube 嵌入要看得到來源網站，不然會播不了
        ifr.className = "yt-frame";
        a.parentNode.replaceChild(ifr, a);
      });
      if (fb && own && own.content) fb.addEventListener("click", function () {
        var curr = fig.querySelector(".yt-facade, .yt-frame");
        if (curr) curr.parentNode.replaceChild(own.content.cloneNode(true), curr);
        var cap = fig.querySelector(".video-note");
        if (cap) cap.parentNode.removeChild(cap);
        fig.classList.remove("yt");
        var v = fig.querySelector("video");
        if (v && v.play) { var pr = v.play(); if (pr && pr.catch) pr.catch(function () {}); }
      });
    })(figs[f]);

    // 會員頁 Facebook 綁定碼：客人去 Messenger 傳碼，回到這一頁自動確認綁好了沒
    // （粉專在 Messenger 回「綁定好了」要等 Meta 審過才送得到一般客人，不能只靠那則回覆）
    var fbBox = document.querySelector("[data-fb-poll]");
    if (fbBox && window.fetch) {
      var fbMsg = fbBox.querySelector(".pts-code-wait");
      var fbUntil = Date.now() + (parseInt(fbBox.getAttribute("data-fb-minutes"), 10) || 30) * 60000;
      var fbBusy = false, fbTimer = null;
      var fbSay = function (t) { if (fbMsg) fbMsg.textContent = t; };
      var fbCheck = function () {
        if (fbBusy || document.hidden) return;
        if (Date.now() > fbUntil) { fbStop(); fbSay("綁定碼已經過期，請回到會員頁重新取得。"); return; }
        fbBusy = true;
        fetch(fbBox.getAttribute("data-fb-poll"), { credentials: "same-origin", cache: "no-store" })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (d) {
            fbBusy = false;
            if (!d || d.state === "waiting") return;
            fbStop();
            if (d.state === "used") {
              fbBox.classList.add("is-done");
              fbSay("綁定好了 ✅");
              setTimeout(function () { location.replace("/member?ok=fb_bound#fb"); }, 1500);
            } else if (d.state === "login") {
              fbSay("登入已經過期，請重新登入會員再取得綁定碼。");
            } else {
              fbSay("這組綁定碼已經失效，請回到會員頁重新取得。");
            }
          })
          .catch(function () { fbBusy = false; });
      };
      var fbStop = function () { clearInterval(fbTimer); document.removeEventListener("visibilitychange", fbCheck); };
      fbTimer = setInterval(fbCheck, 4000);
      document.addEventListener("visibilitychange", fbCheck);
    }

    // 「聽力不求人」按「帶著結果預約」：把結果摘要放進備註欄（客人可以自己刪掉）
    var page = document.body.getAttribute("data-page");
    if (page === "booking-done") {
      try { sessionStorage.removeItem(HT); } catch (e) {}
      return;
    }
    var note = document.getElementById("f-note");
    if (note && page === "booking") {
      var ht = null;
      try { ht = sessionStorage.getItem(HT); } catch (e) {}
      if (ht && note.value.indexOf("聽力不求人") < 0) {
        note.value = note.value ? note.value + "\n" + ht : ht;
        var hint = document.querySelector(".ht-attached");
        if (hint) hint.hidden = false;
      }
    }
  });

  // 左側選單（螢幕 ≥ 1440px 才有）：John 11-25 圓角半透明框、兩欄，固定在左上（框離頁首和畫面邊緣的距離＝.side 的 padding）。
  //   框比畫面矮（幾乎都是）：一直停在頁首下面。畫面太矮放不下：不出捲軸，往下捲時框跟著頁面往上走，最後一項出現就停在畫面下緣；
  //   往上捲時跟著頁面往下走，頂端碰到頁首就停住。
  //   做法：sticky 同時設 top（貼畫面下緣的位置）和 bottom（貼頁首的位置），框就在這兩個位置之間跟著頁面走（瀏覽器自己捲，不會抖）；
  //   換捲動方向時調整框上方空白塊（spacer）的高度，讓框從原地開始跟著頁面走，不會跳。
  //   （不能用 margin-top：sticky 不會把框移進自己的 margin 裡，往上捲時就停不住）
  document.addEventListener("DOMContentLoaded", function () {
    var side = document.querySelector(".side"), nav = side && side.querySelector(".side-nav"), hdr = document.querySelector(".hdr");
    if (!nav || !hdr) return;
    var tall = false, dir = 0, lastY = window.pageYOffset, lastTop = 0, padT = 0, padB = 0;
    var spacer = document.createElement("div");
    spacer.setAttribute("aria-hidden", "true");
    side.insertBefore(spacer, nav);
    // 讓框沒被 sticky 推動時的位置落在畫面上 want 的高度（之後就從那裡跟著頁面走）；
    //   空白塊不能超出左欄，否則頁面底下會多出空白
    var anchor = function (want) {
      var max = side.clientHeight - padT - padB - nav.offsetHeight;
      spacer.style.height = Math.max(0, Math.min(max, want - side.getBoundingClientRect().top - padT)) + "px";
    };
    var fit = function () {
      var h = nav.offsetHeight;
      if (!h) return;                                   // 1439px 以下左側選單不顯示
      var cs = window.getComputedStyle(side);
      padT = parseFloat(cs.paddingTop) || 0;
      padB = parseFloat(cs.paddingBottom) || 0;
      var vh = window.innerHeight, top = hdr.offsetHeight + padT, room = vh - top - padB;
      tall = h > room;
      if (!tall) {
        nav.style.top = top + "px";
        nav.style.bottom = "";
        spacer.style.height = "0px";
        return;
      }
      nav.style.top = (vh - padB - h) + "px";          // 框底停在畫面下緣上方 padB
      nav.style.bottom = (vh - h - top) + "px";        // 框頂停在頁首下方 padT（負值：允許框往下超出畫面）
      anchor(nav.getBoundingClientRect().top);
      lastY = window.pageYOffset;
      lastTop = nav.getBoundingClientRect().top;
    };
    // 捲動事件瀏覽器每個畫面最多送一次。換方向的那一下，框還貼在上緣或下緣沒動，
    //   所以放到「這一下也跟著頁面走」該在的位置：上一次的位置減掉這一下捲的距離（本來就在跟著走的話，就是它現在的位置）
    window.addEventListener("scroll", function () {
      var y = window.pageYOffset, d = y > lastY ? 1 : (y < lastY ? -1 : 0);
      if (tall && d && d !== dir) anchor(lastTop - (y - lastY));
      if (d) dir = d;
      lastY = y;
      if (tall) lastTop = nav.getBoundingClientRect().top;
    }, { passive: true });
    window.addEventListener("resize", fit);
    if (window.ResizeObserver) new ResizeObserver(fit).observe(nav);   // 字放大、從平板寬度拉大到 1440 時重算
    fit();
  });
})();

// 打電話／LINE／預約按鈕的點擊數（11-25 改善清單 D2；伺服器端見 app/clicks.py）：
//   只送「哪種按鈕、在哪一頁」，不送是誰；sendBeacon 在背景送出，不會擋住撥號、開 LINE 或換頁。
(function () {
  if (!navigator.sendBeacon || !window.URLSearchParams) return;
  function kind(a) {
    var h = a.getAttribute("href") || "";
    if (h.indexOf("tel:") === 0) return "tel";
    if (h === "/line" || h.indexOf("https://line.me/") === 0) return "line";
    if (/^\/(booking|home-visit)([?#]|$)/.test(h)) return "booking";
    return "";
  }
  document.addEventListener("click", function (e) {
    var a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
    var k = a ? kind(a) : "";
    if (!k) return;
    var d = new URLSearchParams();
    d.set("k", k);
    d.set("p", location.pathname);
    try { navigator.sendBeacon("/api/click", d); } catch (err) { /* 送不出去就算了 */ }
  }, true);
})();
