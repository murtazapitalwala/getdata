from __future__ import annotations

from datetime import date

from stock_analysis.sources.nasdaq import Nasdaq
from stock_analysis.sources.yahoo_finance import YahooFinance


def main() -> None:
    yahoo = YahooFinance()

    close = yahoo.get_close_on_date("NFLX", date(2026, 2, 6))
    print("Close:", close)

    put = Nasdaq().get_put_premium("NFLX", date(2026, 2, 13), 80.0)
    print("Put:", put)


if __name__ == "__main__":
    main()
