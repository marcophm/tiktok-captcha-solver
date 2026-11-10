"""Client gọi API SadCaptcha (puzzle). Chịu lỗi mạng: timeout, thử lại có backoff, che API key trong log."""
from __future__ import annotations

import logging
import random
import time

import requests

log = logging.getLogger("sadcaptcha")


class SadCaptchaError(Exception):
    pass


class SadCaptchaClient:
    BASE_URL = "https://www.sadcaptcha.com/api/v1"

    def __init__(self, api_key: str, timeout: tuple[float, float] = (5, 25), retries: int = 3) -> None:
        self._key = api_key.strip()
        self._timeout = timeout
        self._retries = max(1, retries)
        self._session = requests.Session()

    def close(self) -> None:
        self._session.close()

    def _scrub(self, text: object) -> str:
        """URL có chứa licenseKey → luôn che trước khi ghi log / gửi Telegram."""
        s = str(text)
        return s.replace(self._key, "***") if self._key else s

    def solve_puzzle(self, puzzle_b64: str, piece_b64: str) -> float:
        """Trả về slideXProportion (0..1): tỉ lệ quãng cần kéo so với chiều rộng ảnh puzzle."""
        if not self._key:
            raise SadCaptchaError("Chưa nhập SadCaptcha API Key")
        payload = {"puzzleImageB64": puzzle_b64, "pieceImageB64": piece_b64}
        url = f"{self.BASE_URL}/puzzle"
        last_error = "không rõ"

        for attempt in range(1, self._retries + 1):
            try:
                resp = self._session.post(
                    url, params={"licenseKey": self._key}, json=payload, timeout=self._timeout
                )
            except requests.RequestException as exc:
                last_error = self._scrub(exc)
                log.warning("Gọi SadCaptcha lỗi mạng (lần %d/%d): %s", attempt, self._retries, last_error)
            else:
                if resp.status_code == 200:
                    return self._parse(resp)
                snippet = self._scrub(resp.text[:200]).strip()
                last_error = f"HTTP {resp.status_code} {snippet}"
                # 4xx (sai key, hết credit, ảnh sai...) thử lại cũng vô ích — trừ 429
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    raise SadCaptchaError(f"SadCaptcha từ chối yêu cầu: {last_error}")
                log.warning("SadCaptcha trả lỗi (lần %d/%d): %s", attempt, self._retries, last_error)

            if attempt < self._retries:
                time.sleep(min(2 ** attempt, 8) + random.uniform(0, 0.5))

        raise SadCaptchaError(f"Không gọi được SadCaptcha sau {self._retries} lần: {last_error}")

    def _parse(self, resp: requests.Response) -> float:
        try:
            value = float(resp.json()["slideXProportion"])
        except (ValueError, KeyError, TypeError) as exc:
            raise SadCaptchaError(
                f"Phản hồi SadCaptcha không hợp lệ: {self._scrub(resp.text[:200])}"
            ) from exc
        if not 0.0 <= value <= 1.0:
            raise SadCaptchaError(f"slideXProportion ngoài khoảng 0..1: {value}")
        return value
