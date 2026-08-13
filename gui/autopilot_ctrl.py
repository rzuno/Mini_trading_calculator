"""Autopilot controller — the bridge between the Daily v^ grid engine
(core/autopilot.py) and the running app.

The autopilot IS the v^ grid again (2026-08: the V-Commandos campaign bot
was retired to the card, where its numbers are typed into the broker app by
hand — see the Gearbox manual). What the bot is good at is the tooth cycle:
chasing the curve in real time inside a fixed daily ladder, buying the lower
bar and selling the upper bar, over and over. That is this engine.

Every poll does one obvious sequence:

    read the broker → hand the snapshot to the engine → execute what it
    returns → save if the engine changed → push the payload to the window

One background thread polls each watched stock every POLL_SECONDS, touching
ONLY that ticker: price, holdings(symbol), open orders(symbol), buying power.

**The card grid is not touched by this thread at all.** The cards are the
V-Commandos worksheet for hand trading: they refresh when Save & Refresh is
pressed, and otherwise sit still. The one exception is the card's AUTOPILOT
button colour, which reports WATCH/LIVE — reading a badge is not the same as
rewriting the sheet underneath it.

Modes per stock:
    WATCH — polling, grid and fill detection. Nothing is ever sent.
    LIVE  — real Toss LIMIT/DAY orders, sent on the engine's decision the
            moment an adjacent grid level is crossed. Regular market hours
            only; when the session ends LIVE drops back to WATCH.

Two rules keep this safe around hand trading:

    * **Only our own orders are ever cancelled.** Ownership is an order id
      placed during this run, or the clientOrderId prefix when the broker
      echoes it (per Toss_api.json the OPEN list does NOT — so after a
      restart a resting bot order reads as foreign: never cancelled, never
      duplicated). The grid engine itself cancels nothing: one order at a
      time, and it waits.
    * **The broker is the authority.** The engine reconciles actual shares
      straight to the level targets, so a hand trade folds into the base
      and needs no special case anywhere in this file.

Error policy: a failed poll skips the whole cycle and retries — nothing is
placed on missing data. A run of failures raises an alert on the cockpit's
status line, never a dialog: a modal opened from this thread is
application-modal, lands behind the cockpit, and freezes the card grid with
nothing visible to dismiss.

Engine state persists in data/autopilot_state.json under the bare ticker
key — the same place the grid always saved, so a pre-removal adventure
record restores as its own. Campaign records under 'ticker#VCG' are left
strictly alone for their own future restoration.
"""

import os
import re
import json
import time
import calendar
import threading
from datetime import datetime, date, timezone, timedelta
import tkinter as tk

from core.autopilot import GridEngine, GRID_SCALES, POLL_SECONDS
from core.calc import fmt_order_price

_TZ_KR = timezone(timedelta(hours=9))
_LOG_PATH = os.path.join('logs', 'autopilot443.log')
_STATE_STORE = os.path.join('data', 'autopilot_state.json')

_BACKOFF_INSUFFICIENT = 300     # the broker refused a buy: the army is out
_BACKOFF_HOURS_CLOSED = 300
_BACKOFF_OTHER = 60
_TICKS_KEPT = 7200              # ~10h of 5s ticks for the live chart
_FAIL_ANNOUNCE = 6              # consecutive bad polls (~30s) → one alert
_OHLC_REFRESH_S = 300           # refetch the 5-day candles every 5 minutes
_DAILY_RETRY_S = 60             # throttle completed-bars retries

# Every order this controller creates carries this clientOrderId prefix. It
# is the idempotency key Toss deduplicates on, and best-effort recognition:
# per Toss_api.json the OPEN-orders list omits clientOrderId, so a restart
# orphan is treated as foreign (the engine pauses and waits) rather than
# recognized — the safe direction.
_BOT_CLIENT_ID_PREFIX = 'vhg-ap-'


def is_bot_owned_order(order, runtime_ids=()):
    """Ours only if we can prove it: our clientOrderId prefix, or an id this
    run placed. Everything else belongs to the commander and is untouchable."""
    coid = str((order or {}).get('clientOrderId') or '')
    if coid.startswith(_BOT_CLIENT_ID_PREFIX):
        return True
    oid = (order or {}).get('orderId') or (order or {}).get('id')
    return oid is not None and oid in (runtime_ids or ())


