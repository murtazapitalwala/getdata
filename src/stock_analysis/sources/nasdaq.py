from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import re

from ..http import HttpClient


NASDAQ_OPTION_CHAIN_URL = "https://api.nasdaq.com/api/quote/{ticker}/option-chain"
NASDAQ_QUOTE_URL = "https://api.nasdaq.com/api/quote/{ticker}/info"


@dataclass(frozen=True)
class NasdaqPutPremium:
    ticker: str
    expiry: date
    strike: float
    bid: Optional[float]
    ask: Optional[float]
    last: Optional[float]
    mid: Optional[float]
    contract_symbol: Optional[str]
    urls: List[str]


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s in {"--", "N/A"}:
        return None
    # Nasdaq returns plain numbers as strings.
    try:
        return float(s)
    except ValueError:
        return None


class Nasdaq:
    def __init__(self, http: Optional[HttpClient] = None) -> None:
        self._http = http or HttpClient()

    def _fetch_option_chain(
        self,
        ticker: str,
        *,
        fromdate: date | None = None,
        todate: date | None = None,
    ) -> tuple[dict, str]:
        url = NASDAQ_OPTION_CHAIN_URL.format(ticker=ticker.lower())
        params: Dict[str, Any] = {"assetclass": "stocks"}
        if fromdate is not None:
            params["fromdate"] = fromdate.isoformat()
        if todate is not None:
            params["todate"] = todate.isoformat()

        data, meta = self._http.get_json(url, params=params)
        if not (data.get("data") and data["data"].get("table")):
            raise RuntimeError(f"Unexpected Nasdaq payload for {ticker}: {data.get('message') or data}")
        return data, meta.url

    def get_underlying_from_option_chain(self, ticker: str) -> tuple[float, str]:
        data, url = self._fetch_option_chain(ticker)
        last_trade = (data.get("data") or {}).get("lastTrade") or ""
        # Example: "LAST TRADE: $82.2 (AS OF FEB 6, 2026)"
        m = re.search(r"\$\s*(\d+(?:\.\d+)?)", str(last_trade))
        if not m:
            raise RuntimeError(f"Could not parse underlying lastTrade for {ticker}: {last_trade!r}")
        return float(m.group(1)), url

    def get_put_premium(self, ticker: str, expiry: date, strike: float) -> NasdaqPutPremium:
        # Nasdaq's option-chain endpoint defaults to a limited expiry window.
        # Request the specific expiry to ensure the desired chain is returned.
        data, url_used = self._fetch_option_chain(ticker, fromdate=expiry, todate=expiry)
        rows: List[Dict[str, Any]] = data["data"]["table"].get("rows") or []
        if not rows:
            raise RuntimeError(f"No option-chain rows returned for {ticker} from Nasdaq")

        current_group: Optional[date] = None
        chosen: Optional[Dict[str, Any]] = None

        for r in rows:
            group = r.get("expirygroup")
            if group:
                # Example: "February 13, 2026"
                try:
                    current_group = datetime.strptime(group.strip(), "%B %d, %Y").date()
                except ValueError:
                    current_group = None
                continue

            if current_group != expiry:
                continue

            r_strike = _to_float(r.get("strike"))
            if r_strike is None:
                continue
            if abs(r_strike - float(strike)) > 1e-6:
                continue

            chosen = r
            break

        if chosen is None:
            raise RuntimeError(f"No put row found for {ticker} expiry={expiry} strike={strike} via Nasdaq")

        bid = _to_float(chosen.get("p_Bid"))
        ask = _to_float(chosen.get("p_Ask"))
        last = _to_float(chosen.get("p_Last"))
        mid: Optional[float] = None
        if bid is not None and ask is not None:
            mid = (bid + ask) / 2.0
        elif last is not None:
            mid = last

        drill = str(chosen.get("drillDownURL") or "")
        contract_symbol: Optional[str] = None
        # Drilldown URL typically ends with an OCC-like symbol, e.g. nflx--260213c00080000
        if drill:
            contract_symbol = drill.rsplit("/", 1)[-1]

        return NasdaqPutPremium(
            ticker=ticker,
            expiry=expiry,
            strike=float(strike),
            bid=bid,
            ask=ask,
            last=last,
            mid=mid,
            contract_symbol=contract_symbol,
            urls=[url_used],
        )

    def get_put_chain(self, ticker: str, expiry: date) -> tuple[list[dict[str, Any]], str]:
        # Nasdaq's option-chain endpoint defaults to a limited expiry window.
        # Request the specific expiry to ensure the desired chain is returned.
        data, url_used = self._fetch_option_chain(ticker, fromdate=expiry, todate=expiry)
        rows: List[Dict[str, Any]] = data["data"]["table"].get("rows") or []
        if not rows:
            raise RuntimeError(f"No option-chain rows returned for {ticker} from Nasdaq")

        current_group: Optional[date] = None
        puts: list[dict[str, Any]] = []
        for r in rows:
            group = r.get("expirygroup")
            if group:
                try:
                    current_group = datetime.strptime(group.strip(), "%B %d, %Y").date()
                except ValueError:
                    current_group = None
                continue

            if current_group != expiry:
                continue

            strike_f = _to_float(r.get("strike"))
            if strike_f is None:
                continue

            bid = _to_float(r.get("p_Bid"))
            ask = _to_float(r.get("p_Ask"))
            last = _to_float(r.get("p_Last"))
            mid: Optional[float] = None
            if bid is not None and ask is not None:
                mid = (bid + ask) / 2.0
            elif last is not None:
                mid = last

            puts.append(
                {
                    "strike": strike_f,
                    "bid": bid,
                    "ask": ask,
                    "last": last,
                    "mid": mid,
                }
            )

        if not puts:
            raise RuntimeError(f"No puts found for {ticker} expiry={expiry} via Nasdaq")

        puts.sort(key=lambda x: float(x["strike"]))
        return puts, url_used

    def get_available_expiries(self, ticker: str) -> tuple[list[date], str]:
        data, url_used = self._fetch_option_chain(ticker)
        rows: List[Dict[str, Any]] = data["data"]["table"].get("rows") or []
        expiries: list[date] = []
        for r in rows:
            group = r.get("expirygroup")
            if not group:
                continue
            try:
                expiries.append(datetime.strptime(group.strip(), "%B %d, %Y").date())
            except ValueError:
                continue
        expiries = sorted(set(expiries))
        if not expiries:
            raise RuntimeError(f"No expiry groups found for {ticker} via Nasdaq")
        return expiries, url_used

    def pick_nearest_expiry(self, ticker: str, *, asof: date) -> tuple[date, str]:
        expiries, url_used = self.get_available_expiries(ticker)
        future = [e for e in expiries if e >= asof]
        if not future:
            return expiries[-1], url_used
        return future[0], url_used

    def get_last_trade_price(self, ticker: str) -> tuple[float, str]:
        url = NASDAQ_QUOTE_URL.format(ticker=ticker.lower())
        data, meta = self._http.get_json(url, params={"assetclass": "stocks"})
        info = (data.get("data") or {}).get("primaryData") or {}
        last = info.get("lastSalePrice") or info.get("lastTrade")
        if last is None:
            raise RuntimeError(f"No lastSalePrice in Nasdaq info payload for {ticker}")
        s = str(last).strip().replace("$", "")
        return float(s), meta.url
