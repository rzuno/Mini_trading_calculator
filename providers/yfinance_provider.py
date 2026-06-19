"""Yahoo Finance provider — wraps the existing, verified ``core.data_feed``.

This keeps the current behavior intact (so ``main`` stays runnable) and also
serves as the source for the 3-month FX average even when Toss is the primary
provider (Toss has no FX history endpoint; see the migration notes).
"""

from typing import Optional

from core import data_feed
from providers.market_data_base import MarketDataProvider


class YahooMarketProvider(MarketDataProvider):
    name = 'yahoo'

    def fetch_all(self, tickers: list, fx_ticker: str = 'USDKRW=X') -> tuple:
        return data_feed.fetch_all(tickers, fx_ticker)

    def get_quote(self, ticker: str) -> Optional[float]:
        return data_feed._fetch_ticker_data(ticker).get('price')

    def get_fx_rate(self, fx_ticker: str = 'USDKRW=X') -> Optional[float]:
        rate, _ = data_feed.fetch_fx_rate(fx_ticker)
        return rate

    def fx_3m_average(self, fx_ticker: str = 'USDKRW=X') -> Optional[float]:
        """Trailing 3-month USD/KRW mean — the piece Toss cannot provide."""
        _, avg = data_feed.fetch_fx_rate(fx_ticker)
        return avg
