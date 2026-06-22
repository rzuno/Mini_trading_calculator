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

# Candles are in the tighter MARKET_DATA_CHART rate-limit group, so per-symbol
# candle calls are bounded in concurrency and retried on HTTP 429.
_CHART_CONCURRENCY = 3
_MAX_RETRIES = 4
_BACKOFF = 0.7        # seconds; grows linearly per retry
_BACKOFF_CAP = 5.0

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


def from_toss_symbol(symbol: str, market_country: str) -> str:
    """Toss symbol -> internal ticker. KR 6-digit codes get the .KS suffix."""
    return f"{symbol}.KS" if market_country == 'KR' else symbol


def _to_float(x, default=None):
    try:
        if x in (None, ''):
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


class TossMarketProvider(MarketDataProvider):
    name = 'toss'

    # Toss invalidates a client's previous token whenever a new one is issued,
    # so ALL instances for a given client_id must share ONE token. (A second
    # instance minting its own token — e.g. the Account Info reader — would
    # otherwise 401 the main provider's next request.) Keyed by client_id.
    _token_cache = {}            # client_id -> (token, expiry_epoch)
    _token_lock = threading.Lock()

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

    def _access_token(self, force: bool = False) -> str:
        """Return a shared access token for this client_id, minting a new one
        only when missing/expired (or forced after a 401)."""
        cid = self._client_id
        with TossMarketProvider._token_lock:
            tok, exp = TossMarketProvider._token_cache.get(cid, (None, 0.0))
            if tok and not force and time.time() < exp:
                return tok
            resp = requests.post(
                f"{self.base}/oauth2/token",
                data={'grant_type': 'client_credentials',
                      'client_id': self._client_id,
                      'client_secret': self._client_secret},
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            token = data['access_token']
            # Refresh a minute early; default to 5 min if expires_in is absent.
            exp = time.time() + int(data.get('expires_in', 300)) - 60
            TossMarketProvider._token_cache[cid] = (token, exp)
            return token

    def _get(self, path: str, params: dict, account=None) -> dict:
        """GET with bearer auth. Retries on HTTP 429 (rate limit) using the
        Retry-After / X-RateLimit-Reset header (else linear backoff), and once
        on 401 by re-minting the shared token. Account-scoped reads pass the
        accountSeq via the X-Tossinvest-Account header."""
        last = None
        tried_reauth = False
        for attempt in range(_MAX_RETRIES):
            headers = {'Authorization': f'Bearer {self._access_token()}'}
            if account is not None:
                headers['X-Tossinvest-Account'] = str(account)
            resp = requests.get(
                f"{self.base}{path}", params=params,
                headers=headers, timeout=_TIMEOUT)
            if resp.status_code == 429:
                last = resp
                wait = resp.headers.get('Retry-After') \
                    or resp.headers.get('X-RateLimit-Reset')
                try:
                    wait = float(wait)
                except (TypeError, ValueError):
                    wait = _BACKOFF * (attempt + 1)
                time.sleep(min(wait, _BACKOFF_CAP))
                continue
            if resp.status_code == 401 and not tried_reauth:
                tried_reauth = True
                self._access_token(force=True)   # token stale → re-mint once
                continue
            resp.raise_for_status()
            body = resp.json()
            # BFF common envelope: the payload lives under "result".
            return body.get('result', body)
        if last is not None:
            last.raise_for_status()
        return {}

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

        # Candles: one call per symbol (MARKET_DATA_CHART group). Bound the
        # concurrency so we don't trip the chart rate limit; _get retries 429.
        sem = threading.Semaphore(_CHART_CONCURRENCY)

        def _one(t):
            try:
                with sem:
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

    # -- Account reads (Phase 2, read-only) -----------------------------------

    def get_accounts(self) -> list:
        """List brokerage accounts: [{accountNo, accountSeq, accountType}]."""
        res = self._get('/api/v1/accounts', {})
        return res if isinstance(res, list) else (res.get('result', []) if res else [])

    def get_holdings(self, account_seq, symbol: str = None) -> dict:
        params = {'symbol': to_toss_symbol(symbol)} if symbol else {}
        return self._get('/api/v1/holdings', params, account=account_seq) or {}

    def get_buying_power(self, account_seq, currency: str) -> Optional[float]:
        r = self._get('/api/v1/buying-power', {'currency': currency},
                      account=account_seq) or {}
        return _to_float(r.get('cashBuyingPower'))

    def get_open_orders(self, account_seq, symbol: str = None) -> list:
        """Working (un-filled) orders: PENDING / PARTIAL_FILLED / pending-cancel
        / pending-replace. This is what the API can read back — app-side
        conditional/reserved orders do not appear here until they trigger."""
        params = {'status': 'OPEN'}
        if symbol:
            params['symbol'] = to_toss_symbol(symbol)
        res = self._get('/api/v1/orders', params, account=account_seq) or {}
        return res.get('orders', []) if isinstance(res, dict) else []

    # -- Order placement (LIVE — use deliberately) ----------------------------

    def _post(self, path: str, body: dict = None, account=None) -> tuple:
        """POST with bearer auth + 429/401 handling. Returns (status_code,
        json_body); does NOT raise on 4xx so callers can inspect the business
        error (e.g. order-hours-closed) in body['error']."""
        last = None
        tried_reauth = False
        for attempt in range(_MAX_RETRIES):
            headers = {'Authorization': f'Bearer {self._access_token()}',
                       'Content-Type': 'application/json'}
            if account is not None:
                headers['X-Tossinvest-Account'] = str(account)
            resp = requests.post(f"{self.base}{path}", json=body or {},
                                 headers=headers, timeout=_TIMEOUT)
            if resp.status_code == 429:
                last = resp
                wait = resp.headers.get('Retry-After') \
                    or resp.headers.get('X-RateLimit-Reset')
                try:
                    wait = float(wait)
                except (TypeError, ValueError):
                    wait = _BACKOFF * (attempt + 1)
                time.sleep(min(wait, _BACKOFF_CAP))
                continue
            if resp.status_code == 401 and not tried_reauth:
                tried_reauth = True
                self._access_token(force=True)
                continue
            try:
                data = resp.json()
            except ValueError:
                data = {}
            return resp.status_code, data
        if last is not None:
            try:
                return last.status_code, last.json()
            except ValueError:
                return last.status_code, {}
        return 0, {}

    def place_limit_order(self, ticker, side, price, qty, account_seq,
                          time_in_force='DAY', client_order_id=None) -> tuple:
        """Place a single LIMIT order. side='BUY'|'SELL'. KR price is an integer
        (KRW, on the tick grid); US price is decimal. Returns (status, body);
        on success body['result'] carries the orderId. REAL order — caller is
        responsible for confirmation/guards."""
        payload = {
            'symbol':      to_toss_symbol(ticker),
            'side':        side,
            'orderType':   'LIMIT',
            'timeInForce': time_in_force,
            'quantity':    str(int(qty)),
            'price':       str(price),
        }
        if client_order_id:
            payload['clientOrderId'] = client_order_id
        return self._post('/api/v1/orders', payload, account=account_seq)

    def cancel_order(self, order_id, account_seq) -> tuple:
        """Cancel a working order by id. Returns (status, body)."""
        return self._post(f'/api/v1/orders/{order_id}/cancel', {},
                          account=account_seq)

    def account_snapshot(self) -> Optional[dict]:
        """Read-only snapshot for the GUI's Account Info view: normalized
        holdings + cash buying power per currency. Uses the first account.
        Returns None when there is no account."""
        accts = self.get_accounts()
        if not accts:
            return None
        seq = accts[0].get('accountSeq')
        overview = self.get_holdings(seq)

        items = []
        for it in overview.get('items', []) or []:
            mv = it.get('marketValue') or {}
            pl = it.get('profitLoss') or {}
            items.append({
                'ticker':   from_toss_symbol(it.get('symbol', ''),
                                             it.get('marketCountry', '')),
                'symbol':   it.get('symbol'),
                'name':     it.get('name'),
                'country':  it.get('marketCountry'),
                'currency': it.get('currency'),
                'shares':   _to_float(it.get('quantity'), 0) or 0,
                'avg':      _to_float(it.get('averagePurchasePrice'), 0) or 0,
                'last':     _to_float(it.get('lastPrice'), 0) or 0,
                'value':    _to_float(mv.get('amount')),
                'pl_rate':  _to_float(pl.get('rate')),
            })
        return {
            'account_seq': seq,
            'items':       items,
            'cash_krw':    self.get_buying_power(seq, 'KRW'),
            'cash_usd':    self.get_buying_power(seq, 'USD'),
        }
