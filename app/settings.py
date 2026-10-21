"""
settings.py — 讀 config.toml（路徑可用 Y1WEB_CONFIG 指定）。

config.toml 含資料庫密碼與表單簽章金鑰，不進版控；config.example.toml 是樣板。
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
_PATH = Path(os.environ.get("Y1WEB_CONFIG", str(ROOT / "config.toml")))

_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "server": {"host": "0.0.0.0", "port": 8006, "forwarded_allow_ips": "127.0.0.1"},
    "db": {"host": "localhost", "user": "y1web_app", "password": "", "database": "y1web",
           "charset": "utf8mb4"},
    "site": {"name": "韻譯助聽器 睿聲中正門市", "base_url": "http://192.168.1.88:8006", "secret": ""},
    "line": {"basic_id": "@021tpjrz"},
    "booking": {"code_days": 7},
}


def _load() -> Dict[str, Dict[str, Any]]:
    cfg = {k: dict(v) for k, v in _DEFAULTS.items()}
    if _PATH.exists():
        with open(_PATH, "rb") as f:
            for sec, vals in tomllib.load(f).items():
                cfg.setdefault(sec, {}).update(vals)
    return cfg


_CFG = _load()
SERVER = _CFG["server"]
DB = _CFG["db"]
SITE = _CFG["site"]
LINE = _CFG["line"]
BOOKING = _CFG["booking"]
