#!/usr/bin/env python3
"""
setup_db.py — 建立 y1web 用到的資料庫物件（可重跑，已存在的會略過）。

做四件事：
  1. y1web 資料庫：web_settings / web_faq / web_posts，加上商品型錄的唯讀檢視 v_catalog
     （只挑公開欄位：不含售價、進價、供應商、備註）。
  2. crm1.booking_requests 加官網要用的欄位：source / kind / address / link_code / link_expires。
  3. y1member 資料庫（會員）＋ crm1.v_member_customer（官網讀客戶資料只能透過它，見 MEMBER_VIEW）。
  4. 資料庫帳號 y1web_app：y1web.* 只能 SELECT；crm1.booking_requests 只能 INSERT 指定欄位，
     不能讀；y1member 只能讀寫資料列（MEMBER_GRANTS）；其他 crm1 資料表都碰不到。

要用管理者帳號執行，連線資訊讀 y1crm 的 config.toml（root 才讀得到）：
  sudo /home/john/y1web/venv/bin/python /home/john/y1web/scripts/setup_db.py
y1web_app 的密碼第一次執行時自動產生，寫進 /home/john/y1web/config.toml（權限 640，root:y1web）。
"""
from __future__ import annotations

import secrets
import sys
import tomllib
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parent.parent
CRM_CONFIG = Path("/home/john/y1crm/config.toml")
APP_USER = "y1web_app"

WEB_TABLES = [
    """CREATE TABLE IF NOT EXISTS web_settings (
      k          VARCHAR(50)  NOT NULL PRIMARY KEY,
      v          JSON         NOT NULL,
      updated_by VARCHAR(45)  DEFAULT NULL,
      updated_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='官網設定（門市資訊、首頁文字）'""",
    """CREATE TABLE IF NOT EXISTS web_faq (
      id           INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      category     VARCHAR(30)  NOT NULL DEFAULT '一般',
      question     VARCHAR(200) NOT NULL,
      answer_md    TEXT         NOT NULL,
      sort         INT          NOT NULL DEFAULT 0,
      status       ENUM('draft','published') NOT NULL DEFAULT 'draft',
      show_on_line TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '放進 LINE 常見問題',
      use_for_ai   TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '給 AI 客服當知識（有價格的題目不可勾）',
      updated_by   VARCHAR(45)  DEFAULT NULL,
      updated_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      UNIQUE KEY uq_faq_q (question),
      KEY idx_faq_list (status, category, sort)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='常見問題（官網／LINE／AI 共用）'""",
    """CREATE TABLE IF NOT EXISTS web_posts (
      id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      kind        ENUM('article','event','product','about','share','care','subsidy') NOT NULL,
      slug        VARCHAR(80)  NOT NULL,
      title       VARCHAR(120) NOT NULL,
      summary     VARCHAR(300) DEFAULT NULL,
      body_md     MEDIUMTEXT   DEFAULT NULL,
      category    VARCHAR(30)  DEFAULT NULL,
      status      ENUM('draft','published') NOT NULL DEFAULT 'draft',
      publish_at  DATETIME     DEFAULT NULL,
      start_date  DATE         DEFAULT NULL COMMENT '活動開始（event）',
      end_date    DATE         DEFAULT NULL COMMENT '活動結束（event）',
      sort        INT          NOT NULL DEFAULT 0,
      cover       VARCHAR(80)  DEFAULT NULL COMMENT '封面圖：media/uploads 底下的檔名（y1crm 官網管理上傳）',
      extra       JSON         DEFAULT NULL COMMENT 'product：brand、match、sources；about：en；share：person、model、hearing、consent；care：types、video；subsidy：when、agency、link',
      updated_by  VARCHAR(45)  DEFAULT NULL,
      updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      UNIQUE KEY uq_post_slug (kind, slug),
      KEY idx_post_list (kind, status, sort)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='聽力專欄、活動、產品系列、關於韻譯、好評分享、使用小教室、補助小幫手'""",
]

