from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any, Dict, Optional

from .options_math import (
    BsInputs,
    bs_call_delta,
    bs_put_delta,
    implied_vol_call_bisect,
    implied_vol_put_bisect,
)
from .sources.nasdaq import Nasdaq


class OptionEngine:
    def __init__(self, *, nasdaq: Optional[Nasdaq] = None) -> None:
        self._nasdaq = nasdaq or Nasdaq()

    def get_latest_price(self, ticker: str) -> Dict[str, Any]:
        price, url = self._nasdaq.get_underlying_from_option_chain(ticker)
        return {"ticker": ticker.upper(), "price": float(price), "source": url}

    def get_option_premium(
        self,
        *,
        ticker: str,
        expiry: date,
        strike: float,
        right: str = "put",
    ) -> Dict[str, Any]:
        right = str(right).strip().lower()
        if right not in {"put", "call"}:
            raise ValueError("right must be 'put' or 'call'")

        if right == "put":
            p = self._nasdaq.get_put_premium(ticker, expiry, float(strike))
            d = asdict(p)
            d["right"] = "put"
            return d

        c = self._nasdaq.get_call_premium(ticker, expiry, float(strike))
        d = asdict(c)
        d["right"] = "call"
        return d

    def find_strike_for_delta(
        self,
        *,
        ticker: str,
        expiry: date,
        target_delta: float,
        asof: date,
        right: str = "put",
        spot: float | None = None,
        r: float = 0.0,
        q: float = 0.0,
    ) -> Dict[str, Any]:
        right = str(right).strip().lower()
        if right not in {"put", "call"}:
            raise ValueError("right must be 'put' or 'call'")
        if expiry <= asof:
            raise ValueError("expiry must be after asof")

        s = float(spot) if spot is not None else self._nasdaq.get_underlying_from_option_chain(ticker)[0]
        chain, url_chain = (
            self._nasdaq.get_put_chain(ticker, expiry)
            if right == "put"
            else self._nasdaq.get_call_chain(ticker, expiry)
        )

        t_years = (expiry - asof).days / 365.0
        base = BsInputs(s=s, k=1.0, t=t_years, r=float(r), q=float(q))

        best: Optional[tuple[float, Dict[str, Any]]] = None

        for row in chain:
            mid = row.get("mid")
            k = float(row["strike"])
            if mid is None or mid <= 0:
                continue

            inp = BsInputs(s=base.s, k=k, t=base.t, r=base.r, q=base.q)
            iv = implied_vol_put_bisect(inp, float(mid)) if right == "put" else implied_vol_call_bisect(inp, float(mid))
            if iv is None:
                continue

            d = bs_put_delta(inp, iv) if right == "put" else bs_call_delta(inp, iv)
            diff = abs(d - float(target_delta))

            cand = {
                "ticker": ticker.upper(),
                "asof": asof.isoformat(),
                "expiry": expiry.isoformat(),
                "spot": float(s),
                "right": right,
                "target_delta": float(target_delta),
                "strike": float(k),
                "delta": float(d),
                "iv": float(iv),
                "premium_mid": float(mid),
                "premium_bid": row.get("bid"),
                "premium_ask": row.get("ask"),
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
            right="put",
            spot=spot,
            r=r,
            q=q,
        )

    def strike_and_premium_for_delta_right(
        self,
        *,
        ticker: str,
        target_delta: float,
        asof: date,
        right: str,
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
            right=right,
            spot=spot,
            r=r,
            q=q,
        )

    def covered_call(
        self,
        *,
        ticker: str,
        expiry: date,
        asof: date,
        strike: float | None = None,
        target_delta: float = 0.20,
        spot: float | None = None,
        r: float = 0.0,
        q: float = 0.0,
        shares: int = 100,
    ) -> Dict[str, Any]:
        if shares <= 0:
            raise ValueError("shares must be positive")
        if shares % 100 != 0:
            raise ValueError("shares must be a multiple of 100 (1 option contract = 100 shares)")

        s = float(spot) if spot is not None else self._nasdaq.get_underlying_from_option_chain(ticker)[0]
        if strike is None:
            chosen = self.find_strike_for_delta(
                ticker=ticker,
                expiry=expiry,
                target_delta=float(target_delta),
                asof=asof,
                right="call",
                spot=s,
                r=r,
                q=q,
            )
        else:
            # User specified a strike; fetch premium and compute IV/delta for that strike.
            call = self._nasdaq.get_call_premium(ticker, expiry, float(strike))
            prem = call.mid if call.mid is not None else call.bid
            if prem is None:
                raise RuntimeError("No usable call premium (mid/bid) returned from Nasdaq")
            t_years = (expiry - asof).days / 365.0
            inp = BsInputs(s=float(s), k=float(strike), t=float(t_years), r=float(r), q=float(q))
            iv = implied_vol_call_bisect(inp, float(prem))
            delta = bs_call_delta(inp, float(iv)) if iv is not None else None

            chosen = {
                "ticker": ticker.upper(),
                "asof": asof.isoformat(),
                "expiry": expiry.isoformat(),
                "spot": float(s),
                "right": "call",
                "target_delta": float(target_delta),
                "strike": float(strike),
                "delta": float(delta) if delta is not None else None,
                "iv": float(iv) if iv is not None else None,
                "premium_mid": float(prem),
                "premium_bid": call.bid,
                "premium_ask": call.ask,
                "source": call.urls[0] if call.urls else None,
            }

        premium = float(chosen["premium_mid"])
        k = float(chosen["strike"])
        t_years = (expiry - asof).days / 365.0

        breakeven = s - premium
        max_profit_per_share = (k - s) + premium
        max_profit_total = max_profit_per_share * float(shares)
        premium_total = premium * float(shares)
        cost_basis_total = (s - premium) * float(shares)

        max_return_pct = max_profit_per_share / s if s > 0 else None
        annualized_max_return_pct = (max_return_pct / t_years) if (max_return_pct is not None and t_years > 0) else None

        return {
            "ticker": ticker.upper(),
            "asof": asof.isoformat(),
            "expiry": expiry.isoformat(),
            "spot": float(s),
            "right": "call",
            "shares": int(shares),
            "strike": float(k),
            "target_delta": float(target_delta),
            "delta": float(chosen.get("delta")) if chosen.get("delta") is not None else None,
            "iv": float(chosen.get("iv")) if chosen.get("iv") is not None else None,
            "premium_mid": premium,
            "premium_total": premium_total,
            "breakeven": float(breakeven),
            "max_profit_per_share": float(max_profit_per_share),
            "max_profit_total": float(max_profit_total),
            "max_return_pct": float(max_return_pct) if max_return_pct is not None else None,
            "annualized_max_return_pct": float(annualized_max_return_pct) if annualized_max_return_pct is not None else None,
            "cost_basis_total": float(cost_basis_total),
            "source": chosen.get("source"),
        }
