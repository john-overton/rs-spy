import pandas as pd
import typer.testing

import scripts.run_validation_studies as script


def _m1_session(date, n_minutes, start_price, drift, seed):
    import numpy as np
    rng = np.random.default_rng(seed)
    idx = pd.date_range(f"{date} 09:30", periods=n_minutes, freq="1min", tz="America/New_York").tz_convert("UTC")
    noise = rng.normal(0, 0.05, n_minutes)
    close = start_price + np.cumsum(np.full(n_minutes, drift) + noise)
    high = close + abs(rng.normal(0.05, 0.02, n_minutes))
    low = close - abs(rng.normal(0.05, 0.02, n_minutes))
    open_ = close - drift - noise
    volume = rng.integers(500, 1500, n_minutes).astype(float)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)


def test_main_runs_end_to_end_and_writes_expected_reports(tmp_path, monkeypatch):
    from rs_spy.data.resample import resample_ohlcv

    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-02-02", periods=15)]
    spy_m1 = pd.concat([_m1_session(d, 390, 100 + i, 0.0005, 1 + i) for i, d in enumerate(dates)])
    qqq_m1 = pd.concat([_m1_session(d, 390, 200 + i, 0.0006, 20 + i) for i, d in enumerate(dates)])
    aapl_m1 = pd.concat([_m1_session(d, 390, 150 + i, 0.0008, 40 + i) for i, d in enumerate(dates)])
    spy_m5, qqq_m5, aapl_m5 = resample_ohlcv(spy_m1, "5min"), resample_ohlcv(qqq_m1, "5min"), resample_ohlcv(aapl_m1, "5min")

    def _d1(m1):
        d = m1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
        d.index = d.index.tz_localize(None)
        return d

    all_m1 = {"SPY": spy_m1, "QQQ": qqq_m1, "AAPL": aapl_m1}
    all_m5 = {"SPY": spy_m5, "QQQ": qqq_m5, "AAPL": aapl_m5}
    all_d1 = {"SPY": _d1(spy_m1), "QQQ": _d1(qqq_m1), "AAPL": _d1(aapl_m1)}

    class FakeUniverseEntry:
        def __init__(self, symbol, sector):
            self.symbol, self.sector = symbol, sector

    class FakeUniverse:
        primary_benchmark, secondary_benchmark = "SPY", "QQQ"
        trade_symbols = ["AAPL"]
        universe = [FakeUniverseEntry("AAPL", "Technology")]

    monkeypatch.setattr(script, "load_universe", lambda *_: FakeUniverse())
    monkeypatch.setattr(script, "load_earnings_blackout", lambda *_: {})
    monkeypatch.setattr(script, "connect", lambda *_, **__: object())
    monkeypatch.setattr(script, "load_universe_m1_bars", lambda con, syms: all_m1)
    monkeypatch.setattr(script, "load_universe_m5_bars", lambda con, syms: all_m5)
    monkeypatch.setattr(script, "load_universe_daily_bars", lambda con, syms: all_d1)

    settings = script.get_settings()
    monkeypatch.setattr(settings, "reports_dir", tmp_path)
    monkeypatch.setattr(script, "get_settings", lambda: settings)

    runner = typer.testing.CliRunner()
    result = runner.invoke(script.app, [])
    assert result.exit_code == 0, result.output

    out_dir = tmp_path / "m7_studies"
    for name in (
        "baseline_trades.csv", "ablation_trades.csv", "ablation_summary_long.csv",
        "ablation_summary_short.csv", "walk_away_signals.csv", "rrs_sensitivity.csv",
        "bias_confusion.csv", "time_of_day_regime.csv",
    ):
        assert (out_dir / name).exists(), f"missing {name}"
