from __future__ import annotations

import logging
import subprocess
import threading
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import FastAPI, HTTPException, Query, Request

from .engine import OptionEngine

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short=7", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "unknown"

_GIT_COMMIT = _git_sha()

# Tickers to pre-warm on startup, with their asset class.
_WARMUP_TICKERS: list[tuple[str, str]] = [
    ("TQQQ", "etf"),
    ("SOXL", "etf"),
    ("TSLA", "stocks"),
    ("TSLL", "etf"),
    ("GOOG", "stocks"),
    ("MSFT", "stocks"),
    ("TNA",  "etf"),
    ("META", "stocks"),
    ("AMZN", "stocks"),
]

# (timeframe, lookback_days) pairs to warm per ticker
_WARMUP_COMBOS: list[tuple[str, int]] = [
    ("1d",  365),   # 1 year daily
    ("1w",  730),   # 2 years weekly
    ("1h",   31),   # 1 month hourly
]


def _warmup_cache() -> None:
    today = date.today()
    total = len(_WARMUP_TICKERS) * len(_WARMUP_COMBOS)
    done = 0
    for ticker, asset_class in _WARMUP_TICKERS:
        for tf, days in _WARMUP_COMBOS:
            start_d = today - timedelta(days=days)
            try:
                _historical_engine.get_historical_prices(
                    ticker,
                    from_date=start_d,
                    to_date=today,
                    timeframe=tf,
                    asset_class=asset_class,
                )
                done += 1
                logger.info("Cache warm-up [%d/%d] OK  %s %s %s→%s", done, total, ticker, tf, start_d, today)
            except Exception:
                done += 1
                logger.warning("Cache warm-up [%d/%d] FAIL %s %s", done, total, ticker, tf, exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start warm-up in a background thread so the server is ready immediately.
    t = threading.Thread(target=_warmup_cache, name="cache-warmup", daemon=True)
    t.start()
    logger.info("Cache warm-up started in background (%d combos)", len(_WARMUP_TICKERS) * len(_WARMUP_COMBOS))
    yield


app = FastAPI(
    title="Stock Analysis API",
    version="0.1.0",
    servers=[{"url": "https://getdata-uufz.onrender.com"}],
    lifespan=lifespan,
)
#app = FastAPI(title="Stock Analysis API", version="0.1.0", servers=[{"url": "http://localhost:8080"}])  # local override for testing
engine = OptionEngine()
# Dedicated engine with 5-minute HTTP timeouts for long-running historical fetches.
_historical_engine = OptionEngine(http_timeout_s=600.0)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info("→ %s %s", request.method, request.url.path)
    response = await call_next(request)
    logger.info("← %s %s %s", request.method, request.url.path, response.status_code)
    return response


@app.get("/")
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/version")
def version():
    return {"commit": _GIT_COMMIT}


@app.get("/price")
def get_price(
    ticker: str = Query(..., description="Ticker symbol, e.g. META"),
    asset_class: str = Query("stocks", description="Asset class: stocks or etf"),
):
    try:
        return engine.get_latest_price(ticker, asset_class=asset_class)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/historical-prices")
def historical_prices(
    ticker: str = Query(..., description="Ticker symbol, e.g. TSLA"),
    from_date: date | None = Query(None, description="Start date inclusive (YYYY-MM-DD). Default: 1 year ago"),
    to_date: date | None = Query(None, description="End date inclusive (YYYY-MM-DD). Default: today"),
    timeframe: str = Query("1d", pattern="^(1[dDwWhH])$", description="Timeframe: 1d (daily), 1w (weekly), or 1h (hourly; historical via Yahoo)"),
    asset_class: str = Query("stocks", description="Asset class for Nasdaq lookup: stocks or etf"),
    refresh: bool = Query(False, description="Set true to bypass cache and force a fresh fetch"),
):
    end_d = to_date or date.today()
    start_d = from_date or (end_d - timedelta(days=365))
    if start_d > end_d:
        raise HTTPException(status_code=400, detail="from_date must be on or before to_date")

    logger.info("Fetching historical prices for %s from %s to %s (refresh=%s)", ticker, start_d, end_d, refresh)
    try:
        result = _historical_engine.get_historical_prices(
            ticker,
            from_date=start_d,
            to_date=end_d,
            timeframe=timeframe,
            asset_class=asset_class,
            refresh=refresh,
        )
        logger.info(
            "Historical prices OK for %s: %d rows source=%s fallback=%s",
            ticker,
            len(result.get("prices", [])),
            result.get("source"),
            result.get("fallback_used"),
        )
        return result
    except Exception as e:  # noqa: BLE001
        logger.exception("Historical prices FAILED for %s", ticker)
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/delta-strike")
def delta_strike(
    ticker: str = Query(...),
    spot: float = Query(..., description="Underlying spot price"),
    expiry: date = Query(..., description="Option expiry date (YYYY-MM-DD)"),
    right: str = Query("put", description="Option right: put or call"),
    target_delta: float = Query(-0.20, description="Target option delta (put typically negative, call positive)"),
    asof: date = Query(default_factory=date.today, description="As-of date for T (YYYY-MM-DD)"),
    r: float = Query(0.0, description="Risk-free rate (annualized)"),
    q: float = Query(0.0, description="Dividend yield (annualized)"),
    asset_class: str = Query("stocks", description="Asset class: stocks or etf"),
):
    try:
        return engine.find_strike_for_delta(
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
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/strike-premium")
def strike_premium(
    ticker: str = Query(...),
    right: str = Query("put", description="Option right: put or call"),
    target_delta: float = Query(..., description="Target option delta (put typically negative, call positive)"),
    expiry: date | None = Query(None, description="Option expiry date (YYYY-MM-DD); default nearest"),
    asof: date = Query(default_factory=date.today, description="As-of date for T (YYYY-MM-DD)"),
    spot: float | None = Query(None, description="Override spot price"),
    r: float = Query(0.0),
    q: float = Query(0.0),
    asset_class: str = Query("stocks", description="Asset class: stocks or etf"),
):
    try:
        return engine.strike_and_premium_for_delta_right(
            ticker=ticker,
            target_delta=target_delta,
            asof=asof,
            right=right,
            expiry=expiry,
            spot=spot,
            r=r,
            q=q,
            asset_class=asset_class,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/option-premium")
def option_premium(
    ticker: str = Query(...),
    expiry: date = Query(..., description="Option expiry date (YYYY-MM-DD)"),
    strike: float = Query(..., description="Option strike"),
    right: str = Query("put", description="Option right: put or call"),
    asof: date = Query(default_factory=date.today, description="As-of date for T (YYYY-MM-DD)"),
    spot: float | None = Query(None, description="Override spot price"),
    r: float = Query(0.0),
    q: float = Query(0.0),
    asset_class: str = Query("stocks", description="Asset class: stocks or etf"),
):
    try:
        return engine.get_option_premium(
            ticker=ticker,
            expiry=expiry,
            strike=strike,
            right=right,
            asof=asof,
            spot=spot,
            r=r,
            q=q,
            asset_class=asset_class,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/covered-call")
def covered_call(
    ticker: str = Query(...),
    expiry: date = Query(..., description="Option expiry date (YYYY-MM-DD)"),
    asof: date = Query(default_factory=date.today, description="As-of date for T (YYYY-MM-DD)"),
    spot: float | None = Query(None, description="Override spot price"),
    strike: float | None = Query(None, description="Call strike; if omitted, pick from target_delta"),
    target_delta: float = Query(0.20, description="Target call delta (positive), used if strike omitted"),
    shares: int = Query(100, description="Share count (must be multiple of 100)"),
    r: float = Query(0.0),
    q: float = Query(0.0),
    asset_class: str = Query("stocks", description="Asset class: stocks or etf"),
):
    try:
        return engine.covered_call(
            ticker=ticker,
            expiry=expiry,
            asof=asof,
            spot=spot,
            strike=strike,
            target_delta=target_delta,
            shares=shares,
            r=r,
            q=q,
            asset_class=asset_class,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/technicals")
def technicals(
    ticker: str = Query(..., description="Ticker symbol, e.g. META"),
):
    logger.info("Fetching technicals for %s", ticker)
    try:
        result = engine.get_technicals(ticker)
        logger.info("Technicals OK for %s: price=%s date=%s", ticker, result.get("latest_price"), result.get("latest_date"))
        return result
    except Exception as e:  # noqa: BLE001
        logger.exception("Technicals FAILED for %s", ticker)
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/options-chain")
def options_chain(
    ticker: str = Query(..., description="Ticker symbol, e.g. AAPL"),
    weeks_out: int = Query(3, description="Weeks of expirations to return (1-8, default 3)"),
    asset_class: str = Query("stocks", description="Asset class: stocks or etf"),
):
    weeks_out = min(max(weeks_out, 1), 8)
    logger.info("Fetching Nasdaq option chain for %s weeks_out=%d", ticker, weeks_out)
    try:
        result = engine.get_nasdaq_option_chain(ticker, weeks_out=weeks_out, asset_class=asset_class)
        logger.info("Nasdaq option chain OK for %s: %d expirations", ticker, len(result["expirations"]))
        return result
    except Exception as e:  # noqa: BLE001
        logger.exception("Option chain FAILED for %s", ticker)
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/recommendations")
def recommendations(
    ticker: str = Query(..., description="Ticker symbol, e.g. GOOG"),
):
    logger.info("Fetching recommendations for %s", ticker)
    try:
        result = engine.get_recommendations(ticker)
        logger.info("Recommendations OK for %s: %d recs, %d upgrades",
                     ticker, len(result["recommendations"]), len(result["upgrades_downgrades"]))
        return result
    except Exception as e:  # noqa: BLE001
        logger.exception("Recommendations FAILED for %s", ticker)
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/analyst-targets")
def analyst_targets(
    ticker: str = Query(..., description="Ticker symbol, e.g. MSFT"),
):
    logger.info("Fetching analyst targets for %s", ticker)
    try:
        result = engine.get_analyst_targets(ticker)
        logger.info("Analyst targets OK for %s: mean=%s", ticker, result.get("target_mean"))
        return result
    except Exception as e:  # noqa: BLE001
        logger.exception("Analyst targets FAILED for %s", ticker)
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/news")
def news(
    ticker: str = Query(..., description="Ticker symbol, e.g. TSLA"),
    asset_class: str = Query("stocks", description="Asset class: stocks or etf"),
):
    logger.info("Fetching news for %s", ticker)
    try:
        result = engine.get_news(ticker, asset_class=asset_class)
        logger.info(
            "News OK for %s: %d articles, sector=%s industry=%s",
            ticker,
            len(result["news"]),
            (result.get("macro") or {}).get("sector"),
            (result.get("macro") or {}).get("industry"),
        )
        return result
    except Exception as e:  # noqa: BLE001
        logger.exception("News FAILED for %s", ticker)
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/nasdaq-analyst-targets")
def nasdaq_analyst_targets(
    ticker: str = Query(..., description="Ticker symbol, e.g. MSFT"),
    asset_class: str = Query("stocks", description="Asset class: stocks or etf"),
):
    logger.info("Fetching Nasdaq analyst targets for %s", ticker)
    try:
        result = engine.get_nasdaq_analyst_targets(ticker, asset_class=asset_class)
        logger.info("Nasdaq analyst targets OK for %s: target=%s", ticker, result.get("one_year_target"))
        return result
    except Exception as e:  # noqa: BLE001
        logger.exception("Nasdaq analyst targets FAILED for %s", ticker)
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/analyst-ratings")
def analyst_ratings(
    ticker: str = Query(..., description="Ticker symbol, e.g. NFLX"),
):
    logger.info("Fetching analyst ratings for %s", ticker)
    try:
        result = engine.get_analyst_ratings(ticker)
        logger.info(
            "Analyst ratings OK for %s: consensus=%s target_avg=%s analysts=%s",
            ticker, result.get("consensus"), result.get("price_target_average"), result.get("total_analysts"),
        )
        return result
    except Exception as e:  # noqa: BLE001
        logger.exception("Analyst ratings FAILED for %s", ticker)
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/support-resistance")
def support_resistance(
    ticker: str,
    start: str,
    end: str,
    interval: str = "d",
    pivot_window: int = 2,
    tolerance_pct: float = 0.02,
    min_touches: int = 2,
    max_zones_per_side: int = 6,
):
    try:
        return _historical_engine.get_volume_weighted_support_resistance(
            ticker=ticker,
            start=start,
            end=end,
            interval=interval,
            pivot_window=pivot_window,
            tolerance_pct=tolerance_pct,
            min_touches=min_touches,
            max_zones_per_side=max_zones_per_side,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


def main() -> None:
    import uvicorn

    uvicorn.run("stock_analysis.api:app", host="0.0.0.0", port=8080, reload=False)


if __name__ == "__main__":
    main()
