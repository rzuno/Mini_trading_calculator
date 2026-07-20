"""Autopilot controller — the bridge between the pure Daily v^ grid engine
(core/autopilot.py) and the running app.

One background thread polls every watched stock every POLL_SECONDS (10 s),
touching ONLY that ticker: price, holdings(symbol), open orders(symbol),
buying power. The main panel stays refresh-button-driven; only the Autopilot
window (and the card's button color) follow ticks.

ONE strategy: the Daily v^ Linear Weighted Grid (the daily adventure). The
bot is fully self-contained — it does NOT read the card gear system; the
cards remain the manual-trading aid.

Modes per stock:
    WATCH — grid + ticks + fill detection, NO orders from the bot. A due
            transition is exposed as a trigger; the window's Buy/Sell
            buttons can fire it manually (through this controller).
    LIVE  — real Toss LIMIT/DAY orders, fired only when a grid level is
            crossed. Allowed only while the market is in REGULAR hours;
            when the session ends, LIVE drops back to WATCH automatically.

Error policy: a failed poll skips the whole cycle and retries; ~6 straight
failures announce a data problem once. insufficient-buying-power announces
once and backs off 5 min (the SELL side stays managed — no hard stop);
order-hours-closed backs off 5 min; opposite-pending retries next cycle.

Engine state (anchor, level, base inventory, day log) persists in
data/autopilot_state.json so a restart re-arms exactly where it left off.
"""

import os
import re
import json
import time
import calendar
import threading
from datetime import datetime, date, timezone, timedelta
import tkinter as tk
from tkinter import messagebox

from core.autopilot import GridEngine, POLL_SECONDS
from core.calc import fmt_order_price

_TZ_KR = timezone(timedelta(hours=9))
_LOG_PATH = os.path.join('logs', 'autopilot443.log')
_STATE_STORE = os.path.join('data', 'autopilot_state.json')

_BACKOFF_HOURS_CLOSED = 300     # seconds
_BACKOFF_INSUFFICIENT = 300     # broker refused the buy: army is out
_BACKOFF_OTHER = 60
_TICKS_KEPT = 3600              # ~10h of 10s ticks for the live chart
_FAIL_ANNOUNCE = 6              # consecutive bad polls (~60s) → one warning


# ── Market sessions ───────────────────────────────────────────────────────────

def _us_dst(d: date) -> bool:
    """US DST: second Sunday of March → first Sunday of November."""
    sundays_mar = [w[6] for w in calendar.monthcalendar(d.year, 3) if w[6]]
    sundays_nov = [w[6] for w in calendar.monthcalendar(d.year, 11) if w[6]]
    return (date(d.year, 3, sundays_mar[1]) <= d
            < date(d.year, 11, sundays_nov[0]))


