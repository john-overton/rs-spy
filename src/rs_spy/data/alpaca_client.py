"""Thin wrapper over alpaca-py's StockHistoricalDataClient.

All vendor-specific types/calls are isolated here (and in schemas.py) so swapping
data vendors later only touches this module.
"""
import logging
import time
from datetime import datetime

import pandas as pd
from alpaca.common.exceptions import APIError
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from rs_spy.config import Settings
from rs_spy.data.rate_limiter import SlidingWindowLimiter
from rs_spy.data.schemas import Timespan

logger = logging.getLogger(__name__)

_MAX_RETRIES = 5
_BACKOFF_BASE_S = 5.0
_BACKOFF_CAP_S = 60.0

_TIMEFRAME: dict[Timespan, TimeFrame] = {
    "day": TimeFrame.Day,
    "minute": TimeFrame.Minute,
}

BAR_COLUMNS = [
    "symbol",
    "timespan",
    "ts",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "vwap",
    "trade_count",
]


class AlpacaClient:
    def __init__(self, settings: Settings):
        if not settings.alpaca_api_key_id or not settings.alpaca_api_secret_key:
            raise ValueError(
                "ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY are not set. "
                "Copy .env.example to .env and fill in your free Alpaca account keys."
            )
        self._client = StockHistoricalDataClient(
            api_key=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_api_secret_key,
        )
        self._limiter = SlidingWindowLimiter()

    def fetch_bars(
        self,
        symbols: list[str],
        timespan: Timespan,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Fetch bars for one or more symbols in a single request.

        Returns a DataFrame with columns BAR_COLUMNS, empty (but correctly
        shaped) if no bars are returned.
        """
        request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=_TIMEFRAME[timespan],
            start=start,
            end=end,
            adjustment=Adjustment.ALL,
            feed=DataFeed.IEX,
        )
        bar_set = self._request_with_retry(request)

        rows = []
        for symbol, bars in bar_set.data.items():
            for bar in bars:
                rows.append(
                    {
                        "symbol": symbol,
                        "timespan": timespan,
                        "ts": bar.timestamp,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                        "vwap": bar.vwap,
                        "trade_count": bar.trade_count,
                    }
                )
        return pd.DataFrame(rows, columns=BAR_COLUMNS)

    def _request_with_retry(self, request: StockBarsRequest):
        for attempt in range(1, _MAX_RETRIES + 1):
            self._limiter.acquire()
            try:
                return self._client.get_stock_bars(request)
            except APIError as exc:
                is_rate_limited = "429" in str(exc) or "rate limit" in str(exc).lower()
                if not is_rate_limited or attempt == _MAX_RETRIES:
                    raise
                backoff = min(_BACKOFF_BASE_S * (2 ** (attempt - 1)), _BACKOFF_CAP_S)
                logger.warning(
                    "rate limited (attempt %d/%d), backing off %.0fs", attempt, _MAX_RETRIES, backoff
                )
                time.sleep(backoff)
        raise RuntimeError("unreachable")  # loop always returns or raises
