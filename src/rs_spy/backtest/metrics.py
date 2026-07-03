"""Primary backtest metrics. algo-spec/08-backtesting-and-validation.md §2."""
import pandas as pd


def compute_metrics(trades: pd.DataFrame, equity_curve: pd.Series, trading_days: int) -> dict:
    if trades.empty:
        return {
            "n_trades": 0,
            "win_rate": None,
            "profit_factor": None,
            "avg_win": None,
            "avg_loss": None,
            "avg_win_loss_ratio": None,
            "max_drawdown_pct": _max_drawdown_pct(equity_curve),
            "trades_per_day": 0.0,
            "total_pnl": 0.0,
        }

    wins = trades[trades["pnl"] > 0]
    losses = trades[trades["pnl"] <= 0]
    gross_win = wins["pnl"].sum()
    gross_loss = -losses["pnl"].sum()

    win_rate = len(wins) / len(trades)
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    avg_win = wins["pnl"].mean() if not wins.empty else 0.0
    avg_loss = losses["pnl"].mean() if not losses.empty else 0.0
    avg_win_loss_ratio = (avg_win / abs(avg_loss)) if avg_loss != 0 else float("inf")

    return {
        "n_trades": len(trades),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_win_loss_ratio": avg_win_loss_ratio,
        "max_drawdown_pct": _max_drawdown_pct(equity_curve),
        "trades_per_day": len(trades) / trading_days if trading_days else 0.0,
        "total_pnl": trades["pnl"].sum(),
    }


def _max_drawdown_pct(equity_curve: pd.Series) -> float | None:
    if equity_curve is None or equity_curve.empty:
        return None
    running_max = equity_curve.cummax()
    drawdown = (equity_curve - running_max) / running_max
    return float(drawdown.min())


def metrics_by_direction(trades: pd.DataFrame, equity_start: float) -> dict[str, dict]:
    out = {}
    for direction, group in trades.groupby("direction"):
        wins = group[group["pnl"] > 0]
        losses = group[group["pnl"] <= 0]
        gross_win = wins["pnl"].sum()
        gross_loss = -losses["pnl"].sum()
        out[direction] = {
            "n_trades": len(group),
            "win_rate": len(wins) / len(group) if len(group) else None,
            "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
            "total_pnl": group["pnl"].sum(),
        }
    return out
