// 每日聽練的離線快取：頁面和程式先抓網路（有新版就用新版），抓不到才用快取；錄音檔先用快取。
// 改了錄音檔就把 CACHE 的版本號加一。
// Cloudflare 會讓瀏覽器把 .js／.css 存 4 小時，所以安裝時用 cache:"reload"、之後抓網路用 cache:"no-cache"，
// 都會回伺服器確認，不會拿到瀏覽器裡的舊版（2026-10-31，y1ear.com 上線）。
// v3（2026-11-01）：會員同步上線。舊版 v2 會把 ../api/member/… 的回應也存進快取，換版號讓 activate 把整個舊快取刪掉
// v4（2026-11-02）：每天多記各項題數／答對／等級和練習時間
var CACHE = "practice-v4";
var SHELL = ["./", "practice.css", "practice.js", "manifest.webmanifest", "icon.svg", "icon-192.png"]
  .concat([0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map(function (n) { return "audio/digits/" + n + ".wav"; }));

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(SHELL.map(function (u) { return new Request(u, { cache: "reload" }); })); }).then(function () { return self.skipWaiting(); }));
});
self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k.indexOf("practice-") === 0 && k !== CACHE; })
      .map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});
// 只處理這個資料夾裡的檔案：會員同步（../api/member/…）、登入頁都直接走網路，不進快取（個人資料、共用手機）
var SCOPE = new URL("./", self.location.href).pathname;
self.addEventListener("fetch", function (e) {
  var req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin || url.pathname.indexOf(SCOPE) !== 0) return;
  if (req.url.indexOf("/audio/") >= 0) {
    e.respondWith(caches.match(req).then(function (r) { return r || fetch(req); }));
    return;
  }
  // 換頁（navigate）的 Request 不能再加參數；頁面本身伺服器送 no-cache，Cloudflare 也不會改
  var net = req.mode === "navigate" ? fetch(req) : fetch(req, { cache: "no-cache" });
  e.respondWith(net.then(function (r) {
    if (r.ok) { var copy = r.clone(); caches.open(CACHE).then(function (c) { c.put(req, copy); }); }
    return r;
  }).catch(function () { return caches.match(req); }));
});
