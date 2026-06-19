"""Read-only Toss connection test (Phase 1).

Verifies credentials and the market-data reads the calculator needs. Calls NO
account or order endpoints. Run from the repo root:

    python scripts/toss_connection_test.py

Credentials come from a gitignored .env (see .env.example):
    TOSS_CLIENT_ID=...
    TOSS_CLIENT_SECRET=...
    TOSS_API_BASE=https://openapi.tossinvest.com   (optional)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers.toss_market_provider import TossMarketProvider  # noqa: E402
from providers.yfinance_provider import YahooMarketProvider    # noqa: E402


def _mask(s: str) -> str:
    if not s:
        return '(missing)'
    return f"{s[:4]}…({len(s)} chars)"


def main():
    print("== Toss read-only connection test ==")
    try:
        toss = TossMarketProvider.from_env(
            fx_average_provider=YahooMarketProvider())
    except ValueError as e:
        print("FAILED:", e)
        return 1

    print(f"client_id: {_mask(toss._client_id)}   base: {toss.base}")

    # 1. Token
    try:
        toss._access_token()
        print("token: acquired OK (not shown)")
    except Exception as e:
        print("token: FAILED —", type(e).__name__, str(e)[:160])
        return 1

    # 2. Quotes (one KR + one US)
    print("\n-- prices --")
    try:
        prices = toss.get_prices(['005930.KS', 'AAPL'])
        for t in ('005930.KS', 'AAPL'):
            print(f"  {t}: {prices.get(t)}")
    except Exception as e:
        print("  prices FAILED —", type(e).__name__, str(e)[:160])

    # 3. Completed daily candles -> High5 (one KR + one US)
    print("\n-- candles (5 completed) --")
    for t in ('005930.KS', 'AAPL'):
        try:
            bars = toss.get_completed_daily_bars(t, 5)
            if bars:
                hi = max(b['high'] for b in bars)
                lo = min(b['low'] for b in bars)
                print(f"  {t}: {len(bars)} bars  High5={hi}  Low5={lo}  "
                      f"dates={[b['date'] for b in bars]}")
            else:
                print(f"  {t}: no candles returned")
        except Exception as e:
            print(f"  {t}: FAILED —", type(e).__name__, str(e)[:160])

    # 4. FX (current from Toss; 3-month average from Yahoo fallback)
    print("\n-- exchange rate --")
    try:
        print("  USD/KRW (Toss midRate):", toss.get_exchange_rate('USD', 'KRW'))
    except Exception as e:
        print("  FX FAILED —", type(e).__name__, str(e)[:160])
    try:
        print("  3-month avg (Yahoo):",
              YahooMarketProvider().fx_3m_average('USDKRW=X'))
    except Exception as e:
        print("  3m avg FAILED —", type(e).__name__, str(e)[:160])

    print("\nDone. No account or order endpoint was called.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
