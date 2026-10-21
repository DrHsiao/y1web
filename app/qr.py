"""qr.py — 產生 QR Code 的 SVG（給電腦版「用手機掃描」用；不載入外部圖片、不用行內樣式）。"""
from __future__ import annotations

from functools import lru_cache

import qrcode
from markupsafe import Markup


@lru_cache(maxsize=256)
def svg(data: str, label: str = "QR Code") -> Markup:
    qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(data)
    qr.make(fit=True)
    m = qr.get_matrix()
    n = len(m)
    path = "".join(f"M{x} {y}h1v1h-1z" for y, row in enumerate(m) for x, on in enumerate(row) if on)
    label = label.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    return Markup(f'<svg class="qr" viewBox="0 0 {n} {n}" role="img" aria-label="{label}" '
                  f'shape-rendering="crispEdges"><rect width="{n}" height="{n}" fill="#fff"/>'
                  f'<path d="{path}" fill="#000"/></svg>')