def avg_completed_day_v(bars, trading_date):
    """Mean of the last five COMPLETED days' ranges ((H−L)/L in %). Today's
    in-progress bar is excluded — its range is still growing. This is the
    scale-picking indicator: the commander reads avg/3 as the grid hint.
    THE grid volatility is daily, deliberately — the 5-day span V that
    picks the card's gear measures a different thing (Gearbox manual §17.1)."""
    today_iso = trading_date or ''
    today_md = (today_iso[5:].replace('-', '/')
                if len(today_iso) >= 10 else None)
    done = [b for b in (bars or [])
            if not (str(b.get('ts') or '')[:10] == today_iso
                    or (today_md and b.get('date') == today_md))]
    vs = [(b['high'] - b['low']) / b['low'] * 100.0
          for b in done[-5:] if b.get('low')]
    return (sum(vs) / len(vs)) if vs else None


def merge_live_bar(ohlc, price, trading_date):
    """A copy of the bars with the LIVE price folded into TODAY's bar (close
    follows the tick, high/low stretch to include it), so the candle moves
    with the Now line instead of freezing at fetch time."""
    view = [dict(b) for b in (ohlc or [])]
    if not view or not price:
        return view
    last = view[-1]
    today_iso = trading_date or ''
    today_md = (today_iso[5:].replace('-', '/')
                if len(today_iso) >= 10 else None)
    ts = str(last.get('ts') or '')
    if ts[:10] == today_iso or (today_md and last.get('date') == today_md):
        last['close'] = price
        last['high'] = max(last['high'], price)
        last['low'] = min(last['low'], price)
    return view


# ── Market sessions ──────────────────────────────────────────────────────────

def _us_dst(d: date) -> bool:
    """US DST: second Sunday of March → first Sunday of November."""
    sundays_mar = [w[6] for w in calendar.monthcalendar(d.year, 3) if w[6]]
    sundays_nov = [w[6] for w in calendar.monthcalendar(d.year, 11) if w[6]]
    return (date(d.year, 3, sundays_mar[1]) <= d
            < date(d.year, 11, sundays_nov[0]))


