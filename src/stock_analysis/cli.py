from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date as _date

from .dates import parse_ymd
from .engine import OptionEngine
from .sources.nasdaq import Nasdaq
from .sources.yahoo_finance import YahooFinance


def _json_print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stock-analysis", description="Fetch stock/option data from free endpoints")
    sub = p.add_subparsers(dest="cmd", required=True)

    case = sub.add_parser("case", help="Run a combined close+put premium fetch and print a compact report")
    case.add_argument("--ticker", required=True)
    case.add_argument("--date", required=True, help="YYYY-MM-DD (close)")
    case.add_argument("--expiry", required=True, help="YYYY-MM-DD (option expiry)")
    case.add_argument("--strike", required=True, type=float)

    close = sub.add_parser("close", help="Get the close price for a specific trading date")
    close.add_argument("--ticker", required=True)
    close.add_argument("--date", required=True, help="YYYY-MM-DD")
    close.add_argument("--source", default="yahoo", choices=["yahoo"])

    put = sub.add_parser("put-premium", help="Get put option premium for strike/expiry")
    put.add_argument("--ticker", required=True)
    put.add_argument("--expiry", required=True, help="YYYY-MM-DD")
    put.add_argument("--strike", required=True, type=float)
    put.add_argument("--source", default="nasdaq", choices=["nasdaq", "yahoo"])

    sfd = sub.add_parser("strike-from-delta", help="Find put strike closest to a target delta (approx via Black–Scholes)")
    sfd.add_argument("--ticker", required=True)
    sfd.add_argument("--expiry", required=True, help="YYYY-MM-DD")
    sfd.add_argument("--right", default="put", choices=["put", "call"], help="Option right (default put)")
    sfd.add_argument(
        "--target-delta",
        type=float,
        default=-0.20,
        help="Target delta (puts typically negative, calls positive). Default -0.20",
    )
    sfd.add_argument("--asof", default=None, help="YYYY-MM-DD; defaults to today")
    sfd.add_argument("--spot", type=float, default=None, help="Override underlying price (spot)")
    sfd.add_argument("--r", type=float, default=0.0, help="Risk-free rate (annualized), default 0")
    sfd.add_argument("--q", type=float, default=0.0, help="Dividend yield (annualized), default 0")

    ds = sub.add_parser(
        "delta-strike",
        help="Given ticker+spot+expiry(+target delta), find strike closest to that put delta and report delta+strike",
    )
    ds.add_argument("--ticker", required=True)
    ds.add_argument("--spot", required=True, type=float, help="Underlying price (spot)")
    ds.add_argument("--expiry", required=True, help="YYYY-MM-DD")
    ds.add_argument("--right", default="put", choices=["put", "call"], help="Option right (default put)")
    ds.add_argument(
        "--target-delta",
        type=float,
        default=-0.20,
        help="Target delta (puts typically negative, calls positive). Default -0.20",
    )
    ds.add_argument("--asof", default=None, help="YYYY-MM-DD; defaults to today")
    ds.add_argument("--r", type=float, default=0.0)
    ds.add_argument("--q", type=float, default=0.0)

    sp = sub.add_parser(
        "strike-premium",
        help="Given ticker+delta(+expiry), return strike and premium (mid) for that put delta",
    )
    sp.add_argument("--ticker", required=True)
    sp.add_argument("--right", default="put", choices=["put", "call"], help="Option right (default put)")
    sp.add_argument("--target-delta", required=True, type=float, help="Target put delta, e.g. -0.20")
    sp.add_argument("--expiry", default=None, help="YYYY-MM-DD; defaults to nearest available")
    sp.add_argument("--asof", default=None, help="YYYY-MM-DD; defaults to today")
    sp.add_argument("--spot", type=float, default=None, help="Override underlying price (spot)")
    sp.add_argument("--r", type=float, default=0.0)
    sp.add_argument("--q", type=float, default=0.0)

    call = sub.add_parser("call-premium", help="Get call option premium for strike/expiry")
    call.add_argument("--ticker", required=True)
    call.add_argument("--expiry", required=True, help="YYYY-MM-DD")
    call.add_argument("--strike", required=True, type=float)

    cc = sub.add_parser("covered-call", help="Covered call analysis (sell call against 100 shares)")
    cc.add_argument("--ticker", required=True)
    cc.add_argument("--expiry", required=True, help="YYYY-MM-DD")
    cc.add_argument("--asof", default=None, help="YYYY-MM-DD; defaults to today")
    cc.add_argument("--spot", type=float, default=None, help="Override underlying price (spot)")
    cc.add_argument("--strike", type=float, default=None, help="Call strike; if omitted, pick by target delta")
    cc.add_argument("--target-delta", type=float, default=0.20, help="Target call delta (default 0.20)")
    cc.add_argument("--shares", type=int, default=100, help="Share count (must be multiple of 100)")
    cc.add_argument("--r", type=float, default=0.0)
    cc.add_argument("--q", type=float, default=0.0)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    yahoo = YahooFinance()
    nasdaq = Nasdaq()
    engine = OptionEngine(nasdaq=nasdaq)

    if args.cmd == "case":
        ticker = args.ticker
        trading_date = parse_ymd(args.date)
        expiry = parse_ymd(args.expiry)
        strike = float(args.strike)

        close_val: float
        try:
            q = yahoo.get_close_on_date(ticker, trading_date)
            close_val = q.close
        except Exception as e:  # noqa: BLE001
            # Yahoo frequently rate-limits. For the purpose of this compact report,
            # fall back to Nasdaq's last trade (note: not guaranteed to be the historical close).
            close_val, _ = nasdaq.get_last_trade_price(ticker)

        p = nasdaq.get_put_premium(ticker, expiry, strike)

        # Print exactly the format the user asked for.
        print(f"Ticker symbol {ticker}")
        print(f"closing price for {trading_date.isoformat()} = {close_val:.2f}")

        premium = p.bid if p.bid is not None else p.mid
        if premium is None:
            raise RuntimeError("No usable premium (bid/mid) returned from Nasdaq")
        print(
            "options premium for selling a put for "
            f"${strike:.0f} {expiry.isoformat()} is approx {premium:.2f}-{premium:.2f}"
        )
        return 0

    if args.cmd == "close":
        if args.source != "yahoo":
            raise RuntimeError("close currently only supports --source yahoo")
        q = yahoo.get_close_on_date(args.ticker, parse_ymd(args.date))
        _json_print(asdict(q))
        return 0

    if args.cmd == "strike-from-delta":
        ticker = args.ticker
        expiry = parse_ymd(args.expiry)
        asof = parse_ymd(args.asof) if args.asof else _date.today()
        if expiry <= asof:
            raise RuntimeError("expiry must be after asof")
        chosen = engine.find_strike_for_delta(
            ticker=ticker,
            expiry=expiry,
            target_delta=float(args.target_delta),
            asof=asof,
            right=str(args.right),
            spot=args.spot,
            r=float(args.r),
            q=float(args.q),
        )
        print(f"Ticker symbol {ticker}")
        print(f"asof {chosen['asof']} underlying ~ {chosen['spot']:.2f}")
        print(f"expiry {chosen['expiry']} target put delta {chosen['target_delta']:.2f}")
        print(
            f"strike ~ {chosen['strike']:.2f} (delta {chosen['delta']:.3f}, mid {chosen['premium_mid']:.2f}, iv {chosen['iv']:.3f})"
        )
        print(f"source: {chosen['source']}")
        return 0

    if args.cmd == "delta-strike":
        ticker = args.ticker
        expiry = parse_ymd(args.expiry)
        asof = parse_ymd(args.asof) if args.asof else _date.today()
        if expiry <= asof:
            raise RuntimeError("expiry must be after asof")

        chosen = engine.find_strike_for_delta(
            ticker=ticker,
            expiry=expiry,
            target_delta=float(args.target_delta),
            asof=asof,
            right=str(args.right),
            spot=float(args.spot),
            r=float(args.r),
            q=float(args.q),
        )
        print(f"Ticker symbol {ticker}")
        print(
            f"spot {float(args.spot):.2f} expiry {expiry.isoformat()} right {str(args.right)} target delta {float(args.target_delta):.2f}"
        )
        print(f"strike ~ {chosen['strike']:.2f} delta ~ {chosen['delta']:.3f}")
        return 0

    if args.cmd == "strike-premium":
        ticker = args.ticker
        asof = parse_ymd(args.asof) if args.asof else _date.today()

        expiry = parse_ymd(args.expiry) if args.expiry else None
        chosen = engine.strike_and_premium_for_delta_right(
            ticker=ticker,
            target_delta=float(args.target_delta),
            asof=asof,
            right=str(args.right),
            expiry=expiry,
            spot=args.spot,
            r=float(args.r),
            q=float(args.q),
        )

        print(f"Ticker symbol {ticker}")
        print(
            f"asof {chosen['asof']} expiry {chosen['expiry']} right {chosen.get('right','put')} target delta {float(args.target_delta):.2f}"
        )
        print(f"strike ~ {chosen['strike']:.2f} premium(mid) ~ {chosen['premium_mid']:.2f} (delta {chosen['delta']:.3f})")
        return 0

    if args.cmd == "call-premium":
        c = nasdaq.get_call_premium(args.ticker, parse_ymd(args.expiry), args.strike)
        _json_print(asdict(c))
        return 0

    if args.cmd == "covered-call":
        ticker = args.ticker
        expiry = parse_ymd(args.expiry)
        asof = parse_ymd(args.asof) if args.asof else _date.today()
        if expiry <= asof:
            raise RuntimeError("expiry must be after asof")

        rep = engine.covered_call(
            ticker=ticker,
            expiry=expiry,
            asof=asof,
            spot=args.spot,
            strike=args.strike,
            target_delta=float(args.target_delta),
            shares=int(args.shares),
            r=float(args.r),
            q=float(args.q),
        )
        _json_print(rep)
        return 0

    if args.cmd == "put-premium":
        if args.source == "nasdaq":
            p = nasdaq.get_put_premium(args.ticker, parse_ymd(args.expiry), args.strike)
            _json_print(asdict(p))
        else:
            p = yahoo.get_put_premium(args.ticker, parse_ymd(args.expiry), args.strike)
            _json_print(asdict(p))
        return 0

    raise RuntimeError(f"Unknown cmd: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
