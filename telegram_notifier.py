"""Gửi tin nhắn/ảnh Telegram qua hàng đợi nền: mạng chập chờn không làm đứng luồng giải captcha."""
from __future__ import annotations

import logging
import queue
import threading
import time

import numpy as np
import requests

from vision import encode_jpeg

log = logging.getLogger("telegram")

MAX_TEXT = 4000      # giới hạn Telegram là 4096
MAX_CAPTION = 1000   # giới hạn Telegram là 1024


class TelegramError(Exception):
    pass


class TelegramNotifier:
    API = "https://api.telegram.org"

    def __init__(self, token: str, chat_id: str) -> None:
        self._token = token.strip()
        self._chat_id = chat_id.strip()
        self._session = requests.Session()
        self._q: "queue.Queue[tuple | None]" = queue.Queue(maxsize=50)
        self._thread = threading.Thread(target=self._run, name="telegram", daemon=True)

    # ---------- vòng đời ----------

    def start(self) -> None:
        self._thread.start()

    def stop(self, flush_timeout: float = 8.0) -> None:
        """Chờ gửi nốt các tin còn trong hàng đợi (tối đa flush_timeout giây) rồi đóng."""
        try:
            self._q.put(None, timeout=1)
        except queue.Full:
            pass
        if self._thread.is_alive():
            self._thread.join(flush_timeout)
        self._session.close()

    # ---------- API công khai (không chặn) ----------

    def send_text(self, text: str) -> None:
        self._enqueue(("text", text[:MAX_TEXT], None))

    def send_photo(self, frame_bgr: np.ndarray, caption: str) -> None:
        try:
            jpg = encode_jpeg(frame_bgr)
        except ValueError:
            log.exception("Không mã hoá được ảnh, gửi chữ thay thế")
            self.send_text(caption)
            return
        self._enqueue(("photo", caption[:MAX_CAPTION], jpg))

    # ---------- gửi đồng bộ (nút "Gửi thử") ----------

    def send_text_blocking(self, text: str) -> str | None:
        """Trả về None nếu OK, hoặc chuỗi mô tả lỗi."""
        try:
            self._call("sendMessage", {"chat_id": self._chat_id, "text": text})
        except TelegramError as exc:
            return str(exc)
        finally:
            self._session.close()
        return None

    # ---------- nội bộ ----------

    def _enqueue(self, item: tuple) -> None:
        try:
            self._q.put_nowait(item)
        except queue.Full:
            try:
                self._q.get_nowait()  # bỏ tin cũ nhất nếu mạng tắc quá lâu
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(item)
            except queue.Full:
                pass
            log.warning("Hàng đợi Telegram đầy – đã bỏ tin cũ nhất")

    def _scrub(self, text: object) -> str:
        """URL API chứa bot token → che đi trước khi ghi log."""
        s = str(text)
        return s.replace(self._token, "<token>") if self._token else s

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            kind, text, jpg = item
            try:
                if kind == "photo":
                    try:
                        self._call(
                            "sendPhoto",
                            {"chat_id": self._chat_id, "caption": text},
                            files={"photo": ("screen.jpg", jpg, "image/jpeg")},
                        )
                    except TelegramError as exc:
                        log.error("Gửi ảnh Telegram thất bại (%s) → gửi bản chữ", exc)
                        self._call("sendMessage", {"chat_id": self._chat_id, "text": text})
                else:
                    self._call("sendMessage", {"chat_id": self._chat_id, "text": text})
            except TelegramError as exc:
                log.error("Gửi Telegram thất bại, bỏ qua tin này: %s", exc)
            except Exception:  # noqa: BLE001 - luồng nền không được chết
                log.exception("Lỗi không mong đợi khi gửi Telegram")

    def _call(self, method: str, data: dict, files: dict | None = None, attempts: int = 3) -> None:
        if not self._token or not self._chat_id:
            raise TelegramError("Thiếu Bot Token hoặc Chat ID")
        url = f"{self.API}/bot{self._token}/{method}"
        last = "không rõ"
        for attempt in range(1, attempts + 1):
            wait = 2 ** attempt
            try:
                resp = self._session.post(url, data=data, files=files, timeout=(5, 30))
            except requests.RequestException as exc:
                last = self._scrub(exc)
            else:
                if resp.status_code == 200:
                    return
                last = f"HTTP {resp.status_code}: {self._scrub(resp.text[:200])}"
                if resp.status_code == 429:
                    wait = min(self._retry_after(resp), 30)
                elif 400 <= resp.status_code < 500:
                    break  # sai token / chat id / nội dung: thử lại không giúp ích
            if attempt < attempts:
                time.sleep(wait)
        raise TelegramError(last)

    @staticmethod
    def _retry_after(resp: requests.Response) -> float:
        try:
            return float(resp.json()["parameters"]["retry_after"])
        except (ValueError, KeyError, TypeError):
            return 5.0
