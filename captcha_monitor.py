"""Luồng nền: quét màn hình → phát hiện captcha → giải qua SadCaptcha → báo Telegram."""
from __future__ import annotations

import logging
import random
import threading
import time
from datetime import datetime

import mss
import numpy as np
import pyautogui

from config import APP_DIR, SAMPLE_PATH
from human_mouse import human_drag
from sadcaptcha import SadCaptchaClient, SadCaptchaError
from telegram_notifier import TelegramNotifier
from vision import (
    Match,
    crop_region,
    encode_png_b64,
    find_template,
    grab_screen,
    load_template,
    occlude_rect,
    save_png,
)

log = logging.getLogger("monitor")


def _stamp() -> str:
    return datetime.now().strftime("%H:%M:%S %d/%m/%Y")


class CaptchaMonitor(threading.Thread):
    def __init__(self, cfg: dict) -> None:
        super().__init__(name="captcha-monitor", daemon=True)
        self.cfg = cfg  # bản sao chụp lại lúc bấm "Bắt đầu"
        self.stop_event = threading.Event()
        self._template: np.ndarray | None = None
        self._notifier: TelegramNotifier | None = None
        self._alerted_at: float | None = None  # mốc đã xử lý captcha hiện tại (tránh báo lặp)
        self._error_streak = 0

    # ------------------------------------------------------------------ vòng đời

    def run(self) -> None:
        cfg = self.cfg
        try:
            self._template = load_template(SAMPLE_PATH)
        except (OSError, ValueError) as exc:
            log.error("Chưa có ảnh mẫu hợp lệ (%s). Bấm 'Chụp ảnh mẫu' trước.", exc)
            return

        if cfg["telegram_token"] and cfg["telegram_chat_id"]:
            self._notifier = TelegramNotifier(cfg["telegram_token"], cfg["telegram_chat_id"])
            self._notifier.start()
        else:
            log.warning("Chưa cấu hình Telegram → chỉ ghi log, không gửi cảnh báo.")

        log.info("Bắt đầu theo dõi (quét mỗi %.1fs, ngưỡng khớp %.2f)", cfg["scan_interval_sec"], cfg["match_threshold"])
        self._notify_text(f"🟢 Bắt đầu theo dõi captcha TikTok\n🕒 {_stamp()}")
        try:
            with mss.mss() as sct:  # mss phải được tạo trong chính luồng dùng nó
                while not self.stop_event.is_set():
                    started = time.monotonic()
                    try:
                        self._tick(sct)
                        self._error_streak = 0
                    except pyautogui.FailSafeException:
                        log.error("FAILSAFE: chuột chạm góc màn hình → dừng khẩn cấp.")
                        break
                    except Exception:  # noqa: BLE001 - lỗi tạm thời (khoá màn hình, mạng...) không được làm chết luồng
                        self._error_streak += 1
                        if self._error_streak == 1 or self._error_streak % 20 == 0:
                            log.exception("Lỗi khi quét (lần liên tiếp thứ %d), sẽ thử lại", self._error_streak)
                    elapsed = time.monotonic() - started
                    self.stop_event.wait(max(0.2, cfg["scan_interval_sec"] - elapsed))
        finally:
            self._notify_text(f"🔴 Đã dừng theo dõi captcha TikTok\n🕒 {_stamp()}")
            if self._notifier:
                self._notifier.stop()
            log.info("Đã dừng theo dõi.")

    # ------------------------------------------------------------------ một lượt quét

    def _tick(self, sct) -> None:
        cfg = self.cfg
        frame, _ = grab_screen(sct, cfg["monitor_index"])
        match = find_template(frame, self._template, cfg["match_threshold"])
        if match is None:
            self._alerted_at = None  # popup đã biến mất → lần sau có captcha mới sẽ báo lại
            return

        now = time.monotonic()
        if self._alerted_at is not None and now - self._alerted_at < cfg["realert_after_sec"]:
            return  # vẫn là captcha cũ đã xử lý (thất bại) → không spam
        self._alerted_at = now
        self._handle(sct, frame, match)
        self._alerted_at = time.monotonic()

    # ------------------------------------------------------------------ xử lý captcha

    def _handle(self, sct, frame: np.ndarray, match: Match) -> None:
        cfg = self.cfg
        log.info("PHÁT HIỆN captcha (độ khớp %.2f)", match.score)
        self._notify_photo(frame, f"⚠️ Phát hiện captcha TikTok\nĐộ khớp: {match.score:.2f}\n🕒 {_stamp()}")

        if not all(cfg["regions"].values()):
            log.warning("Chưa hiệu chỉnh vùng captcha → chỉ cảnh báo, không tự giải.")
            self._notify_text("ℹ️ Chưa hiệu chỉnh vùng captcha nên không tự giải. Hãy xử lý thủ công.")
            return
        if not cfg["sadcaptcha_key"]:
            log.warning("Chưa có SadCaptcha API Key → chỉ cảnh báo.")
            self._notify_text("ℹ️ Chưa nhập SadCaptcha API Key nên không tự giải. Hãy xử lý thủ công.")
            return

        client = SadCaptchaClient(cfg["sadcaptcha_key"])
        try:
            ok, detail = self._solve_loop(sct, client)
        finally:
            client.close()

        if ok:
            log.info("THÀNH CÔNG: %s", detail)
            self._notify_text(f"✅ Giải captcha thành công\n{detail}\n🕒 {_stamp()}")
        else:
            log.error("THẤT BẠI: %s", detail)
            frame, _ = grab_screen(sct, cfg["monitor_index"])
            self._notify_photo(frame, f"❌ Giải captcha thất bại\n{detail}\nCần xử lý thủ công!\n🕒 {_stamp()}")

    def _solve_loop(self, sct, client: SadCaptchaClient) -> tuple[bool, str]:
        cfg = self.cfg
        attempts = cfg["max_attempts"]
        last_error = "không rõ"

        for attempt in range(1, attempts + 1):
            # Nghỉ ngẫu nhiên 1–2s trước khi kéo để tránh hành vi quá máy móc
            delay = random.uniform(*cfg["pre_drag_delay_sec"])
            log.info("Lần %d/%d: chờ %.1fs trước khi kéo...", attempt, attempts, delay)
            if self.stop_event.wait(delay):
                return False, "Đã dừng theo dõi giữa chừng."

            frame, mon = grab_screen(sct, cfg["monitor_index"])
            match = find_template(frame, self._template, cfg["match_threshold"])
            if match is None:
                return True, "Popup captcha đã biến mất trước khi kéo (có thể đã được giải)."

            try:
                distance, start = self._plan_drag(frame, match, mon, client)
                human_drag(start[0], start[1], distance, self.stop_event)
            except pyautogui.FailSafeException:
                raise
            except InterruptedError:
                return False, "Đã dừng theo dõi giữa chừng."
            except (SadCaptchaError, ValueError) as exc:
                last_error = str(exc)
                log.error("Lần %d/%d lỗi: %s", attempt, attempts, last_error)
                continue
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
                log.exception("Lần %d/%d lỗi không mong đợi", attempt, attempts)
                continue

            if self.stop_event.wait(cfg["post_drag_wait_sec"]):
                return False, "Đã dừng theo dõi giữa chừng."
            frame, _ = grab_screen(sct, cfg["monitor_index"])
            if find_template(frame, self._template, cfg["match_threshold"]) is None:
                return True, f"Popup đã biến mất sau lần kéo thứ {attempt} (quãng kéo ≈ {distance:.0f}px)."
            last_error = f"Popup vẫn còn sau lần kéo thứ {attempt} (≈ {distance:.0f}px)"
            log.warning(last_error)

        return False, f"Đã thử {attempts} lần không thành công. Lỗi gần nhất: {last_error}"

    def _plan_drag(self, frame, match: Match, mon: dict, client: SadCaptchaClient):
        """Cắt ảnh → gọi API → trả về (quãng kéo px, (x, y) điểm bấm trên màn hình ảo)."""
        cfg = self.cfg
        r = cfg["regions"]
        puzzle = crop_region(frame, match, r["puzzle"])
        piece = crop_region(frame, match, r["piece"])

        # Ảnh chụp màn hình có mảnh ghép nằm đè lên ảnh nền → che nó đi trước khi gửi
        px, py = r["piece"][0] - r["puzzle"][0], r["piece"][1] - r["puzzle"][1]
        occlude_rect(puzzle, (px, py, r["piece"][2], r["piece"][3]), cfg["occlude_padding_px"])

        if cfg["debug_save"]:
            self._save_debug(puzzle, piece)

        proportion = client.solve_puzzle(encode_png_b64(puzzle), encode_png_b64(piece))
        distance = proportion * puzzle.shape[1] * cfg["drag_scale"] + cfg["drag_offset_px"]

        sx, sy, sw, sh = r["slider"]
        cx = mon["left"] + match.x + sx + sw / 2 + random.uniform(-sw * 0.15, sw * 0.15)
        cy = mon["top"] + match.y + sy + sh / 2 + random.uniform(-sh * 0.15, sh * 0.15)
        log.info("SadCaptcha: slideXProportion=%.4f → kéo %.0fpx", proportion, distance)
        return distance, (cx, cy)

    @staticmethod
    def _save_debug(puzzle: np.ndarray, piece: np.ndarray) -> None:
        try:
            folder = APP_DIR / "debug"
            folder.mkdir(exist_ok=True)
            tag = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_png(folder / f"{tag}_puzzle.png", puzzle)
            save_png(folder / f"{tag}_piece.png", piece)
        except OSError:
            log.warning("Không lưu được ảnh debug")

    # ------------------------------------------------------------------ Telegram (không bao giờ ném lỗi)

    def _notify_text(self, text: str) -> None:
        if self._notifier:
            self._notifier.send_text(text)

    def _notify_photo(self, frame: np.ndarray, caption: str) -> None:
        if self._notifier:
            self._notifier.send_photo(frame, caption)
