from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Set

from ..http import HttpClient

logger = logging.getLogger(__name__)

AV_BASE_URL = "https://www.alphavantage.co/query"

_DEFAULT_KEYS: List[str] = [
    k.strip()
    for k in os.environ.get(
        "ALPHA_VANTAGE_API_KEYS",
        "1Y3WLNA2ZE44Q4QW,DBM3Y3OYJ4T6O5EV,YVF5CM9YZ32ZFPKP,"
        "LGQOW2T0ANCF4VU7,KUWHQYHLZBSF1PVZ,WER4WIBFO7Z03ULR,"
        "ARWGHZY7FOSREPWR,7GMVDOCOBIQB5U2C,30KA4XFV0LYC84DD,"
        "OFKOMKJYLJSDFHTE",
    ).split(",")
    if k.strip()
]


class DailyLimitExhausted(RuntimeError):
    """Raised when a key hits the 25 req/day ceiling."""


class MinuteLimitHit(RuntimeError):
    """Raised when a key hits the 5 req/min ceiling."""


class AllKeysExhausted(RuntimeError):
    """Raised when every available key has hit its daily limit."""


class KeyManager:
    """Rotate through API keys, skipping daily-exhausted ones."""

    def __init__(self, keys: Optional[List[str]] = None) -> None:
        self._keys: List[str] = keys or list(_DEFAULT_KEYS)
        self._exhausted: Set[str] = set()
        self._exhausted_date: Optional[date] = None  # UTC date when keys were marked
        self._current_idx: int = 0

    def _reset_if_new_day(self) -> None:
        today = datetime.now(timezone.utc).date()
        if self._exhausted_date != today:
            self._exhausted.clear()
            self._exhausted_date = today
            self._current_idx = 0
            logger.info("New UTC day — all API keys reset.")

    def get_key(self) -> str:
        self._reset_if_new_day()
        if len(self._exhausted) >= len(self._keys):
            raise AllKeysExhausted(
                f"All {len(self._keys)} Alpha Vantage keys exhausted for today."
            )
        # Find next non-exhausted key starting from current index
        for _ in range(len(self._keys)):
            key = self._keys[self._current_idx]
            if key not in self._exhausted:
                return key
            self._current_idx = (self._current_idx + 1) % len(self._keys)
        raise AllKeysExhausted("All keys exhausted.")  # shouldn't reach here

    def mark_exhausted(self, key: str) -> None:
        self._reset_if_new_day()
        self._exhausted.add(key)
        self._current_idx = (self._current_idx + 1) % len(self._keys)
        remaining = len(self._keys) - len(self._exhausted)
        logger.warning(
            "Key …%s daily-exhausted. %d key(s) remaining.",
            key[-4:], remaining,
        )

    @property
    def keys_remaining(self) -> int:
        self._reset_if_new_day()
        return len(self._keys) - len(self._exhausted)


# Module-level singleton so state persists across requests.
_key_manager = KeyManager()


@dataclass(frozen=True)
class CloseQuote:
    ticker: str
    trading_date: date
    close: float
    url: str


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


def _latest_value(data: Dict[str, Any], meta_key: str, value_key: str) -> Optional[float]:
    """Extract the most recent indicator value from an Alpha Vantage technical indicator response."""
    ts = data.get(meta_key)
    if not ts or not isinstance(ts, dict):
        return None
    latest_date = next(iter(ts), None)
    if latest_date is None:
        return None
    raw = ts[latest_date].get(value_key)
    if raw is None:
        return None
    try:
        return round(float(raw), 4)
    except (ValueError, TypeError):
        return None


# ── Local indicator math ────────────────────────────────────────────

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
    """Exponential moving average using SMA as seed."""
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
    # Align: ema_fast has (len-fast+1) entries starting at index fast-1,
    # ema_slow has (len-slow+1) entries starting at index slow-1.
    # Offset of ema_fast entries to skip so both align to the same date.
    offset = slow - fast
    macd_line_series = [ema_fast[offset + i] - ema_slow[i] for i in range(len(ema_slow))]
    if len(macd_line_series) < signal:
        return empty
    signal_series = _ema(macd_line_series, signal)
    # signal_series[i] corresponds to macd_line_series[signal-1 + i]
    ml = macd_line_series[-1]
    sl = signal_series[-1]
    return {
        "macd_line": round(ml, 4),
        "signal_line": round(sl, 4),
        "macd_histogram": round(ml - sl, 4),
    }


