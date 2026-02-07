from __future__ import annotations

from datetime import date

from fastapi import FastAPI, HTTPException, Query

from .engine import OptionEngine

app = FastAPI(title="Stock Analysis API", version="0.1.0", servers=[{"url": "https://getdata-uufz.onrender.com"}])
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
    target_delta: float = Query(-0.20, description="Target put delta (negative), e.g. -0.20"),
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
            spot=spot,
            r=r,
            q=q,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/strike-premium")
def strike_premium(
    ticker: str = Query(...),
    target_delta: float = Query(..., description="Target put delta (negative), e.g. -0.20"),
    expiry: date | None = Query(None, description="Option expiry date (YYYY-MM-DD); default nearest"),
    asof: date = Query(default_factory=date.today, description="As-of date for T (YYYY-MM-DD)"),
    spot: float | None = Query(None, description="Override spot price"),
    r: float = Query(0.0),
    q: float = Query(0.0),
):
    try:
        return engine.strike_and_premium_for_delta(
            ticker=ticker,
            target_delta=target_delta,
            asof=asof,
            expiry=expiry,
            spot=spot,
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
