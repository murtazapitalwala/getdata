from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


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
        import yfinance as yf

        t = ticker.upper()
        df = yf.download(t, period="1y", interval="1d", progress=False, auto_adjust=True)
        if df.empty:
            raise RuntimeError(f"yfinance returned no data for {t}")

        close_col = df["Close"]
        if hasattr(close_col, "columns"):
            close_col = close_col.iloc[:, 0]
        closes = close_col.dropna().tolist()
        latest_date = str(df.index[-1].date())
        latest_price = round(closes[-1], 4)

        return Technicals(
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