# 既有資料庫的補欄位（CREATE TABLE IF NOT EXISTS 不會改已存在的表）
POST_UPGRADES = [
    ("kind 加 about／share",
     "SELECT COLUMN_TYPE LIKE '%%share%%' FROM information_schema.COLUMNS "
     "WHERE TABLE_SCHEMA='y1web' AND TABLE_NAME='web_posts' AND COLUMN_NAME='kind'",
     "ALTER TABLE web_posts MODIFY kind ENUM('article','event','product','about','share') NOT NULL"),
    ("kind 加 care／subsidy",
     "SELECT COLUMN_TYPE LIKE '%%subsidy%%' FROM information_schema.COLUMNS "
     "WHERE TABLE_SCHEMA='y1web' AND TABLE_NAME='web_posts' AND COLUMN_NAME='kind'",
     "ALTER TABLE web_posts MODIFY kind ENUM('article','event','product','about','share','care','subsidy') NOT NULL"),
    ("加 cover 欄位",
     "SELECT COUNT(*) FROM information_schema.COLUMNS "
     "WHERE TABLE_SCHEMA='y1web' AND TABLE_NAME='web_posts' AND COLUMN_NAME='cover'",
     "ALTER TABLE web_posts ADD COLUMN cover VARCHAR(80) DEFAULT NULL "
     "COMMENT '封面圖：media/uploads 底下的檔名（y1crm 官網管理上傳）' AFTER extra"),
]

# 商品型錄的公開欄位。售價、進價、供應商、備註（有「一對銷售價」這類字）一律不給。
CATALOG_VIEW = """CREATE OR REPLACE SQL SECURITY DEFINER VIEW y1web.v_catalog AS
  SELECT id, brand, model_name, spec, category, ha_form, ha_tech_level, ha_channels,
         ha_battery, ha_bluetooth, ha_tcoil, ha_ip_rating, ha_fit_levels, compatible_models
  FROM crm1.products WHERE is_active = 1"""

# crm1.booking_requests 新欄位（名稱, 定義）
BOOKING_COLUMNS = [
    ("source", "ENUM('line','web') NOT NULL DEFAULT 'line' COMMENT '預約來源'"),
    ("kind", "ENUM('store','home') NOT NULL DEFAULT 'store' COMMENT '門市預約／到府服務'"),
    ("address", "VARCHAR(200) DEFAULT NULL COMMENT '到府地址'"),
    ("link_code", "CHAR(6) DEFAULT NULL COMMENT '官網預約碼：客人傳到 LINE 就把這筆連上他的 LINE'"),
    ("link_expires", "DATETIME DEFAULT NULL COMMENT '預約碼期限'"),
]
WEB_INSERT_COLUMNS = ["contact_name", "phone", "req_date", "req_hour", "note",
                      "source", "kind", "address", "link_code", "link_expires"]

