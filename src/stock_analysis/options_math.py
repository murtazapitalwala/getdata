from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


def _norm_cdf(x: float) -> float:
    # Standard normal CDF via erf
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass(frozen=True)
class BsInputs:
    s: float  # spot
    k: float  # strike
    t: float  # time to expiry in years
    r: float = 0.0  # risk-free rate
    q: float = 0.0  # dividend yield


def bs_put_price(inp: BsInputs, sigma: float) -> float:
    if inp.t <= 0:
        return max(inp.k - inp.s, 0.0)
    if sigma <= 0:
        # zero vol => forward intrinsic discounted (approx)
        f = inp.s * math.exp((inp.r - inp.q) * inp.t)
        return math.exp(-inp.r * inp.t) * max(inp.k - f, 0.0)

    vsqrt = sigma * math.sqrt(inp.t)
    d1 = (math.log(inp.s / inp.k) + (inp.r - inp.q + 0.5 * sigma * sigma) * inp.t) / vsqrt
    d2 = d1 - vsqrt

    nd1 = _norm_cdf(-d1)
    nd2 = _norm_cdf(-d2)

    pv_k = inp.k * math.exp(-inp.r * inp.t)
    pv_s = inp.s * math.exp(-inp.q * inp.t)
    return pv_k * nd2 - pv_s * nd1


def bs_put_delta(inp: BsInputs, sigma: float) -> float:
    if inp.t <= 0:
        return -1.0 if inp.s < inp.k else 0.0
    if sigma <= 0:
        f = inp.s * math.exp((inp.r - inp.q) * inp.t)
        return -math.exp(-inp.q * inp.t) if f < inp.k else 0.0

    vsqrt = sigma * math.sqrt(inp.t)
    d1 = (math.log(inp.s / inp.k) + (inp.r - inp.q + 0.5 * sigma * sigma) * inp.t) / vsqrt
    return -math.exp(-inp.q * inp.t) * _norm_cdf(-d1)


def implied_vol_put_bisect(
    inp: BsInputs,
    target_price: float,
    *,
    lo: float = 1e-6,
    hi: float = 5.0,
    max_iter: int = 80,
    tol: float = 1e-6,
) -> Optional[float]:
    if target_price <= 0 or inp.s <= 0 or inp.k <= 0 or inp.t <= 0:
        return None

    # Basic no-arb bounds for a European put.
    lower = max(inp.k * math.exp(-inp.r * inp.t) - inp.s * math.exp(-inp.q * inp.t), 0.0)
    upper = inp.k * math.exp(-inp.r * inp.t)
    if not (lower - 1e-9 <= target_price <= upper + 1e-9):
        return None

    f_lo = bs_put_price(inp, lo) - target_price
    f_hi = bs_put_price(inp, hi) - target_price

    if f_lo == 0:
        return lo
    if f_hi == 0:
        return hi

    if f_lo * f_hi > 0:
        # Not bracketed
        return None

    a, b = lo, hi
    fa, fb = f_lo, f_hi
    for _ in range(max_iter):
        m = 0.5 * (a + b)
        fm = bs_put_price(inp, m) - target_price
        if abs(fm) < tol or (b - a) < 1e-6:
            return m
        if fa * fm <= 0:
            b, fb = m, fm
        else:
            a, fa = m, fm

    return 0.5 * (a + b)
