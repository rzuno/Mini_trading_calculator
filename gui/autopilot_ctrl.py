"""Daily 443 autopilot controller — the bridge between the pure engine
(core/autopilot.py) and the running app.

Owns ONE background poll thread for all autopiloted stocks (max 2). Each cycle
touches only the autopiloted ticker (§30.3): price, holdings(symbol), open
orders(symbol), buying power — the main panel's data stays as of the last
Save & Refresh. UI updates are marshaled to the tk thread with root.after.

Modes per stock:
    DRY  — real price feed, virtual orders/fills/cash (PaperBroker). Safe.
    LIVE — real Toss LIMIT/DAY orders. Entered only via a confirm dialog.

Error policy (§30.4): insufficient-buying-power stops the stock's autopilot
(popup + red badge); order-hours-closed backs off placements 5 minutes;
opposite-pending retries next cycle; other rejections back off 60 s.
"""

import os
import re
import time
import threading
from datetime import datetime, timezone, timedelta
import tkinter as tk
from tkinter import messagebox

from core.autopilot import Daily443Engine, POLL_SECONDS, MAX_AUTOPILOT
from core.calc import fmt_order_price, fmt_price

_TZ_KR = timezone(timedelta(hours=9))
_TZ_US = timezone(timedelta(hours=-5))
_LOG_PATH = os.path.join('logs', 'autopilot443.log')

_BACKOFF_HOURS_CLOSED = 300     # seconds
_BACKOFF_OTHER = 60
_TICKS_KEPT = 2400              # ~10h of 15s ticks for the daily chart
_FAIL_ANNOUNCE = 6              # consecutive bad polls (~90s) → one warning

BADGE = {'DRY': ('443 DRY', '#E08000'),
         'LIVE': ('443 LIVE', '#CC0000'),
         'STOP': ('443 STOP', '#880000')}


class PaperBroker:
    """Virtual per-ticker account for DRY RUN: real prices, simulated orders.
    A resting order fills fully the moment the live price crosses its line."""

    def __init__(self, shares=0, avg=0.0, cash=None):
        self.shares = int(shares or 0)
        self.avg = float(avg or 0.0)
        self.cash = cash          # None = unknown (reserve gate disabled)
        self.orders = {}
        self._next = 1

    def on_price(self, price):
        if price is None:
            return
        for oid, o in list(self.orders.items()):
            if o['side'] == 'BUY' and price <= o['price']:
                new = self.shares + o['qty']
                self.avg = ((self.avg * self.shares + o['price'] * o['qty'])
                            / new) if new else 0.0
                self.shares = new
                if self.cash is not None:
                    self.cash -= o['price'] * o['qty']
                del self.orders[oid]
            elif o['side'] == 'SELL' and price >= o['price']:
                self.shares = max(0, self.shares - o['qty'])
                if self.shares == 0:
                    self.avg = 0.0
                if self.cash is not None:
                    self.cash += o['price'] * o['qty']
                del self.orders[oid]

    def place(self, side, price, qty):
        oid = f'paper{self._next}'
        self._next += 1
        self.orders[oid] = {'side': side, 'price': price, 'qty': int(qty)}
        return oid

    def cancel(self, oid):
        self.orders.pop(oid, None)

    def open_orders(self):
        return [{'id': oid, 'side': o['side'], 'price': o['price'],
                 'qty_open': o['qty'], 'filled': 0}
                for oid, o in self.orders.items()]


