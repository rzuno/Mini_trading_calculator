"""Compare Toss vs Yahoo market data for the watchlist (read-only).

Use this to confirm there is "no discrepancy" before switching the calculator
over to Toss. Prints current price and 5-completed-session High5 from both
providers side by side with the percentage difference.

    python scripts/compare_market_data.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.csv_io import load_positions                      # noqa: E402
from providers.toss_market_provider import TossMarketProvider  # noqa: E402
from providers.yfinance_provider import YahooMarketProvider    # noqa: E402


def _pct(a, b):
    if a is None or b is None or b == 0:
        return None
    return (a - b) / b * 100.0


def main():
    tickers = [p['ticker'] for p in load_positions()]
    print(f"Comparing {len(tickers)} tickers...\n")

    yahoo = YahooMarketProvider()
    try:
        toss = TossMarketProvider.from_env(fx_average_provider=yahoo)
    except ValueError as e:
        print("FAILED:", e)
        return 1

    y_data, y_fx, y_avg = yahoo.fetch_all(tickers)
    t_data, t_fx, t_avg = toss.fetch_all(tickers)

    hdr = f"{'ticker':<11}{'Toss price':>14}{'Yahoo price':>14}{'Δ%':>8}" \
          f"{'Toss H5':>14}{'Yahoo H5':>14}{'Δ%':>8}"
    print(hdr)
    print('-' * len(hdr))
    for t in tickers:
        td, yd = t_data.get(t, {}), y_data.get(t, {})
        tp, yp = td.get('price'), yd.get('price')
        th, yh = td.get('5d_high'), yd.get('5d_high')
        dp, dh = _pct(tp, yp), _pct(th, yh)
        print(f"{t:<11}{str(tp):>14}{str(yp):>14}"
              f"{('%.2f' % dp) if dp is not None else '--':>8}"
              f"{str(th):>14}{str(yh):>14}"
              f"{('%.2f' % dh) if dh is not None else '--':>8}")

    print(f"\nFX  Toss={t_fx}  Yahoo={y_fx}   "
          f"3m-avg Toss={t_avg}  Yahoo={y_avg}")
    print("(Toss 3m-avg is sourced from Yahoo by design — they should match.)")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
