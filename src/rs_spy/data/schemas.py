from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Timespan = Literal["day", "minute"]


class AggBar(BaseModel):
    symbol: str
    timespan: Timespan
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float | None = None
    trade_count: int | None = None


class FetchTask(BaseModel):
    symbol: str
    timespan: Timespan
    year: int
    start: datetime
    end: datetime