class AutopilotController:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self._lock = threading.Lock()
        self._slots = {}          # ticker -> slot dict
        self._listeners = {}      # ticker -> [callback(ui_dict)]
        self._units = {'KRW': 0.0, 'USD': 0.0}
        self._thread = None
        self._wake = threading.Event()
        try:
            os.makedirs('logs', exist_ok=True)
        except OSError:
            pass

    # ── Logging (§25) ─────────────────────────────────────────────────────────

    def _log(self, ticker, msg):
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {ticker}  {msg}"
        try:
            with open(_LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except OSError:
            pass

    # ── Enable / disable (UI thread) ──────────────────────────────────────────

    def refresh_units(self):
        """Cache unit cash from the tk vars (tk objects must not be read from
        the poll thread)."""
        self._units['KRW'] = self.app._get_unit_cash('KRW')
        self._units['USD'] = self.app._get_unit_cash('USD')

    def enable(self, ticker):
        if not self.app._auto:
            return False, 'Switch to Toss (auto) mode first.'
        with self._lock:
            if ticker in self._slots:
                return True, 'already on'
            active = [t for t, s in self._slots.items() if not s.get('stopped')]
            if len(active) >= MAX_AUTOPILOT:
                return False, (f'Max {MAX_AUTOPILOT} autopilot stocks '
                               f'({", ".join(active)}). Turn one off first.')
            self._slots[ticker] = {
                'engine': None, 'live': False, 'paper': None,
                'backoff_until': 0.0, 'stopped': False,
                'prev_close': None, 'prev_close_date': None,
                'ticks': [],           # (epoch, price) history for the daily chart
                'fail_n': 0, 'fail_warned': False,
                'ui': {'ticker': ticker, 'state': 'ARMING', 'status': 'arming…',
                       'lines': {}, 'mode': 'DRY', 'price': None},
            }
        self.refresh_units()
        self._log(ticker, 'AUTOPILOT ENABLED (DRY RUN)')
        self._ensure_thread()
        self._wake.set()
        return True, 'arming (dry run)'

    def disable(self, ticker):
        with self._lock:
            slot = self._slots.pop(ticker, None)
        if slot:
            self._log(ticker, 'AUTOPILOT DISABLED (resting orders left as-is)')
        self.root.after(0, self._apply_row_badge, ticker, None)
        self._notify(ticker, None)

    def set_live(self, ticker, live: bool):
        """Flip DRY↔LIVE. Going LIVE discards the paper account (the next
        cycle reads the real one); going DRY re-seeds paper from reality."""
        with self._lock:
            slot = self._slots.get(ticker)
            if not slot:
                return False, 'not enabled'
            slot['live'] = bool(live)
            slot['paper'] = None          # re-seeded lazily in the poll
            if slot.get('stopped'):       # re-arm after a stop
                slot['stopped'] = False
                slot['engine'] = None
        self._log(ticker, f"MODE -> {'LIVE' if live else 'DRY RUN'}")
        self._wake.set()
        return True, ('LIVE — real orders' if live else 'dry run')

    def is_enabled(self, ticker) -> bool:
        return ticker in self._slots

    def is_managing(self, ticker) -> bool:
        """True while the bot may place/cancel orders (blocks the graph's
        manual order buttons)."""
        slot = self._slots.get(ticker)
        return bool(slot) and not slot.get('stopped')

    def active_tickers(self):
        return list(self._slots.keys())

    def ui_state(self, ticker):
        slot = self._slots.get(ticker)
        return dict(slot['ui']) if slot else None

    # ── Panel subscriptions (graph windows) ───────────────────────────────────

    def subscribe(self, ticker, fn):
        self._listeners.setdefault(ticker, []).append(fn)

    def unsubscribe(self, ticker, fn):
        try:
            self._listeners.get(ticker, []).remove(fn)
        except ValueError:
            pass

    def _notify(self, ticker, ui):
        for fn in list(self._listeners.get(ticker, [])):
            try:
                fn(ui)
            except Exception:
                self.unsubscribe(ticker, fn)

    def graph_context(self, ticker):
        """Callables for the chart's 443 panel."""
        return {
            'ticker': ticker,
            'is_enabled': lambda: self.is_enabled(ticker),
            'is_managing': lambda: self.is_managing(ticker),
            'ui_state': lambda: self.ui_state(ticker),
            'enable': lambda: self.enable(ticker),
            'disable': lambda: self.disable(ticker),
            'set_live': lambda live: self.set_live(ticker, live),
            'subscribe': lambda fn: self.subscribe(ticker, fn),
            'unsubscribe': lambda fn: self.unsubscribe(ticker, fn),
        }

    # ── Row badge (card banner) ───────────────────────────────────────────────

    def _find_row(self, ticker):
        for row in self.app.deployed_rows + self.app.empty_rows:
            if row.ticker == ticker:
                return row
        return None

    def _apply_row_badge(self, ticker, badge_key):
        row = self._find_row(ticker)
        if row is None:
            return
        if badge_key is None:
            row.set_autopilot(None)
        else:
            text, color = BADGE[badge_key]
            row.set_autopilot(text, color)

    def on_rows_rebuilt(self):
        """Cards are recreated on every refresh — re-apply badges + units."""
        self.refresh_units()
        for ticker, slot in list(self._slots.items()):
            self._apply_row_badge(ticker, slot['ui'].get('badge_key', 'DRY'))

    # ── Poll thread ───────────────────────────────────────────────────────────

    def _ensure_thread(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def _loop(self):
        while True:
            with self._lock:
                items = [(t, s) for t, s in self._slots.items()
                         if not s.get('stopped')]
            for ticker, slot in items:
                try:
                    self._cycle(ticker, slot)
                except Exception as e:
                    self._log(ticker, f'poll error: {type(e).__name__}: {e}')
                    self._data_failure(ticker, slot, f'{type(e).__name__}')
                    try:
                        self._push_ui(ticker, slot,
                                      status='NO DATA (retrying) — '
                                             f'{type(e).__name__}')
                    except Exception:
                        pass
            self._wake.wait(POLL_SECONDS)
            self._wake.clear()

    # ── Data-problem announcement (§30.7) ─────────────────────────────────────
    # The bot never guesses on missing data: a failed poll skips the whole
    # cycle (no orders placed or cancelled) and retries in POLL_SECONDS.
    # After _FAIL_ANNOUNCE consecutive failures it warns ONCE; it does not
    # disable itself — resting orders stay on Toss and die at market close.

    def _data_failure(self, ticker, slot, why):
        slot['fail_n'] = slot.get('fail_n', 0) + 1
        if slot['fail_n'] >= _FAIL_ANNOUNCE and not slot.get('fail_warned'):
            slot['fail_warned'] = True
            self._log(ticker, f'DATA PROBLEM announced after '
                              f"{slot['fail_n']} failed polls ({why})")

            def popup():
                messagebox.showwarning(
                    '443 Autopilot — data problem',
                    f'{ticker}: Toss data has been unavailable for '
                    f"~{slot['fail_n'] * POLL_SECONDS}s ({why}).\n\n"
                    'The bot is idle and keeps retrying every poll.\n'
                    'No orders are sent while data is missing; resting '
                    'orders stay on Toss (DAY orders die at close).')
            try:
                self.root.after(0, popup)
            except (RuntimeError, tk.TclError):
                pass

    def _data_recovered(self, ticker, slot):
        if slot.get('fail_n'):
            if slot.get('fail_warned'):
                self._log(ticker, f"data recovered after {slot['fail_n']} "
                                  f'failed polls')
            slot['fail_n'] = 0
            slot['fail_warned'] = False

    # ── One poll cycle for one stock ──────────────────────────────────────────

    def _trading_date(self, ticker):
        tz = _TZ_KR if ticker.endswith('.KS') else _TZ_US
        return datetime.now(tz).strftime('%Y-%m-%d')

    def _prev_close(self, prov, ticker, slot):
        """Previous completed close, cached per trading date (1 candle call/day)."""
        today = self._trading_date(ticker)
        if slot['prev_close'] is not None and slot['prev_close_date'] == today:
            return slot['prev_close']
        try:
            bars = prov.get_completed_daily_bars(ticker, 2)
            if bars:
                slot['prev_close'] = bars[-1]['close']
                slot['prev_close_date'] = today
        except Exception:
            pass
        return slot['prev_close']

    def _real_snapshot(self, prov, seq, ticker):
        ccy = 'KRW' if ticker.endswith('.KS') else 'USD'
        price = prov.get_prices([ticker]).get(ticker)

        shares, avg = 0, 0.0
        for it in (prov.get_holdings(seq, ticker) or {}).get('items', []) or []:
            sym = it.get('symbol') or ''
            tick = (sym + '.KS') if it.get('marketCountry') == 'KR' else sym
            if tick == ticker or sym == ticker:
                try:
                    shares = int(round(float(it.get('quantity') or 0)))
                    avg = float(it.get('averagePurchasePrice') or 0)
                except (TypeError, ValueError):
                    pass
                break

        orders = []
        for o in prov.get_open_orders(seq, ticker) or []:
            try:
                p = float(o.get('price'))
            except (TypeError, ValueError):
                continue
            try:
                qty = float(o.get('quantity') or 0)
            except (TypeError, ValueError):
                qty = 0.0
            filled = 0.0
            try:
                filled = float((o.get('execution') or {}).get('filledQuantity') or 0)
            except (TypeError, ValueError):
                pass
            orders.append({'id': o.get('orderId'), 'side': o.get('side'),
                           'price': p, 'qty_open': int(max(0, qty - filled)),
                           'filled': filled})

        bp = prov.get_buying_power(seq, ccy)
        return {'price': price, 'shares': shares, 'avg_cost': avg,
                'orders': orders, 'buying_power': bp}

    def _cycle(self, ticker, slot):
        prov = self.app._toss_provider()
        if prov is None:
            self._push_ui(ticker, slot, status='Toss unavailable')
            return
        seq = self.app._account_seq(prov)
        if not seq:
            self._push_ui(ticker, slot, status='no Toss account')
            return

        real = self._real_snapshot(prov, seq, ticker)
        live = slot['live']

        # Tick history for the daily chart; a missing price counts as a data
        # failure (the engine still runs — it is safe without a price, it just
        # cannot check triggers).
        if real['price'] is not None:
            slot['ticks'].append((time.time(), real['price']))
            del slot['ticks'][:-_TICKS_KEPT]
            self._data_recovered(ticker, slot)
        else:
            self._data_failure(ticker, slot, 'no price from Toss')

        if not live:
            # DRY RUN: seed the paper account from reality once, then let the
            # live price drive virtual fills.
            if slot['paper'] is None:
                slot['paper'] = PaperBroker(real['shares'], real['avg_cost'],
                                            real['buying_power'])
                self._log(ticker,
                          f"paper account seeded: {real['shares']} shares "
                          f"@ {real['avg_cost']:,.0f}, cash {real['buying_power']}")
            paper = slot['paper']
            paper.on_price(real['price'])
            snap = {'price': real['price'], 'shares': paper.shares,
                    'avg_cost': paper.avg, 'orders': paper.open_orders(),
                    'buying_power': paper.cash}
        else:
            snap = real

        ccy = 'KRW' if ticker.endswith('.KS') else 'USD'
        snap['unit_cash'] = self._units.get(ccy) or 0.0
        snap['trading_date'] = self._trading_date(ticker)
        snap['prev_close'] = self._prev_close(prov, ticker, slot)

        if slot['engine'] is None:
            slot['engine'] = Daily443Engine(
                ticker, anchor=None, trading_date=snap['trading_date'],
                log=lambda m, t=ticker: self._log(t, m))
            self._log(ticker, f"engine armed (prev close "
                              f"{snap['prev_close'] or 'unknown'})")
        engine = slot['engine']

        acts = engine.poll(snap)
        self._execute(ticker, slot, prov, seq, engine, acts)
        self._push_ui(ticker, slot, snap=snap)

    # ── Action executor ───────────────────────────────────────────────────────

    def _execute(self, ticker, slot, prov, seq, engine, acts):
        now = time.time()
        for act in acts:
            kind = act[0]
            if kind == 'stop':
                slot['stopped'] = True
                self._on_stopped(ticker, act[1])
            elif kind == 'cancel':
                _, oid, label = act
                self._log(ticker, f'cancel [{label}] {oid}')
                if slot['live']:
                    try:
                        st, body = prov.cancel_order(oid, seq)
                        if st != 200:
                            code = (body.get('error') or {}).get('code') or st
                            self._log(ticker, f'cancel rejected: {code}')
                    except Exception as e:
                        self._log(ticker, f'cancel error: {e}')
                else:
                    slot['paper'].cancel(oid)
                time.sleep(0.3)   # give the cancel a beat before a re-place
            elif kind == 'place':
                _, side, price, qty, label = act
                if now < slot['backoff_until']:
                    self._log(ticker, f'skip place [{label}] (backing off '
                                      f'{slot["backoff_until"] - now:.0f}s)')
                    continue
                wire = fmt_order_price(ticker, price)
                self._log(ticker, f'place [{label}] {side} {qty} @ {wire} '
                                  f"({'LIVE' if slot['live'] else 'dry'})")
                if not slot['live']:
                    slot['paper'].place(side, price, qty)
                    continue
                coid = re.sub(r'[^A-Za-z0-9_-]', '',
                              f"ap443{ticker}{side}")[:26] \
                    + datetime.now().strftime('%H%M%S')
                try:
                    st, body = prov.place_limit_order(
                        ticker, side, wire, qty, seq, client_order_id=coid)
                except Exception as e:
                    self._log(ticker, f'place error: {e}')
                    slot['backoff_until'] = time.time() + _BACKOFF_OTHER
                    continue
                if st == 200 and (body.get('result') or {}).get('orderId'):
                    continue
                code = str((body.get('error') or {}).get('code') or st)
                self._log(ticker, f'place rejected: {code}')
                if 'insufficient' in code and 'buying' in code:
                    engine.stop(f'broker: {code}')
                    slot['stopped'] = True
                    self._on_stopped(ticker, f'broker rejected the buy: {code}')
                elif 'hours' in code or 'closed' in code:
                    slot['backoff_until'] = time.time() + _BACKOFF_HOURS_CLOSED
                elif 'opposite' in code:
                    pass                      # cancel settling; retry next poll
                else:
                    slot['backoff_until'] = time.time() + _BACKOFF_OTHER

    def _on_stopped(self, ticker, reason):
        self._log(ticker, f'AUTOPILOT STOPPED: {reason}')

        def popup():
            self._apply_row_badge(ticker, 'STOP')
            messagebox.showwarning(
                '443 Autopilot stopped',
                f'{ticker}\n\n{reason}\n\nAutopilot is OFF for this stock. '
                f'Any resting SELL was left in place.')
        try:
            self.root.after(0, popup)
        except (RuntimeError, tk.TclError):
            pass

    # ── UI push (marshaled to the tk thread) ──────────────────────────────────

    def _push_ui(self, ticker, slot, snap=None, status=None):
        engine = slot.get('engine')
        stopped = slot.get('stopped')
        badge_key = 'STOP' if stopped else ('LIVE' if slot['live'] else 'DRY')
        ccy = 'KRW' if ticker.endswith('.KS') else 'USD'
        ui = {
            'ticker': ticker,
            'state': engine.state if engine else 'ARMING',
            'status': status or (engine.status if engine else 'arming…'),
            'lines': dict(engine.lines) if engine else {},
            'anchor': engine.anchor if engine else None,
            'anchor_source': engine.anchor_source if engine else 'close',
            'chase_count': engine.chase_count if engine else 0,
            'events': list(engine.events) if engine else [],
            'mode': 'LIVE' if slot['live'] else 'DRY',
            'badge_key': badge_key,
            'price': (snap or {}).get('price'),
            'shares': (snap or {}).get('shares'),
            'avg_cost': (snap or {}).get('avg_cost'),
            'buying_power': (snap or {}).get('buying_power'),
            'unit_cash': self._units.get(ccy) or 0.0,
            'orders': list((snap or {}).get('orders') or []),
            'ticks': list(slot.get('ticks') or []),
            'ts': datetime.now().strftime('%H:%M:%S'),
        }
        slot['ui'] = ui

        def apply():
            if ticker not in self._slots:
                return
            self._apply_row_badge(ticker, badge_key)
            row = self._find_row(ticker)
            if row is not None and ui['price']:
                row.update_live(price=ui['price'])
                row.compute()
            self._notify(ticker, ui)
        try:
            self.root.after(0, apply)
        except (RuntimeError, tk.TclError):
            pass          # app shutting down / no mainloop
