"""One-shot sector enrichment for the M10 universe-500 top-up (build-time only).

Pulls `sector` per symbol from Yahoo Finance via yfinance and writes a committed
config/sectors_500.yaml. Runtime code never imports yfinance -- it lives in the
`universe` extras group (pip install -e ".[universe]") and is imported lazily
inside main() only, so the hermetic tests can import this module without it.

    python scripts/enrich_sectors.py --symbols-file /tmp/topup.txt
    python scripts/enrich_sectors.py HOOD SOFI PLTR

Unresolved symbols are omitted from the YAML (consumers default them to
UNKNOWN) and printed so the operator can hand-patch the file if worthwhile.
"""
import logging
from datetime import date
from pathlib import Path

import typer
import yaml

app = typer.Typer()

OUT_PATH = Path(__file__).resolve().parents[1] / "config" / "sectors_500.yaml"


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


def _yfinance_fetch(sym: str) -> str | None:
    import yfinance  # lazy: build-time dependency only (see module docstring)

    info = yfinance.Ticker(sym).info or {}
    return info.get("sector")


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

    sectors, unresolved = collect_sectors(symbols, _yfinance_fetch)
    doc = sectors_yaml_doc(
        sectors, source_note=f"yfinance {date.today().isoformat()} (one-shot, see M10 spec)"
    )
    OUT_PATH.write_text(yaml.safe_dump(doc, sort_keys=False))
    typer.echo(f"{len(sectors)} resolved -> {OUT_PATH}; {len(unresolved)} unresolved: {unresolved}")


if __name__ == "__main__":
    app()
