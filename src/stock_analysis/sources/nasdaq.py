from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import re

from ..http import HttpClient


NASDAQ_OPTION_CHAIN_URL = "https://api.nasdaq.com/api/quote/{ticker}/option-chain"
NASDAQ_QUOTE_URL = "https://api.nasdaq.com/api/quote/{ticker}/info"
NASDAQ_SUMMARY_URL = "https://api.nasdaq.com/api/quote/{ticker}/summary"


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


@dataclass(frozen=True)
class NasdaqCallPremium:
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
        asset_class: str = "stocks",
    ) -> tuple[dict, str]:
        url = NASDAQ_OPTION_CHAIN_URL.format(ticker=ticker.lower())
        params: Dict[str, Any] = {"assetclass": asset_class}
        if fromdate is not None:
            params["fromdate"] = fromdate.isoformat()
        if todate is not None:
            params["todate"] = todate.isoformat()

        data, meta = self._http.get_json(url, params=params)
        if not (data.get("data") and data["data"].get("table")):
            raise RuntimeError(f"Unexpected Nasdaq payload for {ticker}: {data.get('message') or data}")
        return data, meta.url

    def get_underlying_from_option_chain(self, ticker: str, asset_class: str = "stocks") -> tuple[float, str]:
        data, url = self._fetch_option_chain(ticker, asset_class=asset_class)
        last_trade = (data.get("data") or {}).get("lastTrade") or ""
        # Example: "LAST TRADE: $82.2 (AS OF FEB 6, 2026)"
        m = re.search(r"\$\s*(\d+(?:\.\d+)?)", str(last_trade))
        if not m:
            raise RuntimeError(f"Could not parse underlying lastTrade for {ticker}: {last_trade!r}")
        return float(m.group(1)), url

    def get_put_premium(self, ticker: str, expiry: date, strike: float, asset_class: str = "stocks") -> NasdaqPutPremium:
        # Nasdaq's option-chain endpoint defaults to a limited expiry window.
        # Request the specific expiry to ensure the desired chain is returned.
        data, url_used = self._fetch_option_chain(ticker, fromdate=expiry, todate=expiry, asset_class=asset_class)
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

    def get_call_premium(self, ticker: str, expiry: date, strike: float, asset_class: str = "stocks") -> NasdaqCallPremium:
        # Nasdaq's option-chain endpoint defaults to a limited expiry window.
        # Request the specific expiry to ensure the desired chain is returned.
        data, url_used = self._fetch_option_chain(ticker, fromdate=expiry, todate=expiry, asset_class=asset_class)
        rows: List[Dict[str, Any]] = data["data"]["table"].get("rows") or []
        if not rows:
            raise RuntimeError(f"No option-chain rows returned for {ticker} from Nasdaq")

        current_group: Optional[date] = None
        chosen: Optional[Dict[str, Any]] = None

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

            r_strike = _to_float(r.get("strike"))
            if r_strike is None:
                continue
            if abs(r_strike - float(strike)) > 1e-6:
                continue

            chosen = r
            break

        if chosen is None:
            raise RuntimeError(f"No call row found for {ticker} expiry={expiry} strike={strike} via Nasdaq")

        bid = _to_float(chosen.get("c_Bid"))
        ask = _to_float(chosen.get("c_Ask"))
        last = _to_float(chosen.get("c_Last"))
        mid: Optional[float] = None
        if bid is not None and ask is not None:
            mid = (bid + ask) / 2.0
        elif last is not None:
            mid = last

        drill = str(chosen.get("drillDownURL") or "")
        contract_symbol: Optional[str] = None
        if drill:
            contract_symbol = drill.rsplit("/", 1)[-1]

        return NasdaqCallPremium(
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

    def get_put_chain(self, ticker: str, expiry: date, asset_class: str = "stocks") -> tuple[list[dict[str, Any]], str]:
        # Nasdaq's option-chain endpoint defaults to a limited expiry window.
        # Request the specific expiry to ensure the desired chain is returned.
        data, url_used = self._fetch_option_chain(ticker, fromdate=expiry, todate=expiry, asset_class=asset_class)
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

    def get_call_chain(self, ticker: str, expiry: date, asset_class: str = "stocks") -> tuple[list[dict[str, Any]], str]:
        # Nasdaq's option-chain endpoint defaults to a limited expiry window.
        # Request the specific expiry to ensure the desired chain is returned.
        data, url_used = self._fetch_option_chain(ticker, fromdate=expiry, todate=expiry, asset_class=asset_class)
        rows: List[Dict[str, Any]] = data["data"]["table"].get("rows") or []
        if not rows:
            raise RuntimeError(f"No option-chain rows returned for {ticker} from Nasdaq")

        current_group: Optional[date] = None
        calls: list[dict[str, Any]] = []
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

            bid = _to_float(r.get("c_Bid"))
            ask = _to_float(r.get("c_Ask"))
            last = _to_float(r.get("c_Last"))
            mid: Optional[float] = None
            if bid is not None and ask is not None:
                mid = (bid + ask) / 2.0
            elif last is not None:
                mid = last

            calls.append(
                {
                    "strike": strike_f,
                    "bid": bid,
                    "ask": ask,
                    "last": last,
                    "mid": mid,
                }
            )

        if not calls:
            raise RuntimeError(f"No calls found for {ticker} expiry={expiry} via Nasdaq")

        calls.sort(key=lambda x: float(x["strike"]))
        return calls, url_used

    def get_available_expiries(self, ticker: str, asset_class: str = "stocks") -> tuple[list[date], str]:
        data, url_used = self._fetch_option_chain(ticker, asset_class=asset_class)
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

    def pick_nearest_expiry(self, ticker: str, *, asof: date, asset_class: str = "stocks") -> tuple[date, str]:
        expiries, url_used = self.get_available_expiries(ticker, asset_class=asset_class)
        future = [e for e in expiries if e >= asof]
        if not future:
            return expiries[-1], url_used
        return future[0], url_used

    def get_last_trade_price(self, ticker: str, asset_class: str = "stocks") -> tuple[float, str]:
        url = NASDAQ_QUOTE_URL.format(ticker=ticker.lower())
        data, meta = self._http.get_json(url, params={"assetclass": asset_class})
        info = (data.get("data") or {}).get("primaryData") or {}
        last = info.get("lastSalePrice") or info.get("lastTrade")
        if last is None:
            raise RuntimeError(f"No lastSalePrice in Nasdaq info payload for {ticker}")
        s = str(last).strip().replace("$", "")
        return float(s), meta.url

    def get_full_chain(
        self,
        ticker: str,
        *,
        asof: date | None = None,
        weeks_out: int = 3,
        asset_class: str = "stocks",
    ) -> tuple[dict[str, Any], str]:
        """Fetch options chain (calls + puts) for all expirations within the next weeks_out weeks."""
        today = asof or date.today()
        todate = today + timedelta(weeks=weeks_out)
        data, url_used = self._fetch_option_chain(
            ticker, fromdate=today, todate=todate, asset_class=asset_class
        )

        # Extract underlying price from lastTrade (e.g. "LAST TRADE: $415.23 (AS OF ...)") 
        last_trade = (data.get("data") or {}).get("lastTrade") or ""
        m = re.search(r"\$\s*(\d+(?:\.\d+)?)", str(last_trade))
        underlying_price: Optional[float] = float(m.group(1)) if m else None

        rows: List[Dict[str, Any]] = data["data"]["table"].get("rows") or []
        expirations: list[dict[str, Any]] = []
        current_group: Optional[date] = None
        current_calls: list[dict[str, Any]] = []
        current_puts: list[dict[str, Any]] = []

        for r in rows:
            group = r.get("expirygroup")
            if group:
                if current_group is not None and (current_calls or current_puts):
                    expirations.append({
                        "expiry": current_group.isoformat(),
                        "calls": sorted(current_calls, key=lambda x: x["strike"]),
                        "puts": sorted(current_puts, key=lambda x: x["strike"]),
                    })
                current_calls = []
                current_puts = []
                try:
                    current_group = datetime.strptime(group.strip(), "%B %d, %Y").date()
                except ValueError:
                    current_group = None
                continue

            if current_group is None:
                continue

            strike_f = _to_float(r.get("strike"))
            if strike_f is None:
                continue

            p_bid = _to_float(r.get("p_Bid"))
            p_ask = _to_float(r.get("p_Ask"))
            p_last = _to_float(r.get("p_Last"))
            p_mid = ((p_bid + p_ask) / 2.0) if (p_bid is not None and p_ask is not None) else p_last

            c_bid = _to_float(r.get("c_Bid"))
            c_ask = _to_float(r.get("c_Ask"))
            c_last = _to_float(r.get("c_Last"))
            c_mid = ((c_bid + c_ask) / 2.0) if (c_bid is not None and c_ask is not None) else c_last

            if any(v is not None for v in [p_bid, p_ask, p_last]):
                current_puts.append({
                    "strike": strike_f,
                    "bid": p_bid,
                    "ask": p_ask,
                    "last": p_last,
                    "mid": p_mid,
                    "open_interest": _to_float(r.get("p_OI")),
                    "volume": _to_float(r.get("p_Vol")),
                })
            if any(v is not None for v in [c_bid, c_ask, c_last]):
                current_calls.append({
                    "strike": strike_f,
                    "bid": c_bid,
                    "ask": c_ask,
                    "last": c_last,
                    "mid": c_mid,
                    "open_interest": _to_float(r.get("c_OI")),
                    "volume": _to_float(r.get("c_Vol")),
                })

        # flush final group
        if current_group is not None and (current_calls or current_puts):
            expirations.append({
                "expiry": current_group.isoformat(),
                "calls": sorted(current_calls, key=lambda x: x["strike"]),
                "puts": sorted(current_puts, key=lambda x: x["strike"]),
            })

        return {"underlying_price": underlying_price, "expirations": expirations}, url_used

    def get_analyst_targets(
        self, ticker: str, asset_class: str = "stocks"
    ) -> tuple[dict[str, Any], str]:
        """Fetch analyst 1-year price target from Nasdaq summary endpoint."""
        url = NASDAQ_SUMMARY_URL.format(ticker=ticker.lower())
        data, meta = self._http.get_json(url, params={"assetclass": asset_class})
        summary_data = ((data.get("data") or {}).get("summaryData")) or {}

        def _price(v: Any) -> Optional[float]:
            if v is None:
                return None
            s = str(v).strip().replace("$", "").replace(",", "")
            if not s or s in {"--", "N/A"}:
                return None
            try:
                return float(s)
            except ValueError:
                return None

        one_yr = _price((summary_data.get("OneYrTarget") or {}).get("value"))

        return {
            "ticker": ticker.upper(),
            "one_year_target": one_yr,
        }, meta.url
