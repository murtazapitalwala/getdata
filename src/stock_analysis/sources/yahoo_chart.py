from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

from ..http import HttpClient

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _value_at(seq: list[Any], idx: int) -> Any:
    if idx < 0 or idx >= len(seq):
        return None
    return seq[idx]


class YahooChart:
    def __init__(self, http: Optional[HttpClient] = None) -> None:
        self._http = http or HttpClient()

    @staticmethod
    def _pick_range(fromdate: date, todate: date) -> str:
        days = (todate - fromdate).days + 1
        if days <= 7:
            return "7d"
        if days <= 31:
            return "1mo"
        if days <= 93:
            return "3mo"
        if days <= 186:
            return "6mo"
        if days <= 370:
            return "1y"
        if days <= 740:
            return "2y"
        # 60m historical windows beyond this become unstable/unavailable on free endpoints.
        raise ValueError("timeframe=1h supports up to 2 years on Yahoo free chart endpoint")

    def get_hourly_prices(
        self,
        ticker: str,
        *,
        fromdate: date,
        todate: date,
    ) -> tuple[dict[str, Any], str]:
        if fromdate > todate:
            raise ValueError("fromdate must be on or before todate")
        range_arg = self._pick_range(fromdate, todate)

        url = YAHOO_CHART_URL.format(ticker=ticker.upper())
        data, meta = self._http.get_json(
            url,
            params={
                "interval": "60m",
                "range": range_arg,
                "includePrePost": "false",
                "events": "div,splits",
            },
        )

        chart = data.get("chart") or {}
        result = chart.get("result") or []
        if not result:
            err = chart.get("error")
            raise RuntimeError(f"Yahoo chart returned no result for {ticker}: {err}")

        payload = result[0]
        timestamps = payload.get("timestamp") or []
        quote = ((payload.get("indicators") or {}).get("quote") or [{}])[0]
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        prices: list[dict[str, Any]] = []
        for i, ts in enumerate(timestamps):
            dt_utc = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            d = dt_utc.date()
            if d < fromdate or d > todate:
                continue

            o = _to_float(_value_at(opens, i))
            h = _to_float(_value_at(highs, i))
            l = _to_float(_value_at(lows, i))
            c = _to_float(_value_at(closes, i))
            v = _value_at(volumes, i)
            vol = int(v) if isinstance(v, (int, float)) and v is not None else 0

            if o is None and h is None and l is None and c is None:
                continue

            prices.append(
                {
                    "date": dt_utc.isoformat().replace("+00:00", "Z"),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": vol,
                }
            )

        prices.sort(key=lambda x: x["date"])
        if not prices:
            raise RuntimeError(f"No hourly rows found for {ticker} in range")

        return {
            "ticker": ticker.upper(),
            "from_date": fromdate.isoformat(),
            "to_date": todate.isoformat(),
            "prices": prices,
            "count": len(prices),
        }, meta.url
