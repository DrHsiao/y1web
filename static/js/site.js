// y1web 共用：字放大、深淺色、捲動淡入、聽力測驗結果帶進預約表單。
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
