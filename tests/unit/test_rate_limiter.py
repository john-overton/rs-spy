import time

from rs_spy.data.rate_limiter import SlidingWindowLimiter


def test_acquire_does_not_block_under_limit():
    limiter = SlidingWindowLimiter(max_calls=5, window_s=60.0)
    start = time.monotonic()
    for _ in range(5):
        limiter.acquire()
    assert time.monotonic() - start < 1.0


def test_acquire_blocks_once_limit_exceeded():
    limiter = SlidingWindowLimiter(max_calls=2, window_s=0.2)
    start = time.monotonic()
    limiter.acquire()
    limiter.acquire()
    limiter.acquire()  # should sleep ~0.2s for the window to clear
    elapsed = time.monotonic() - start
    assert elapsed >= 0.15