def market_phase(ticker: str, now_utc=None) -> str:
    """'REGULAR' | 'PRE' | 'AFTER' | 'CLOSED' for the stock's home market.
    KR 09:00–15:30 KST; US 09:30–16:00 ET, DST-aware."""
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

    # ── Engine-state persistence ─────────────────────────────────────────────

    def _load_store(self):
        try:
            with open(_STATE_STORE, encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _store_key(ticker):
        """The grid saves under the bare ticker — where it always did.
        Campaign records at 'ticker#VCG' are never read or written here."""
        return ticker

    def _saved_for(self, ticker):
        """A restorable grid record: the bare-ticker key (or the short-lived
        'ticker#GRID' key of the two-strategy era). A campaign record is
        refused — its shape is not a grid adventure."""
        for key in (ticker, f'{ticker}#GRID'):
            saved = self._store.get(key)
            if not isinstance(saved, dict):
                continue
            if saved.get('strategy') == 'V_COMMANDOS_GEARBOX':
                continue
            if 'anchor' in saved or 'grid_ready' in saved or 'step' in saved:
                return saved
        return None

    def _save_state(self, ticker, engine):
        self._store[self._store_key(ticker)] = engine.to_dict()
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

    def _log(self, ticker, msg):
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {ticker}  {msg}"
        try:
            with open(_LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except OSError:
            pass

    def _alert(self, ticker, slot, msg):
        """Say something urgent without taking the application hostage.

        Everything here runs on the poll thread, and `messagebox` from a
        background thread is application-modal: if it lands behind the
        cockpit, every other window — including the card grid — stops
        accepting clicks, with nothing visible to dismiss. So: no dialogs.
        The message goes to the log and to the slot, and the cockpit prints
        it in red on the status line."""
        self._log(ticker, f'ALERT: {msg}')
        if slot is not None:
            slot['alert'] = msg

    # ── Watch / modes (UI thread) ────────────────────────────────────────────

    def refresh_units(self):
        self._units['KRW'] = self.app._get_unit_cash('KRW')
        self._units['USD'] = self.app._get_unit_cash('USD')

    def watch(self, ticker):
        """Start (or keep) watching a stock — bare WATCH, no orders."""
        if not self.app._auto:
            return False, 'Switch to Toss (auto) mode first.'
        with self._lock:
            if ticker in self._slots:
                return True, 'already watching'
            self._slots[ticker] = {
                'engine': None, 'mode': 'WATCH',
                'backoff_until': 0.0,
                'prev_close': None, 'day_v_avg': None,
                'bars': [], 'daily_date': None, 'daily_retry_after': 0.0,
                'ohlc': None, 'ohlc_ts': 0.0, 'ohlc_view': None,
                'ticks': [], 'my_ids': set(),
                'fail_n': 0, 'insuff_warned': False, 'alert': None,
                'ui': {'ticker': ticker, 'state': 'ARMING',
                       'status': 'arming…', 'mode': 'WATCH', 'grid': [],
                       'price': None, 'phase': market_phase(ticker)},
            }
        self.refresh_units()
        self._log(ticker, 'WATCH started')
        self._ensure_thread()
        self._wake.set()
        return True, 'watching'

    def disable(self, ticker):
        """Stop watching. Resting orders are LEFT AS THEY ARE — closing a
        window is not an instruction to trade, and that order may well be one
        you want filled. Remove one you do not want in the Toss app."""
        with self._lock:
            slot = self._slots.pop(ticker, None)
        if slot:
            self._log(ticker, 'autopilot off (any resting orders left as-is)')
            self.root.after(0, self._apply_row_badge, ticker, None)
        self._notify(ticker, None)

    def set_mode(self, ticker, mode):
        """WATCH ↔ LIVE. LIVE only during regular market hours."""
        slot = self._slots.get(ticker)
        if not slot:
            return False, 'not watching'
        if mode not in ('WATCH', 'LIVE'):
            return False, f'unknown mode {mode}'
        if mode == 'LIVE' and market_phase(ticker) != 'REGULAR':
            return False, (f'the market is {market_phase(ticker)} — LIVE runs '
                           f'only during regular hours')
        with self._lock:
            slot['mode'] = mode
            slot['alert'] = None
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

    # ── Grid scale (UI thread, from the cockpit) ─────────────────────────────

    def set_scale(self, ticker, step):
        """Choose the grid spacing for this stock (persists across days).
        Refused while LIVE, and — one grid per day — after the first grid
        trade of the adventure (the engine enforces that part)."""
        slot = self._slots.get(ticker)
        if not slot:
            return False, 'not watching'
        if slot['mode'] == 'LIVE':
            return False, 'turn LIVE off before changing the grid scale'
        engine = slot.get('engine')
        if engine is None:
            return False, 'still arming — try again in a moment'
        ok, msg = engine.set_scale(step)
        self._log(ticker, f'scale request {step * 100:g}%: {msg}')
        if ok:
            self._persist(ticker, engine)
        self._wake.set()
        return ok, msg

    def _persist(self, ticker, engine):
        if getattr(engine, 'dirty', False) and self._save_state(ticker, engine):
            engine.dirty = False

    def cancel_all(self, ticker):
        """Cancel OUR resting orders on this ticker. Orders placed from the
        app or the web are deliberately left alone."""
        prov = self.app._toss_provider()
        if prov is None:
            return False, 'Toss unavailable'
        slot = self._slots.get(ticker) or {}
        try:
            seq = self.app._account_seq(prov)
            orders = prov.get_open_orders(seq, ticker) or []
            mine = [o for o in orders
                    if is_bot_owned_order(o, slot.get('my_ids', ()))]
            n = 0
            for o in mine:
                st, _ = prov.cancel_order(o['orderId'], seq)
                if st == 200:
                    n += 1
                    slot.get('my_ids', set()).discard(o['orderId'])
            self._log(ticker, f'manual cancel: {n}/{len(mine)} of ours '
                              f'({len(orders)} resting in total)')
            self._wake.set()
            return True, f'cancelled {n}/{len(mine)}'
        except Exception as e:
            return False, f'cancel error: {e}'

    # ── Cockpit subscriptions ────────────────────────────────────────────────

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
        """The callables the cockpit is handed."""
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
            'cancel_all': lambda: self.cancel_all(ticker),
            'ohlc': lambda: self._ohlc_for(ticker),
            'set_scale': lambda s: self.set_scale(ticker, s),
            'scales': lambda: list(GRID_SCALES),
        }

    def _ohlc_for(self, ticker):
        slot = self._slots.get(ticker)
        view = slot.get('ohlc_view') if slot else None
        if view:
            return list(view)
        return list(self.app._ohlc_data.get(ticker, []))

    # ── Card badge ───────────────────────────────────────────────────────────

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
        """Cards are recreated on every Save & Refresh — re-apply the badge
        colours and the cached units. Nothing else: the new cards carry the
        commander's own settings, which are none of the bot's business."""
        self.refresh_units()
        for ticker, slot in list(self._slots.items()):
            self._apply_row_badge(ticker, slot['ui'].get('mode', 'WATCH'))

    # ── Poll thread ──────────────────────────────────────────────────────────

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
                    self._data_failure(ticker, slot, type(e).__name__)
                    try:
                        self._push_ui(ticker, slot,
                                      status=f'retrying — {type(e).__name__}')
                    except Exception:
                        pass
            self._wake.wait(POLL_SECONDS)
            self._wake.clear()

    def _data_failure(self, ticker, slot, why):
        """A failed poll skips the whole cycle: nothing is placed on missing
        data. After a run of them, say so on the status line."""
        slot['fail_n'] = slot.get('fail_n', 0) + 1
        if slot['fail_n'] == _FAIL_ANNOUNCE:
            self._alert(ticker, slot,
                        f'no data for ~{slot["fail_n"] * POLL_SECONDS}s '
                        f'({why}) — retrying; nothing is sent meanwhile')

    def _data_recovered(self, ticker, slot):
        if slot.get('fail_n'):
            if slot['fail_n'] >= _FAIL_ANNOUNCE:
                self._log(ticker, f'data recovered after {slot["fail_n"]} '
                                  f'failed polls')
            slot['fail_n'] = 0
            slot['alert'] = None

    # ── One poll cycle ───────────────────────────────────────────────────────

    def _trading_date(self, ticker):
        tz = _TZ_KR if ticker.endswith('.KS') else timezone(timedelta(hours=-5))
        return datetime.now(tz).strftime('%Y-%m-%d')

    def _daily_bars(self, prov, ticker, slot):
        """The last five COMPLETED sessions, fetched once per trading day:
        prev_close (the adventure's reference) and the average day-V — the
        DAILY volatility the grid runs on, whose /3 is the scale hint."""
        today = self._trading_date(ticker)
        if slot['daily_date'] == today:
            return slot['prev_close']
        if time.time() < slot.get('daily_retry_after', 0.0):
            return slot['prev_close']
        try:
            bars = prov.get_completed_daily_bars(ticker, 5)
        except Exception:
            bars = None
        if not bars:
            slot['daily_retry_after'] = time.time() + _DAILY_RETRY_S
            return slot['prev_close']
        slot['prev_close'] = bars[-1]['close']
        slot['bars'] = [dict(b) for b in bars[-5:]]
        slot['day_v_avg'] = avg_completed_day_v(slot['bars'], today)
        slot['daily_date'] = today
        return slot['prev_close']

    def _refresh_ohlc(self, prov, ticker, slot, snap):
        """Keep the cockpit's candles honest: refetch every 5 minutes, and
        fold the live tick into today's bar on every poll."""
        now = time.time()
        if now - slot['ohlc_ts'] >= _OHLC_REFRESH_S:
            slot['ohlc_ts'] = now
            try:
                bars = prov.get_candles(ticker, count=6)
                if bars:
                    slot['ohlc'] = [dict(b) for b in bars[-5:]]
            except Exception:
                slot['ohlc_ts'] = now - _OHLC_REFRESH_S + 60   # retry sooner
        base = slot['ohlc'] or self.app._ohlc_data.get(ticker) or []
        slot['ohlc_view'] = merge_live_bar(base, snap.get('price'),
                                           snap.get('trading_date'))

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
                filled = float(
                    (o.get('execution') or {}).get('filledQuantity') or 0)
            except (TypeError, ValueError):
                pass
            orders.append({'id': o.get('orderId'), 'side': o.get('side'),
                           'price': p, 'qty_open': int(max(0, qty - filled)),
                           'filled': filled,
                           'mine': is_bot_owned_order(o, my_ids)})

        return {'price': price, 'shares': shares, 'avg_cost': avg,
                'orders': orders,
                'buying_power': prov.get_buying_power(seq, ccy)}

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
        if snap['price'] is not None:
            slot['ticks'].append((time.time(), snap['price']))
            del slot['ticks'][:-_TICKS_KEPT]
            self._data_recovered(ticker, slot)
        else:
            self._data_failure(ticker, slot, 'no price from Toss')

        ccy = 'KRW' if ticker.endswith('.KS') else 'USD'
        snap['unit_cash'] = self._units.get(ccy) or 0.0
        snap['trading_date'] = self._trading_date(ticker)
        snap['prev_close'] = self._daily_bars(prov, ticker, slot)
        snap['can_trade'] = (slot['mode'] == 'LIVE')
        snap['phase'] = market_phase(ticker)
        self._refresh_ohlc(prov, ticker, slot, snap)

        if slot['engine'] is None:
            saved = self._saved_for(ticker)
            slot['engine'] = GridEngine(
                ticker, trading_date=snap['trading_date'], saved=saved,
                log=lambda m, t=ticker: self._log(t, m))
            self._log(ticker, 'grid engine armed'
                              + (' (state restored)' if saved else ''))
        engine = slot['engine']

        acts = engine.poll(snap)
        self._execute(ticker, slot, prov, seq, engine, acts)
        if getattr(engine, 'dirty', False):
            # Stay dirty on a failed write so the fills retry next poll.
            if self._save_state(ticker, engine):
                engine.dirty = False
        self._push_ui(ticker, slot, snap=snap)

    # ── Action executor ──────────────────────────────────────────────────────

    def _execute(self, ticker, slot, prov, seq, engine, acts):
        now = time.time()
        for act in acts:
            if slot['mode'] != 'LIVE':
                continue          # WATCH emits none of these; safety net
            if act[0] == 'place':
                _, side, price, qty, label = act
                if now < slot['backoff_until']:
                    self._log(ticker, f'skip [{label}] (backing off '
                                      f'{slot["backoff_until"] - now:.0f}s)')
                    continue
                self._place_real(ticker, slot, prov, seq, engine,
                                 side, price, qty, label)

    def _place_real(self, ticker, slot, prov, seq, engine, side, price, qty,
                    label):
        """Send one real LIMIT/DAY order."""
        wire = fmt_order_price(ticker, price)
        coid = (_BOT_CLIENT_ID_PREFIX
                + re.sub(r'[^A-Za-z0-9]', '', f'{ticker}{side}')[:12]
                + datetime.now().strftime('%H%M%S%f')[:10])
        self._log(ticker, f'place [{label}] {side} {qty} @ {wire}')
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
            # The army really is out. Toss is the wall; back off and keep
            # managing the sell side.
            slot['backoff_until'] = time.time() + _BACKOFF_INSUFFICIENT
            if not slot.get('insuff_warned'):
                slot['insuff_warned'] = True
                self._alert(ticker, slot,
                            f'Toss refused the buy ({code}) — the army cannot '
                            f'fund it. The sell side stays managed; buying '
                            f'retries in 5 minutes.')
        elif 'hours' in code or 'closed' in code:
            slot['backoff_until'] = time.time() + _BACKOFF_HOURS_CLOSED
        elif 'opposite' in code:
            pass              # a foreign order blocks the side; retry next poll
        else:
            slot['backoff_until'] = time.time() + _BACKOFF_OTHER
        return False, f'rejected: {code}'

    # ── UI push (marshaled to the tk thread) ─────────────────────────────────

    def _push_ui(self, ticker, slot, snap=None, status=None):
        """One flat dict describing the adventure right now. Every field is
        read defensively, so a poll can never die on the way to the screen."""
        engine = slot.get('engine')
        badge_key = slot['mode']
        ccy = 'KRW' if ticker.endswith('.KS') else 'USD'
        ui = {
            'ticker': ticker,
            'state': engine.state if engine else 'ARMING',
            'status': status or (engine.status if engine else 'arming…'),
            'trigger': dict(getattr(engine, 'trigger', {}) or {}),
            'trigger_note': getattr(engine, 'trigger_note', None),
            'grid': [dict(g) for g in getattr(engine, 'grid', []) or []],
            'level': getattr(engine, 'current_level', 0),
            'grid_ready': getattr(engine, 'grid_ready', False),
            'anchor': engine.anchor if engine else None,
            'anchor_level': getattr(engine, 'anchor_level', 0),
            'step': getattr(engine, 'step', 0.03),
            'scale_locked': bool(engine
                                 and (getattr(engine, 'bot_fills', 0) > 0
                                      or getattr(engine, '_pending', None))),
            'day_v_avg': slot.get('day_v_avg'),
            'reference_close': getattr(engine, 'reference_close', None),
            'opening_price': getattr(engine, 'opening_price', None),
            'gap_mode': getattr(engine, 'gap_mode', 'NONE'),
            'base_inventory': getattr(engine, 'base_inventory', 0),
            'unit_qty': getattr(engine, 'unit_qty', 0),
            'buy_value': getattr(engine, 'buy_value', 0.0),
            'sell_value': getattr(engine, 'sell_value', 0.0),
            'fills': getattr(engine, 'fills', 0),
            'events': list(getattr(engine, 'events', []) or []),
            'buy_state': getattr(engine, 'buy_state', 'OK') or 'OK',
            'alert': slot.get('alert'),
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
            # The ONLY thing the poll thread touches on the main window is the
            # card's autopilot badge colour. The cards themselves are left
            # alone; they are refreshed by Save & Refresh, not by ticks.
            if self._slots.get(ticker) is not slot:
                return
            self._apply_row_badge(ticker, badge_key)
            self._notify(ticker, ui)
        try:
            self.root.after(0, apply)
        except (RuntimeError, tk.TclError):
            pass          # app shutting down / no mainloop
