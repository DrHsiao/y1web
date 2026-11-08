# Facebook 粉專串接

> 2026-10-24 建立。John：「這個網站要與 Facebook 和 LINE 同步內容，並且能收集即時的回覆。」
> 程式在 **y1crm**：`api/fb_page.py`、`scripts/schema_fb.py`（資料表）、`scripts/fb_setup.py`（檢查／訂閱）、
> CRM「客戶 → 粉專訊息」`/fb-chat`、官網管理「📘 分享到粉專」。官網只改了隱私權政策（`/privacy#delete`）。

## 1. 做法

- **官網是唯一的內容來源**：文章只在 y1crm 官網管理寫一次。「聽力專欄」「使用小教室」已發佈的文章可以按「分享到粉專」，發一則連結貼文（Facebook 自己抓官網的標題、摘要、封面圖）。價格字眼一律擋；「保證」「改善聽力」這類用語先提醒、店員確認後才發。好評分享不能分享（見證有限制）。
- **y1crm 是收件匣**：Messenger 私訊和粉專貼文留言即時送進 `/fb-chat`，店員直接在 CRM 回覆（不收費、不佔 LINE 額度）。
  - Messenger 只能在客人最後一則訊息後 24 小時內回覆（Meta 規定），超過就請改電話或 LINE。
  - 店員在 Meta Business Suite 直接回也會記進來，而且這段對話的 AI 會自動暫停（LINE 做不到這點）。
- **AI**（本機，資料不出區網）：
  - Messenger：`config.toml [facebook] ai_reply` = `draft`（預設，只寫建議）／`auto`（直接回）／`off`。禁區與 LINE 同一套（價格、醫療轉人工）。
  - 公開留言：**一律只寫建議**，店員按「送出」才公開。
- **LINE 不變**：LINE 的回覆仍在官方帳號 App（免費）；新文章不建議每篇都群發（每位好友算一則）。

## 2. John 要做的（Meta 的畫面常改，做的時候我一步一步帶）

1. business.facebook.com 建立企業管理平台帳號，把粉專加進去（「帳號 → 粉絲專頁 → 新增」）。⚠ Meta 規定：成為粉專管理員一週內不能把粉專加進企業管理平台（John 的粉專要到 2026-10-31 13:37 之後）；沒加進來就不能指派給系統用戶，會出現「無法指派資產」。
2. developers.facebook.com 註冊開發者，建立「商家」類型的 app，連結上面的企業帳號。
3. App「基本資料」填：隱私權政策網址＝`https://y1ear.com/privacy`、資料刪除說明網址＝`https://y1ear.com/privacy#delete`（10-31 起外部打得開）、圖示（`static/img/app-icon-1024.png`，1024×1024，從 `static/img/app-icon.svg` 輸出；區網下載 http://192.168.1.88:8006/static/img/app-icon-1024.png）、類別。這兩個網址要從外部打得開（y1ear.com 可以）。
4. 企業管理平台建立「系統使用者」，指派粉專（完整控制）和 app，產生**不會過期**的權杖，勾：
   `pages_show_list`、`pages_read_engagement`、`pages_manage_posts`、`pages_manage_metadata`、`pages_read_user_content`、`pages_manage_engagement`、`pages_messaging`。