# 會員（docs/會員系統.md）：另開資料庫 y1member，不動 crm1.customers 的欄位。
# subject、雜湊都用 ascii_bin：Google sub、LINE userId 要分大小寫比對
MEMBER_TABLES = [
    """CREATE TABLE IF NOT EXISTS members (
      id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      display_name  VARCHAR(50)  NOT NULL DEFAULT '' COMMENT '稱呼：第一次登入取 Google／LINE 的名字',
      customer_id   BIGINT UNSIGNED DEFAULT NULL COMMENT 'crm1.customers.id；NULL＝沒連結。一位客人只能連一個會員',
      link_method   ENUM('line','code','request') DEFAULT NULL COMMENT 'LINE 自動比對／店內綁定碼／線上申請',
      linked_at     DATETIME     DEFAULT NULL,
      consent_at    DATETIME     DEFAULT NULL COMMENT '連結客戶資料時勾選個資告知同意的時間',
      terms_at      DATETIME     NOT NULL COMMENT '建立會員（登入頁有隱私權政策說明）的時間',
      last_login_at DATETIME     DEFAULT NULL,
      disabled      TINYINT(1)   NOT NULL DEFAULT 0,
      created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY uq_member_customer (customer_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='官網會員'""",
    """CREATE TABLE IF NOT EXISTS member_identities (
      id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      member_id     BIGINT UNSIGNED NOT NULL,
      provider      ENUM('google','line') NOT NULL,
      subject       VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT 'Google sub／LINE userId',
      email         VARCHAR(254) DEFAULT NULL COMMENT '只有 Google 有；帳號設定頁讓客人認得是哪個帳號',
      created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
      last_login_at DATETIME     DEFAULT NULL,
      UNIQUE KEY uq_ident (provider, subject),
      UNIQUE KEY uq_ident_member (member_id, provider),
      CONSTRAINT fk_ident_member FOREIGN KEY (member_id) REFERENCES members (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='會員的登入方式（Google、LINE 各最多一個）'""",
    """CREATE TABLE IF NOT EXISTS member_sessions (
      token_hash    CHAR(64)     CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY COMMENT '登入憑證的 SHA-256；憑證本身不存',
      member_id     BIGINT UNSIGNED NOT NULL,
      expires_at    DATETIME     NOT NULL,
      last_seen_at  DATETIME     NOT NULL,
      device        VARCHAR(120) DEFAULT NULL COMMENT 'User-Agent 前段，帳號設定頁列出登入中的裝置用',
      created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
      KEY idx_sess_member (member_id),
      KEY idx_sess_expires (expires_at),
      CONSTRAINT fk_sess_member FOREIGN KEY (member_id) REFERENCES members (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='會員登入憑證'""",
    """CREATE TABLE IF NOT EXISTS member_progress (
      member_id     BIGINT UNSIGNED NOT NULL,
      app           VARCHAR(20)  CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT 'practice＝每日聽練',
      data          JSON         NOT NULL,
      updated_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (member_id, app),
      CONSTRAINT fk_prog_member FOREIGN KEY (member_id) REFERENCES members (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='會員的練習紀錄'""",
    """CREATE TABLE IF NOT EXISTS link_codes (
      id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      customer_id   BIGINT UNSIGNED NOT NULL,
      code_hash     CHAR(64)     CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
      expires_at    DATETIME     NOT NULL,
      used_at       DATETIME     DEFAULT NULL,
      used_by       BIGINT UNSIGNED DEFAULT NULL COMMENT '用掉這個碼的會員',
      created_by    VARCHAR(45)  NOT NULL COMMENT '產生的店員（y1crm 帳號）',
      created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY uq_link_code (code_hash),
      CONSTRAINT fk_code_member FOREIGN KEY (used_by) REFERENCES members (id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='店內綁定碼（y1crm 產生，10 分鐘、一次）'""",
    """CREATE TABLE IF NOT EXISTS link_requests (
      id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      member_id     BIGINT UNSIGNED NOT NULL,
      contact_name  VARCHAR(50)  NOT NULL,
      phone         VARCHAR(20)  NOT NULL,
      status        ENUM('new','linked','rejected','cancelled') NOT NULL DEFAULT 'new',
      customer_id   BIGINT UNSIGNED DEFAULT NULL COMMENT '店員確認後連上的客人',
      handled_by    VARCHAR(45)  DEFAULT NULL,
      handled_at    DATETIME     DEFAULT NULL,
      created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
      KEY idx_req_status (status, created_at),
      CONSTRAINT fk_req_member FOREIGN KEY (member_id) REFERENCES members (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='線上申請連結門市客戶資料（店員確認）'""",
]
# y1web_app 對 y1member 只給到資料列，不給 CREATE／ALTER／DROP；link_codes 由 y1crm 產生，官網只能標記用掉
MEMBER_GRANTS = [
    "GRANT SELECT, INSERT, UPDATE, DELETE ON y1member.members TO {u}",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON y1member.member_identities TO {u}",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON y1member.member_sessions TO {u}",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON y1member.member_progress TO {u}",
    "GRANT SELECT, UPDATE (used_at, used_by) ON y1member.link_codes TO {u}",
    "GRANT SELECT, INSERT, UPDATE (status, member_id) ON y1member.link_requests TO {u}",   # member_id：合併會員時搬過去
    "GRANT SELECT ON crm1.v_member_customer TO {u}",
]
# 官網讀客戶資料的唯一入口（D6）：只有編號、稱呼（姓＋先生／女士）、LINE userId 的雜湊。
# 身分證、生日、地址、電話、聽檢、備註都不在這裡；LINE userId 本身也不給，官網拿登入得到的 userId 算雜湊來比對。
# TEMPTABLE＝不能透過這個檢視表改 crm1.customers（DEFINER 檢視表若可更新，會用建立者的權限寫進去）
MEMBER_VIEW = """CREATE OR REPLACE ALGORITHM=TEMPTABLE SQL SECURITY DEFINER VIEW crm1.v_member_customer AS
  SELECT id AS customer_id,
         CONCAT(LEFT(name, 1), CASE gender WHEN 'M' THEN '先生' WHEN 'F' THEN '女士' ELSE '' END) AS salutation,
         IF(line_user_id IS NULL OR line_user_id = '', NULL, SHA2(line_user_id, 256)) AS line_hash
  FROM crm1.customers"""


