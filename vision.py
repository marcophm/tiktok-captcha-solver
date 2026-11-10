"""Chụp màn hình, nhận diện popup bằng template matching, cắt/mã hoá ảnh."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

import cv2
import mss
import numpy as np


@dataclass(frozen=True)
class Match:
    """Vị trí ảnh mẫu tìm thấy, toạ độ trong khung hình của màn hình (px)."""

    x: int
    y: int
    w: int
    h: int
    score: float


def grab_screen(sct: "mss.base.MSSBase", monitor_index: int) -> tuple[np.ndarray, dict]:
    """Chụp một màn hình, trả về (ảnh BGR, dict monitor có left/top/width/height).

    Lưu ý: đối tượng mss không an toàn giữa các luồng → mỗi luồng tự tạo mss.mss() riêng.
    """
    monitors = sct.monitors
    if not 1 <= monitor_index < len(monitors):
        monitor_index = 1
    mon = monitors[monitor_index]
    frame = cv2.cvtColor(np.array(sct.grab(mon)), cv2.COLOR_BGRA2BGR)
    return frame, mon


# ---------- đọc/ghi ảnh (an toàn với đường dẫn có dấu tiếng Việt trên Windows) ----------

def load_template(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"Không đọc được ảnh mẫu: {path}")
    return img


def save_png(path: Path, image: np.ndarray) -> None:
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("Không mã hoá được ảnh PNG")
    buf.tofile(str(path))


# ---------- nhận diện ----------

def find_template(frame: np.ndarray, template: np.ndarray, threshold: float) -> Match | None:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    th, tw = template.shape[:2]
    if gray.shape[0] < th or gray.shape[1] < tw:
        return None
    res = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
    res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)  # mẫu đơn sắc có thể sinh NaN
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    if max_val < threshold:
        return None
    return Match(int(max_loc[0]), int(max_loc[1]), tw, th, float(max_val))


# ---------- cắt / che / mã hoá ----------

def crop_region(frame: np.ndarray, match: Match, region: list[int]) -> np.ndarray:
    """Cắt vùng [dx, dy, w, h] tính tương đối từ góc trên-trái của ảnh mẫu."""
    dx, dy, w, h = region
    x, y = match.x + dx, match.y + dy
    fh, fw = frame.shape[:2]
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, fw), min(y + h, fh)
    if x1 - x0 < 2 or y1 - y0 < 2:
        raise ValueError("Vùng cắt nằm ngoài màn hình – hãy hiệu chỉnh lại")
    return frame[y0:y1, x0:x1].copy()


def occlude_rect(image: np.ndarray, rect: tuple[int, int, int, int], padding: int) -> None:
    """Che mảnh ghép trong ảnh nền bằng hình elip màu xanh (theo hướng dẫn ảnh chụp màn hình của SadCaptcha)."""
    x, y, w, h = rect
    center = (int(x + w / 2), int(y + h / 2))
    axes = (max(1, int(w / 2 + padding)), max(1, int(h / 2 + padding)))
    cv2.ellipse(image, center, axes, 0, 0, 360, (255, 0, 0), -1)  # BGR: xanh dương


def encode_png_b64(image: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("Không mã hoá được ảnh PNG")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def encode_jpeg(image: np.ndarray, quality: int = 70, max_width: int = 1600) -> bytes:
    """JPEG nhẹ để gửi Telegram nhanh, đỡ lỗi khi mạng yếu."""
    h, w = image.shape[:2]
    if w > max_width:
        image = cv2.resize(image, (max_width, int(h * max_width / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("Không mã hoá được ảnh JPEG")
    return buf.tobytes()
