import threading
from typing import Optional
import yfinance as yf


def _fetch_ticker_data(ticker: str) -> dict:
    """Fetch price, 5-day closes, and OHLC for a single ticker.

    Uses period='1mo' and takes the last 5 trading days to ensure
    complete data.  For Korean (.KS) stocks the most recent bar's
    close can be stale in yfinance history(); we patch it with the
    live regularMarketPrice from ticker.info.
    """
    result = {'price': None, '5d_high': None, '5d_low': None,
              '5d_closes': [], '5d_ohlc': []}
    try:
        t = yf.Ticker(ticker)

        # ── Live price from info (most accurate) ────────────────────────────
        live_price = None
        try:
            info = t.info
            live_price = info.get('regularMarketPrice') or info.get('currentPrice')
            if live_price is not None:
                live_price = float(live_price)
                if live_price <= 0:
                    live_price = None
        except Exception:
            pass

        # Fallback: fast_info
        if live_price is None:
            try:
                p = t.fast_info.last_price
                if p is not None and float(p) > 0:
                    live_price = float(p)
            except Exception:
                pass

        # ── Historical OHLC ─────────────────────────────────────────────────
        hist = t.history(period='1mo')
        if not hist.empty:
            hist = hist.tail(5)

            # Patch the last bar's close with live price if available
            if live_price is not None and len(hist) > 0:
                hist.iloc[-1, hist.columns.get_loc('Close')] = live_price

            result['price'] = float(hist['Close'].iloc[-1])
            result['5d_high'] = float(hist['High'].max())
            result['5d_low'] = float(hist['Low'].min())
            result['5d_closes'] = [float(c) for c in hist['Close'].tolist()]
            for idx, row in hist.iterrows():
                result['5d_ohlc'].append({
                    'date': idx.strftime('%m/%d'),
                    'open': float(row['Open']),
                    'high': float(row['High']),
                    'low':  float(row['Low']),
                    'close': float(row['Close']),
                })

        if live_price is not None:
            result['price'] = live_price
    except Exception:
        pass
    return result


def fetch_fx_rate(fx_ticker: str = 'USDKRW=X') -> tuple:
    """Return (current_rate, avg_3m) for the FX pair.

    The 3-month average is the mean daily close over the trailing ~3 months
    and is used as the reference point for the FX banner. Either element is
    None when unavailable.
    """
    rate = None
    avg_3m = None
    try:
        t = yf.Ticker(fx_ticker)

        try:
            price = t.fast_info.last_price
            if price is not None and float(price) > 0:
                rate = float(price)
        except Exception:
            pass

        hist = t.history(period='3mo')
        if not hist.empty:
            closes = hist['Close'].dropna()
            if len(closes) > 0:
                avg_3m = float(closes.mean())
                if rate is None:
                    rate = float(closes.iloc[-1])
    except Exception:
        pass
    return rate, avg_3m


def fetch_all(tickers: list, fx_ticker: str = 'USDKRW=X') -> tuple:
    """
    Returns (data_dict, fx_rate, fx_avg_3m).
    data_dict maps ticker -> {price, 5d_high, 5d_closes, 5d_ohlc}
    Fetches all tickers concurrently.
    """
    results = {}

    def _fetch_one(ticker):
        results[ticker] = _fetch_ticker_data(ticker)

    threads = [threading.Thread(target=_fetch_one, args=(t,), daemon=True)
               for t in tickers]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    fx_rate, fx_avg_3m = fetch_fx_rate(fx_ticker)
    return results, fx_rate, fx_avg_3m
