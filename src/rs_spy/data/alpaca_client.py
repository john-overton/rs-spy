"""Thin wrapper over alpaca-py's StockHistoricalDataClient.

All vendor-specific types/calls are isolated here (and in schemas.py) so swapping
data vendors later only touches this module.
"""
import logging
import time
from datetime import datetime

import pandas as pd
from alpaca.common.exceptions import APIError
from alpaca.data.enums import Adjustment, DataFeed, MostActivesBy
from alpaca.data.historical.screener import ScreenerClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import MarketMoversRequest, MostActivesRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

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

ASSET_COLUMNS = [
    "symbol",
    "name",
    "exchange",
    "tradable",
    "shortable",
    "fractionable",
    "optionable",
]

# Alpaca marks option availability as an entry in Asset.attributes; the exact
# label has drifted across API versions, so accept both known spellings.
_OPTION_ATTRIBUTES = {"options_enabled", "has_options"}


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
        self._trading_client = TradingClient(
            api_key=settings.alpaca_api_key_id,
            secret_key=settings.alpaca_api_secret_key,
            paper=True,
        )
        self._screener_client = ScreenerClient(
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
        bar_set = self._request_with_retry(self._client.get_stock_bars, request)

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

    def fetch_assets(self) -> pd.DataFrame:
        """All active US-equity assets, normalized to ASSET_COLUMNS.

        Alpaca has no security-type field (common stock vs ETF/ADR are all
        `us_equity`) and no shares float -- the scan's listing gate works from
        name/exchange heuristics instead (see scan/config.py).
        """
        assets = self._request_with_retry(
            self._trading_client.get_all_assets,
            GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY),
        )
        rows = []
        for a in assets:
            attributes = set(a.attributes or [])
            rows.append(
                {
                    "symbol": a.symbol,
                    "name": a.name or "",
                    "exchange": str(getattr(a.exchange, "value", a.exchange)),
                    "tradable": bool(a.tradable),
                    "shortable": bool(a.shortable),
                    "fractionable": bool(a.fractionable),
                    "optionable": bool(attributes & _OPTION_ATTRIBUTES),
                }
            )
        return pd.DataFrame(rows, columns=ASSET_COLUMNS)

    def fetch_screener_snapshots(
        self, top_actives: int = 100, top_movers: int = 50
    ) -> dict[str, dict]:
        """Live screener snapshots (most-actives by volume/trades, movers).

        These endpoints are REAL-TIME ONLY (no as-of parameter exists) -- every
        day not captured is lost forever, hence the nightly recorder. Payloads
        are raw model_dump(mode="json") dicts, stored verbatim as JSONB.
        """
        out: dict[str, dict] = {}
        out["most_actives_volume"] = self._request_with_retry(
            self._screener_client.get_most_actives,
            MostActivesRequest(by=MostActivesBy.VOLUME, top=top_actives),
        ).model_dump(mode="json")
        out["most_actives_trades"] = self._request_with_retry(
            self._screener_client.get_most_actives,
            MostActivesRequest(by=MostActivesBy.TRADES, top=top_actives),
        ).model_dump(mode="json")
        out["market_movers"] = self._request_with_retry(
            self._screener_client.get_market_movers,
            MarketMoversRequest(top=top_movers),
        ).model_dump(mode="json")
        return out

    def _request_with_retry(self, fn, *args, **kwargs):
        """Retry `fn(*args, **kwargs)` on a rate-limited APIError with
        exponential backoff. Shared by fetch_bars, fetch_assets, and
        fetch_screener_snapshots -- every outbound Alpaca call goes through
        this one retry policy."""
        for attempt in range(1, _MAX_RETRIES + 1):
            self._limiter.acquire()
            try:
                return fn(*args, **kwargs)
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