5. 把機密填進設定檔（**不要貼在對話裡**）：`sudo nano /home/john/y1crm/config.toml` 最下面的 `[facebook]` 段（10-24 已加好，每個欄位上面都有說明「去哪裡找」）：`app_secret`、`page_token`（直接貼系統用戶權杖，程式第一次使用時自己換成粉專權杖）。`page_id`、`app_id` 不是機密，自己填或告訴我都可以；`verify_token` 已自動產生，步驟 7 貼到 Meta 後台。
6. .26 nginx 加一段轉發（見 §3）。
7. App 的 Webhooks：物件選 Page，回呼網址 `https://ap888.duckdns.org/api/fb/webhook`、驗證權杖＝`verify_token`，訂閱 `feed`、`messages`、`message_echoes`。
8. 跑 `./venv/bin/python scripts/fb_setup.py subscribe`，讓粉專把通知送過來；重啟 y1crm。
   **2026-10-31 進度**：1–5 John 完成（系統用戶權杖、不會過期，`check` 通過、會換成粉專權杖）；y1crm 17:4x 已重啟（不是開發模式了）。
   `subscribe`：John 10-31 自己跑了，**訂閱成功**（feed、messages、message_echoes）。
   6 ✅ 10-31 .26 nginx 加好（外部 GET 403、未簽章 POST 401）；第一次沒生效是 Windows 上同時跑了兩份 nginx，全部關掉再開一份才好。7 待做。
   權杖勾了很多用不到的權限（business_management、WhatsApp、Instagram、廣告等），建議之後重新產生、只勾上面 7 個。
9. 先用 John 自己的帳號（app 管理員）留言、傳私訊測試。**一般客人的留言和私訊要等 Meta 審查通過**（App Review＋商家驗證，app 改成「上線」），送審需要的說明與錄影腳本我準備。
   **2026-11-01 進度**：10-31 用 John 帳號測試，Messenger 雙向通；留言在開發模式收不到。Meta 的 feed「測試」通知收得到（簽章過、0 個事件＝範例不是留言）。
   John 把 app 切到「上線」後，11-01 10:34 第一則真的留言進來了（thread 13，名字有抓到，本機 AI 建議 1.9 秒）。
   還要驗：不在 app 角色名單的人留言、私訊收不收得到；從 CRM 回覆留言（公開回覆）還沒真機測過。

## 3. 對外入口（.26 nginx，比照 LINE webhook）

2026-10-31 起 .26 的 443 是 nginx（LINE webhook 也是經它轉到 .88:8004）。在 `ap888.duckdns.org` 那個 443 的 `server { }` 裡，
找到 `/api/line/webhook` 那段，旁邊加一段一樣的：

```nginx
location = /api/fb/webhook {
    proxy_pass http://192.168.1.88:8004;      # 不要在後面加路徑，網址參數（hub.challenge）才會原樣帶過去
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

存檔後在 nginx 資料夾執行 `nginx -t`（檢查設定）再 `nginx -s reload`。驗證：手機 4G 打開
`https://ap888.duckdns.org/api/fb/webhook` 看到 403（沒帶驗證權杖）＝通了；看到 404 是還沒轉。

## 4. 安全

- 每個 webhook 都驗 `X-Hub-Signature-256`（App 密鑰），驗不過 401；訂閱驗證比對 verify_token。
- 呼叫 Graph API 帶 `appsecret_proof`；權杖放標頭，不進網址也不進 log。
- 留言和私訊是任何人都寫得進來的：只存資料、只交給本機 AI 產文字，不觸發其他動作。不接雲端 AI、不接 agy／Claude（會把客人訊息送出區網，也有提示注入風險）。
- 客人刪了留言，這邊也清掉內容；客人要求刪資料，在 `/fb-chat` 按「刪除紀錄」。
- 沒填 `page_token`＝開發模式：發文、回覆都只記錄不送出，畫面上會標示。

## 5. 驗證紀錄（2026-10-24，開發模式）

- 單元測試 `tests/test_fb_page.py` 18 項（簽章、事件整理、貼文用語檢查、24 小時視窗、AI 提示詞）；y1crm 全部 79 項通過。
- 本機測試伺服器（127.0.0.1:18999，提醒排程關閉）實測 32 項：簽章 401、重送不重複、AI 建議、Business Suite 回覆→人工接手、自己 app 的回音略過、留言併串／編輯／刪除、CRM 回覆、24 小時擋下、價格擋下、療效用語提醒、分享紀錄、留言在 auto 模式仍只寫建議、問價格轉人工。測試資料（`Utest_`、「測試請忽略」）已刪除。
