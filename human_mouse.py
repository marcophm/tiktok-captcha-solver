"""Mô phỏng kéo chuột giống người: có gia tốc/giảm tốc, lệch nhẹ trục Y, vượt quá rồi chỉnh lại."""
from __future__ import annotations

import logging
import math
import random
import threading
import time

import pyautogui

pyautogui.PAUSE = 0       # tự điều khiển thời gian nghỉ giữa các bước
pyautogui.FAILSAFE = True  # đưa chuột vào góc trên-trái màn hình để dừng khẩn cấp

log = logging.getLogger("mouse")

Step = tuple[float, float, float]  # (dx, dy, thời gian nghỉ sau bước này)


def _ease(t: float) -> float:
    """Smootherstep: chậm lúc bắt đầu, nhanh ở giữa, chậm lúc kết thúc."""
    return t * t * t * (t * (6 * t - 15) + 10)


def build_path(distance: float, rng=random) -> list[Step]:
    """Sinh quỹ đạo tương đối so với điểm bấm chuột. Bước cuối luôn kết thúc đúng tại `distance`."""
    steps = rng.randint(40, 58)
    overshoot = rng.uniform(2.0, 5.0) if distance > 40 and rng.random() < 0.65 else 0.0
    target = distance + overshoot

    amp = rng.uniform(0.8, 2.4)          # biên độ lệch trục Y (px)
    freq = rng.uniform(0.7, 1.5)
    phase = rng.uniform(0.0, 2 * math.pi)
    drift = rng.uniform(-1.2, 1.2)       # trôi dần lên/xuống
    base = rng.uniform(0.012, 0.022)     # nhịp thời gian cơ bản (giây)

    path: list[Step] = []
    for i in range(1, steps + 1):
        t = i / steps
        dx = target * _ease(t) + (rng.gauss(0, 0.3) if i < steps else 0.0)
        dy = (
            amp * math.sin(2 * math.pi * freq * t + phase) * math.sqrt(math.sin(math.pi * t))
            + drift * t
            + rng.gauss(0, 0.2)
        )
        delay = base * (1.7 - math.sin(math.pi * t)) * rng.uniform(0.8, 1.25)  # nhanh ở giữa, chậm ở hai đầu
        path.append((dx, dy, delay))

    if overshoot:
        # dừng một nhịp như người nhận ra đã kéo lố, rồi lùi lại từ từ
        dx, dy, delay = path[-1]
        path[-1] = (dx, dy, delay + rng.uniform(0.08, 0.18))
        back = rng.randint(4, 8)
        for j in range(1, back + 1):
            u = j / back
            path.append((target - overshoot * _ease(u), dy + rng.gauss(0, 0.2), rng.uniform(0.025, 0.05)))
    return path


def human_drag(
    start_x: float,
    start_y: float,
    distance: float,
    stop_event: threading.Event | None = None,
    rng=random,
) -> None:
    """Di chuột tới (start_x, start_y), nhấn giữ, kéo sang phải `distance` px rồi thả.

    Luôn nhả chuột trong `finally` để không bị kẹt nút chuột khi có lỗi/dừng khẩn cấp.
    Ném InterruptedError nếu stop_event được đặt giữa chừng.
    """
    pyautogui.moveTo(
        int(start_x), int(start_y),
        duration=rng.uniform(0.35, 0.7), tween=pyautogui.easeOutQuad,
    )
    time.sleep(rng.uniform(0.12, 0.30))

    pressed = False
    try:
        pyautogui.mouseDown()
        pressed = True
        time.sleep(rng.uniform(0.08, 0.20))
        for dx, dy, delay in build_path(distance, rng):
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError("Đã dừng khi đang kéo")
            pyautogui.moveTo(round(start_x + dx), round(start_y + dy))
            time.sleep(delay)
        time.sleep(rng.uniform(0.12, 0.30))  # giữ thêm chút trước khi thả
    finally:
        if pressed:
            try:
                pyautogui.mouseUp()
            except Exception:  # noqa: BLE001 - không để lỗi nhả chuột che lỗi gốc
                log.exception("Không nhả được chuột")
