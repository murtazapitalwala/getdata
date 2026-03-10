from __future__ import annotations

import logging
import subprocess
from datetime import date

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

app = FastAPI(title="Stock Analysis API", version="0.1.0", servers=[{"url": "https://getdata-uufz.onrender.com"}])
engine = OptionEngine()

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
):
    logger.info("Fetching news for %s", ticker)
    try:
        result = engine.get_news(ticker)
        logger.info("News OK for %s: %d articles", ticker, len(result["news"]))
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


def main() -> None:
    import uvicorn

    uvicorn.run("stock_analysis.api:app", host="0.0.0.0", port=8080, reload=False)


if __name__ == "__main__":
    main()
