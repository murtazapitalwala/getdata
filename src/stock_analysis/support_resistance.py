from __future__ import annotations

from dataclasses import asdict, dataclass
from math import exp
from statistics import mean
from typing import Any, Dict, List, Literal, Optional


ZoneType = Literal["support", "resistance"]


@dataclass
class Pivot:
    index: int
    date: str
    price: float
    kind: ZoneType
    volume: float
    open: float
    high: float
    low: float
    close: float


@dataclass
class Zone:
    kind: ZoneType
    low: float
    high: float
    center: float
    touches: int
    raw_score: float
    volume_weighted_score: float
    strength: str
    dates: list[str]
    pivot_prices: list[float]
    avg_volume: float
    zone_width_pct: float
    latest_touch_date: str


def _strength_label(score: float) -> str:
    if score >= 12:
        return "strong"
    if score >= 6:
        return "medium"
    return "weak"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def find_pivots(prices: List[Dict[str, Any]], window: int = 2) -> List[Pivot]:
    """
    Detect swing highs/lows from OHLCV bars.

    Pivot low:
      current low is lower than lows of surrounding bars

    Pivot high:
      current high is higher than highs of surrounding bars
    """
    if len(prices) < (window * 2 + 1):
        return []

    pivots: list[Pivot] = []

    for i in range(window, len(prices) - window):
        row = prices[i]

        low = _safe_float(row["low"])
        high = _safe_float(row["high"])

        left = prices[i - window:i]
        right = prices[i + 1:i + 1 + window]
        neighbors = left + right

        is_pivot_low = all(low < _safe_float(x["low"]) for x in neighbors)
        is_pivot_high = all(high > _safe_float(x["high"]) for x in neighbors)

        volume = _safe_float(row.get("volume"), 0.0)

        if is_pivot_low:
            pivots.append(
                Pivot(
                    index=i,
                    date=str(row["date"]),
                    price=low,
                    kind="support",
                    volume=volume,
                    open=_safe_float(row["open"]),
                    high=high,
                    low=low,
                    close=_safe_float(row["close"]),
                )
            )

        if is_pivot_high:
            pivots.append(
                Pivot(
                    index=i,
                    date=str(row["date"]),
                    price=high,
                    kind="resistance",
                    volume=volume,
                    open=_safe_float(row["open"]),
                    high=high,
                    low=low,
                    close=_safe_float(row["close"]),
                )
            )

    return pivots


def _recency_weight(pivot_index: int, total_bars: int, recency_bias: float = 3.0) -> float:
    """
    More recent pivots should matter more.
    Returns roughly between 1.0 and 2.0 depending on recency.
    """
    if total_bars <= 1:
        return 1.0

    normalized = pivot_index / (total_bars - 1)  # older -> 0, recent -> 1
    return 1.0 + (normalized * recency_bias / 3.0)


def _volume_weight(pivot_volume: float, average_volume: float) -> float:
    """
    Volume weighting:
    - 1.0 when around average volume
    - >1.0 when above average volume
    - <1.0 when below average volume
    Clamped so one extreme candle doesn't dominate.
    """
    if average_volume <= 0:
        return 1.0

    ratio = pivot_volume / average_volume
    return max(0.6, min(2.0, ratio))


def _cluster_pivots(
    pivots: List[Pivot],
    kind: ZoneType,
    tolerance_pct: float,
) -> List[List[Pivot]]:
    same_kind = sorted((p for p in pivots if p.kind == kind), key=lambda p: p.price)
    if not same_kind:
        return []

    clusters: list[list[Pivot]] = []

    for pivot in same_kind:
        placed = False

        for cluster in clusters:
            center = mean(p.price for p in cluster)
            tolerance = center * tolerance_pct

            if abs(pivot.price - center) <= tolerance:
                cluster.append(pivot)
                placed = True
                break

        if not placed:
            clusters.append([pivot])

    return clusters


def _dedupe_overlapping_zones(zones: List[Zone], overlap_threshold: float = 0.6) -> List[Zone]:
    """
    Keep the stronger zone when two zones overlap heavily.
    """
    if not zones:
        return []

    zones_sorted = sorted(zones, key=lambda z: z.center)
    kept: list[Zone] = []

    for zone in zones_sorted:
        replaced = False

        for i, kept_zone in enumerate(kept):
            overlap_low = max(zone.low, kept_zone.low)
            overlap_high = min(zone.high, kept_zone.high)

            if overlap_high <= overlap_low:
                continue

            overlap = overlap_high - overlap_low
            smaller_width = min(zone.high - zone.low, kept_zone.high - kept_zone.low)

            if smaller_width <= 0:
                continue

            overlap_ratio = overlap / smaller_width

            if overlap_ratio >= overlap_threshold:
                if zone.volume_weighted_score > kept_zone.volume_weighted_score:
                    kept[i] = zone
                replaced = True
                break

        if not replaced:
            kept.append(zone)

    return sorted(kept, key=lambda z: z.volume_weighted_score, reverse=True)


