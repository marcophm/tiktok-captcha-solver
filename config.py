"""Đường dẫn ứng dụng và đọc/ghi config.json."""
from __future__ import annotations

import copy
import json
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("config")


def _app_dir() -> Path:
    """Thư mục chứa config.json / sample.png.

    Khi đóng gói --onefile, __file__ trỏ vào thư mục tạm (_MEIPASS) bị xoá khi
    thoát, nên phải dùng thư mục chứa file .exe.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _app_dir()
# Tài nguyên đóng gói kèm exe (vd. app.ico qua --add-data)
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
CONFIG_PATH = APP_DIR / "config.json"
SAMPLE_PATH = APP_DIR / "sample.png"
LOG_PATH = APP_DIR / "app.log"

REGION_KEYS = ("puzzle", "piece", "slider")

DEFAULT_CONFIG: dict = {
    "telegram_token": "",
    "telegram_chat_id": "",
    "sadcaptcha_key": "",
    # --- nhận diện ---
    "scan_interval_sec": 3.0,
    "match_threshold": 0.80,
    "monitor_index": 1,  # 1 = màn hình chính (mss)
    # --- giải captcha ---
    "max_attempts": 2,
    "pre_drag_delay_sec": [1.0, 2.0],
    "post_drag_wait_sec": 2.5,
    "realert_after_sec": 300,  # captcha còn đó sau ngần này giây thì xử lý lại
    "drag_scale": 1.0,  # quãng kéo = slideXProportion * chiều rộng ảnh puzzle * drag_scale
    "drag_offset_px": 0.0,  # cộng thêm (px) để bù sai số
    "occlude_padding_px": 4,
    "debug_save": False,  # lưu ảnh puzzle/piece vào thư mục debug/
    # Vùng cắt, toạ độ TƯƠNG ĐỐI so với góc trên-trái của ảnh mẫu: [dx, dy, w, h]
    "regions": {k: None for k in REGION_KEYS},
}


def _num(value, default, lo, hi, cast=float):
    try:
        v = cast(value)
    except (TypeError, ValueError):
        return default
    return min(max(v, lo), hi)


def _region(value):
    try:
        dx, dy, w, h = (int(v) for v in value)
    except (TypeError, ValueError):
        return None
    return [dx, dy, w, h] if w > 0 and h > 0 else None


def _delay_pair(value, default):
    try:
        lo, hi = sorted(max(0.0, float(v)) for v in value)
        return [lo, hi]
    except (TypeError, ValueError):
        return list(default)


def normalize(cfg: dict) -> dict:
    """Ép kiểu / giới hạn giá trị để file sửa tay không làm app văng lỗi."""
    d = DEFAULT_CONFIG
    for key in ("telegram_token", "telegram_chat_id", "sadcaptcha_key"):
        cfg[key] = str(cfg.get(key) or "").strip()
    cfg["scan_interval_sec"] = _num(cfg.get("scan_interval_sec"), d["scan_interval_sec"], 1.0, 600.0)
    cfg["match_threshold"] = _num(cfg.get("match_threshold"), d["match_threshold"], 0.3, 0.99)
    cfg["monitor_index"] = _num(cfg.get("monitor_index"), d["monitor_index"], 1, 16, int)
    cfg["max_attempts"] = _num(cfg.get("max_attempts"), d["max_attempts"], 1, 5, int)
    cfg["pre_drag_delay_sec"] = _delay_pair(cfg.get("pre_drag_delay_sec"), d["pre_drag_delay_sec"])
    cfg["post_drag_wait_sec"] = _num(cfg.get("post_drag_wait_sec"), d["post_drag_wait_sec"], 0.5, 15.0)
    cfg["realert_after_sec"] = _num(cfg.get("realert_after_sec"), d["realert_after_sec"], 30, 86400)
    cfg["drag_scale"] = _num(cfg.get("drag_scale"), d["drag_scale"], 0.2, 3.0)
    cfg["drag_offset_px"] = _num(cfg.get("drag_offset_px"), d["drag_offset_px"], -100.0, 100.0)
    cfg["occlude_padding_px"] = _num(cfg.get("occlude_padding_px"), d["occlude_padding_px"], 0, 30, int)
    cfg["debug_save"] = bool(cfg.get("debug_save", False))
    regions = cfg.get("regions") if isinstance(cfg.get("regions"), dict) else {}
    cfg["regions"] = {k: _region(regions.get(k)) for k in REGION_KEYS}
    return cfg


def load_config() -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if not CONFIG_PATH.exists():
        return normalize(cfg)
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("config.json phải là một JSON object")
    except (OSError, ValueError) as exc:
        backup = CONFIG_PATH.with_name("config.json.bak")
        log.warning("config.json lỗi (%s) → sao lưu thành %s và dùng mặc định", exc, backup.name)
        try:
            os.replace(CONFIG_PATH, backup)
        except OSError:
            pass
        return normalize(cfg)
    cfg.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
    return normalize(cfg)


def save_config(cfg: dict) -> None:
    """Ghi nguyên tử: ghi file tạm rồi thay thế, tránh hỏng file nếu mất điện giữa chừng."""
    tmp = CONFIG_PATH.with_name("config.json.tmp")
    tmp.write_text(json.dumps(normalize(copy.deepcopy(cfg)), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)