def market_phase(ticker: str, now_utc=None) -> str:
    """'REGULAR' | 'PRE' | 'AFTER' | 'CLOSED' for the stock's home market.
    KR: 09:00–15:30 KST regular (pre 08:00, after →20:00).
    US: 09:30–16:00 ET regular, DST-aware (pre 04:00, after →20:00)."""
    now = now_utc or datetime.now(timezone.utc)
    if ticker.endswith('.KS'):
        local = now.astimezone(_TZ_KR)
        pre, op, cl, af = 8 * 60, 9 * 60, 15 * 60 + 30, 20 * 60
    else:
        off = -4 if _us_dst((now + timedelta(hours=-5)).date()) else -5
        local = now.astimezone(timezone(timedelta(hours=off)))
        pre, op, cl, af = 4 * 60, 9 * 60 + 30, 16 * 60, 20 * 60
    if local.weekday() >= 5:
        return 'CLOSED'
    m = local.hour * 60 + local.minute
    if op <= m < cl:
        return 'REGULAR'
    if pre <= m < op:
        return 'PRE'
    if cl <= m < af:
        return 'AFTER'
    return 'CLOSED'


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
        self._store = self._load_store()

    # ── Engine-state persistence (restore across restarts) ───────────────────

    def _load_store(self):
        try:
            with open(_STATE_STORE, encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_state(self, ticker, engine):
        self._store[ticker] = engine.to_dict()
        try:
            os.makedirs('data', exist_ok=True)
            tmp = _STATE_STORE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._store, f, ensure_ascii=False, indent=1)
            os.replace(tmp, _STATE_STORE)
            return True
        except OSError as e:
            self._log(ticker, f'state save failed: {e}')
            return False

    # ── Logging ───────────────────────────────────────────────────────────────

    def _log(self, ticker, msg):
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {ticker}  {msg}"
        try:
            with open(_LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except OSError:
            pass

    # ── Watch / modes (UI thread) ─────────────────────────────────────────────

    def refresh_units(self):
        self._units['KRW'] = self.app._get_unit_cash('KRW')
        self._units['USD'] = self.app._get_unit_cash('USD')

    def watch(self, ticker):
        """Start (or keep) watching a stock — bare WATCH mode, no orders.
        Called when the Autopilot window opens. No stock-count limit."""
        if not self.app._auto:
            return False, 'Switch to Toss (auto) mode first.'
        with self._lock:
            if ticker in self._slots:
                return True, 'already watching'
            self._slots[ticker] = {
                'engine': None, 'mode': 'WATCH',
                'backoff_until': 0.0,
                'prev_close': None, 'prev_close_date': None,
                'ticks': [], 'my_ids': set(),
                'fail_n': 0, 'fail_warned': False, 'insuff_warned': False,
                'ui': {'ticker': ticker, 'state': 'ARMING',
                       'status': 'arming…', 'grid': [], 'mode': 'WATCH',
                       'price': None, 'phase': market_phase(ticker)},
            }
        self.refresh_units()
        self._log(ticker, 'WATCH started')
        self._ensure_thread()
        self._wake.set()
        return True, 'watching'

    def disable(self, ticker):
        with self._lock:
            slot = self._slots.pop(ticker, None)
        if slot:
            self._log(ticker, 'autopilot off (watch stopped; any resting '
                              'orders left as-is)')
        self.root.after(0, self._apply_row_badge, ticker, None)
        self._notify(ticker, None)

    def set_mode(self, ticker, mode):
        """WATCH ↔ LIVE. LIVE only during regular market hours."""
        slot = self._slots.get(ticker)
        if not slot:
            return False, 'not watching'
        if mode not in ('WATCH', 'LIVE'):
            return False, f'unknown mode {mode}'
        if mode == 'LIVE':
            ph = market_phase(ticker)
            if ph != 'REGULAR':
                return False, (f'market is {ph} — LIVE runs only during '
                               f'regular hours')
        with self._lock:
            slot['mode'] = mode
        self._log(ticker, f'MODE → {mode}')
        self._wake.set()
        return True, mode

    def mode_of(self, ticker):
        slot = self._slots.get(ticker)
        return slot['mode'] if slot else None

    def is_enabled(self, ticker) -> bool:
        return ticker in self._slots

    def ui_state(self, ticker):
        slot = self._slots.get(ticker)
        return dict(slot['ui']) if slot else None

    # ── Manual fire / cancel (UI thread, from the Autopilot window) ──────────

    def manual_fire(self, ticker, side):
        """Fire the currently-crossed line manually (WATCH mode). Sends the
        exact same order the bot would send in LIVE."""
        slot = self._slots.get(ticker)
        if not slot:
            return False, 'not watching'
        if slot['mode'] == 'LIVE':
            return False, 'LIVE mode fires by itself'
        with self._lock:
            trig = (slot['ui'].get('trigger') or {}).get(side)
            engine = slot['engine']
        if not trig:
            return False, f'no {side} line crossed'
        prov = self.app._toss_provider()
        if prov is None:
            return False, 'Toss unavailable'
        try:
            seq = self.app._account_seq(prov)
        except Exception as e:
            return False, f'account error: {e}'
        if not seq:
            return False, 'no Toss account'
        ok, msg = self._place_real(ticker, slot, prov, seq,
                                   side, trig['price'], trig['qty'],
                                   trig.get('label', side) + ' (manual)')
        if ok and engine is not None:
            engine.note_manual_order(side, trig['price'], trig['qty'],
                                     level=trig.get('level'),
                                     target=trig.get('target'))
        self._wake.set()
        return ok, msg

    def cancel_all(self, ticker):
        """Cancel every live Toss order for this ticker (ours or not)."""
        prov = self.app._toss_provider()
        if prov is None:
            return False, 'Toss unavailable'
        try:
            seq = self.app._account_seq(prov)
            orders = prov.get_open_orders(seq, ticker) or []
            n = 0
            for o in orders:
                st, _ = prov.cancel_order(o['orderId'], seq)
                if st == 200:
                    n += 1
            self._log(ticker, f'manual cancel: {n}/{len(orders)} orders')
            self._wake.set()
            return True, f'cancelled {n}/{len(orders)}'
        except Exception as e:
            return False, f'cancel error: {e}'

    # ── Panel subscriptions (Autopilot windows) ───────────────────────────────

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
        """Callables for the Autopilot window."""
        return {
            'ticker': ticker,
            'is_enabled': lambda: self.is_enabled(ticker),
            'ui_state': lambda: self.ui_state(ticker),
            'mode_of': lambda: self.mode_of(ticker),
            'set_mode': lambda m: self.set_mode(ticker, m),
            'disable': lambda: self.disable(ticker),
            'market_phase': lambda: market_phase(ticker),
            'subscribe': lambda fn: self.subscribe(ticker, fn),
            'unsubscribe': lambda fn: self.unsubscribe(ticker, fn),
            'manual_fire': lambda side: self.manual_fire(ticker, side),
            'cancel_all': lambda: self.cancel_all(ticker),
            'ohlc': lambda: list(self.app._ohlc_data.get(ticker, [])),
        }

    # ── Card button / badge ───────────────────────────────────────────────────

    def _find_row(self, ticker):
        for row in self.app.deployed_rows + self.app.empty_rows:
            if row.ticker == ticker:
                return row
        return None

    def _apply_row_badge(self, ticker, badge_key):
        row = self._find_row(ticker)
        if row is not None:
            row.set_autopilot(badge_key)

    def on_rows_rebuilt(self):
        """Cards are recreated on every refresh — re-apply statuses + units."""
        self.refresh_units()
        for ticker, slot in list(self._slots.items()):
            self._apply_row_badge(ticker, slot['ui'].get('mode', 'WATCH'))

    # ── Poll thread ───────────────────────────────────────────────────────────

    def _ensure_thread(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def _loop(self):
        while True:
            with self._lock:
                items = list(self._slots.items())
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

    # ── Data-problem announcement ─────────────────────────────────────────────
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
                    'Autopilot — data problem',
                    f'{ticker}: Toss data has been unavailable for '
                    f"~{slot['fail_n'] * POLL_SECONDS}s ({why}).\n\n"
                    'The watcher is idle and keeps retrying every poll.\n'
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
        tz = _TZ_KR if ticker.endswith('.KS') else timezone(timedelta(hours=-5))
        return datetime.now(tz).strftime('%Y-%m-%d')

    def _prev_close(self, prov, ticker, slot):
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

    def _real_snapshot(self, prov, seq, ticker, my_ids):
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
            oid = o.get('orderId')
            orders.append({'id': oid, 'side': o.get('side'),
                           'price': p, 'qty_open': int(max(0, qty - filled)),
                           'filled': filled, 'mine': oid in my_ids})

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

        # LIVE runs only during regular hours; drop to WATCH at the close.
        if slot['mode'] == 'LIVE' and market_phase(ticker) != 'REGULAR':
            slot['mode'] = 'WATCH'
            self._log(ticker, 'market left regular hours — LIVE → WATCH')

        snap = self._real_snapshot(prov, seq, ticker, slot['my_ids'])
        mode = slot['mode']

        if snap['price'] is not None:
            slot['ticks'].append((time.time(), snap['price']))
            del slot['ticks'][:-_TICKS_KEPT]
            self._data_recovered(ticker, slot)
        else:
            self._data_failure(ticker, slot, 'no price from Toss')

        ccy = 'KRW' if ticker.endswith('.KS') else 'USD'
        snap['unit_cash'] = self._units.get(ccy) or 0.0
        snap['trading_date'] = self._trading_date(ticker)
        snap['prev_close'] = self._prev_close(prov, ticker, slot)
        snap['can_trade'] = (mode == 'LIVE')
        snap['phase'] = market_phase(ticker)

        if slot['engine'] is None:
            slot['engine'] = GridEngine(
                ticker, trading_date=snap['trading_date'],
                saved=self._store.get(ticker),
                log=lambda m, t=ticker: self._log(t, m))
            self._log(ticker, 'grid engine armed'
                              + (' (state restored)'
                                 if self._store.get(ticker) else ''))
        engine = slot['engine']

        acts = engine.poll(snap)
        self._execute(ticker, slot, prov, seq, engine, acts)
        if getattr(engine, 'dirty', False):
            # Keep dirty on a failed write so campaign fills and one-time
            # migrations (such as legacy HOLD cleanup) retry next poll.
            if self._save_state(ticker, engine):
                engine.dirty = False
        self._push_ui(ticker, slot, snap=snap)

    # ── Action executor ───────────────────────────────────────────────────────

    def _place_real(self, ticker, slot, prov, seq, side, price, qty, label):
        """Send one real LIMIT/DAY order; shared by LIVE and manual fire."""
        wire = fmt_order_price(ticker, price)
        self._log(ticker, f'place [{label}] {side} {qty} @ {wire}')
        coid = re.sub(r'[^A-Za-z0-9_-]', '',
                      f"ap{ticker}{side}")[:26] \
            + datetime.now().strftime('%H%M%S')
        try:
            st, body = prov.place_limit_order(
                ticker, side, wire, qty, seq, client_order_id=coid)
        except Exception as e:
            self._log(ticker, f'place error: {e}')
            slot['backoff_until'] = time.time() + _BACKOFF_OTHER
            return False, f'order error: {e}'
        order_id = (body.get('result') or {}).get('orderId')
        if st == 200 and order_id:
            slot['my_ids'].add(order_id)
            slot['insuff_warned'] = False
            return True, f'{side} {qty} @ {wire} placed'
        code = str((body.get('error') or {}).get('code') or st)
        self._log(ticker, f'place rejected: {code}')
        if 'insufficient' in code and 'buying' in code:
            # Army is really out: announce once, back off, keep watching —
            # Toss is the wall.
            slot['backoff_until'] = time.time() + _BACKOFF_INSUFFICIENT
            if not slot.get('insuff_warned'):
                slot['insuff_warned'] = True
                self._popup(ticker,
                            f'Toss rejected the buy ({code}).\n\n'
                            'The army cannot fund it. The watcher keeps '
                            'managing the SELL and retries buying every '
                            '5 minutes.')
        elif 'hours' in code or 'closed' in code:
            slot['backoff_until'] = time.time() + _BACKOFF_HOURS_CLOSED
        elif 'opposite' in code:
            pass           # a foreign order blocks the side; retry next poll
        else:
            slot['backoff_until'] = time.time() + _BACKOFF_OTHER
        return False, f'rejected: {code}'

    def _execute(self, ticker, slot, prov, seq, engine, acts):
        mode = slot['mode']
        now = time.time()
        for act in acts:
            kind = act[0]
            if kind == 'notify':
                # Reserved for explicit one-time engine warnings. Reserve
                # exhaustion itself is visual-only in the Autopilot window.
                self._popup(ticker, act[1])
            elif mode != 'LIVE':
                continue           # engine emits none in WATCH; safety net
            elif kind == 'cancel':
                _, oid, label = act
                self._log(ticker, f'cancel [{label}] {oid}')
                try:
                    st, body = prov.cancel_order(oid, seq)
                    if st != 200:
                        code = (body.get('error') or {}).get('code') or st
                        self._log(ticker, f'cancel rejected: {code}')
                except Exception as e:
                    self._log(ticker, f'cancel error: {e}')
                time.sleep(0.3)   # give the cancel a beat before a re-place
            elif kind == 'place':
                _, side, price, qty, label = act
                if now < slot['backoff_until']:
                    self._log(ticker, f'skip place [{label}] (backing off '
                                      f'{slot["backoff_until"] - now:.0f}s)')
                    continue
                self._place_real(ticker, slot, prov, seq,
                                 side, price, qty, label)

    def _popup(self, ticker, msg):
        self._log(ticker, f'ANNOUNCE: {msg}')

        def popup():
            messagebox.showwarning('Autopilot', f'{ticker}\n\n{msg}')
        try:
            self.root.after(0, popup)
        except (RuntimeError, tk.TclError):
            pass

    # ── UI push (marshaled to the tk thread) ──────────────────────────────────

    def _push_ui(self, ticker, slot, snap=None, status=None):
        engine = slot.get('engine')
        badge_key = slot['mode']
        ccy = 'KRW' if ticker.endswith('.KS') else 'USD'
        ui = {
            'ticker': ticker,
            'state': engine.state if engine else 'ARMING',
            'status': status or (engine.status if engine else 'arming…'),
            'trigger': dict(engine.trigger) if engine else {},
            'trigger_note': getattr(engine, 'trigger_note', None),
            # Grid (daily adventure) exposure
            'grid': [dict(g) for g in getattr(engine, 'grid', []) or []],
            'level': getattr(engine, 'current_level', 0),
            'grid_ready': getattr(engine, 'grid_ready', False),
            'anchor': engine.anchor if engine else None,
            'reference_close': getattr(engine, 'reference_close', None),
            'opening_price': getattr(engine, 'opening_price', None),
            'gap_mode': getattr(engine, 'gap_mode', 'NONE'),
            'base_inventory': getattr(engine, 'base_inventory', 0),
            'unit_qty': getattr(engine, 'unit_qty', 0),
            'buy_value': getattr(engine, 'buy_value', 0.0),
            'sell_value': getattr(engine, 'sell_value', 0.0),
            'fills': getattr(engine, 'fills', 0),
            'events': list(engine.events) if engine else [],
            'buy_state': getattr(engine, 'buy_state', 'OK') or 'OK',
            'mode': slot['mode'],
            'badge_key': badge_key,
            'phase': market_phase(ticker),
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
