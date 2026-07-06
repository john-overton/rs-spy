"""One-shot sector enrichment for the M10 universe-500 top-up (build-time only).

Vendor substitution (2026-07-05): the approved design pulled `sector` per symbol
from Yahoo Finance via yfinance, one request per symbol. Yahoo has since hard-
blocked the quoteSummary API (HTTP 401 "Invalid Crumb" / "User is unable to
access this feature" -- reproduced with yfinance 1.5.1, 0/372 symbols resolved).
This script now uses Nasdaq's public screener API instead: a single request
(`https://api.nasdaq.com/api/screener/stocks?limit=25000&download=true`) returns
`sector` for ~6,400 NYSE/NASDAQ/AMEX symbols (ADRs included), so the whole
universe is resolved in one shot rather than per-symbol. No extra dependency is
needed -- it's a plain stdlib `urllib` GET with a browser User-Agent.

Quirk: class-share symbols use slash notation in the Nasdaq data (`BRK/B`) while
Alpaca/our universe uses dots (`BRK.B`). Lookups try the symbol as-is, then fall
back to `sym.replace(".", "/")`. Empty-string sectors in the source data count
as unresolved.

Runtime code never imports the fetch path -- `_nasdaq_sector_map` is imported
lazily inside main() only, so the hermetic tests can import this module (and
exercise the pure `make_lookup`/`collect_sectors`/`sectors_yaml_doc` seams)
without making a network call.

    python scripts/enrich_sectors.py --symbols-file /tmp/topup.txt
    python scripts/enrich_sectors.py HOOD SOFI PLTR

Unresolved symbols are omitted from the YAML (consumers default them to
UNKNOWN) and printed so the operator can hand-patch the file if worthwhile.
"""
import json
import logging
import urllib.request
from datetime import date
from pathlib import Path

import typer
import yaml

app = typer.Typer()

OUT_PATH = Path(__file__).resolve().parents[1] / "config" / "sectors_500.yaml"
NASDAQ_SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks?limit=25000&download=true"


def collect_sectors(symbols: list[str], fetch_sector) -> tuple[dict[str, str], list[str]]:
    """Resolve sectors via the injected fetcher. Any exception or None/empty
    result marks the symbol unresolved -- enrichment is best-effort by design."""
    sectors: dict[str, str] = {}
    unresolved: list[str] = []
    for sym in symbols:
        try:
            sector = fetch_sector(sym)
        except Exception:  # noqa: BLE001 -- one flaky symbol must not kill the batch
            sector = None
        if sector:
            sectors[sym] = sector
        else:
            unresolved.append(sym)
    return sectors, unresolved


def sectors_yaml_doc(sectors: dict[str, str], *, source_note: str) -> dict:
    return {"_source": source_note, "sectors": dict(sorted(sectors.items()))}


def _nasdaq_sector_map() -> dict[str, str]:
    """One-shot fetch of {symbol: sector} for every symbol with a non-empty
    sector in Nasdaq's public screener API. See module docstring for why
    this replaced the per-symbol yfinance calls."""
    req = urllib.request.Request(
        NASDAQ_SCREENER_URL,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        payload = json.load(resp)
    rows = payload["data"]["rows"]
    return {row["symbol"]: row["sector"] for row in rows if row.get("sector")}


def make_lookup(sector_map: dict[str, str]):
    """Build a fetch_sector(sym) callable over a pre-fetched sector map,
    trying `sym` as-is then the dot->slash class-share variant (BRK.B ->
    BRK/B) that Nasdaq's data uses."""

    def fetch_sector(sym: str) -> str | None:
        if sym in sector_map:
            return sector_map[sym]
        return sector_map.get(sym.replace(".", "/"))

    return fetch_sector


@app.command()
def main(
    symbols: list[str] = typer.Argument(None),
    symbols_file: Path = typer.Option(None, help="newline-separated symbol list"),
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if symbols_file is not None:
        symbols = [s.strip() for s in symbols_file.read_text().splitlines() if s.strip()]
    if not symbols:
        raise typer.BadParameter("pass symbols or --symbols-file")

    sector_map = _nasdaq_sector_map()
    fetch_sector = make_lookup(sector_map)
    sectors, unresolved = collect_sectors(symbols, fetch_sector)
    doc = sectors_yaml_doc(
        sectors,
        source_note=(
            f"nasdaq-screener {date.today().isoformat()} "
            "(one-shot; yfinance blocked by Yahoo 401 -- see docstring)"
        ),
    )
    OUT_PATH.write_text(yaml.safe_dump(doc, sort_keys=False))
    typer.echo(f"{len(sectors)} resolved -> {OUT_PATH}; {len(unresolved)} unresolved: {unresolved}")


if __name__ == "__main__":
    app()
