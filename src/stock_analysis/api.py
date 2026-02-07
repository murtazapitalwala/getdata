from __future__ import annotations

from datetime import date

from fastapi import FastAPI, HTTPException, Query

from .engine import OptionEngine

app = FastAPI(title="Stock Analysis API", version="0.1.0", servers=[{"url": "http://localhost:8080"}])
engine = OptionEngine()

@app.get("/")
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/price")
def get_price(ticker: str = Query(..., description="Ticker symbol, e.g. META")):
    try:
        return engine.get_latest_price(ticker)
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
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))


def main() -> None:
    import uvicorn

    uvicorn.run("stock_analysis.api:app", host="0.0.0.0", port=8080, reload=False)


if __name__ == "__main__":
    main()
