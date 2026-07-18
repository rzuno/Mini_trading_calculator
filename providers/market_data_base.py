"""Common market-data shape and provider interface.

The internal per-stock dict is intentionally identical to what the GUI already
consumes from ``core.data_feed.fetch_all`` so providers are drop-in:

    {
        'price':      float | None,   # live/last price
        '5d_high':    float | None,   # high over the 5 recent sessions
        '5d_low':     float | None,   # low  over the 5 recent sessions
        '5d_closes':  list[float],    # closes of the 5 recent sessions
        '5d_ohlc':    list[dict],     # [{date, open, high, low, close}, ...]
        'prev_close': float | None,   # last COMPLETED session close (the
                                      # vantage point the load hangs off)
    }
"""

from typing import Optional


def empty_stock_data() -> dict:
    return {'price': None, '5d_high': None, '5d_low': None,
            '5d_closes': [], '5d_ohlc': [], 'prev_close': None}


class MarketDataProvider:
    """Interface every provider implements.

    The single integration point used by the GUI is ``fetch_all``; the finer
    methods exist mainly for read-only test/compare scripts.
    """

    name = 'base'

    def fetch_all(self, tickers: list, fx_ticker: str = 'USDKRW=X') -> tuple:
        """Return (data_dict, fx_rate, fx_avg_3m).

        data_dict maps internal ticker -> stock-data dict (see module docstring).
        fx_rate is the current USD/KRW; fx_avg_3m is the trailing 3-month mean
        (used as the FX switch-ladder reference). Either FX value may be None.
        """
        raise NotImplementedError

    # -- Optional finer-grained reads (used by scripts) -----------------------

    def get_quote(self, ticker: str) -> Optional[float]:
        raise NotImplementedError

    def get_completed_daily_bars(self, ticker: str, count: int) -> list:
        raise NotImplementedError

    def get_fx_rate(self, fx_ticker: str = 'USDKRW=X') -> Optional[float]:
        raise NotImplementedError