def build_volume_weighted_zones(
    prices: List[Dict[str, Any]],
    kind: ZoneType,
    pivot_window: int = 2,
    tolerance_pct: float = 0.02,
    min_touches: int = 2,
    max_zones: int = 6,
) -> List[Zone]:
    """
    Build support or resistance zones using:
    - pivot count
    - volume weighting
    - recency weighting
    - compactness bonus
    """
    pivots = find_pivots(prices, window=pivot_window)
    clusters = _cluster_pivots(pivots=pivots, kind=kind, tolerance_pct=tolerance_pct)

    average_volume = mean(_safe_float(x.get("volume"), 0.0) for x in prices) if prices else 0.0
    total_bars = len(prices)

    zones: list[Zone] = []

    for cluster in clusters:
        if len(cluster) < min_touches:
            continue

        pivot_prices = [p.price for p in cluster]
        volumes = [p.volume for p in cluster]

        zone_low = min(pivot_prices)
        zone_high = max(pivot_prices)
        zone_center = mean(pivot_prices)
        zone_width = zone_high - zone_low
        zone_width_pct = (zone_width / zone_center) if zone_center else 0.0

        # Raw score = number of touches
        raw_score = float(len(cluster))

        # Compactness bonus:
        # tighter zones are stronger than wide messy zones
        compactness_bonus = max(0.0, 2.5 - (zone_width_pct * 100.0))

        # Volume-weighted score:
        # each pivot contributes based on volume and recency
        vw_score = 0.0
        for p in cluster:
            vw = _volume_weight(p.volume, average_volume)
            rw = _recency_weight(p.index, total_bars)
            vw_score += vw * rw

        final_score = vw_score + compactness_bonus

        latest_touch = max(cluster, key=lambda p: p.index)

        zones.append(
            Zone(
                kind=kind,
                low=round(zone_low, 2),
                high=round(zone_high, 2),
                center=round(zone_center, 2),
                touches=len(cluster),
                raw_score=round(raw_score, 2),
                volume_weighted_score=round(final_score, 2),
                strength=_strength_label(final_score),
                dates=[p.date for p in cluster],
                pivot_prices=[round(p.price, 2) for p in cluster],
                avg_volume=round(mean(volumes), 2) if volumes else 0.0,
                zone_width_pct=round(zone_width_pct * 100.0, 2),
                latest_touch_date=latest_touch.date,
            )
        )

    zones = _dedupe_overlapping_zones(zones)
    zones = sorted(zones, key=lambda z: z.volume_weighted_score, reverse=True)

    return zones[:max_zones]


def compute_volume_weighted_support_resistance(
    ticker: str,
    prices: List[Dict[str, Any]],
    pivot_window: int = 2,
    tolerance_pct: float = 0.02,
    min_touches: int = 2,
    max_zones_per_side: int = 6,
) -> Dict[str, Any]:
    if not prices:
        raise ValueError("prices cannot be empty")

    supports = build_volume_weighted_zones(
        prices=prices,
        kind="support",
        pivot_window=pivot_window,
        tolerance_pct=tolerance_pct,
        min_touches=min_touches,
        max_zones=max_zones_per_side,
    )

    resistances = build_volume_weighted_zones(
        prices=prices,
        kind="resistance",
        pivot_window=pivot_window,
        tolerance_pct=tolerance_pct,
        min_touches=min_touches,
        max_zones=max_zones_per_side,
    )

    latest = prices[-1]
    avg_volume_all = mean(_safe_float(x.get("volume"), 0.0) for x in prices) if prices else 0.0

    return {
        "ticker": ticker.upper(),
        "asof": str(latest["date"]),
        "latest_close": round(_safe_float(latest["close"]), 2),
        "average_volume": round(avg_volume_all, 2),
        "parameters": {
            "pivot_window": pivot_window,
            "tolerance_pct": tolerance_pct,
            "min_touches": min_touches,
            "max_zones_per_side": max_zones_per_side,
            "volume_weighted": True,
        },
        "supports": [asdict(z) for z in supports],
        "resistances": [asdict(z) for z in resistances],
    }