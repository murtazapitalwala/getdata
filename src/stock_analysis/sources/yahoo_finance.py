from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import json
import re

from ..dates import epoch_to_exchange_date, parse_ymd, ymd_range_epoch_utc
from ..http import FetchResult, HttpClient


YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
YAHOO_CHART_URL_ALT = "https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
YAHOO_OPTIONS_URL = "https://query2.finance.yahoo.com/v7/finance/options/{ticker}"
YAHOO_CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
YAHOO_CRUMB_URL_ALT = "https://query2.finance.yahoo.com/v1/test/getcrumb"
YAHOO_QUOTE_PAGE = "https://finance.yahoo.com/quote/{ticker}"


@dataclass(frozen=True)
class CloseQuote:
    ticker: str
    trading_date: date
    close: float
    currency: Optional[str]
    exchange: Optional[str]
    urls: List[str]


@dataclass(frozen=True)
class PutPremium:
    ticker: str
    expiry: date
    strike: float
    bid: Optional[float]
    ask: Optional[float]
    last: Optional[float]
    mid: Optional[float]
    currency: Optional[str]
    contract_symbol: Optional[str]
    urls: List[str]


def _float_eq(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


class YahooFinance:
    def __init__(self, http: Optional[HttpClient] = None) -> None:
        self._http = http or HttpClient()
        self._crumb: Optional[str] = None

    def _ensure_crumb(self, ticker_for_cookies: str) -> str:
        if self._crumb:
            return self._crumb

        # Prime cookies by visiting an HTML page on finance.yahoo.com.
        # This is required for some endpoints (notably options) which validate a "crumb" token.
        html, _ = self._http.get_text(YAHOO_QUOTE_PAGE.format(ticker=ticker_for_cookies))

        # Preferred: extract crumb from the HTML to avoid hitting getcrumb (often rate-limited).
        marker = '"CrumbStore":{"crumb":"'
        idx = html.find(marker)
        if idx != -1:
            start = idx + len(marker)
            end = html.find('"', start)
            if end != -1:
                raw = html[start:end]
                try:
                    crumb = json.loads('"' + raw + '"')
                except Exception:  # noqa: BLE001
                    crumb = raw
                crumb = (crumb or "").strip()
                if crumb and "<" not in crumb:
                    self._crumb = crumb
                    return crumb

        # Fallback: call getcrumb.
        last_err: Optional[Exception] = None
        for crumb_url in (YAHOO_CRUMB_URL, YAHOO_CRUMB_URL_ALT):
            for attempt in range(1, 6):
                try:
                    crumb_text, _ = self._http.get_text(crumb_url)
                    crumb = (crumb_text or "").strip()
                    if crumb and "<" not in crumb:
                        self._crumb = crumb
                        return crumb
                    snippet = re.sub(r"\s+", " ", (crumb_text or ""))[:120]
                    raise RuntimeError(f"Unexpected crumb payload (got: {snippet!r})")
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    # If we're being throttled, wait longer.
                    msg = str(e)
                    if "HTTP 429" in msg or "Too Many Requests" in msg:
                        import time

                        time.sleep(1.5 * attempt)
                        continue
                    break

        raise RuntimeError(f"Failed to obtain Yahoo crumb token: {last_err}")



    def get_close_on_date(self, ticker: str, trading_date: date) -> CloseQuote:
        period1, period2 = ymd_range_epoch_utc(trading_date)
        params = {
            "period1": period1,
            "period2": period2,
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        }

        last_err: Optional[Exception] = None
        for url_tmpl in (YAHOO_CHART_URL, YAHOO_CHART_URL_ALT):
            url = url_tmpl.format(ticker=ticker)
            for attempt in range(1, 4):
                try:
                    data, meta = self._http.get_json(url, params=params)
                    break
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    msg = str(e)
                    if "HTTP 429" in msg or "Too Many Requests" in msg:
                        import time

                        time.sleep(1.2 * attempt)
                        continue
                    raise
            else:
                continue
            # succeeded
            break
        else:
            raise RuntimeError(f"Yahoo chart fetch failed: {last_err}")

        chart = (data.get("chart") or {})
        if chart.get("error"):
            raise RuntimeError(f"Yahoo chart error: {chart['error']}")

        result = (chart.get("result") or [None])[0] or {}
        timestamps: List[int] = result.get("timestamp") or []
        indicators = (result.get("indicators") or {})
        quote = (indicators.get("quote") or [None])[0] or {}
        closes: List[Optional[float]] = quote.get("close") or []
        meta_obj = result.get("meta") or {}
        exchange_tz = meta_obj.get("exchangeTimezoneName")
        currency = meta_obj.get("currency")
        exchange = meta_obj.get("exchangeName")

        by_date: Dict[date, float] = {}
        for ts, c in zip(timestamps, closes):
            if c is None:
                continue
            d = epoch_to_exchange_date(ts, exchange_tz)
            by_date[d] = float(c)

        if trading_date not in by_date:
            available = ", ".join(sorted({d.isoformat() for d in by_date.keys()}))
            raise RuntimeError(
                f"No close for {ticker} on {trading_date.isoformat()} via Yahoo. "
                f"Available in window: {available}"
            )

        return CloseQuote(
            ticker=ticker,
            trading_date=trading_date,
            close=by_date[trading_date],
            currency=currency,
            exchange=exchange,
            urls=[meta.url],
        )

    def _get_expiration_epoch(self, ticker: str, expiry: date) -> Tuple[int, List[str]]:
        # First call without date to discover available expirations.
        url = YAHOO_OPTIONS_URL.format(ticker=ticker)
        crumb = self._ensure_crumb(ticker)
        data, meta = self._http.get_json(url, params={"crumb": crumb})
        chain = (((data.get("optionChain") or {}).get("result") or [None])[0]) or {}
        exp_epochs: List[int] = chain.get("expirationDates") or []

        if not exp_epochs:
            raise RuntimeError(f"No expirationDates returned for {ticker} from Yahoo")

        # Yahoo epochs are seconds since epoch (UTC). Match by UTC date.
        matches = [e for e in exp_epochs if date.fromtimestamp(e) == expiry]
        if matches:
            return matches[0], [meta.url]

        # If exact date isn't found, pick the nearest as a helpful fallback.
        nearest = min(exp_epochs, key=lambda e: abs((date.fromtimestamp(e) - expiry).days))
        return nearest, [meta.url]

    def get_put_premium(self, ticker: str, expiry: date, strike: float) -> PutPremium:
        exp_epoch, urls = self._get_expiration_epoch(ticker, expiry)

        crumb = self._ensure_crumb(ticker)

        url = YAHOO_OPTIONS_URL.format(ticker=ticker)
        data, meta = self._http.get_json(url, params={"date": exp_epoch, "crumb": crumb})
        urls = urls + [meta.url]

        chain = (((data.get("optionChain") or {}).get("result") or [None])[0]) or {}
        quote = chain.get("quote") or {}
        currency = quote.get("currency")

        options = (chain.get("options") or [None])[0] or {}
        puts: List[Dict[str, Any]] = options.get("puts") or []

        if not puts:
            raise RuntimeError(f"No puts returned for {ticker} {expiry} from Yahoo")

        chosen: Optional[Dict[str, Any]] = None
        for p in puts:
            p_strike = p.get("strike")
            if p_strike is None:
                continue
            try:
                p_strike_f = float(p_strike)
            except (TypeError, ValueError):
                continue
            if _float_eq(p_strike_f, float(strike), tol=1e-3):
                chosen = p
                break

        if chosen is None:
            strikes = sorted({float(p.get("strike")) for p in puts if p.get("strike") is not None})
            raise RuntimeError(
                f"No put with strike {strike} found for {ticker} {expiry} via Yahoo. "
                f"Example strikes: {strikes[:15]}"
            )

        bid = chosen.get("bid")
        ask = chosen.get("ask")
        last = chosen.get("lastPrice")
        contract_symbol = chosen.get("contractSymbol")

        bid_f = float(bid) if bid is not None else None
        ask_f = float(ask) if ask is not None else None
        last_f = float(last) if last is not None else None

        mid: Optional[float] = None
        if bid_f is not None and ask_f is not None and bid_f > 0 and ask_f > 0:
            mid = (bid_f + ask_f) / 2.0
        elif last_f is not None:
            mid = last_f

        return PutPremium(
            ticker=ticker,
            expiry=expiry,
            strike=float(strike),
            bid=bid_f,
            ask=ask_f,
            last=last_f,
            mid=mid,
            currency=currency,
            contract_symbol=contract_symbol,
            urls=urls,
        )
