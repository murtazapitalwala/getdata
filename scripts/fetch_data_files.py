"""Fetch and save historical price data files for all configured tickers.

Saves to:
  data/{ticker_lower}_dailydata.json   — 1 year of daily bars
  data/{ticker_lower}_weeklydata.json  — 2 years of weekly bars

Run from the workspace root:
  python scripts/fetch_data_files.py
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# Make the package importable whether installed editable or run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_analysis.engine import OptionEngine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DATA_DIR.mkdir(exist_ok=True)

# ── Tickers ────────────────────────────────────────────────────────────────
TICKERS: list[tuple[str, str]] = [
    ("SOXL", "etf"),
    ("TQQQ", "etf"),
    ("TNA",  "etf"),
    ("AMZU", "etf"),
    ("METU", "etf"),
    ("NFLU", "etf"),
    ("GGLL", "etf"),
    ("TSLL", "etf"),
    ("NFLX", "stocks"),
    ("TSLA", "stocks"),
    ("MSFT", "stocks"),
    ("GOOG", "stocks"),
    ("META", "stocks"),
    ("AMZN", "stocks"),
    ("AAPL", "stocks"),
    ("SOFI", "stocks"),
    ("HIMS", "stocks"),
    ("DIS",  "stocks"),
    ("PLTR", "stocks"),
    ("UUUU", "stocks"),
]

# (timeframe, lookback_days, file_suffix)
COMBOS: list[tuple[str, int, str]] = [
    ("1d", 365, "dailydata"),
    ("1w", 730, "weeklydata"),
]

# ── Engine ─────────────────────────────────────────────────────────────────
engine = OptionEngine()
today = date.today()


def fetch_and_save(ticker: str, asset_class: str, tf: str, days: int, suffix: str) -> bool:
    start = today - timedelta(days=days)
    out_file = DATA_DIR / f"{ticker.lower()}_{suffix}.json"
    logger.info("Fetching %-6s %-3s  %s → %s", ticker, tf, start, today)
    try:
        result = engine.get_historical_prices(
            ticker,
            from_date=start,
            to_date=today,
            timeframe=tf,
            asset_class=asset_class,
        )
        with out_file.open("w") as fh:
            json.dump(result, fh)
        count = result.get("count", len(result.get("prices") or []))
        source = result.get("source", "?")
        logger.info("  ✓ %-6s %-3s  %d bars  source=%-12s  → %s", ticker, tf, count, source, out_file.name)
        return True
    except Exception as exc:
        logger.error("  ✗ %-6s %-3s  FAILED: %s", ticker, tf, exc)
        return False


def main() -> None:
    total = len(TICKERS) * len(COMBOS)
    succeeded = 0
    failed = 0
    logger.info("═" * 60)
    logger.info("FETCH STARTED — %d files (%d tickers × %d timeframes)", total, len(TICKERS), len(COMBOS))
    logger.info("═" * 60)
    for ticker, asset_class in TICKERS:
        for tf, days, suffix in COMBOS:
            ok = fetch_and_save(ticker, asset_class, tf, days, suffix)
            if ok:
                succeeded += 1
            else:
                failed += 1
    logger.info("═" * 60)
    logger.info("FETCH FINISHED — %d succeeded, %d failed", succeeded, failed)
    logger.info("═" * 60)


if __name__ == "__main__":
    main()
