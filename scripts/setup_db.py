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
     y1web.web_clicks（按鈕點擊數，11-25）只能新增、把次數加一（CLICK_GRANT）。

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
      kind        ENUM('article','event','product','about','share','care','subsidy','slide') NOT NULL,
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
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='聽力專欄、活動、產品系列、關於韻譯、好評分享、使用小教室、補助小幫手、首頁輪播'""",
    # 11-25（改善清單 D2）：打電話／LINE／預約按鈕每天在哪一頁被按了幾次（app/clicks.py）。只有次數，不記是誰。
    """CREATE TABLE IF NOT EXISTS web_clicks (
      day   DATE         NOT NULL,
      kind  VARCHAR(12)  NOT NULL COMMENT 'tel 打電話、line 加 LINE、booking 預約（app/clicks.KINDS）',
      page  VARCHAR(80)  NOT NULL COMMENT '在哪一頁按的（網址路徑）',
      n     INT UNSIGNED NOT NULL DEFAULT 0,
      PRIMARY KEY (day, kind, page)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='官網按鈕點擊數（只有次數）'""",
]
# y1web_app 對 web_clicks 只能新增一列、把次數加一（讀靠 y1web.* 的 SELECT）
CLICK_GRANT = "GRANT INSERT (day, kind, page, n), UPDATE (n) ON y1web.web_clicks TO {u}"

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
    ("kind 加 slide（首頁輪播，2026-11-18）",
     "SELECT COLUMN_TYPE LIKE '%%slide%%' FROM information_schema.COLUMNS "
     "WHERE TABLE_SCHEMA='y1web' AND TABLE_NAME='web_posts' AND COLUMN_NAME='kind'",
     "ALTER TABLE web_posts MODIFY kind ENUM('article','event','product','about','share','care','subsidy','slide') NOT NULL"),
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
# 會員集點帳本的來源（John 11-19「門市聽檢讓店員在點數管理中增加…兌換時…減少，並註明原因」）
# 11-19 晚 John「每日聽力練習連續 7 天 +10 點」→ 加 streak
POINTS_SOURCE = "ENUM('practice','hearing_online','hearing_store','redeem','adjust','streak')"
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
    # 粉專集點（2026-11-19，y1crm api/fb_points.py）：會員頁產生綁定碼 → 客人用 Messenger 傳給粉專 → y1crm 寫 member_fb
    """CREATE TABLE IF NOT EXISTS fb_link_codes (
      id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      member_id     BIGINT UNSIGNED NOT NULL,
      code_hash     CHAR(64)     CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '6 位數綁定碼的 SHA-256',
      expires_at    DATETIME     NOT NULL,
      used_at       DATETIME     DEFAULT NULL,
      created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY uq_fb_code (code_hash),
      KEY idx_fb_code_member (member_id),
      CONSTRAINT fk_fbcode_member FOREIGN KEY (member_id) REFERENCES members (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Facebook 綁定碼（官網產生，30 分鐘、一次；y1crm 收到 Messenger 時用掉）'""",
    """CREATE TABLE IF NOT EXISTS member_fb (
      id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      member_id     BIGINT UNSIGNED NOT NULL,
      person_hash   CHAR(64)     CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '粉專編號（PSID＝留言者編號）的 SHA-256；編號本身不存',
      fb_name       VARCHAR(100) DEFAULT NULL COMMENT 'Facebook 名字，會員頁讓客人認得是哪個帳號',
      linked_at     DATETIME     NOT NULL,
      UNIQUE KEY uq_fb_person (person_hash),
      KEY idx_fb_member (member_id),
      CONSTRAINT fk_fb_member FOREIGN KEY (member_id) REFERENCES members (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='會員綁定的 Facebook（粉專集點用；y1crm 寫入）'""",
    # 會員集點的點數帳本（2026-11-19）：每日聽練、線上聽力測驗由官網寫；門市聽檢、兌換、調整由 y1crm 店員寫；
    # 粉專留言另外算（見 docs/會員集點.md）。改欄位要同步改下面 POINTS_UPGRADES（舊表靠它補）。
    """CREATE TABLE IF NOT EXISTS member_points (
      id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      member_id     BIGINT UNSIGNED NOT NULL,
      source        {POINTS_SOURCE} NOT NULL COMMENT '每日聽練／線上聽力測驗／門市聽力檢查／兌換贈品／點數調整／聽練連續 7 天',
      ref           VARCHAR(40)  CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '哪一次：聽練＝日期 YYYY-MM-DD；線上測驗＝年月 YYYY-MM；店員登記＝s＋亂數',
      points        SMALLINT     NOT NULL DEFAULT 1 COMMENT '記點當時的點數，扣點是負的（規則改了也不動舊紀錄）',
      note          VARCHAR(100) DEFAULT NULL COMMENT '店員寫的加減點原因（會員看得到）',
      created_by    VARCHAR(45)  DEFAULT NULL COMMENT '登記的店員（會員看不到）',
      status        ENUM('counted','void') NOT NULL DEFAULT 'counted' COMMENT '計分／作廢（y1crm 經理以上）',
      void_by       VARCHAR(45)  DEFAULT NULL,
      void_at       DATETIME     DEFAULT NULL,
      void_reason   VARCHAR(100) DEFAULT NULL,
      created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY uq_mp (member_id, source, ref),
      KEY idx_mp_time (created_at),
      CONSTRAINT fk_mp_member FOREIGN KEY (member_id) REFERENCES members (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='會員集點的點數帳本（加點、扣點）'""".replace("{POINTS_SOURCE}", POINTS_SOURCE),
    # John 11-19「會員可以留聽檢記錄」：線上聽力測驗做完，會員自己按「存到會員紀錄」才存（app/hearing_rec.py）
    """CREATE TABLE IF NOT EXISTS member_hearing (
      id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      member_id     BIGINT UNSIGNED NOT NULL,
      tested_at     DATETIME     NOT NULL COMMENT '存的時間（測完當下）',
      data          JSON         NOT NULL COMMENT '{right:{頻率:{hl,nr}}, left:{…}, fh}（hearing_rec.clean 整理過）',
      pta_r         SMALLINT     NOT NULL COMMENT '右耳 500/1k/2k/4k 平均（dB HL）',
      pta_l         SMALLINT     NOT NULL,
      created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
      KEY idx_mh_member (member_id, tested_at),
      CONSTRAINT fk_mh_member FOREIGN KEY (member_id) REFERENCES members (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='會員保存的線上聽力測驗結果（只有會員自己看得到）'""",
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
    # 粉專集點：綁定碼官網產生、member_fb 由 y1crm 寫（官網只能看、解除、合併會員時搬過去）
    "GRANT SELECT, INSERT, DELETE ON y1member.fb_link_codes TO {u}",
    "GRANT SELECT, UPDATE (member_id), DELETE ON y1member.member_fb TO {u}",
    # 會員集點帳本：官網記聽練、線上測驗的點數；合併會員時搬過去（撞到同一天的刪掉）；店員加減點、作廢由 y1crm 做
    "GRANT SELECT, INSERT, UPDATE (member_id), DELETE ON y1member.member_points TO {u}",
    "GRANT SELECT, INSERT, UPDATE (member_id), DELETE ON y1member.member_hearing TO {u}",
]
FB_POINTS_GRANT = "GRANT SELECT ON crm1.v_member_fb_points TO {u}"
# 官網讀客戶資料的唯一入口（D6）：只有編號、稱呼（姓＋先生／女士）、LINE userId 的雜湊。
# 身分證、生日、地址、電話、聽檢、備註都不在這裡；LINE userId 本身也不給，官網拿登入得到的 userId 算雜湊來比對。
# TEMPTABLE＝不能透過這個檢視表改 crm1.customers（DEFINER 檢視表若可更新，會用建立者的權限寫進去）
MEMBER_VIEW = """CREATE OR REPLACE ALGORITHM=TEMPTABLE SQL SECURITY DEFINER VIEW crm1.v_member_customer AS
  SELECT id AS customer_id,
         CONCAT(LEFT(name, 1), CASE gender WHEN 'M' THEN '先生' WHEN 'F' THEN '女士' ELSE '' END) AS salutation,
         IF(line_user_id IS NULL OR line_user_id = '', NULL, SHA2(line_user_id, 256)) AS line_hash
  FROM crm1.customers"""


# 粉專集點（y1crm api/fb_points.py）：官網只看得到留言者編號的雜湊、貼文、時間、狀態、點數、留言開頭。
# crm1.fb_comment_points 由 y1crm scripts/schema_fb.py 建；還沒建就先略過（之後重跑這支會補上）。
FB_POINTS_VIEW = """CREATE OR REPLACE ALGORITHM=TEMPTABLE SQL SECURITY DEFINER VIEW crm1.v_member_fb_points AS
  SELECT SHA2(p.person_id, 256) AS person_hash, p.post_id, p.created_at, p.status,
         IF(p.status = 'counted', p.points, 0) AS points,
         COALESCE((SELECT w.title FROM crm1.fb_posts fp JOIN y1web.web_posts w ON w.id = fp.web_post_id
                    WHERE fp.fb_post_id = p.post_id ORDER BY fp.id DESC LIMIT 1),
                  (SELECT d.topic FROM crm1.fb_post_drafts d WHERE d.fb_post_id = p.post_id LIMIT 1)) AS post_title,
         (SELECT LEFT(m.text, 40) FROM crm1.fb_messages m WHERE m.ext_mid = p.comment_id) AS excerpt
  FROM crm1.fb_comment_points p"""


# 舊的 member_points（11-19 上午版）補成現在的樣子：店員加減點要負數、原因、經手人。可重跑。
POINTS_UPGRADES = [
    ("source", "MODIFY source " + POINTS_SOURCE + " NOT NULL COMMENT '每日聽練／線上聽力測驗／門市聽力檢查／兌換贈品／點數調整／聽練連續 7 天'"),
    ("points", "MODIFY points SMALLINT NOT NULL DEFAULT 1 COMMENT '記點當時的點數，扣點是負的（規則改了也不動舊紀錄）'"),
    ("note", "ADD COLUMN note VARCHAR(100) DEFAULT NULL COMMENT '店員寫的加減點原因（會員看得到）' AFTER points"),
    ("created_by", "ADD COLUMN created_by VARCHAR(45) DEFAULT NULL COMMENT '登記的店員（會員看不到）' AFTER note"),
]
_POINTS_WANT = {"source": POINTS_SOURCE.lower(), "points": "smallint"}
# 11-19 上午版的門市聽檢是從 hearing_tests 自動算（這個檢視表）；下午 John 改成店員在點數管理加點，官網不再讀聽檢月份
OLD_HEARING_VIEW = "crm1.v_member_hearing_points"


def _upgrade_points(cur) -> None:
    cur.execute("SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA='y1member' AND TABLE_NAME='member_points'")
    have = {name: ctype.lower() for name, ctype in cur.fetchall()}
    for col, ddl in POINTS_UPGRADES:
        if col in _POINTS_WANT:
            if have.get(col) == _POINTS_WANT[col]:
                continue
        elif col in have:
            continue
        cur.execute(f"ALTER TABLE y1member.member_points {ddl}")
        print(f"y1member.member_points {ddl.split(' COMMENT')[0]}")


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
    cur.execute(CLICK_GRANT.format(u=f"'{APP_USER}'@'localhost'"))
    cols = ", ".join(WEB_INSERT_COLUMNS)
    cur.execute(f"GRANT INSERT ({cols}) ON crm1.booking_requests TO '{APP_USER}'@'localhost'")

    cur.execute("CREATE DATABASE IF NOT EXISTS y1member CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    cur.execute("USE y1member")
    for ddl in MEMBER_TABLES:
        cur.execute(ddl)
    cur.execute(MEMBER_VIEW)
    _upgrade_points(cur)
    cur.execute(f"REVOKE IF EXISTS SELECT ON {OLD_HEARING_VIEW} FROM '{APP_USER}'@'localhost'")
    cur.execute(f"DROP VIEW IF EXISTS {OLD_HEARING_VIEW}")
    for g in MEMBER_GRANTS:
        cur.execute(g.format(u=f"'{APP_USER}'@'localhost'"))
    cur.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='crm1' "
                "AND TABLE_NAME='fb_comment_points'")
    if cur.fetchone()[0]:
        cur.execute(FB_POINTS_VIEW)
        cur.execute(FB_POINTS_GRANT.format(u=f"'{APP_USER}'@'localhost'"))
    else:
        print("略過粉專集點檢視表：crm1.fb_comment_points 還沒建（先跑 y1crm scripts/schema_fb.py 再重跑這支）")
    cur.execute(f"SHOW GRANTS FOR '{APP_USER}'@'localhost'")
    for (g,) in cur.fetchall():
        print(" ", g)
    con.close()
    print("完成。")


if __name__ == "__main__":
    main()
