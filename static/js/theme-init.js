// 在畫面出現前套用「字放大」與「深色背景」，避免先閃一下白底或小字。
// CSP 不允許內嵌 script，所以獨立成檔、放在 <head> 裡同步載入（不加 defer）。
// 深淺色：使用者按過就照他選的；沒選過就跟著手機／電腦本身的設定。
(function () {
  var root = document.documentElement;
  var sysDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  var big = null, theme = null;
  try { big = localStorage.getItem("y1web-big"); theme = localStorage.getItem("y1web-theme"); } catch (e) {}
  root.classList.add("js");
  if (big === "1") root.classList.add("big");
  if (theme === "dark" || (theme !== "light" && sysDark)) root.classList.add("dark");
})();
