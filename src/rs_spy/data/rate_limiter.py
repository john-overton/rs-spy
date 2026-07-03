"""Lightweight sliding-window rate limiter.

Alpaca's free tier is 200 API calls/minute (confirmed against the actual
account, not the plan's original "10k calls/min" assumption -- that number
was wrong). Non-binding for daily-bar backfills (a handful of calls total),
but load-bearing for minute-bar backfills (data/ingest.py's `backfill()`
chunks and batches specifically to keep each call within Alpaca's ~10k-row
single-page response limit, so this limiter's "1 acquire = 1 real HTTP
request" assumption holds and the 200/min ceiling is actually respected).
"""
import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, max_calls: int = 200, window_s: float = 60.0):
        self.max_calls = max_calls
        self.window_s = window_s
        self._calls: deque[float] = deque()

    def acquire(self) -> None:
        now = time.monotonic()
        while self._calls and self._calls[0] <= now - self.window_s:
            self._calls.popleft()
        if len(self._calls) >= self.max_calls:
            sleep_for = self._calls[0] + self.window_s - now
            if sleep_for > 0:
                time.sleep(sleep_for)
        self._calls.append(time.monotonic())
