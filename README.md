# Stock analysis (free data fetch)

Fetch historical close prices and options chain data using free, public JSON endpoints (Yahoo Finance).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

## Usage

Historical close for a specific trading date:

```bash
python scripts/fetch_nflx_example.py
```

Or use the CLI:

```bash
python -m stock_analysis close --ticker NFLX --date 2026-02-06 --source yahoo
python -m stock_analysis put-premium --ticker NFLX --expiry 2026-02-13 --strike 80 --source nasdaq

# Find strike closest to a put delta (approx, from mid prices)
python -m stock_analysis strike-from-delta --ticker META --expiry 2026-02-13 --target-delta -0.20 --asof 2026-02-06

# (1) Given ticker+spot+expiry, return strike + achieved delta
python -m stock_analysis delta-strike --ticker META --spot 661.46 --expiry 2026-02-13 --target-delta -0.20 --asof 2026-02-06

# (2) Given ticker+delta(+expiry), return strike + premium(mid)
python -m stock_analysis strike-premium --ticker META --target-delta -0.20 --expiry 2026-02-13 --asof 2026-02-06
```

## FastAPI service (port 8080)

Start the API server:

```bash
"Stock analysis/.venv/bin/python" -m stock_analysis.api
```

Or with uvicorn directly:

```bash
"Stock analysis/.venv/bin/python" -m uvicorn stock_analysis.api:app --host 0.0.0.0 --port 8080
```

Endpoints (all parameters are query/URI parameters):

1) Latest price

```text
GET /price?ticker=NFLX
```

2) Given ticker + spot + expiry, return strike + delta near target

```text
GET /delta-strike?ticker=META&spot=661.46&expiry=2026-02-13&asof=2026-02-06&target_delta=-0.20
```

3) Given ticker + delta, return strike + premium(mid). Expiry optional.

```text
GET /strike-premium?ticker=META&target_delta=-0.20&asof=2026-02-06&expiry=2026-02-13
```

Interactive docs:

```text
http://localhost:8080/docs
```

## Notes

- This uses Yahoo Finance public JSON endpoints (no key). These endpoints are unofficial and can change.
- Options data is fetched from Nasdaq's public JSON endpoint (no key) because Yahoo options endpoints are often rate-limited.
- The CLI prints the exact URLs used for transparency.
