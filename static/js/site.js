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
      var fbPost = function (url) {
        try { return /(^|\.)facebook\.com$/.test(new URL(url).hostname); } catch (e) { return false; }
      };
      var pushed = false;
      var qOpen = function (card) {
        var key = card.getAttribute("data-platform"), full = card.getAttribute("data-full"), url = card.getAttribute("data-url");
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
          img.alt = person + " 的評論附圖";
          visual.appendChild(img);
        }
        if (mapSrc) (visual.firstChild ? extra : visual).appendChild(frame("qd-map", mapSrc, "Google 地圖：門市位置與評分"));
        $q(".qd-inner").classList.toggle("solo", !visual.firstChild);
        if (fb && full) {                          // Facebook 原文在左邊，圖片放右邊文字下面
          var pic = document.createElement("img");
          pic.className = "qd-img";
          pic.src = full;
          pic.alt = person + " 的評論附圖";
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
})();
