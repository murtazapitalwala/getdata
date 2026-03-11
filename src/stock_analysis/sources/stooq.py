from __future__ import annotations

import csv
from datetime import date
from io import StringIO
from typing import Any, Dict, List, Optional

from ..http import HttpClient

STOOQ_DAILY_CSV_URL = "https://stooq.com/q/d/l/?s={symbol}.us&i=d"


def _to_float(v: Any) -> Optional[float]:
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_int(v: Any) -> Optional[int]:
    f = _to_float(v)
    return int(f) if f is not None else None


class Stooq:
    def __init__(self, http: Optional[HttpClient] = None) -> None:
        self._http = http or HttpClient()

    def get_historical_prices(
        self,
        ticker: str,
        *,
        fromdate: date,
        todate: date,
    ) -> tuple[dict[str, Any], str]:
        if fromdate > todate:
            raise ValueError("fromdate must be on or before todate")

        symbol = ticker.strip().lower()
        url = STOOQ_DAILY_CSV_URL.format(symbol=symbol)
        text, meta = self._http.get_text(url)

        rows: List[Dict[str, Any]] = list(csv.DictReader(StringIO(text)))
        if not rows:
            raise RuntimeError(f"No Stooq CSV rows returned for {ticker}")

        prices: list[dict[str, Any]] = []
        for row in rows:
            raw_date = (row.get("Date") or row.get("date") or "").strip()
            if not raw_date:
                continue
            try:
                d = date.fromisoformat(raw_date)
            except ValueError:
                continue

            if d < fromdate or d > todate:
                continue

            prices.append(
                {
                    "date": d.isoformat(),
                    "open": _to_float(row.get("Open") or row.get("open")),
                    "high": _to_float(row.get("High") or row.get("high")),
                    "low": _to_float(row.get("Low") or row.get("low")),
                    "close": _to_float(row.get("Close") or row.get("close")),
                    "volume": _to_int(row.get("Volume") or row.get("volume")),
                }
            )

        prices.sort(key=lambda x: x["date"])
        if not prices:
            raise RuntimeError(f"No Stooq historical rows found for {ticker} in range")

        return {
            "ticker": ticker.upper(),
            "from_date": fromdate.isoformat(),
            "to_date": todate.isoformat(),
            "prices": prices,
            "count": len(prices),
        }, meta.url
