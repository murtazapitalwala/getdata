from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any, Dict, Optional

from .options_math import BsInputs, bs_put_delta, implied_vol_put_bisect
from .sources.nasdaq import Nasdaq


class OptionEngine:
    def __init__(self, *, nasdaq: Optional[Nasdaq] = None) -> None:
        self._nasdaq = nasdaq or Nasdaq()

    def get_latest_price(self, ticker: str) -> Dict[str, Any]:
        price, url = self._nasdaq.get_underlying_from_option_chain(ticker)
        return {"ticker": ticker.upper(), "price": float(price), "source": url}

    def find_strike_for_delta(
        self,
        *,
        ticker: str,
        expiry: date,
        target_delta: float,
        asof: date,
        spot: float | None = None,
        r: float = 0.0,
        q: float = 0.0,
    ) -> Dict[str, Any]:
        if expiry <= asof:
            raise ValueError("expiry must be after asof")

        s = float(spot) if spot is not None else self._nasdaq.get_underlying_from_option_chain(ticker)[0]
        puts, url_chain = self._nasdaq.get_put_chain(ticker, expiry)

        t_years = (expiry - asof).days / 365.0
        base = BsInputs(s=s, k=1.0, t=t_years, r=float(r), q=float(q))

        best: Optional[tuple[float, Dict[str, Any]]] = None

        for p in puts:
            mid = p.get("mid")
            k = float(p["strike"])
            if mid is None or mid <= 0:
                continue

            inp = BsInputs(s=base.s, k=k, t=base.t, r=base.r, q=base.q)
            iv = implied_vol_put_bisect(inp, float(mid))
            if iv is None:
                continue

            d = bs_put_delta(inp, iv)
            diff = abs(d - float(target_delta))

            cand = {
                "ticker": ticker.upper(),
                "asof": asof.isoformat(),
                "expiry": expiry.isoformat(),
                "spot": float(s),
                "target_delta": float(target_delta),
                "strike": float(k),
                "delta": float(d),
                "iv": float(iv),
                "premium_mid": float(mid),
                "premium_bid": p.get("bid"),
                "premium_ask": p.get("ask"),
                "source": url_chain,
            }

            if best is None or diff < best[0]:
                best = (diff, cand)

        if best is None:
            raise RuntimeError("Could not compute delta for any strike (missing mid prices or IV solve failed)")

        return best[1]

    def strike_and_premium_for_delta(
        self,
        *,
        ticker: str,
        target_delta: float,
        asof: date,
        expiry: date | None = None,
        spot: float | None = None,
        r: float = 0.0,
        q: float = 0.0,
    ) -> Dict[str, Any]:
        if expiry is None:
            expiry, _ = self._nasdaq.pick_nearest_expiry(ticker, asof=asof)

        return self.find_strike_for_delta(
            ticker=ticker,
            expiry=expiry,
            target_delta=target_delta,
            asof=asof,
            spot=spot,
            r=r,
            q=q,
        )