class AlphaVantage:
    def __init__(self, key_manager: Optional[KeyManager] = None, http: Optional[HttpClient] = None) -> None:
        self._km = key_manager or _key_manager
        self._http = http or HttpClient()

    @staticmethod
    def _classify_error(msg: str) -> str:
        """Return 'daily', 'minute', or 'unknown' based on the AV error message."""
        lower = msg.lower()
        # Burst / per-second / per-minute throttle — NOT a daily exhaustion.
        # AV message: "…spread out…more sparingly (1 request per second)…"
        # or older: "…5 calls per minute…"
        if "per second" in lower or "per minute" in lower or "call frequency" in lower or "sparingly" in lower:
            return "minute"
        # True daily exhaustion (only if none of the above matched)
        if "25 per day" in lower or "daily" in lower:
            return "daily"
        return "unknown"

    def _indicator(self, function: str, symbol: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        consecutive_daily: Dict[str, int] = {}  # key -> count of consecutive "daily" hits
        MAX_DAILY_RETRIES = 3  # retry a key this many times before marking exhausted

        while True:
            key = self._km.get_key()          # may raise AllKeysExhausted
            params: Dict[str, str] = {
                "function": function,
                "symbol": symbol.upper(),
                "apikey": key,
            }
            if extra:
                params.update(extra)
            data, _meta = self._http.get_json(AV_BASE_URL, params=params)

            if "Information" not in data and "Note" not in data:
                return data  # success

            msg = data.get("Information") or data.get("Note") or ""
            kind = self._classify_error(msg)

            if kind == "daily":
                consecutive_daily[key] = consecutive_daily.get(key, 0) + 1
                if consecutive_daily[key] >= MAX_DAILY_RETRIES:
                    self._km.mark_exhausted(key)
                    continue  # move to next key
                # Could be IP-based throttle disguised as daily limit — retry with delay
                logger.info(
                    "Daily-limit msg on key …%s (attempt %d/%d) — sleeping 3 s before retry",
                    key[-4:], consecutive_daily[key], MAX_DAILY_RETRIES,
                )
                time.sleep(3)
                continue  # retry same key

            if kind == "minute":
                logger.info("Rate-limited on key …%s — sleeping 2 s", key[-4:])
                time.sleep(2)
                continue  # retry same key

            # Unknown error — retry with delay before giving up
            consecutive_daily[key] = consecutive_daily.get(key, 0) + 1
            if consecutive_daily[key] >= MAX_DAILY_RETRIES:
                self._km.mark_exhausted(key)
                continue
            logger.info("Unknown AV error on key …%s — sleeping 3 s", key[-4:])
            time.sleep(3)
            continue

    def get_sma(self, ticker: str, time_period: int) -> Optional[float]:
        try:
            data = self._indicator("SMA", ticker, {
                "interval": "daily",
                "time_period": str(time_period),
                "series_type": "close",
            })
        except RuntimeError:
            return None
        return _latest_value(data, "Technical Analysis: SMA", "SMA")

    def get_rsi(self, ticker: str, time_period: int = 14) -> Optional[float]:
        try:
            data = self._indicator("RSI", ticker, {
                "interval": "daily",
                "time_period": str(time_period),
                "series_type": "close",
            })
        except RuntimeError:
            return None
        return _latest_value(data, "Technical Analysis: RSI", "RSI")

    def get_macd(self, ticker: str) -> Dict[str, Optional[float]]:
        try:
            data = self._indicator("MACDEXT", ticker, {
                "interval": "daily",
                "series_type": "close",
            })
        except RuntimeError:
            return {"macd_line": None, "signal_line": None, "macd_histogram": None}
        ts_key = "Technical Analysis: MACDEXT"
        ts = data.get(ts_key)
        if not ts or not isinstance(ts, dict):
            return {"macd_line": None, "signal_line": None, "macd_histogram": None}
        latest_date = next(iter(ts), None)
        if latest_date is None:
            return {"macd_line": None, "signal_line": None, "macd_histogram": None}
        entry = ts[latest_date]
        def _f(key: str) -> Optional[float]:
            v = entry.get(key)
            if v is None:
                return None
            try:
                return round(float(v), 4)
            except (ValueError, TypeError):
                return None
        return {
            "macd_line": _f("MACD"),
            "signal_line": _f("MACD_Signal"),
            "macd_histogram": _f("MACD_Hist"),
        }

    def get_close_on_date(self, ticker: str, trading_date: date) -> CloseQuote:
        """Return the close price for *ticker* on *trading_date* using TIME_SERIES_DAILY."""
        data = self._indicator("TIME_SERIES_DAILY", ticker, {"outputsize": "compact"})
        ts = data.get("Time Series (Daily)")
        if not ts or not isinstance(ts, dict):
            raise RuntimeError(f"No daily price data returned for {ticker} from Alpha Vantage")

        key = trading_date.isoformat()
        if key not in ts:
            available = sorted(ts.keys())[-10:]
            raise RuntimeError(
                f"No close for {ticker} on {key} via Alpha Vantage. "
                f"Recent dates: {', '.join(available)}"
            )
        raw = ts[key].get("4. close")
        if raw is None:
            raise RuntimeError(f"No close value for {ticker} on {key} via Alpha Vantage")
        return CloseQuote(
            ticker=ticker.upper(),
            trading_date=trading_date,
            close=round(float(raw), 4),
            url=f"{AV_BASE_URL}?function=TIME_SERIES_DAILY&symbol={ticker.upper()}&outputsize=compact",
        )

    def get_technicals(self, ticker: str) -> Technicals:
        """Fetch daily prices in ONE API call and compute all indicators locally."""
        t = ticker.upper()
        data = self._indicator("TIME_SERIES_DAILY", ticker, {"outputsize": "compact"})
        ts = data.get("Time Series (Daily)")
        if not ts or not isinstance(ts, dict):
            raise RuntimeError(f"No daily price data returned for {t} from Alpha Vantage")

        # Sort dates ascending and extract close prices
        sorted_dates = sorted(ts.keys())
        closes = [float(ts[d]["4. close"]) for d in sorted_dates]
        latest_date = sorted_dates[-1] if sorted_dates else None
        latest_price = closes[-1] if closes else None

        url = f"{AV_BASE_URL}?function=TIME_SERIES_DAILY&symbol={t}&outputsize=compact"
        urls = [url]

        # SMA 200 needs more history than compact provides; fetch via dedicated endpoint.
        sma_200 = self.get_sma(ticker, 200)
        if sma_200 is not None:
            urls.append(f"{AV_BASE_URL}?function=SMA&symbol={t}&interval=daily&time_period=200&series_type=close")

        return Technicals(
            ticker=t,
            latest_price=latest_price,
            latest_date=latest_date,
            sma_20=_sma(closes, 20),
            sma_50=_sma(closes, 50),
            sma_100=_sma(closes, 100),
            sma_200=sma_200,
            rsi_14=_rsi(closes, 14),
            **_macd(closes),
            urls=urls,
        )
