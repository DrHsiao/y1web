# y1web — 韻譯助聽器 睿聲中正門市官網

FastAPI＋Jinja2，port 8006。預約寫進 y1crm 的「預約留言」，客人把預約碼傳到 LINE 就能收到提醒。
需求、設計、待決事項見 `docs/`。

## 執行

```bash
# 服務（專用帳號 y1web、systemd 隔離）
sudo systemctl status y1web
sudo systemctl restart y1web          # 改了程式或 static 後要重啟（CSS/JS 版本號在啟動時計算）
journalctl -u y1web -f

# 第一次安裝（可重跑）
cp config.example.toml config.toml && sudo chown root:y1web config.toml && sudo chmod 640 config.toml
sudo venv/bin/python scripts/setup_db.py        # 建 y1web 資料庫、型錄檢視、crm1 新欄位、y1web_app 帳號
sudo venv/bin/python scripts/seed_content.py    # 門市設定、FAQ、產品系列（--force 才會覆蓋已有內容）
sudo venv/bin/python scripts/sync_images.py     # 型錄照 → media/catalog/*.webp（只留機身、不露價格）；型錄加新照片後重跑
sudo cp deploy/y1web.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now y1web
```

## 測試

```bash
./venv/bin/python -m unittest discover -s tests
```

## 注意

- `config.toml` 有資料庫密碼與表單簽章金鑰，不進版控。
- 預約規則（明天起 14 天、週日除外、09–18 整點）和 y1crm `api/line_bot.py` 的 LINE 預約相同，要一起改。
- 產品介紹（`scripts/products.toml`）不寫價格，只寫原廠公開資料；正式上線前要經過門市或聽力師審閱。
- 視覺比照 ear.com.tw（John 指定），但文字、圖片、程式都自己寫。聽力測驗的方法見 `docs/設計說明.md` §14。
- 關於韻譯、聽力專欄、好評分享由 y1crm「官網管理」（`/web-admin`）編輯；預設內容在 `scripts/cms_defaults.toml`（只新增不覆蓋）。
- `media/` 不進版控：`media/catalog` 是 `sync_images.py` 產生的商品照；`media/uploads` 是官網管理上傳的圖（y1crm 以 root 寫入）。