def _admin_conn():
    with open(CRM_CONFIG, "rb") as f:
        db = tomllib.load(f)["db"]
    return pymysql.connect(host=db.get("host", "localhost"), user=db["user"], password=db["password"],
                           charset="utf8mb4", autocommit=True)


def _app_password() -> str:
    """沿用 config.toml 裡已有的密碼；沒有就產生一組並寫回。"""
    cfg = ROOT / "config.toml"
    if not cfg.exists():
        sys.exit(f"找不到 {cfg}，請先從 config.example.toml 複製一份。")
    text = cfg.read_text(encoding="utf-8")
    pw = tomllib.loads(text).get("db", {}).get("password", "")
    if pw and pw != "CHANGE_ME":
        return pw
    pw = secrets.token_urlsafe(24)
    cfg.write_text(text.replace('password = "CHANGE_ME"', f'password = "{pw}"', 1), encoding="utf-8")
    print("已產生 y1web_app 密碼並寫入 config.toml")
    return pw


def main() -> None:
    pw = _app_password()
    con = _admin_conn()
    cur = con.cursor()
    cur.execute("CREATE DATABASE IF NOT EXISTS y1web CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    cur.execute("USE y1web")
    for ddl in WEB_TABLES:
        cur.execute(ddl)
    for label, check, ddl in POST_UPGRADES:
        cur.execute(check)
        if not cur.fetchone()[0]:
            cur.execute(ddl)
            print(f"web_posts {label}")
    cur.execute(CATALOG_VIEW)
    # 官網管理上傳的圖片（y1crm 以 root 寫入；y1web 唯讀）
    up = ROOT / "media" / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    up.chmod(0o755)

    cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA='crm1' AND TABLE_NAME='booking_requests'")
    have = {r[0] for r in cur.fetchall()}
    for name, ddl in BOOKING_COLUMNS:
        if name not in have:
            cur.execute(f"ALTER TABLE crm1.booking_requests ADD COLUMN {name} {ddl}")
            print(f"crm1.booking_requests 加欄位 {name}")
    cur.execute("SELECT COUNT(*) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA='crm1' "
                "AND TABLE_NAME='booking_requests' AND INDEX_NAME='uq_bk_link_code'")
    if not cur.fetchone()[0]:
        cur.execute("ALTER TABLE crm1.booking_requests ADD UNIQUE KEY uq_bk_link_code (link_code)")

    cur.execute(f"CREATE USER IF NOT EXISTS '{APP_USER}'@'localhost' IDENTIFIED BY %s", (pw,))
    cur.execute(f"ALTER USER '{APP_USER}'@'localhost' IDENTIFIED BY %s", (pw,))
    cur.execute(f"GRANT SELECT ON y1web.* TO '{APP_USER}'@'localhost'")
    cols = ", ".join(WEB_INSERT_COLUMNS)
    cur.execute(f"GRANT INSERT ({cols}) ON crm1.booking_requests TO '{APP_USER}'@'localhost'")

    cur.execute("CREATE DATABASE IF NOT EXISTS y1member CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    cur.execute("USE y1member")
    for ddl in MEMBER_TABLES:
        cur.execute(ddl)
    cur.execute(MEMBER_VIEW)
    for g in MEMBER_GRANTS:
        cur.execute(g.format(u=f"'{APP_USER}'@'localhost'"))
    cur.execute(f"SHOW GRANTS FOR '{APP_USER}'@'localhost'")
    for (g,) in cur.fetchall():
        print(" ", g)
    con.close()
    print("完成。")


if __name__ == "__main__":
    main()
