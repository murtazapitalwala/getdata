from __future__ import annotations

import time
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

from .options_math import (
    BsInputs,
    bs_call_delta,
    bs_put_delta,
    implied_vol_call_bisect,
    implied_vol_put_bisect,
)
from .sources.alpha_vantage import AlphaVantage
from .sources.nasdaq import Nasdaq
from .sources.stockanalysis import StockAnalysis
from .sources.stooq import Stooq
from .sources.yahoo_chart import YahooChart
from .sources.yfinance_source import YFinanceSource
from .support_resistance import compute_volume_weighted_support_resistance


def _max_num(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if a >= b else b


def _min_num(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if a <= b else b


def _aggregate_weekly(prices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not prices:
        return []

    weekly: list[dict[str, Any]] = []
    current_key: tuple[int, int] | None = None
    bucket: dict[str, Any] | None = None

    for row in prices:
        d = date.fromisoformat(str(row["date"]))
        iso = d.isocalendar()
        key = (iso.year, iso.week)

        if key != current_key:
            if bucket is not None:
                weekly.append(bucket)
            bucket = {
                "date": row.get("date"),  # week end date (last trading day seen in that week)
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "volume": row.get("volume") or 0,
            }
            current_key = key
            continue

        assert bucket is not None
        bucket["date"] = row.get("date")
        if bucket.get("open") is None and row.get("open") is not None:
            bucket["open"] = row.get("open")
        bucket["high"] = _max_num(bucket.get("high"), row.get("high"))
        bucket["low"] = _min_num(bucket.get("low"), row.get("low"))
        if row.get("close") is not None:
            bucket["close"] = row.get("close")
        bucket["volume"] = int(bucket.get("volume") or 0) + int(row.get("volume") or 0)

    if bucket is not None:
        weekly.append(bucket)
    return weekly


def _parse_iso_dt(v: str) -> datetime:
    s = str(v).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _aggregate_hourly(prices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not prices:
        return []

    hourly: list[dict[str, Any]] = []
    current_key: tuple[int, int, int, int] | None = None
    bucket: dict[str, Any] | None = None

    for row in sorted(prices, key=lambda x: str(x.get("date") or "")):
        try:
            dt = _parse_iso_dt(str(row.get("date")))
        except Exception:
            continue

        key = (dt.year, dt.month, dt.day, dt.hour)
        hour_start = dt.replace(minute=0, second=0, microsecond=0)

        if key != current_key:
            if bucket is not None:
                hourly.append(bucket)

            close_v = row.get("close")
            open_v = row.get("open") if row.get("open") is not None else close_v
            high_v = row.get("high") if row.get("high") is not None else close_v
            low_v = row.get("low") if row.get("low") is not None else close_v
            bucket = {
                "date": hour_start.isoformat().replace("+00:00", "Z"),
                "open": open_v,
                "high": high_v,
                "low": low_v,
                "close": close_v,
                "volume": int(row.get("volume") or 0),
            }
            current_key = key
            continue

        assert bucket is not None
        if bucket.get("open") is None and row.get("open") is not None:
            bucket["open"] = row.get("open")
        bucket["high"] = _max_num(bucket.get("high"), row.get("high"))
        bucket["low"] = _min_num(bucket.get("low"), row.get("low"))
        if row.get("close") is not None:
            bucket["close"] = row.get("close")
        bucket["volume"] = int(bucket.get("volume") or 0) + int(row.get("volume") or 0)

    if bucket is not None:
        hourly.append(bucket)
    return hourly


class OptionEngine:
    def __init__(
        self,
        *,
        nasdaq: Optional[Nasdaq] = None,
        alpha_vantage: Optional[AlphaVantage] = None,
        yfinance: Optional[YFinanceSource] = None,
        stockanalysis: Optional[StockAnalysis] = None,
        stooq: Optional[Stooq] = None,
        yahoo_chart: Optional[YahooChart] = None,
    ) -> None:
        self._nasdaq = nasdaq or Nasdaq()
        self._alpha_vantage = alpha_vantage or AlphaVantage()
        self._yfinance = yfinance or YFinanceSource()
        self._stockanalysis = stockanalysis or StockAnalysis()
        self._stooq = stooq or Stooq()
        self._yahoo_chart = yahoo_chart or YahooChart()
        self._historical_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}
        self._historical_cache_ttl_s = 300.0

    def get_latest_price(self, ticker: str, asset_class: str = "stocks") -> Dict[str, Any]:
        price, url = self._nasdaq.get_underlying_from_option_chain(ticker, asset_class=asset_class)
        return {"ticker": ticker.upper(), "price": float(price), "source": url}

    def get_option_premium(
        self,
        *,
        ticker: str,
        expiry: date,
        strike: float,
        right: str = "put",
        asof: date | None = None,
        spot: float | None = None,
        r: float = 0.0,
        q: float = 0.0,
        asset_class: str = "stocks",
    ) -> Dict[str, Any]:
        right = str(right).strip().lower()
        if right not in {"put", "call"}:
            raise ValueError("right must be 'put' or 'call'")

        asof_d = asof or date.today()
        s = float(spot) if spot is not None else self._nasdaq.get_underlying_from_option_chain(ticker, asset_class=asset_class)[0]
        t_years = (expiry - asof_d).days / 365.0
        inp = BsInputs(s=float(s), k=float(strike), t=float(max(t_years, 0.0)), r=float(r), q=float(q))

        def _iv_and_delta(premium_mid: float) -> tuple[float | None, float | None]:
            if premium_mid <= 0:
                return None, None
            if right == "put":
                iv = implied_vol_put_bisect(inp, float(premium_mid)) if inp.t > 0 else None
                delta = bs_put_delta(inp, float(iv)) if iv is not None else (bs_put_delta(inp, 0.0) if inp.t <= 0 else None)
                return iv, delta
            iv = implied_vol_call_bisect(inp, float(premium_mid)) if inp.t > 0 else None
            delta = bs_call_delta(inp, float(iv)) if iv is not None else (bs_call_delta(inp, 0.0) if inp.t <= 0 else None)
            return iv, delta

        if right == "put":
            p = self._nasdaq.get_put_premium(ticker, expiry, float(strike), asset_class=asset_class)
            d = asdict(p)
            d["right"] = "put"
            d["asof"] = asof_d.isoformat()
            d["spot"] = float(s)
            d["r"] = float(r)
            d["q"] = float(q)
            if d.get("mid") is not None:
                iv, delta = _iv_and_delta(float(d["mid"]))
                d["iv"] = float(iv) if iv is not None else None
                d["delta"] = float(delta) if delta is not None else None
            else:
                d["iv"] = None
                d["delta"] = None
            return d

        c = self._nasdaq.get_call_premium(ticker, expiry, float(strike), asset_class=asset_class)
        d = asdict(c)
        d["right"] = "call"
        d["asof"] = asof_d.isoformat()
        d["spot"] = float(s)
        d["r"] = float(r)
        d["q"] = float(q)
        if d.get("mid") is not None:
            iv, delta = _iv_and_delta(float(d["mid"]))
            d["iv"] = float(iv) if iv is not None else None
            d["delta"] = float(delta) if delta is not None else None
        else:
            d["iv"] = None
            d["delta"] = None
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
        asset_class: str = "stocks",
    ) -> Dict[str, Any]:
        right = str(right).strip().lower()
        if right not in {"put", "call"}:
            raise ValueError("right must be 'put' or 'call'")
        if expiry <= asof:
            raise ValueError("expiry must be after asof")

        s = float(spot) if spot is not None else self._nasdaq.get_underlying_from_option_chain(ticker, asset_class=asset_class)[0]
        chain, url_chain = (
            self._nasdaq.get_put_chain(ticker, expiry, asset_class=asset_class)
            if right == "put"
            else self._nasdaq.get_call_chain(ticker, expiry, asset_class=asset_class)
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
        asset_class: str = "stocks",
    ) -> Dict[str, Any]:
        if expiry is None:
            expiry, _ = self._nasdaq.pick_nearest_expiry(ticker, asof=asof, asset_class=asset_class)

        return self.find_strike_for_delta(
            ticker=ticker,
            expiry=expiry,
            target_delta=target_delta,
            asof=asof,
            right="put",
            spot=spot,
            r=r,
            q=q,
            asset_class=asset_class,
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
        asset_class: str = "stocks",
    ) -> Dict[str, Any]:
        if expiry is None:
            expiry, _ = self._nasdaq.pick_nearest_expiry(ticker, asof=asof, asset_class=asset_class)
        return self.find_strike_for_delta(
            ticker=ticker,
            expiry=expiry,
            target_delta=target_delta,
            asof=asof,
            right=right,
            spot=spot,
            r=r,
            q=q,
            asset_class=asset_class,
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
        asset_class: str = "stocks",
    ) -> Dict[str, Any]:
        if shares <= 0:
            raise ValueError("shares must be positive")
        if shares % 100 != 0:
            raise ValueError("shares must be a multiple of 100 (1 option contract = 100 shares)")

        s = float(spot) if spot is not None else self._nasdaq.get_underlying_from_option_chain(ticker, asset_class=asset_class)[0]
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
                asset_class=asset_class,
            )
        else:
            # User specified a strike; fetch premium and compute IV/delta for that strike.
            call = self._nasdaq.get_call_premium(ticker, expiry, float(strike), asset_class=asset_class)
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

    def get_technicals(self, ticker: str) -> Dict[str, Any]:
        yf = self._yfinance.get_technicals(ticker)
        return {
            "ticker": yf.ticker,
            "latest_price": yf.latest_price,
            "latest_date": yf.latest_date,
            "sma_20": yf.sma_20,
            "sma_50": yf.sma_50,
            "sma_100": yf.sma_100,
            "sma_200": yf.sma_200,
            "rsi_14": yf.rsi_14,
            "macd_line": yf.macd_line,
            "signal_line": yf.signal_line,
            "macd_histogram": yf.macd_histogram,
            "source": "yfinance",
            "sources": yf.urls,
        }

    def get_option_chain(self, ticker: str, expiration: Optional[str] = None) -> Dict[str, Any]:
        return self._yfinance.get_option_chain(ticker, expiration)

    def get_nasdaq_option_chain(
        self,
        ticker: str,
        weeks_out: int = 3,
        asset_class: str = "stocks",
    ) -> Dict[str, Any]:
        """Fetch full options chain for the next weeks_out weeks via Nasdaq."""
        result, url = self._nasdaq.get_full_chain(
            ticker, weeks_out=weeks_out, asset_class=asset_class
        )
        return {
            "ticker": ticker.upper(),
            "weeks_out": weeks_out,
            "underlying_price": result["underlying_price"],
            "expirations": result["expirations"],
            "source": url,
        }

    def get_nasdaq_analyst_targets(
        self,
        ticker: str,
        asset_class: str = "stocks",
    ) -> Dict[str, Any]:
        result, url = self._nasdaq.get_analyst_targets(ticker, asset_class=asset_class)
        result["source"] = url
        return result

    def get_recommendations(self, ticker: str) -> Dict[str, Any]:
        return self._yfinance.get_recommendations(ticker)

    def get_analyst_targets(self, ticker: str) -> Dict[str, Any]:
        sa = self._stockanalysis.get_analyst_ratings(ticker)

        current_price: Optional[float] = None
        try:
            current_price, _ = self._nasdaq.get_last_trade_price(ticker)
        except Exception:
            pass

        return {
            "ticker": ticker.upper(),
            "current_price": current_price,
            "consensus": sa.get("consensus"),
            "strong_buy": sa.get("strong_buy"),
            "buy": sa.get("buy"),
            "hold": sa.get("hold"),
            "sell": sa.get("sell"),
            "strong_sell": sa.get("strong_sell"),
            "total_analysts": sa.get("total_analysts"),
            "price_target_low": sa.get("price_target_low"),
            "price_target_average": sa.get("price_target_average"),
            "price_target_median": sa.get("price_target_median"),
            "price_target_high": sa.get("price_target_high"),
            "price_target_count": sa.get("price_target_count"),
            "recent_ratings": sa.get("recent_ratings"),
            "source": sa.get("source"),
        }

    def get_news(self, ticker: str, asset_class: str = "stocks") -> Dict[str, Any]:
        result, sources = self._nasdaq.get_news_and_macro(ticker, asset_class=asset_class)
        result["sources"] = sources
        result["source"] = "nasdaq"
        return result

    def get_analyst_ratings(self, ticker: str) -> Dict[str, Any]:
        return self._stockanalysis.get_analyst_ratings(ticker)

    def get_historical_prices(
        self,
        ticker: str,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
        timeframe: str = "1d",
        asset_class: str = "stocks",
    ) -> Dict[str, Any]:
        end_d = to_date or date.today()
        start_d = from_date or (end_d - timedelta(days=365))
        if start_d > end_d:
            raise ValueError("from_date must be on or before to_date")
        tf = str(timeframe).strip().lower()
        if tf not in {"1d", "1w", "1h"}:
            raise ValueError("timeframe must be one of: 1d, 1w, 1h")

        cache_key = f"{ticker.upper()}:{start_d.isoformat()}:{end_d.isoformat()}:{asset_class}:{tf}"
        cached = self._historical_cache.get(cache_key)
        now = time.time()
        if cached and (now - cached[0]) < self._historical_cache_ttl_s:
            return cached[1]

        if tf == "1h":
            try:
                hourly, source_url = self._yahoo_chart.get_hourly_prices(
                    ticker,
                    fromdate=start_d,
                    todate=end_d,
                )
                result = {
                    **hourly,
                    "source": "yahoo",
                    "source_url": source_url,
                    "fallback_used": False,
                    "timeframe": tf,
                }
            except Exception as yahoo_err:
                try:
                    yf_hourly = self._yfinance.get_hourly_prices(
                        ticker,
                        fromdate=start_d,
                        todate=end_d,
                    )
                    result = {
                        **yf_hourly,
                        "source": "yfinance",
                        "source_url": (yf_hourly.get("sources") or [None])[0],
                        "fallback_used": True,
                        "fallback_reason": str(yahoo_err),
                        "timeframe": tf,
                    }
                except Exception as yfin_err:
                    # Nasdaq intraday endpoint is only useful for current-day minute data.
                    # Keep it as a final resilience fallback for current day.
                    today = date.today()
                    if start_d == today and end_d == today:
                        intraday, source_url = self._nasdaq.get_intraday_prices(
                            ticker,
                            asset_class=asset_class,
                        )
                        hourly_prices = _aggregate_hourly(intraday.get("prices") or [])
                        result = {
                            **intraday,
                            "prices": hourly_prices,
                            "count": len(hourly_prices),
                            "source": "nasdaq",
                            "source_url": source_url,
                            "fallback_used": True,
                            "fallback_reason": f"yahoo={yahoo_err}; yfinance={yfin_err}",
                            "timeframe": tf,
                        }
                    else:
                        raise RuntimeError(
                            "Hourly range fetch failed (yahoo + yfinance). "
                            "Nasdaq public APIs do not provide historical hourly bars for arbitrary date ranges."
                        ) from yfin_err

            self._historical_cache[cache_key] = (now, result)
            return result

        try:
            primary, source_url = self._nasdaq.get_historical_prices(
                ticker,
                fromdate=start_d,
                todate=end_d,
                asset_class=asset_class,
            )
            result = {
                **primary,
                "source": "nasdaq",
                "source_url": source_url,
                "fallback_used": False,
            }
        except Exception as nasdaq_err:
            try:
                fallback, source_url = self._stooq.get_historical_prices(
                    ticker,
                    fromdate=start_d,
                    todate=end_d,
                )
                result = {
                    **fallback,
                    "source": "stooq",
                    "source_url": source_url,
                    "fallback_used": True,
                    "fallback_reason": str(nasdaq_err),
                }
            except Exception as stooq_err:
                yf_daily, source_url = self._yfinance.get_daily_prices(
                    ticker,
                    fromdate=start_d,
                    todate=end_d,
                )
                result = {
                    **yf_daily,
                    "source": "yfinance",
                    "source_url": source_url,
                    "fallback_used": True,
                    "fallback_reason": f"nasdaq={nasdaq_err}; stooq={stooq_err}",
                }

        if tf == "1w":
            weekly_prices = _aggregate_weekly(result.get("prices") or [])
            result["prices"] = weekly_prices
            result["count"] = len(weekly_prices)

        result["timeframe"] = tf

        self._historical_cache[cache_key] = (now, result)
        return result

    def get_volume_weighted_support_resistance(
        self,
        ticker: str,
        start: str,
        end: str,
        interval: str = "d",
        pivot_window: int = 2,
        tolerance_pct: float = 0.02,
        min_touches: int = 2,
        max_zones_per_side: int = 6,
    ) -> Dict[str, Any]:
        # Map shorthand interval letters to the timeframe format expected by get_historical_prices.
        _interval_map = {"d": "1d", "w": "1w", "h": "1h", "1d": "1d", "1w": "1w", "1h": "1h"}
        tf = _interval_map.get(str(interval).strip().lower(), "1d")

        hist = self.get_historical_prices(
            ticker=ticker,
            from_date=date.fromisoformat(start),
            to_date=date.fromisoformat(end),
            timeframe=tf,
        )

        prices = hist["prices"]

        result = compute_volume_weighted_support_resistance(
            ticker=ticker,
            prices=prices,
            pivot_window=pivot_window,
            tolerance_pct=tolerance_pct,
            min_touches=min_touches,
            max_zones_per_side=max_zones_per_side,
        )

        result["start"] = start
        result["end"] = end
        result["interval"] = interval
        result["timeframe"] = tf
        result["count"] = len(prices)
        result["source"] = hist.get("source")
        result["source_url"] = hist.get("source_url")
        result["fallback_used"] = hist.get("fallback_used", False)

        # Explain empty results so callers understand why no zones were returned.
        if not result["supports"] and not result["resistances"]:
            # Minimum bars needed: pivot_window bars on each side of a candidate,
            # plus at least min_touches distinct pivot bars that must cluster together.
            min_bars_needed = pivot_window * 2 + max(min_touches * 2, 1)
            warnings: list[str] = []
            if len(prices) < min_bars_needed:
                warnings.append(
                    f"Only {len(prices)} bar(s) in the requested range — need at least "
                    f"{min_bars_needed} to form {min_touches}-touch zones with "
                    f"pivot_window={pivot_window}. Expand the date range or set min_touches=1."
                )
            else:
                warnings.append(
                    f"No pivot clusters met the criteria (min_touches={min_touches}, "
                    f"tolerance_pct={tolerance_pct}). "
                    "Try a longer date range, reduce min_touches to 1, or widen tolerance_pct."
                )
            result["warnings"] = warnings

        return result
