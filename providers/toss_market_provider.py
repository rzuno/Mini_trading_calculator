"""Toss Securities market-data provider (READ-ONLY, Phase 1).

Implements only the market-data reads the calculator needs, strictly from the
verified OpenAPI spec (``Toss_api.json`` v1.1.1):

    POST /oauth2/token            OAuth2 client_credentials -> access_token
    GET  /api/v1/prices           current price (batch, ?symbols=005930,AAPL)
    GET  /api/v1/candles          daily candles (?symbol=&interval=1d&count=)
    GET  /api/v1/exchange-rate    current USD/KRW (rate / midRate)

It calls NO account or order endpoints. The 3-month FX average is delegated to
an injected fallback provider (Yahoo), because Toss exposes only a point-in-time
exchange rate, not an FX history series.

Money fields arrive as strings and are parsed to float for display/calculation.
When real orders are added later, parse prices to Decimal at the broker boundary
(per automation_plan.md §8).
"""

import os
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

from providers.market_data_base import MarketDataProvider, empty_stock_data

DEFAULT_BASE = 'https://openapi.tossinvest.com'
_TIMEOUT = 10

# Approximate market timezones for the "is the latest daily bar still today
# (in-progress)?" check. DST for the US is ignored here on purpose — this is a
# heuristic and must be confirmed against GET /api/v1/market-calendar before the
# Toss provider drives live triggers (automation_plan.md §10/§14).
_TZ_KR = timezone(timedelta(hours=9))
_TZ_US = timezone(timedelta(hours=-5))


def load_env_file(path: str) -> dict:
    """Minimal KEY=VALUE .env reader (no python-dotenv dependency). Lines that
    are blank, comments, or lack '=' are ignored. Surrounding quotes stripped."""
    out = {}
    if not path or not os.path.exists(path):
        return out
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def to_toss_symbol(ticker: str) -> str:
    """Internal ticker -> Toss symbol. KR uses the 6-digit code (drop .KS);
    US uses the plain ticker."""
    return ticker[:-3] if ticker.endswith('.KS') else ticker


