from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import yfinance as yf

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_BASE_S = 2.0

# Simple TTL cache: key -> (timestamp, data)
_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL_S = 120  # 2 minutes


@dataclass(frozen=True)
class Technicals:
    ticker: str
    latest_price: Optional[float]
    latest_date: Optional[str]
    sma_20: Optional[float]
    sma_50: Optional[float]
    sma_100: Optional[float]
    sma_200: Optional[float]
    rsi_14: Optional[float]
    macd_line: Optional[float]
    signal_line: Optional[float]
    macd_histogram: Optional[float]
    urls: List[str]


# ── Local indicator math (same as alpha_vantage) ────────────────────

def _sma(closes: List[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    return round(sum(closes[-period:]) / period, 4)


def _rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas[:period]]
    losses = [max(-d, 0.0) for d in deltas[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for d in deltas[period:]:
        avg_gain = (avg_gain * (period - 1) + max(d, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0.0)) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - 100.0 / (1.0 + rs), 4)


def _ema(values: List[float], period: int) -> List[float]:
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, Optional[float]]:
    empty: Dict[str, Optional[float]] = {"macd_line": None, "signal_line": None, "macd_histogram": None}
    if len(closes) < slow + signal:
        return empty
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    offset = slow - fast
    macd_line_series = [ema_fast[offset + i] - ema_slow[i] for i in range(len(ema_slow))]
    if len(macd_line_series) < signal:
        return empty
    signal_series = _ema(macd_line_series, signal)
    ml = macd_line_series[-1]
    sl = signal_series[-1]
    return {
        "macd_line": round(ml, 4),
        "signal_line": round(sl, 4),
        "macd_histogram": round(ml - sl, 4),
    }


class YFinanceSource:
    def get_technicals(self, ticker: str) -> Technicals:
        """Fetch 1 year of daily closes from yfinance and compute all indicators locally."""
        t = ticker.upper()
        logger.info("yfinance: downloading 1y daily data for %s", t)
        df = yf.download(t, period="1y", interval="1d", progress=False, auto_adjust=True)
        logger.info("yfinance: got %d rows for %s", len(df), t)
        if df.empty:
            logger.error("yfinance: no data returned for %s", t)
            raise RuntimeError(f"yfinance returned no data for {t}")

        close_col = df["Close"]
        if hasattr(close_col, "columns"):
            close_col = close_col.iloc[:, 0]
        closes = close_col.dropna().tolist()
        latest_date = str(df.index[-1].date())
        latest_price = round(closes[-1], 4)
        logger.info("yfinance: %s — %d closes, latest=%s price=%s", t, len(closes), latest_date, latest_price)

        result = Technicals(
            ticker=t,
            latest_price=latest_price,
            latest_date=latest_date,
            sma_20=_sma(closes, 20),
            sma_50=_sma(closes, 50),
            sma_100=_sma(closes, 100),
            sma_200=_sma(closes, 200),
            rsi_14=_rsi(closes, 14),
            **_macd(closes),
            urls=[f"https://finance.yahoo.com/quote/{t}"],
        )
        logger.info("yfinance: %s indicators — SMA20=%s SMA50=%s SMA100=%s SMA200=%s RSI=%s MACD=%s",
                     t, result.sma_20, result.sma_50, result.sma_100, result.sma_200, result.rsi_14, result.macd_line)
        return result

    def get_option_chain(self, ticker: str, expiration: Optional[str] = None) -> Dict[str, Any]:
        """Return option chain for a given expiration (or nearest if omitted)."""
        t = ticker.upper()
        cache_key = f"chain:{t}:{expiration or ''}"
        now = time.time()
        if cache_key in _cache:
            ts, cached = _cache[cache_key]
            if now - ts < _CACHE_TTL_S:
                logger.info("yfinance: cache hit for %s exp=%s", t, expiration)
                return cached

        last_err: Optional[Exception] = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                tk = yf.Ticker(t)
                expirations = tk.options
                logger.info("yfinance: %s has %d option expirations (attempt %d)", t, len(expirations), attempt)
                if not expirations:
                    raise RuntimeError(f"No options available for {t}")

                exp = expiration if expiration and expiration in expirations else expirations[0]
                chain = tk.option_chain(exp)
                logger.info("yfinance: %s chain for %s — %d calls, %d puts", t, exp, len(chain.calls), len(chain.puts))
                break
            except Exception as e:
                last_err = e
                if attempt < _MAX_RETRIES:
                    wait = _BACKOFF_BASE_S * (2 ** (attempt - 1))  # 2s, 4s
                    logger.warning("yfinance: %s attempt %d failed (%s) — retrying in %.1fs", t, attempt, e, wait)
                    time.sleep(wait)
                else:
                    logger.error("yfinance: %s all %d attempts failed", t, _MAX_RETRIES)
                    raise

        def _df_to_records(df) -> List[Dict[str, Any]]:
            import math
            df = df.copy()
            for col in df.columns:
                if hasattr(df[col], "dt"):
                    df[col] = df[col].astype(str)
            records = []
            for row in df.to_dict(orient="records"):
                records.append({
                    k: (None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v)
                    for k, v in row.items()
                })
            return records

        result = {
            "ticker": t,
            "expiration": exp,
            "expirations": list(expirations),
            "calls": _df_to_records(chain.calls),
            "puts": _df_to_records(chain.puts),
        }
        _cache[cache_key] = (time.time(), result)
        return result

    def get_recommendations(self, ticker: str) -> Dict[str, Any]:
        """Return analyst recommendations and upgrades/downgrades."""
        t = ticker.upper()
        tk = yf.Ticker(t)

        recs = []
        try:
            df = tk.recommendations
            if df is not None and not df.empty:
                df = df.reset_index()
                for col in df.columns:
                    if hasattr(df[col], "dt"):
                        df[col] = df[col].astype(str)
                recs = df.where(df.notna(), None).to_dict(orient="records")
        except Exception:
            logger.exception("yfinance: %s recommendations failed", t)

        upgrades = []
        try:
            df = tk.upgrades_downgrades
            if df is not None and not df.empty:
                df = df.reset_index().head(50)
                for col in df.columns:
                    if hasattr(df[col], "dt"):
                        df[col] = df[col].astype(str)
                upgrades = df.where(df.notna(), None).to_dict(orient="records")
        except Exception:
            logger.exception("yfinance: %s upgrades_downgrades failed", t)

        logger.info("yfinance: %s — %d recommendations, %d upgrades/downgrades", t, len(recs), len(upgrades))
        return {
            "ticker": t,
            "recommendations": recs,
            "upgrades_downgrades": upgrades,
        }

    def get_analyst_targets(self, ticker: str) -> Dict[str, Any]:
        """Return analyst price targets."""
        t = ticker.upper()
        tk = yf.Ticker(t)
        info = tk.info or {}
        targets = {
            "ticker": t,
            "current_price": info.get("currentPrice"),
            "target_low": info.get("targetLowPrice"),
            "target_mean": info.get("targetMeanPrice"),
            "target_median": info.get("targetMedianPrice"),
            "target_high": info.get("targetHighPrice"),
            "number_of_analysts": info.get("numberOfAnalystOpinions"),
            "recommendation_key": info.get("recommendationKey"),
        }
        logger.info("yfinance: %s analyst targets — mean=%s median=%s", t, targets["target_mean"], targets["target_median"])
        return targets

    def get_news(self, ticker: str) -> Dict[str, Any]:
        """Return recent news headlines for a ticker."""
        t = ticker.upper()
        tk = yf.Ticker(t)
        raw_news = tk.news or []
        articles = []
        for item in raw_news:
            content = item.get("content") or item
            canonical = content.get("canonicalUrl") or {}
            provider = content.get("provider") or {}
            articles.append({
                "title": content.get("title") or item.get("title"),
                "publisher": provider.get("displayName") or item.get("publisher"),
                "link": canonical.get("url") or item.get("link"),
                "published": content.get("pubDate") or item.get("providerPublishTime"),
                "summary": content.get("summary"),
            })
        logger.info("yfinance: %s — %d news articles", t, len(articles))
        return {
            "ticker": t,
            "news": articles,
        }

    def get_hourly_prices(self, ticker: str, *, fromdate: date, todate: date) -> Dict[str, Any]:
        """Return 60m bars via yfinance for a requested date range."""
        if fromdate > todate:
            raise ValueError("fromdate must be on or before todate")

        t = ticker.upper()
        end_exclusive = todate + timedelta(days=1)
        df = yf.download(
            t,
            start=fromdate.isoformat(),
            end=end_exclusive.isoformat(),
            interval="60m",
            progress=False,
            auto_adjust=False,
        )
        if df is None or df.empty:
            raise RuntimeError(f"No yfinance hourly rows returned for {t}")

        def _get_series(name: str):
            if name in df.columns:
                col = df[name]
                if hasattr(col, "columns"):
                    return col.iloc[:, 0]
                return col
            for c in df.columns:
                if isinstance(c, tuple) and len(c) >= 1 and c[0] == name:
                    col = df[c]
                    if hasattr(col, "columns"):
                        return col.iloc[:, 0]
                    return col
            return None

        s_open = _get_series("Open")
        s_high = _get_series("High")
        s_low = _get_series("Low")
        s_close = _get_series("Close")
        s_vol = _get_series("Volume")
        if s_close is None:
            raise RuntimeError(f"Could not parse yfinance hourly columns for {t}")

        prices: list[dict[str, Any]] = []
        for idx in s_close.index:
            ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            else:
                ts = ts.astimezone(timezone.utc)
            d = ts.date()
            if d < fromdate or d > todate:
                continue

            close_v = s_close.loc[idx]
            open_v = s_open.loc[idx] if s_open is not None else close_v
            high_v = s_high.loc[idx] if s_high is not None else close_v
            low_v = s_low.loc[idx] if s_low is not None else close_v
            vol_v = s_vol.loc[idx] if s_vol is not None else 0

            def _n(v):
                try:
                    if v is None:
                        return None
                    fv = float(v)
                    if fv != fv:  # NaN
                        return None
                    return fv
                except Exception:
                    return None

            c = _n(close_v)
            if c is None:
                continue

            prices.append(
                {
                    "date": ts.isoformat().replace("+00:00", "Z"),
                    "open": _n(open_v),
                    "high": _n(high_v),
                    "low": _n(low_v),
                    "close": c,
                    "volume": int(_n(vol_v) or 0),
                }
            )

        prices.sort(key=lambda x: x["date"])
        if not prices:
            raise RuntimeError(f"No yfinance hourly rows found for {t} in range")

        return {
            "ticker": t,
            "from_date": fromdate.isoformat(),
            "to_date": todate.isoformat(),
            "prices": prices,
            "count": len(prices),
            "source": "yfinance",
            "sources": [f"https://finance.yahoo.com/quote/{t}"],
        }
