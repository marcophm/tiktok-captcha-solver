"""Công cụ hiệu chỉnh: chụp ảnh mẫu (sample.png) và chọn các vùng puzzle / mảnh ghép / nút trượt.

Tất cả hàm ở đây chạy ở luồng giao diện (Tkinter) và trả về chuỗi thông báo để hiển thị.
"""
from __future__ import annotations

import logging
import time
import tkinter as tk

import cv2
import mss
import numpy as np
from PIL import Image, ImageTk

from config import SAMPLE_PATH
from vision import find_template, grab_screen, load_template, save_png

log = logging.getLogger("calibration")

Rect = tuple[int, int, int, int]  # x, y, w, h


def select_region(parent: tk.Tk, frame: np.ndarray, monitor: dict, prompt: str) -> Rect | None:
    """Hiện ảnh chụp 'đóng băng' toàn màn hình, người dùng kéo chuột để chọn một vùng.

    Trả về (x, y, w, h) theo toạ độ ảnh chụp, hoặc None nếu huỷ (Esc / chuột phải).
    """
    h, w = frame.shape[:2]
    top = tk.Toplevel(parent)
    top.overrideredirect(True)
    top.geometry(f"{w}x{h}+{monitor['left']}+{monitor['top']}")
    top.attributes("-topmost", True)

    photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
    canvas = tk.Canvas(top, width=w, height=h, highlightthickness=0, cursor="crosshair")
    canvas.pack()
    canvas.create_image(0, 0, image=photo, anchor="nw")
    canvas.image = photo  # giữ tham chiếu để ảnh không bị thu hồi
    canvas.create_rectangle(0, 0, w, 36, fill="#111111", outline="")
    canvas.create_text(
        w // 2, 18, text=f"{prompt}   (Esc hoặc chuột phải = huỷ)",
        fill="white", font=("Segoe UI", 12, "bold"),
    )

    state: dict = {"x0": 0, "y0": 0, "rect": None, "result": None}

    def on_press(e: tk.Event) -> None:
        state["x0"], state["y0"] = e.x, e.y
        if state["rect"] is not None:
            canvas.delete(state["rect"])
        state["rect"] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="#00ff66", width=2)

    def on_drag(e: tk.Event) -> None:
        if state["rect"] is not None:
            canvas.coords(state["rect"], state["x0"], state["y0"], e.x, e.y)

    def on_release(e: tk.Event) -> None:
        if state["rect"] is None:
            return
        x0, x1 = sorted((state["x0"], e.x))
        y0, y1 = sorted((state["y0"], e.y))
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, w), min(y1, h)
        if x1 - x0 < 4 or y1 - y0 < 4:  # kéo quá nhỏ → cho chọn lại
            canvas.delete(state["rect"])
            state["rect"] = None
            return
        state["result"] = (x0, y0, x1 - x0, y1 - y0)
        top.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    canvas.bind("<Button-3>", lambda _e: top.destroy())
    top.bind("<Escape>", lambda _e: top.destroy())

    top.focus_force()
    top.grab_set()
    parent.wait_window(top)
    return state["result"]


def _grab_with_window_hidden(root: tk.Tk, monitor_index: int) -> tuple[np.ndarray, dict]:
    """Ẩn cửa sổ ứng dụng rồi chụp màn hình để cửa sổ không lọt vào ảnh."""
    root.withdraw()
    root.update()
    time.sleep(0.4)
    with mss.mss() as sct:
        return grab_screen(sct, monitor_index)


def capture_sample(root: tk.Tk, monitor_index: int) -> str:
    """Người dùng kéo chọn phần CỐ ĐỊNH của popup captcha → lưu thành sample.png."""
    try:
        frame, mon = _grab_with_window_hidden(root, monitor_index)
        rect = select_region(
            root, frame, mon,
            "Kéo chọn phần CỐ ĐỊNH của popup (tiêu đề/khung), KHÔNG gồm ảnh puzzle",
        )
        if rect is None:
            return "Đã huỷ chụp ảnh mẫu."
        x, y, w, h = rect
        if w < 30 or h < 15:
            return "Vùng chọn quá nhỏ (tối thiểu ~30x15 px) – hãy chọn lại."
        save_png(SAMPLE_PATH, frame[y:y + h, x:x + w])
        return f"Đã lưu ảnh mẫu {w}x{h}px → {SAMPLE_PATH.name}"
    except Exception as exc:  # noqa: BLE001
        log.exception("Lỗi khi chụp ảnh mẫu")
        return f"Lỗi khi chụp ảnh mẫu: {exc}"
    finally:
        root.deiconify()


_STEPS = (
    ("puzzle", "1/3: Kéo chọn VÙNG ẢNH PUZZLE (toàn bộ ảnh nền có lỗ khuyết)"),
    ("piece", "2/3: Kéo chọn MẢNH GHÉP (bao sát mảnh ghép)"),
    ("slider", "3/3: Kéo chọn NÚT TRƯỢT (tay nắm trên thanh kéo)"),
)


def calibrate_regions(root: tk.Tk, monitor_index: int, threshold: float) -> tuple[dict | None, str]:
    """Cần popup captcha đang hiển thị. Trả về (regions | None, thông báo).

    Mỗi vùng được lưu dưới dạng [dx, dy, w, h] tương đối so với góc ảnh mẫu,
    nên popup nằm ở đâu trên màn hình cũng cắt đúng.
    """
    try:
        if not SAMPLE_PATH.exists():
            return None, "Chưa có sample.png – hãy bấm 'Chụp ảnh mẫu' trước."
        template = load_template(SAMPLE_PATH)

        frame, mon = _grab_with_window_hidden(root, monitor_index)
        match = find_template(frame, template, threshold)
        if match is None:
            return None, "Không thấy popup captcha trên màn hình (hoặc ảnh mẫu không khớp). Hãy bật popup rồi thử lại."

        regions: dict = {}
        for key, prompt in _STEPS:
            rect = select_region(root, frame, mon, prompt)
            if rect is None:
                return None, "Đã huỷ hiệu chỉnh."
            regions[key] = [rect[0] - match.x, rect[1] - match.y, rect[2], rect[3]]
        return regions, f"Đã hiệu chỉnh 3 vùng (độ khớp ảnh mẫu {match.score:.2f})."
    except Exception as exc:  # noqa: BLE001
        log.exception("Lỗi khi hiệu chỉnh")
        return None, f"Lỗi khi hiệu chỉnh: {exc}"
    finally:
        root.deiconify()
