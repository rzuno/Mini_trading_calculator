"""Market-data providers.

Phase 1 of the automation plan: abstract the live market-data source behind a
common interface so the calculator can run on Yahoo Finance (current, verified)
or Toss Securities (new, to be verified with real credentials) by flipping a
config switch — without touching the GUI or the calculation engine.

All providers expose the same ``fetch_all(tickers, fx_ticker)`` contract that the
GUI already consumes, returning ``(data_dict, fx_rate, fx_avg_3m)``.
"""

from providers.market_data_base import MarketDataProvider, empty_stock_data
from providers.yfinance_provider import YahooMarketProvider


def get_provider(config: dict) -> MarketDataProvider:
    """Return the market-data provider selected in config.

    ``market_provider``: "yahoo" (default) or "toss". The Toss provider is
    imported lazily so the app keeps running on Yahoo even if Toss-only
    dependencies or credentials are missing.
    """
    name = (config.get('market_provider') or 'yahoo').lower()
    if name == 'toss':
        # Lazy import: only needed when Toss is actually selected.
        from providers.toss_market_provider import TossMarketProvider
        return TossMarketProvider.from_env(
            fx_average_provider=YahooMarketProvider())
    return YahooMarketProvider()


__all__ = ['MarketDataProvider', 'YahooMarketProvider', 'get_provider',
           'empty_stock_data']
