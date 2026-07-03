"""Time-of-day-adjusted Relative Volume (RVOL). algo-spec/02 §4.

Compares each bar's session-cumulative volume to the mean cumulative volume
at the *same ET wall-clock time* across the prior `lookback_sessions`
sessions -- so 10:00am volume is judged against other days' 10:00am volume,
not a full-day average. Causal by construction: a session's own bars never
contribute to its own baseline (the rolling mean is shifted back one
session), and within a session, cumulative volume at bar t only uses bars
0..t.

Keyed by actual ET time-of-day (not arrival order / cumcount): Alpaca's
IEX-only minute feed frequently has gaps for less-liquid names (a bar
exists only if a trade happened on IEX that minute -- confirmed against
real cached data, e.g. one curated-universe symbol has only ~20% of the
bars a fully-populated session would have). Arrival-order indexing would
silently misalign "bar N of the session" across sessions with different
gap patterns; keying by wall-clock time avoids that at the cost of the
rolling baseline itself being NaN whenever any of the trailing
`lookback_sessions` sessions lacks a bar at that exact minute -- expected
to be common for illiquid names/times, and preferred over a
silently-misaligned value.

**Callers must pre-filter `df` to RTH-only bars** via
`data.session.filter_rth()`, same requirement as `vwap.vwap()` -- otherwise
pre/post-market volume would be folded into "session" cumulative volume and
the time-of-day baseline.
"""
import pandas as pd


def rvol(df: pd.DataFrame, lookback_sessions: int = 20) -> pd.Series:
    session = df.index.normalize()
    et = df.index.tz_localize("UTC").tz_convert("America/New_York") if df.index.tz is None else df.index.tz_convert(
        "America/New_York"
    )
    time_of_day = (et - et.normalize()).total_seconds().astype(int) // 60  # minutes since ET midnight

    cum_vol = df["volume"].groupby(session).cumsum()

    wide = pd.DataFrame({"session": session.to_numpy(), "time_of_day": time_of_day, "cum_vol": cum_vol.to_numpy()})
    pivot = wide.pivot(index="session", columns="time_of_day", values="cum_vol")

    expected = pivot.rolling(lookback_sessions, min_periods=lookback_sessions).mean().shift(1)
    expected_flat = expected.stack()
    expected_flat.index.names = ["session", "time_of_day"]

    key = pd.MultiIndex.from_arrays([session.to_numpy(), time_of_day], names=["session", "time_of_day"])
    expected_cum_vol = pd.Series(expected_flat.reindex(key).to_numpy(), index=df.index)

    return (cum_vol / expected_cum_vol).rename("rvol")
