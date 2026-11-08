// 補助小幫手：依使用者的回答排出「可能適用」的補助。答案只在瀏覽器裡比對，不送出、不儲存。
// 每一項的適用條件寫在 data-when（空白分隔的代碼，代碼見 app/content.py SUBSIDY_CONDITIONS）：
//   條件全部回答「是」＝可能適用；沒有「不是」但有「不確定」＝可以問問看；有任何一個「不是」＝先收起來。
(function () {
  "use strict";

  function init() {
    var form = document.getElementById("sb-form");
    if (!form) return;
    var items = Array.prototype.slice.call(document.querySelectorAll(".sb-item"));
    var list = document.getElementById("sb-list"), moreRow = document.getElementById("sb-more-row");

    function answer(code) {
      var el = form.querySelector('input[name="' + code + '"]:checked');
      return el ? el.value : "unknown";
    }

    function judge(item) {
      var when = (item.getAttribute("data-when") || "").split(" ").filter(Boolean);
      var unsure = false;
      for (var i = 0; i < when.length; i++) {
        var a = answer(when[i]);
        if (a === "no") return "no";
        if (a !== "yes") unsure = true;
      }
      return unsure ? "maybe" : "yes";
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var rank = { yes: 0, maybe: 1, no: 2 }, counts = { yes: 0, maybe: 0, no: 0 };
      var judged = items.map(function (it, i) { return { el: it, r: judge(it), i: i }; });
      judged.sort(function (a, b) { return rank[a.r] - rank[b.r] || a.i - b.i; });
      judged.forEach(function (j) {
        counts[j.r]++;
        var badge = j.el.querySelector(".sb-badge");
        badge.hidden = false;
        badge.textContent = { yes: "可能適用", maybe: "可以問問看", no: "條件不符" }[j.r];
        badge.className = "sb-badge is-" + j.r;
        j.el.hidden = j.r === "no";                       // 條件不符的先收起來，按「也看看其他補助」再顯示
        list.appendChild(j.el);
      });
      var head = document.getElementById("sb-head"), sub = document.getElementById("sb-sub");
      if (counts.yes + counts.maybe) {
        head.textContent = "可能和您有關的補助";
        sub.textContent = "「可能適用」是條件都符合；「可以問問看」是有些條件還不確定，可以向受理單位確認。";
      } else {
        head.textContent = "目前沒有找到符合的補助";
        sub.textContent = "條件和規定常有變動，還是歡迎來電或到門市問問看。";
      }
      moreRow.hidden = !counts.no;
      document.getElementById("sb-result").scrollIntoView({ behavior: "smooth" });
    });

    document.getElementById("sb-more").addEventListener("click", function () {
      items.forEach(function (it) { it.hidden = false; });
      moreRow.hidden = true;
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
