from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from ..http import HttpClient

logger = logging.getLogger(__name__)

_BASE_URL = "https://stockanalysis.com/stocks/{ticker}/forecast/"


def _to_float(v: str) -> Optional[float]:
    s = str(v).strip()
    if not s or s in {"null", "undefined", "--", "N/A"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_int(v: str) -> Optional[int]:
    f = _to_float(v)
    return int(f) if f is not None else None


class StockAnalysis:
    def __init__(self, http: Optional[HttpClient] = None) -> None:
        self._http = http or HttpClient()

    def get_analyst_ratings(self, ticker: str) -> Dict[str, Any]:
        """
        Scrape stockanalysis.com/stocks/{ticker}/forecast/ and return:
        - consensus rating
        - buy / hold / sell / strong_buy / strong_sell counts
        - price targets (low, high, average, median, count)
        - recent individual analyst ratings
        """
        url = _BASE_URL.format(ticker=ticker.lower())
        html, _meta = self._http.get_text(url)

        # ── Consensus + counts (most recent month) ──────────────────
        months = re.findall(
            r"\{buy:(\d+),date:\"([^\"]+)\",hold:(\d+),sell:(\d+),month:\"[^\"]+\","
            r"score:[^,]+,total:(\d+),updated:\"[^\"]+\",consensus:\"([^\"]+)\","
            r"strongBuy:(\d+),strongSell:(\d+)\}",
            html,
        )
        consensus: Optional[str] = None
        strong_buy = buy = hold = sell = strong_sell = total_analysts = None
        if months:
            latest = months[-1]
            buy = _to_int(latest[0])
            hold = _to_int(latest[2])
            sell = _to_int(latest[3])
            total_analysts = _to_int(latest[4])
            consensus = latest[5]
            strong_buy = _to_int(latest[6])
            strong_sell = _to_int(latest[7])

        # ── Price targets ───────────────────────────────────────────
        pt_low = pt_high = pt_average = pt_median = None
        pt_count = None
        m = re.search(
            r"targets:\{low:(\d+(?:\.\d+)?),high:(\d+(?:\.\d+)?),count:(\d+),"
            r"median:(\d+(?:\.\d+)?),average:(\d+(?:\.\d+)?)",
            html,
        )
        if m:
            pt_low = _to_float(m.group(1))
            pt_high = _to_float(m.group(2))
            pt_count = _to_int(m.group(3))
            pt_median = _to_float(m.group(4))
            pt_average = _to_float(m.group(5))

        # ── Individual recent ratings ───────────────────────────────
        raw_ratings = re.findall(
            r"\{action_rt:\"([^\"]*)\",pt_now:([^,]+),pt_old:([^,]+),"
            r"firm:\"([^\"]*)\",analyst:\"([^\"]*)\",slug:\"[^\"]*\","
            r"date:\"([^\"]*)\",rating_new:\"([^\"]*)\"",
            html,
        )
        recent_ratings: List[Dict[str, Any]] = []
        for r in raw_ratings:
            recent_ratings.append({
                "action": r[0],
                "price_target": _to_float(r[1]),
                "price_target_prior": _to_float(r[2]),
                "firm": r[3],
                "analyst": r[4],
                "date": r[5],
                "rating": r[6],
            })

        return {
            "ticker": ticker.upper(),
            "consensus": consensus,
            "strong_buy": strong_buy,
            "buy": buy,
            "hold": hold,
            "sell": sell,
            "strong_sell": strong_sell,
            "total_analysts": total_analysts,
            "price_target_low": pt_low,
            "price_target_high": pt_high,
            "price_target_average": pt_average,
            "price_target_median": pt_median,
            "price_target_count": pt_count,
            "recent_ratings": recent_ratings,
            "source": url,
        }
