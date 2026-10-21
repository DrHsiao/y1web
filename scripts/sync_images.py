#!/usr/bin/env python3
"""
sync_images.py — 把 y1crm 商品型錄的照片整理成官網用的圖（media/catalog/*.webp）。

型錄照是從經銷商 DM 裁下來的，常帶著型號字、色條、框線，配件還印著售價。官網不能露出價格，
所以這裡只留機身：找出照片上方最大的一塊圖形、去掉旁邊的框線和文字，放到正方形白底上。
自動裁不好的，在 scripts/image_overrides.toml 指定裁切範圍，或設 skip 不用。

同一張照片（同機型不同等級共用）只輸出一次，檔名是內容雜湊；對照表寫進 y1web.web_catalog_images。
y1web 服務讀不到 y1crm 的上傳目錄，所以要用管理者身分執行：
  sudo /home/john/y1web/venv/bin/python /home/john/y1web/scripts/sync_images.py [--dry]
照片由 John 確認已取得代理授權可使用（2026-10-21）。
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
import tomllib
from pathlib import Path

from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_db import _admin_conn, CRM_CONFIG  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "media" / "catalog"
OVERRIDES = Path(__file__).resolve().parent / "image_overrides.toml"
SIZE = 480                  # 輸出邊長
PAD = 0.08                  # 機身四周留白（佔邊長比例）
WHITE = 236                 # RGB 都 ≥ 這個值當作白底

TABLE = """CREATE TABLE IF NOT EXISTS y1web.web_catalog_images (
  product_id INT NOT NULL PRIMARY KEY COMMENT 'crm1.products.id',
  file VARCHAR(40) NOT NULL COMMENT 'media/catalog/ 底下的檔名',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='官網商品照對照（scripts/sync_images.py 產生）'"""


def _uploads_base() -> Path:
    with open(CRM_CONFIG, "rb") as f:
        return Path(tomllib.load(f)["storage"]["base_dir"])


def _components(mask, w, h):
    """4 連通區塊：回傳 [(像素數, x0, y0, x1, y1)]。mask 是 list[bool]（一維，寬 w）。"""
    seen = bytearray(w * h)
    out = []
    for start in range(w * h):
        if not mask[start] or seen[start]:
            continue
        seen[start] = 1
        stack, n = [start], 0
        x0, y0, x1, y1 = w, h, 0, 0
        while stack:
            i = stack.pop()
            n += 1
            x, y = i % w, i // w
            x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x), max(y1, y)
            for j in ((i - 1) if x > 0 else -1, (i + 1) if x < w - 1 else -1, i - w, i + w):
                if 0 <= j < w * h and mask[j] and not seen[j]:
                    seen[j] = 1
                    stack.append(j)
        out.append((n, x0, y0, x1 + 1, y1 + 1))
    return out


def find_device(im: Image.Image):
    """回傳機身範圍 (x0, y0, x1, y1)：最大的一塊圖形，加上和它重疊的小零件（耳塞、導線）。"""
    g = im.convert("L")
    w, h = g.size
    # 1) 拿掉直的分隔線（DM 版面的框線／色條）：很窄、很長的墨色欄；寬的是機身，不能動
    px = g.load()

    def longest(x):
        best = cur = 0
        for y in range(h):
            cur = cur + 1 if px[x, y] < WHITE else 0
            best = max(best, cur)
        return best

    tall = [longest(x) > h * 0.85 for x in range(w)]      # 分隔線幾乎貫穿整張圖；機身＋下方文字不會連成一條
    x = 0
    while x < w:
        if not tall[x]:
            x += 1
            continue
        x_end = x
        while x_end < w and tall[x_end]:
            x_end += 1
        clear_l = x < 2 or not tall[x - 2]
        clear_r = x_end >= w - 1 or not tall[x_end + 1]
        if x_end - x <= 16 and clear_l and clear_r:
            for xx in range(max(0, x - 1), min(w, x_end + 1)):
                for y in range(h):
                    px[xx, y] = 255
        x = x_end
    # 2) 墨色遮罩 → 膨脹幾個像素讓導線、耳塞和機身連起來 → 縮小一半加快速度
    ink = g.point(lambda v: 255 if v < WHITE else 0).filter(ImageFilter.MaxFilter(7))
    sw, sh = w // 2, h // 2
    small = ink.resize((sw, sh), Image.NEAREST)
    mask = [v > 0 for v in small.tobytes()]
    # 細長的（剩下的線條）、扁長的（一行型號字）都不算機身
    comps = [c for c in _components(mask, sw, sh)
             if max(c[3] - c[1], c[4] - c[2]) < 8 * min(c[3] - c[1], c[4] - c[2])
             and c[3] - c[1] < 3 * (c[4] - c[2])]
    if not comps:
        return (0, 0, w, h)
    main = max(comps)
    _, x0, y0, x1, y1 = main
    bottom = y1                     # 一律和機身本體比，不和逐步擴大後的範圍比
    my = int(sh * 0.06)
    for n, a0, b0, a1, b1 in comps:
        # 耳塞、導線這類零件：和機身同高度、不貼著右邊界（DM 的箭頭裝飾在右邊）。
        # 整塊在機身下方的是型號字、價格，不要。
        if (n, a0, b0, a1, b1) == main or b0 >= bottom - 2 or (b0 + b1) / 2 > bottom + my:
            continue
        if a1 >= sw - 2 or n < 12:
            continue
        x0, y0, x1, y1 = min(x0, a0), min(y0, b0), max(x1, a1), max(y1, b1)
    return (max(0, x0 * 2 - 2), max(0, y0 * 2 - 2), min(w, x1 * 2 + 2), min(h, y1 * 2 + 2))


def render(im: Image.Image, box) -> bytes:
    crop = im.convert("RGB").crop(box)
    cw, ch = crop.size
    inner = int(SIZE * (1 - 2 * PAD))
    scale = min(inner / cw, inner / ch, 2.0)
    crop = crop.resize((max(1, round(cw * scale)), max(1, round(ch * scale))), Image.LANCZOS)
    canvas = Image.new("RGB", (SIZE, SIZE), "white")
    canvas.paste(crop, ((SIZE - crop.width) // 2, (SIZE - crop.height) // 2))
    buf = io.BytesIO()
    canvas.save(buf, "WEBP", quality=84, method=6)
    return buf.getvalue()


def main() -> None:
    dry = "--dry" in sys.argv
    base = _uploads_base()
    over = tomllib.loads(OVERRIDES.read_text("utf-8")) if OVERRIDES.exists() else {}
    db = _admin_conn()
    cur = db.cursor()
    cur.execute("SELECT a.product_id, a.rel_path FROM crm1.product_attachments a "
                "JOIN crm1.products p ON p.id = a.product_id "
                "WHERE p.is_active = 1 AND a.mime LIKE 'image/%%' ORDER BY a.product_id, a.id")
    first = {}
    for pid, rel in cur.fetchall():
        first.setdefault(pid, rel)                  # 每個商品用第一張圖
    done, mapping, skipped = {}, {}, 0
    OUT.mkdir(parents=True, exist_ok=True)
    for pid, rel in first.items():
        raw = (base / rel).read_bytes()
        key = hashlib.sha256(raw).hexdigest()[:12]
        o = over.get(key, {})
        if o.get("skip"):
            skipped += 1
            continue
        if key not in done:
            im = Image.open(io.BytesIO(raw))
            box = tuple(o["box"]) if "box" in o else find_device(im)
            name = f"{key}.webp"
            if not dry:
                (OUT / name).write_bytes(render(im, box))
            done[key] = name
        mapping[pid] = done[key]
    print(f"商品 {len(first)}、輸出 {len(done)} 張、略過 {skipped}")
    if dry:
        return
    keep = set(done.values())
    for p in OUT.glob("*.webp"):
        if p.name not in keep:
            p.unlink()
    cur.execute(TABLE)
    cur.execute("DELETE FROM y1web.web_catalog_images")
    cur.executemany("INSERT INTO y1web.web_catalog_images (product_id, file) VALUES (%s, %s)",
                    list(mapping.items()))
    os.chmod(OUT, 0o755)
    for p in OUT.glob("*.webp"):
        os.chmod(p, 0o644)


if __name__ == "__main__":
    main()
