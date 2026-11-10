"""Cấu hình logging: ghi file xoay vòng + đẩy vào queue để hiển thị lên giao diện."""
from __future__ import annotations

import logging
import queue
from logging.handlers import RotatingFileHandler

from config import LOG_PATH


class QueueLogHandler(logging.Handler):
    """Đưa dòng log vào queue (thread-safe) để Tkinter đọc ở luồng chính."""

    def __init__(self, q: "queue.Queue[str]") -> None:
        super().__init__()
        self._q = q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._q.put_nowait(self.format(record))
        except queue.Full:
            pass  # bỏ qua, không để log làm treo app


def setup_logging(ui_queue: "queue.Queue[str]") -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s", "%H:%M:%S")

    # Không dùng StreamHandler: khi build --windowed thì sys.stdout là None.
    try:
        fh = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass  # thư mục không ghi được → chỉ hiện log trên giao diện

    qh = QueueLogHandler(ui_queue)
    qh.setFormatter(fmt)
    root.addHandler(qh)

    # URL Telegram chứa token → không để thư viện ghi chi tiết request.
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    logging.getLogger("requests").setLevel(logging.ERROR)
