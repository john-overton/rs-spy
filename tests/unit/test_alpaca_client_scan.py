"""fetch_assets / fetch_screener_snapshots -- vendor-response normalization.

Hermetic: the underlying alpaca-py clients are replaced with stubs; only the
row-shaping logic in our wrapper is under test.
"""
from types import SimpleNamespace

from alpaca.common.exceptions import APIError

from rs_spy.config import Settings
from rs_spy.data.alpaca_client import ASSET_COLUMNS, AlpacaClient


def _client() -> AlpacaClient:
    return AlpacaClient(Settings(alpaca_api_key_id="k", alpaca_api_secret_key="s"))


def _asset(symbol, name, exchange, tradable=True, attributes=None):
    return SimpleNamespace(
        symbol=symbol, name=name, exchange=SimpleNamespace(value=exchange),
        tradable=tradable, shortable=True, fractionable=True, attributes=attributes,
    )


def test_fetch_assets_normalizes_vendor_objects_to_a_dataframe():
    client = _client()
    client._trading_client = SimpleNamespace(
        get_all_assets=lambda request: [
            _asset("AAPL", "Apple Inc. Common Stock", "NASDAQ", attributes=["has_options"]),
            _asset("XYZ", "Xyz Corp", "NYSE", attributes=None),
            _asset("NOPE", "NoTrade Inc", "NYSE", tradable=False, attributes=[]),
        ]
    )
    df = client.fetch_assets()
    assert list(df.columns) == ASSET_COLUMNS
    assert df.loc[df.symbol == "AAPL", "optionable"].item() is True
    assert df.loc[df.symbol == "XYZ", "optionable"].item() is False  # attributes=None tolerated
    assert df.loc[df.symbol == "AAPL", "exchange"].item() == "NASDAQ"  # enum -> plain string
    assert df.loc[df.symbol == "NOPE", "tradable"].item() is False


def test_fetch_screener_snapshots_returns_three_json_safe_payloads():
    client = _client()
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def model_dump(self, mode="json"):
            return self._payload

    def fake_most_actives(request):
        calls.append(("most_actives", request.by, request.top))
        return FakeResponse({"most_actives": [{"symbol": "HOOD", "volume": 1e8, "trade_count": 9e5}]})

    def fake_movers(request):
        calls.append(("movers", request.top))
        return FakeResponse({"gainers": [], "losers": [], "market_type": "stocks"})

    client._screener_client = SimpleNamespace(
        get_most_actives=fake_most_actives, get_market_movers=fake_movers
    )
    out = client.fetch_screener_snapshots(top_actives=100, top_movers=50)
    assert set(out) == {"most_actives_volume", "most_actives_trades", "market_movers"}
    assert out["most_actives_volume"]["most_actives"][0]["symbol"] == "HOOD"
    # one most-actives call per ranking metric, with the requested tops
    kinds = [c[0] for c in calls]
    assert kinds.count("most_actives") == 2 and kinds.count("movers") == 1


def test_fetch_assets_drops_duplicate_symbols():
    # a duplicate symbol would violate the (scan_date, symbol) PK in
    # save_scan's COPY -- keep the first occurrence, drop the rest.
    client = _client()
    client._trading_client = SimpleNamespace(
        get_all_assets=lambda request: [
            _asset("AAPL", "Apple Inc. Common Stock", "NASDAQ", attributes=["has_options"]),
            _asset("AAPL", "Apple Inc. Common Stock (dup listing)", "NASDAQ", attributes=None),
            _asset("XYZ", "Xyz Corp", "NYSE", attributes=None),
        ]
    )
    df = client.fetch_assets()
    assert list(df["symbol"]) == ["AAPL", "XYZ"]


def test_fetch_assets_retries_a_transient_rate_limit_then_succeeds(monkeypatch):
    monkeypatch.setattr("rs_spy.data.alpaca_client.time.sleep", lambda *_: None)
    client = _client()
    attempts = []

    def flaky_get_all_assets(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise APIError("429 Too Many Requests")
        return [_asset("AAPL", "Apple Inc. Common Stock", "NASDAQ", attributes=["has_options"])]

    client._trading_client = SimpleNamespace(get_all_assets=flaky_get_all_assets)
    df = client.fetch_assets()
    assert len(attempts) == 2  # one failure, one retry that succeeds
    assert list(df["symbol"]) == ["AAPL"]


def test_fetch_screener_snapshots_retries_a_transient_rate_limit_then_succeeds(monkeypatch):
    monkeypatch.setattr("rs_spy.data.alpaca_client.time.sleep", lambda *_: None)
    client = _client()
    attempts = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def model_dump(self, mode="json"):
            return self._payload

    def flaky_most_actives(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise APIError("429 Too Many Requests")
        return FakeResponse({"most_actives": [{"symbol": "HOOD", "volume": 1e8}]})

    client._screener_client = SimpleNamespace(
        get_most_actives=flaky_most_actives,
        get_market_movers=lambda request: FakeResponse({"gainers": [], "losers": []}),
    )
    out = client.fetch_screener_snapshots()
    # 1 failure + 1 retry-success for the VOLUME call, + 1 success for TRADES
    assert len(attempts) == 3
    assert out["most_actives_volume"]["most_actives"][0]["symbol"] == "HOOD"