class TossMarketProvider(MarketDataProvider):
    name = 'toss'

    def __init__(self, client_id: str, client_secret: str,
                 base: str = DEFAULT_BASE, fx_average_provider=None):
        if not client_id or not client_secret:
            raise ValueError(
                "Toss credentials missing. Put TOSS_CLIENT_ID / "
                "TOSS_CLIENT_SECRET in a .env file (see .env.example).")
        self._client_id = client_id
        self._client_secret = client_secret
        self.base = (base or DEFAULT_BASE).rstrip('/')
        self._fx_avg_provider = fx_average_provider
        self._token = None
        self._token_exp = 0.0
        self._lock = threading.Lock()

    # -- Construction from environment ----------------------------------------

    @classmethod
    def from_env(cls, fx_average_provider=None):
        """Load credentials from .env (preferred) or Toss_api_key.env, both of
        which are gitignored. Expected keys: TOSS_CLIENT_ID, TOSS_CLIENT_SECRET,
        optional TOSS_API_BASE."""
        env = {}
        for candidate in ('.env', 'Toss_api_key.env'):
            env.update(load_env_file(candidate))
        env.update({k: v for k, v in os.environ.items() if k.startswith('TOSS_')})
        return cls(
            client_id=env.get('TOSS_CLIENT_ID', ''),
            client_secret=env.get('TOSS_CLIENT_SECRET', ''),
            base=env.get('TOSS_API_BASE', DEFAULT_BASE),
            fx_average_provider=fx_average_provider)

    # -- Auth ------------------------------------------------------------------

    def _access_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token_exp:
                return self._token
            resp = requests.post(
                f"{self.base}/oauth2/token",
                data={'grant_type': 'client_credentials',
                      'client_id': self._client_id,
                      'client_secret': self._client_secret},
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=_TIMEOUT)
            resp.raise_for_status()
            tok = resp.json()
            self._token = tok['access_token']
            # Refresh a minute early; default to 5 min if expires_in is absent.
            self._token_exp = time.time() + int(tok.get('expires_in', 300)) - 60
            return self._token

    def _get(self, path: str, params: dict) -> dict:
        resp = requests.get(
            f"{self.base}{path}", params=params,
            headers={'Authorization': f'Bearer {self._access_token()}'},
            timeout=_TIMEOUT)
        resp.raise_for_status()
        body = resp.json()
        # BFF common envelope: the payload lives under "result".
        return body.get('result', body)

    # -- Raw reads -------------------------------------------------------------

    def get_prices(self, tickers: list) -> dict:
        """Return {internal_ticker: last_price_float} for up to 200 symbols."""
        if not tickers:
            return {}
        toss_to_internal = {to_toss_symbol(t): t for t in tickers}
        params = {'symbols': ','.join(toss_to_internal.keys())}
        result = self._get('/api/v1/prices', params) or []
        out = {}
        for row in result:
            internal = toss_to_internal.get(row.get('symbol'))
            if internal and row.get('lastPrice') not in (None, ''):
                out[internal] = float(row['lastPrice'])
        return out

    def get_candles(self, ticker: str, count: int = 6, adjusted: bool = True) -> list:
        """Return up to `count` daily candles as
        [{date, open, high, low, close}, ...] in chronological order."""
        result = self._get('/api/v1/candles', {
            'symbol': to_toss_symbol(ticker),
            'interval': '1d',
            'count': count,
            'adjusted': str(bool(adjusted)).lower(),
        }) or {}
        candles = result.get('candles', []) if isinstance(result, dict) else []
        bars = []
        for cd in candles:
            ts = cd.get('timestamp', '')
            bars.append({
                'ts':    ts,
                'date':  ts[5:10].replace('-', '/') if len(ts) >= 10 else ts,
                'open':  float(cd['openPrice']),
                'high':  float(cd['highPrice']),
                'low':   float(cd['lowPrice']),
                'close': float(cd['closePrice']),
            })
        bars.sort(key=lambda b: b['ts'])
        return bars

    def get_exchange_rate(self, base_ccy: str = 'USD',
                          quote_ccy: str = 'KRW') -> Optional[float]:
        """Current USD/KRW using the bank mid rate (매매기준율)."""
        result = self._get('/api/v1/exchange-rate',
                           {'baseCurrency': base_ccy, 'quoteCurrency': quote_ccy})
        if not result:
            return None
        val = result.get('midRate') or result.get('rate')
        return float(val) if val not in (None, '') else None

    # -- Completed-bar helper --------------------------------------------------

    @staticmethod
    def _completed_5(bars: list, currency: str) -> list:
        """Take the 5 most recent COMPLETED daily bars, dropping the latest bar
        when it is still today's in-progress session (heuristic — verify against
        /api/v1/market-calendar before this drives live triggers)."""
        if not bars:
            return []
        tz = _TZ_KR if currency == 'KRW' else _TZ_US
        today = datetime.now(tz).strftime('%Y-%m-%d')
        completed = [b for b in bars if b['ts'][:10] != today]
        # If the only thing dropped was today's bar we use what remains; if the
        # filter removed everything (unexpected), fall back to the raw bars.
        chosen = completed if completed else bars
        return chosen[-5:]

    # -- Integration point -----------------------------------------------------

    def fetch_all(self, tickers: list, fx_ticker: str = 'USDKRW=X') -> tuple:
        data = {t: empty_stock_data() for t in tickers}

        # Prices (single batched call)
        try:
            prices = self.get_prices(tickers)
        except Exception:
            prices = {}
        for t, p in prices.items():
            data[t]['price'] = p

        # Candles (one call per symbol, fetched concurrently)
        def _one(t):
            try:
                bars = self.get_candles(t, count=6)
            except Exception:
                return
            currency = 'KRW' if t.endswith('.KS') else 'USD'
            five = self._completed_5(bars, currency)
            if not five:
                return
            d = data[t]
            d['5d_high']   = max(b['high'] for b in five)
            d['5d_low']    = min(b['low'] for b in five)
            d['5d_closes'] = [b['close'] for b in five]
            d['5d_ohlc']   = [{'date': b['date'], 'open': b['open'],
                               'high': b['high'], 'low': b['low'],
                               'close': b['close']} for b in five]
            # Keep the live price as the source of truth; do NOT patch it into a
            # completed bar (automation_plan.md §10).
            if d['price'] is None and five:
                d['price'] = five[-1]['close']

        threads = [threading.Thread(target=_one, args=(t,), daemon=True)
                   for t in tickers]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        # FX: current rate from Toss; 3-month average from the fallback (Yahoo).
        try:
            fx_rate = self.get_exchange_rate('USD', 'KRW')
        except Exception:
            fx_rate = None
        fx_avg_3m = None
        if self._fx_avg_provider is not None:
            try:
                fx_avg_3m = self._fx_avg_provider.fx_3m_average(fx_ticker)
            except Exception:
                fx_avg_3m = None

        return data, fx_rate, fx_avg_3m

    # -- Finer reads for scripts ----------------------------------------------

    def get_quote(self, ticker: str) -> Optional[float]:
        return self.get_prices([ticker]).get(ticker)

    def get_completed_daily_bars(self, ticker: str, count: int) -> list:
        currency = 'KRW' if ticker.endswith('.KS') else 'USD'
        return self._completed_5(self.get_candles(ticker, count=count + 1),
                                 currency)

    def get_fx_rate(self, fx_ticker: str = 'USDKRW=X') -> Optional[float]:
        return self.get_exchange_rate('USD', 'KRW')
