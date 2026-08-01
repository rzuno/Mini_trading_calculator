"""Autopilot controller — the bridge between the pure campaign engine
(core/vcommandos.py) and the running app.

One background thread polls every watched stock every POLL_SECONDS (5 s),
touching ONLY that ticker: price, holdings(symbol), open orders(symbol),
buying power. The main panel stays refresh-button-driven; only the autopilot
windows (and the cards' button colors) follow ticks.

ONE strategy: V_COMMANDOS_GEARBOX (`core/vcommandos.py`). It follows exactly
the lines the CARD draws — every card pushes {'gear','exit_tiers','auto'} on each
compute, so whatever the commander reads off the card IS what the bot watches.

The Daily v^ grid was removed on 2026-08-01 to stabilise this one; it is
recoverable from commit e148da6 (see "Daily v^ Grid Autopilot Manual.md").

Modes per stock:
    WATCH — lines + ticks + fill detection, NO orders. A crossed line is
            drawn and named, but nothing is sent.
    LIVE  — real Toss LIMIT/DAY orders, sent by the bot itself the moment a
            line is crossed. Allowed only while the market is in REGULAR
            hours; when the session ends, LIVE drops back to WATCH.

Error policy: a failed poll skips the whole cycle and retries; ~6 straight
failures announce a data problem once. Insufficient buying power is shown on
the line/banner without a popup and is re-checked from fresh broker cash on the
next poll (SELL stays managed); order-hours-closed backs off 5 min and
opposite-pending retries next cycle.

Engine state persists in data/autopilot_state.json under 'ticker#VCG', so a
restart re-arms exactly where it left off (and any saved v^ grid state at the
bare ticker key is left untouched, ready if the grid is ever restored).
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

from core.vcommandos import CampaignEngine, POLL_SECONDS, STRATEGY_ID
from core.calc import (calc_volatility, clamp_gear, effective_entry_gear,
                       fmt_order_price, select_auto_gear)

_TZ_KR = timezone(timedelta(hours=9))
_LOG_PATH = os.path.join('logs', 'autopilot443.log')
_STATE_STORE = os.path.join('data', 'autopilot_state.json')

_BACKOFF_HOURS_CLOSED = 300     # seconds
_BACKOFF_OTHER = 60
_TICKS_KEPT = 7200              # ~10h of 5s ticks for the live chart
_FAIL_ANNOUNCE = 6              # consecutive bad polls (~30s) → one warning
_OHLC_REFRESH_S = 300           # refetch the 5-day candles every 5 minutes
_DAILY_RETRY_S = 60             # throttle completed-bars no-data retries

# Every broker order created by this controller carries this stable prefix.
# Toss returns clientOrderId with order history/open-order rows, so ownership
# survives an application restart instead of depending only on the in-memory
# order ids collected during the current run.
_BOT_CLIENT_ID_PREFIX = 'vcg-ap-'
_CLEANUP_RETRY_S = 5
_CARD_REORDER_MIN_S = 15
_IDEMPOTENCY_TTL_S = 600       # Toss documents clientOrderId for ten minutes
_SUBMISSION_RETRY_S = 5

_TERMINAL_ORDER_STATES = {'FILLED', 'CANCELED', 'REJECTED', 'REPLACED'}


def _client_order_id(order):
    """Read a broker/client order id from raw or normalized order data."""
    return (order.get('client_order_id') or order.get('clientOrderId')
            or order.get('client_id'))


def _broker_order_id(order):
    """Read a broker order id from raw or normalized order data."""
    return order.get('id') or order.get('order_id') or order.get('orderId')


def is_bot_owned_order(order, runtime_ids=()):
    """True only for an order this V-Commandos controller can prove it owns.

    Runtime broker ids cover orders placed since launch; the clientOrderId
    prefix covers those same orders after a restart. Price/side/quantity are
    deliberately never used as ownership evidence because an app/web order can
    legitimately look identical to a bot order.
    """
    oid = _broker_order_id(order)
    coid = str(_client_order_id(order) or '')
    return bool((oid and oid in (runtime_ids or ()))
                or coid.startswith(_BOT_CLIENT_ID_PREFIX))


def _as_float(value, default=0.0):
    try:
        return float(value) if value not in (None, '') else default
    except (TypeError, ValueError):
        return default


def _compact_qty(value):
    """Keep fractional broker quantities intact; render whole shares as int."""
    qty = _as_float(value, 0.0)
    return int(qty) if qty.is_integer() else qty


def _broker_epoch(value):
    """Parse an ISO/epoch broker timestamp for bounded fill correlation."""
    if value in (None, ''):
        return None
    try:
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        return number
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_TZ_KR)
        return parsed.timestamp()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _closed_from_date(ticker, value):
    """Conservative order-creation date for a bounded CLOSED scan.

    Toss applies ``from`` to ``orderedAt`` in KST.  A US DAY order can be
    created before KST midnight and finish after it, so using the Eastern
    session date deliberately widens that query by at most one KST day; the
    caller still filters every row by its exact ``filledAt`` timestamp.
    """
    epoch = _broker_epoch(value)
    if epoch is None:
        return None
    scan_utc = datetime.fromtimestamp(epoch, timezone.utc)
    if ticker.endswith('.KS'):
        scan_local = scan_utc.astimezone(_TZ_KR)
    else:
        eastern_day = (scan_utc + timedelta(hours=-5)).date()
        eastern_offset = -4 if _us_dst(eastern_day) else -5
        scan_local = scan_utc.astimezone(
            timezone(timedelta(hours=eastern_offset)))
    return scan_local.strftime('%Y-%m-%d')


def fill_evidence_key(fill):
    """Match ``CampaignEngine.fill_evidence_key`` without importing internals."""
    if not isinstance(fill, dict):
        return None
    oid = fill.get('order_id') or fill.get('id')
    coid = fill.get('client_order_id')
    side = str(fill.get('side') or '').upper()
    qty = _compact_qty(fill.get('qty') or 0)
    stamp = fill.get('filled_at') or fill.get('fill_time') or ''
    if not (oid or coid or stamp):
        return None
    return '|'.join(str(v or '') for v in (oid, coid, side, qty, stamp))


def normalize_open_order(order, runtime_ids=()):
    """Normalize one Toss working-order row for the campaign engine."""
    execution = order.get('execution') or {}
    filled = _as_float(
        execution.get('filledQuantity', order.get('filled')), 0.0)
    if order.get('quantity') not in (None, ''):
        quantity = _as_float(order.get('quantity'), 0.0)
        qty_open = max(0.0, quantity - filled)
    else:
        qty_open = _as_float(order.get('qty_open'), 0.0)
        if qty_open <= 0 and _as_float(order.get('orderAmount'), 0.0) > 0:
            # Amount-based MARKET orders do not have a knowable share count
            # before execution. A positive sentinel keeps the foreign order
            # visible to the ticker-wide pause without pretending it is a
            # quantity that the campaign may trade or cancel.
            qty_open = 1.0
    oid = _broker_order_id(order)
    coid = _client_order_id(order)
    return {
        'id': oid,
        'client_order_id': coid,
        'side': str(order.get('side') or '').upper(),
        'price': _as_float(order.get('price'), None),
        'qty_open': _compact_qty(max(0.0, qty_open)),
        'filled': _compact_qty(filled),
        'mine': is_bot_owned_order(order, runtime_ids),
        'status': str(order.get('status') or '').upper(),
    }


def normalize_order_detail(order, runtime_ids=()):
    """Normalize exact order detail, including terminal lifecycle evidence."""
    if not isinstance(order, dict) or not order:
        return None
    execution = order.get('execution') or {}
    filled = _as_float(execution.get('filledQuantity', order.get('filled')), 0.0)
    quantity = _as_float(order.get('quantity'), 0.0)
    qty_open = max(0.0, quantity - filled) if quantity > 0 else _as_float(
        order.get('qty_open'), 0.0)
    status = str(order.get('status') or order.get('orderStatus') or '').upper()
    terminal = status in _TERMINAL_ORDER_STATES
    # Some detail implementations omit status but expose a completed quantity
    # or cancellation timestamp.  Both are positive terminal evidence.
    if quantity > 0 and filled >= quantity:
        terminal = True
        status = status or 'FILLED'
    if order.get('canceledAt') or order.get('cancelledAt'):
        terminal = True
        status = status or 'CANCELED'
    filled_at = (execution.get('filledAt') or order.get('filledAt')
                 or order.get('filled_at') or order.get('fill_time'))
    return {
        'id': _broker_order_id(order),
        'order_id': _broker_order_id(order),
        'client_order_id': _client_order_id(order),
        'side': str(order.get('side') or '').upper(),
        'price': _as_float(order.get('price'), None),
        'qty': _compact_qty(quantity),
        'qty_open': _compact_qty(qty_open),
        'filled': _compact_qty(filled),
        'actual_fill_price': _as_float(
            execution.get('averageFilledPrice', order.get('actual_fill_price')),
            None),
        'filled_at': filled_at,
        'status': status,
        'terminal': terminal,
        'mine': is_bot_owned_order(order, runtime_ids),
    }


def normalize_recent_fills(orders, ticker, runtime_ids=(), side=None,
                           filled_after=None, filled_before=None,
                           allow_undated=False):
    """Normalize completed broker rows that contain an actual fill.

    CLOSED history also contains rejected/cancelled orders with zero executed
    quantity. Those are not fills and are omitted. ``side`` narrows a holdings
    delta to BUY or SELL so unrelated history cannot be mistaken for the
    change that triggered the history read.
    """
    wanted_symbol = ticker[:-3] if ticker.endswith('.KS') else ticker
    wanted_side = str(side or '').upper()
    fills = []
    for order in orders or []:
        symbol = str(order.get('symbol') or '')
        if symbol and symbol != wanted_symbol and symbol != ticker:
            continue
        order_side = str(order.get('side') or '').upper()
        if wanted_side and order_side != wanted_side:
            continue
        execution = order.get('execution') or {}
        qty = _as_float(
            execution.get('filledQuantity', order.get('qty')), 0.0)
        if qty <= 0:
            continue
        # A limit price is intent, not execution.  Keep missing actual fill
        # data missing so reconciliation can decline a reload instead of
        # inventing one from ``order.price``.
        price = _as_float(
            execution.get('averageFilledPrice',
                          order.get('actual_fill_price')),
            None)
        filled_at = (execution.get('filledAt') or order.get('filledAt')
                     or order.get('filled_at') or order.get('fill_time'))
        filled_epoch = _broker_epoch(filled_at)
        if filled_epoch is None and not allow_undated:
            continue
        if (filled_epoch is not None and filled_after is not None
                and filled_epoch <= float(filled_after)):
            continue
        if (filled_epoch is not None and filled_before is not None
                and filled_epoch > float(filled_before)):
            continue
        oid = _broker_order_id(order)
        coid = _client_order_id(order)
        fills.append({
            'side': order_side,
            'qty': _compact_qty(qty),
            # ``price``/``filled_at`` are the engine-facing canonical keys;
            # the explicit aliases keep the payload self-describing to UI/log
            # consumers without losing the actual average execution value.
            'price': price,
            'actual_fill_price': price,
            'filled_at': filled_at,
            'fill_time': filled_at,
            'order_id': oid,
            'id': oid,
            'client_order_id': coid,
            'mine': is_bot_owned_order(order, runtime_ids),
            'status': str(order.get('status') or '').upper(),
        })
    fills.sort(key=lambda f: str(f.get('filled_at') or ''), reverse=True)
    return fills


def merge_live_bar(ohlc, price, trading_date):
    """A copy of the 5-day bars with the LIVE price folded into TODAY's bar
    (close follows the tick; high/low stretch to include it), so the candle
    moves with the Now line instead of freezing at fetch time. Bars are
    untouched when today's bar is absent or the price is unknown."""
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
        self._card_reorder_after = 0.0
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

    @staticmethod
    def _store_key(ticker):
        """Campaigns save under 'ticker#VCG'. Any v^ grid state saved at the
        bare ticker key is left alone so the grid can be restored later."""
        return f'{ticker}#VCG'

    def _saved_for(self, ticker):
        """Only ever restore a V-Commandos campaign — a leftover grid record
        under the same ticker must not be read as one."""
        saved = self._store.get(self._store_key(ticker))
        if isinstance(saved, dict) and saved.get('strategy') == STRATEGY_ID:
            return saved
        return None

    def _save_state(self, ticker, engine):
        self._store[self._store_key(ticker)] = engine.to_dict()
        try:
            os.makedirs('data', exist_ok=True)
            tmp = _STATE_STORE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._store, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
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
        Called when the campaign window opens. No stock-count limit."""
        if not self.app._auto:
            return False, 'Switch to Toss (auto) mode first.'
        reactivated = False
        with self._lock:
            if ticker in self._slots:
                slot = self._slots[ticker]
                if not slot.get('stopping'):
                    return True, 'already watching'
                # Cleanup is an order-lifecycle substate, not ownership of the
                # window. Reopening attaches to the same durable engine in
                # WATCH (which never places) and lets normal WATCH cleanup keep
                # cancelling/confirming the accepted bot order.
                slot['stopping'] = False
                slot['mode'] = 'WATCH'
                slot['cleanup_after'] = 0.0
                reactivated = True
            else:
                slot = None
        if reactivated:
            self.refresh_units()
            self._pull_card_config(ticker)
            self._log(ticker, 'WATCH reopened while bot-order cleanup continues')
            self._ensure_thread()
            self._wake.set()
            return True, 'watching; bot-order cleanup continues in WATCH'
        with self._lock:
            self._slots[ticker] = {
                'engine': None, 'mode': 'WATCH', 'card': None,
                'engine_lock': threading.RLock(),
                'vantage_request': None,
                'backoff_until': {'BUY': 0.0, 'SELL': 0.0},
                'backoff_reason': {'BUY': None, 'SELL': None},
                'prev_close': None, 'high5': None, 'vol5': None,
                'bars': [], 'daily_date': None, 'daily_retry_after': 0.0,
                'ohlc': None, 'ohlc_ts': 0.0, 'ohlc_view': None,
                'ticks': [], 'my_ids': set(),
                'broker_shares': None,
                'submission_retry_after': 0.0,
                'card_order_key': None,
                'stopping': False, 'cleanup_after': 0.0,
                'cleanup_started_at': None, 'cleanup_client_empty_reads': 0,
                'cleanup_retire_pending_save': False,
                'fail_n': 0, 'fail_warned': False, 'insuff_warned': False,
                'alert': None,
                'ui': {'ticker': ticker, 'state': 'ARMING',
                       'status': 'arming…', 'mode': 'WATCH', 'lines': {},
                       'price': None, 'phase': market_phase(ticker)},
            }
        self.refresh_units()
        self._pull_card_config(ticker)      # UI thread: read the card now
        self._log(ticker, 'WATCH started')
        self._ensure_thread()
        self._wake.set()
        return True, 'watching'

    @staticmethod
    def _accepted_pending(slot):
        pending = getattr((slot or {}).get('engine'), '_pending', None)
        return bool(isinstance(pending, dict) and pending.get('accepted')
                    and (pending.get('order_id')
                         or pending.get('client_order_id')))

    def disable(self, ticker):
        with self._lock:
            slot = self._slots.get(ticker)
            cleanup = self._accepted_pending(slot)
            if slot and cleanup:
                # Retain the slot as a cleanup task until the broker confirms
                # no bot-owned order remains.  This also closes the race where
                # a placement was already in flight as the window was closed.
                slot['stopping'] = True
                slot['mode'] = 'WATCH'
                slot['cleanup_after'] = 0.0
                slot['cleanup_started_at'] = time.time()
                slot['cleanup_client_empty_reads'] = 0
                slot['cleanup_retire_pending_save'] = False
            elif slot:
                # With no accepted bot identity there is nothing safe or
                # useful to clean. Drop bare WATCH immediately so reopening is
                # a fresh, predictable observation session.
                self._slots.pop(ticker, None)
        if slot:
            self._log(
                ticker,
                ('autopilot off (bot-owned order cleanup queued)' if cleanup
                 else 'autopilot off (watch stopped; no bot order pending)'))
            try:
                self.root.after(0, self._apply_row_badge, ticker, None)
            except (RuntimeError, tk.TclError):
                pass
            if cleanup:
                self._wake.set()
        self._notify(ticker, None)

    # ── Card config: the card IS the campaign strategy (UI thread) ───────────

    def set_card_config(self, ticker, cfg):
        """The card pushes {'gear','exit_tiers','auto'} on every compute —
        the ladder the commander sees is the ladder the bot trades."""
        slot = self._slots.get(ticker)
        if slot is None or not cfg:
            return
        with self._lock:
            slot['card'] = dict(cfg)

    def _pull_card_config(self, ticker):
        row = self._find_row(ticker)
        if row is not None and hasattr(row, 'line_config'):
            try:
                self.set_card_config(ticker, row.line_config())
            except Exception:
                pass

    def set_card_gear(self, ticker, gear=None, tiers=None, auto=None):
        """The cockpit's gear/tier controls write to the CARD — the single
        source of truth — and the change is read straight back so the very
        next poll uses it.

        Picking a gear is a manual choice, so it also drops AUTO. `auto` on
        its own toggles the mode without touching the gear, which is what the
        card's own AUTO/MANUAL button does."""
        def apply():
            row = self._find_row(ticker)
            if row is None:
                return
            try:
                if auto is not None:
                    row.auto_var.set(bool(auto))
                if gear is not None:
                    row.auto_var.set(False)
                    row.gear_var.set(clamp_gear(gear))
                if tiers is not None:
                    for var, on in zip(row.tier_vars, tiers):
                        var.set(bool(on))
            except (tk.TclError, AttributeError):
                return
            self._pull_card_config(ticker)
            self._wake.set()          # let the engine trade on it this second
        try:
            self.root.after(0, apply)
        except (RuntimeError, tk.TclError):
            pass

    # ── Vantage (UI thread, from the campaign window) ────────────────────────

    def set_vantage(self, ticker, price=None, label=''):
        """Pin the LOAD's vantage to a price the commander picked off the
        5-day chart, or (price=None) hand it back to Dynamic High5.

        Tk never mutates the campaign engine directly. The poll worker applies
        this queued preference before its next decision, so a candle click
        cannot race fill reconciliation or state persistence.
        """
        with self._lock:
            slot = self._slots.get(ticker)
            if slot is None or slot.get('stopping'):
                return False, 'not watching'
            if slot.get('engine') is None:
                return False, 'still arming — try again in a moment'
            slot['vantage_request'] = {
                'price': float(price) if price else None,
                'label': str(label or ''),
            }
        self._wake.set()
        return True, ('vantage pin queued' if price
                      else 'Dynamic High5 queued')

    def _apply_vantage_request(self, ticker, slot, engine, snap=None):
        """Apply one queued Vantage preference on the poll worker."""
        with self._lock:
            request = slot.get('vantage_request')
            slot['vantage_request'] = None
        if request is None:
            return
        price = request.get('price')
        broker_blocked = (int((snap or {}).get('shares') or 0) > 0
                          or bool((snap or {}).get('orders')))
        ok = (False if broker_blocked else
              (engine.set_manual_vantage(price, request.get('label', ''))
               if price else engine.clear_manual_vantage()))
        if not ok:
            engine.status = ('Vantage unchanged — choose it while EMPTY, before '
                             'a LOAD order is resting')
        else:
            engine.status = ('Pinned Vantage applied' if price
                             else 'Automatic Dynamic High5 restored')

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
        return slot['mode'] if slot and not slot.get('stopping') else None

    def is_enabled(self, ticker) -> bool:
        slot = self._slots.get(ticker)
        return bool(slot and not slot.get('stopping'))

    def ui_state(self, ticker):
        slot = self._slots.get(ticker)
        return dict(slot['ui']) if slot else None

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
            'ohlc': lambda: self._ohlc_for(ticker),
            'set_gear': lambda g: self.set_card_gear(ticker, gear=g),
            'set_tiers': lambda t: self.set_card_gear(ticker, tiers=t),
            'set_auto': lambda a: self.set_card_gear(ticker, auto=a),
            'set_vantage': lambda p, l='': self.set_vantage(ticker, p, l),
        }

    def _ohlc_for(self, ticker):
        """5-day bars for the window: the watcher's own 5-minute refresh
        (with the live tick folded into today's bar) when available,
        otherwise the main panel's last Save & Refresh data."""
        slot = self._slots.get(ticker)
        view = slot.get('ohlc_view') if slot else None
        if view:
            return list(view)
        return list(self.app._ohlc_data.get(ticker, []))

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

    def _sync_card_from_ui(self, ticker, ui):
        """Push the engine/broker truth into the current card, then read its
        controls back.

        This runs only on the Tk thread. Broker quantity/average are copied
        in place when card and broker describe the same position state. An
        EMPTY/DEPLOYED transition uses the already-fresh ticker snapshot to
        rebuild the structural card locally.
        """
        row = self._find_row(ticker)
        if row is None:
            return
        ui = ui or {}
        campaign = ui.get('campaign') or {}
        shares = ui.get('shares')
        avg = ui.get('avg_cost')
        try:
            same_state = (shares is not None
                          and bool(float(shares) > 0)
                          == bool(getattr(row, 'deployed', float(shares) > 0)))
        except (TypeError, ValueError):
            same_state = False
        slot = self._slots.get(ticker)
        if not same_state and shares is not None and slot is not None:
            # The ticker snapshot already contains fresh broker truth. The
            # real MainWindow can transition this one position from cached
            # market data without another catalogue/account network fetch.
            transition = getattr(self.app, '_apply_autopilot_position', None)
            if callable(transition):
                try:
                    transition(ticker, shares, avg)
                    row = self._find_row(ticker)
                    same_state = bool(
                        row is not None
                        and bool(float(shares) > 0)
                        == bool(getattr(row, 'deployed', False)))
                except (AttributeError, TypeError, ValueError, tk.TclError):
                    same_state = False
        update_position = getattr(row, 'update_broker_position', None)
        if same_state and callable(update_position):
            try:
                update_position(shares, avg)
            except (AttributeError, TypeError, ValueError, tk.TclError):
                pass
        # A local structural rebuild must not fall back to the main window's
        # older account-wide OPEN cache. This poll already has the ticker's
        # authoritative OPEN rows, so restore its current order badge too.
        set_order_state = getattr(row, 'set_order_state', None)
        if 'orders' in ui and callable(set_order_state):
            side = next((order.get('side')
                         for order in (ui.get('orders') or [])
                         if order.get('side')), None)
            try:
                set_order_state(side)
            except (AttributeError, tk.TclError):
                pass

        try:
            row.update_live(
                price=ui.get('price'),
                vantage=campaign.get('vantage'),
                vantage_src=campaign.get('vantage_src'),
                volatility=ui.get('vol5'))
        except (AttributeError, tk.TclError):
            return

        try:
            row.compute()
        except (AttributeError, tk.TclError):
            pass

        # AUTO may have selected a different gear from the live V just pushed.
        # Feed that exact card selection back to the engine for the next poll.
        if hasattr(row, 'line_config'):
            try:
                old = dict((self._slots.get(ticker) or {}).get('card') or {})
                cfg = row.line_config()
                self.set_card_config(ticker, cfg)
                if dict(cfg or {}) != old:
                    self._wake.set()
            except Exception:
                pass

        # A price tick must not run the row's global compute callback: that
        # callback persists every card, recomputes the army, and re-grids the
        # whole catalogue. Keep this update local. Card order is refreshed only
        # when its coarse actionable key actually changes, and at most once per
        # controller-wide throttle window. User edits and account refreshes
        # still reorder explicitly through the main window.
        if slot is not None:
            try:
                gap = getattr(row, '_gap', None)
                gap_key = None if gap is None else round(float(gap), 1)
            except (TypeError, ValueError):
                gap_key = None
            order_key = tuple(sorted(
                str(order.get('side') or '')
                for order in (ui.get('orders') or [])
                if order.get('side')))
            card_order_key = (
                bool(getattr(row, 'deployed', False)), gap_key, order_key)
            old_order_key = slot.get('card_order_key')
            slot['card_order_key'] = card_order_key
            now = time.time()
            if (old_order_key is not None
                    and old_order_key != card_order_key
                    and now >= float(getattr(
                        self, '_card_reorder_after', 0.0) or 0.0)):
                reorder = getattr(self.app, '_reorder_cards', None)
                if callable(reorder):
                    try:
                        reorder()
                        self._card_reorder_after = now + _CARD_REORDER_MIN_S
                    except Exception:
                        pass

    def on_rows_rebuilt(self):
        """Cards are recreated on every refresh — re-apply statuses, units,
        restore live campaign/broker values, and re-read their gear configs."""
        self.refresh_units()
        for ticker, slot in list(self._slots.items()):
            if slot.get('stopping'):
                self._apply_row_badge(ticker, None)
                continue
            self._apply_row_badge(ticker, slot['ui'].get('mode', 'WATCH'))
            self._sync_card_from_ui(ticker, slot.get('ui'))

        # A manually selected Vantage is a durable card input, not merely a
        # property of an open cockpit. Restore that explicit pin even while
        # the watcher is off so hand trading from the card and later LIVE
        # watching continue to use the same LOAD line.
        active = set(self._slots)
        for row in self.app.deployed_rows + self.app.empty_rows:
            if row.ticker in active or getattr(row, 'deployed', False):
                continue
            saved = self._saved_for(row.ticker) or {}
            pinned = saved.get('vantage_manual')
            reload_anchor = (saved.get('vantage')
                             if saved.get('vantage_src') == 'reload'
                             and saved.get('last_exit_date')
                             == self._trading_date(row.ticker) else None)
            vantage = pinned or reload_anchor
            source = 'manual' if pinned else ('reload' if reload_anchor else None)
            if not vantage:
                continue
            try:
                row.update_live(vantage=float(vantage), vantage_src=source)
                row.compute()
            except (AttributeError, TypeError, ValueError, tk.TclError):
                pass
        reorder = getattr(self.app, '_reorder_cards', None)
        if callable(reorder):
            try:
                reorder()
            except Exception:
                pass

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

    def _dialog_parent(self, ticker):
        """Use the visible campaign as modal owner, else the main root.

        An unparented native message box can hide behind a Toplevel while its
        grab makes every visible app window appear frozen.
        """
        windows = getattr(self.app, '_ap_windows', {}) or {}
        campaign = windows.get(ticker) if isinstance(windows, dict) else None
        candidate = getattr(campaign, 'win', None)
        if candidate is not None:
            try:
                if candidate.winfo_exists() and candidate.winfo_viewable():
                    return candidate
            except (AttributeError, tk.TclError):
                pass
        return self.root

    def _data_failure(self, ticker, slot, why):
        slot['fail_n'] = slot.get('fail_n', 0) + 1
        if slot['fail_n'] >= _FAIL_ANNOUNCE and not slot.get('fail_warned'):
            slot['fail_warned'] = True
            self._log(ticker, f'DATA PROBLEM announced after '
                              f"{slot['fail_n']} failed polls ({why})")

            # NOT a dialog. A modal opened from the poll thread grabs the
            # whole application, and if it lands behind the cockpit the main
            # card window simply stops responding with nothing on screen to
            # explain why. The warning goes to the alert line instead, where
            # it is visible without stealing the pointer.
            self._alert(ticker, slot,
                        f'NO DATA for ~{slot["fail_n"] * POLL_SECONDS}s '
                        f'({why}) — retrying every poll; nothing is sent '
                        f'while data is missing')

    def _data_recovered(self, ticker, slot):
        slot['alert'] = None
        if slot.get('fail_n'):
            if slot.get('fail_warned'):
                self._log(ticker, f"data recovered after {slot['fail_n']} "
                                  f'failed polls')
            slot['fail_n'] = 0
            slot['fail_warned'] = False

    # ── One poll cycle for one stock ──────────────────────────────────────────

    def _trading_date(self, ticker):
        now = datetime.now(timezone.utc)
        if ticker.endswith('.KS'):
            tz = _TZ_KR
        else:
            # Match market_phase(): New York is UTC-4 in DST and UTC-5 in
            # standard time. A fixed -5 date is wrong around the evening
            # boundary for more than half the trading year.
            probe_date = (now + timedelta(hours=-5)).date()
            offset = -4 if _us_dst(probe_date) else -5
            tz = timezone(timedelta(hours=offset))
        return now.astimezone(tz).strftime('%Y-%m-%d')

    def _daily_vantage(self, prov, ticker, slot):
        """Refresh daily candles once, then derive the last five COMPLETED
        sessions: prev_close, High5, and the strategy's V —
        100×(High5−Low5)/High5, the number AUTO chooses Gear from.

        The bars themselves are kept so today's live-folded candle can join
        the Dynamic High5 window and the cockpit can offer explicit candle-high
        Vantage selection. A proven bot reload uses its execution price instead
        and does not search these bars for an older exit."""
        today = self._trading_date(ticker)
        now = time.time()
        retry_after = float(slot.get('daily_retry_after') or 0.0)
        if slot.get('daily_date') != today and now >= retry_after:
            # One raw six-candle read supplies both views: the chart keeps
            # today's full intraday OHLC, while Vantage uses only completed
            # sessions. Empty/error responses retry soon without becoming a
            # permanent fifth request in the steady watcher.
            slot['daily_retry_after'] = now + _DAILY_RETRY_S
            bars = []
            raw_supported = callable(getattr(prov, 'get_candles', None))
            if raw_supported:
                # Also suppress _refresh_ohlc from immediately repeating this
                # same endpoint after an empty/error response. The daily retry
                # below is intentionally sooner than the five-minute cadence.
                slot['ohlc_ts'] = now
                try:
                    raw = prov.get_candles(ticker, count=6) or []
                except (AttributeError, NotImplementedError):
                    raw_supported = False
                    raw = []
                except Exception:
                    # Toss completed bars use this same endpoint, so another
                    # immediate call would add weight without independent
                    # evidence. Let the bounded retry handle the outage.
                    raw = []
                if raw:
                    raw = [dict(b) for b in raw]
                    slot['ohlc'] = raw[-5:]
                    slot['ohlc_ts'] = now
                    completed = [b for b in raw
                                 if self._bar_date(b) != today]
                    bars = (completed if completed else raw)[-5:]
            if not raw_supported:
                # Compatibility for providers/adapters that expose only the
                # completed-bars abstraction. The Toss controller normally
                # takes the single raw-candle path above.
                completed_getter = getattr(
                    prov, 'get_completed_daily_bars', None)
                if callable(completed_getter):
                    try:
                        bars = completed_getter(ticker, 5) or []
                    except Exception:
                        bars = []
            if bars:
                slot['prev_close'] = bars[-1]['close']
                slot['bars'] = [dict(b) for b in bars[-5:]]
                highs = [b['high'] for b in slot['bars'] if b.get('high')]
                slot['high5'] = max(highs) if highs else None
                slot['daily_date'] = today
                slot['daily_retry_after'] = 0.0
        return slot['prev_close'], slot['high5']

    @staticmethod
    def _bar_date(bar):
        ts = str(bar.get('ts') or '')
        return ts[:10] if len(ts) >= 10 else (bar.get('date') or '')

    def _session_highs(self, slot, snap):
        """[(iso_date, high)] for the recent sessions INCLUDING today, with
        today's high stretched to the live price. A fresh peak therefore lifts
        the vantage — and the LOAD line under it — on the very next poll."""
        out = [(self._bar_date(b), b.get('high'))
               for b in (slot.get('bars') or []) if b.get('high')]
        today = snap.get('trading_date')
        price = snap.get('price')
        live = slot.get('ohlc_view') or []
        today_high = next((b.get('high') for b in reversed(live)
                           if self._bar_date(b) == today and b.get('high')),
                          None)
        if price:
            today_high = max(today_high or price, price)
        if today_high and today:
            out = [(d, h) for d, h in out if d != today]
            out.append((today, today_high))
        return out

    def _refresh_ohlc(self, prov, ticker, slot, snap):
        """Keep the window's 5-day candles honest: refetch them every
        5 minutes, and fold the live tick into TODAY's bar every poll (the
        panel already redraws each tick, so the fold costs nothing). The
        frozen-candle-with-moving-Now-line mismatch is gone."""
        now = time.time()
        if slot.get('ohlc') is None:
            # The normal daily refresh already installed the raw candles. If
            # that API was unsupported and completed-bar fallback was used,
            # seed the first interval from cache instead of adding a second
            # network read. The live tick is folded below.
            cached = (self.app._ohlc_data.get(ticker)
                      or slot.get('bars') or [])
            if cached:
                slot['ohlc'] = [dict(b) for b in cached[-5:]]
                slot['ohlc_ts'] = now
        if now - slot['ohlc_ts'] >= _OHLC_REFRESH_S:
            slot['ohlc_ts'] = now
            try:
                bars = prov.get_candles(ticker, count=6)
                if bars:
                    slot['ohlc'] = [dict(b) for b in bars[-5:]]
            except Exception:
                # keep the old bars; retry in a minute, not in five
                slot['ohlc_ts'] = now - _OHLC_REFRESH_S + 60
        base = (slot['ohlc'] or self.app._ohlc_data.get(ticker)
                or slot.get('bars') or [])
        slot['ohlc_view'] = merge_live_bar(base, snap.get('price'),
                                           snap.get('trading_date'))
        # V and the candle panel must describe the same five bars. In
        # particular, today's live-folded high/low participates immediately;
        # the old completed-only daily cache could leave the card and chart on
        # different gears until the following session.
        latest = (slot.get('ohlc_view') or [])[-5:]
        highs = [b.get('high') for b in latest if b.get('high') is not None]
        lows = [b.get('low') for b in latest if b.get('low') is not None]
        slot['vol5'] = calc_volatility(
            max(highs) if highs else None,
            min(lows) if lows else None)

    def _real_snapshot(self, prov, seq, ticker, my_ids, prev_shares=None,
                       pending_order_id=None, pending_client_order_id=None,
                       fill_after=None, fill_before=None,
                       seen_fill_keys=()):
        """Read the four broker facts needed for one strategy decision.

        Normal polling is deliberately bounded to price, holdings, OPEN and
        buying power. Exact order detail is an exception for a durable pending
        bot identity: OPEN already supplies lifecycle evidence while the order
        is visible, so detail is queried only when that row disappears or the
        broker position changed and fill attribution matters. CLOSED history is
        reserved for shutdown recovery and is never replayed into a live poll.

        The legacy fill-window arguments remain accepted so small adapters and
        external callers do not break; they no longer enable history scanning.
        """
        ccy = 'KRW' if ticker.endswith('.KS') else 'USD'
        price = prov.get_prices([ticker]).get(ticker)
        observed_at = float(fill_before or time.time())

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

        raw_open = prov.get_open_orders(seq, ticker) or []
        orders = []
        for o in raw_open:
            normalized = normalize_open_order(o, my_ids)
            orders.append(normalized)

        pending_order = None
        pending_lookup_error = False

        def matches_pending(raw):
            oid = _broker_order_id(raw)
            coid = _client_order_id(raw)
            return bool((pending_order_id is not None and oid is not None
                         and str(pending_order_id) == str(oid))
                        or (pending_client_order_id is not None
                            and coid is not None
                            and str(pending_client_order_id) == str(coid)))

        # OPEN already proves acceptance for a client-id-only submission.
        raw_pending = next((o for o in raw_open if matches_pending(o)), None)
        if raw_pending is not None:
            pending_order = normalize_order_detail(raw_pending, my_ids)

        try:
            holdings_changed = (prev_shares is not None
                                and int(shares) != int(prev_shares))
        except (TypeError, ValueError):
            holdings_changed = False

        # A missing OPEN row is not terminal proof. Query the stable broker id
        # then; also query it on the one snapshot that carries a holdings delta
        # so bot attribution can use the actual cumulative execution evidence.
        evidence_raw = raw_pending
        get_order = getattr(prov, 'get_order', None)
        if (pending_order_id and callable(get_order)
                and (raw_pending is None or holdings_changed)):
            try:
                raw_detail = get_order(seq, pending_order_id) or {}
                if isinstance(raw_detail, dict) and raw_detail:
                    evidence_raw = raw_detail
                    pending_order = normalize_order_detail(raw_detail, my_ids)
            except Exception as exc:
                pending_lookup_error = True
                self._log(ticker, 'order-detail lookup failed: '
                                  f'{type(exc).__name__}: {exc}')

        recent_fills = (normalize_recent_fills(
            [evidence_raw], ticker, my_ids, allow_undated=True)
            if isinstance(evidence_raw, dict) and evidence_raw else [])

        bp = prov.get_buying_power(seq, ccy)
        return {'price': price, 'shares': shares, 'avg_cost': avg,
                'orders': orders, 'recent_fills': recent_fills,
                'pending_order': pending_order,
                'pending_lookup_error': pending_lookup_error,
                'fills_correlated': False,
                'closed_fills_complete': False,
                'fill_window_after': None,
                'fill_window_before': observed_at,
                'buying_power': bp}

    @staticmethod
    def _evidence_matches_pending(item, pending):
        if not item or not pending:
            return False
        item_id = item.get('order_id') or item.get('id')
        pending_id = pending.get('order_id')
        if item_id is not None and pending_id is not None:
            return str(item_id) == str(pending_id)
        item_client = item.get('client_order_id')
        pending_client = pending.get('client_order_id')
        return (item_client is not None and pending_client is not None
                and str(item_client) == str(pending_client))

    def _finish_cleanup(self, ticker, slot):
        with self._lock:
            if self._slots.get(ticker) is slot and slot.get('stopping'):
                self._slots.pop(ticker, None)
        self._log(ticker, 'autopilot cleanup complete')

    def _cleanup_cycle(self, ticker, slot, prov, seq):
        """Retire only provably bot-owned working orders before dropping a
        disabled slot. Foreign app/web orders are deliberately ignored."""
        now = time.time()
        if now < slot.get('cleanup_after', 0.0):
            return
        try:
            raw_orders = prov.get_open_orders(seq, ticker) or []
        except Exception as exc:
            slot['cleanup_after'] = now + _CLEANUP_RETRY_S
            self._log(ticker, f'cleanup read failed: {type(exc).__name__}: {exc}')
            return

        owned = []
        pending = getattr(slot.get('engine'), '_pending', None) or {}
        for order in raw_orders:
            oid = _broker_order_id(order)
            stable_pending = (pending.get('accepted')
                              and pending.get('order_id') is not None
                              and oid is not None
                              and str(pending.get('order_id')) == str(oid))
            if (not stable_pending
                    and not is_bot_owned_order(order, slot.get('my_ids', ()))):
                continue
            normalized = normalize_open_order(order, slot.get('my_ids', ()))
            if _as_float(normalized.get('qty_open'), 0.0) > 0:
                owned.append(normalized)
        if not owned:
            if slot.get('cleanup_retire_pending_save'):
                # Retirement already cleared the in-memory pending marker, but
                # a failed disk write must never let the cleanup slot vanish.
                if self._save_state(ticker, slot.get('engine')):
                    slot['cleanup_retire_pending_save'] = False
                    self._finish_cleanup(ticker, slot)
                else:
                    slot['cleanup_after'] = now + _CLEANUP_RETRY_S
                return
            if pending.get('accepted') and pending.get('order_id') is not None:
                try:
                    raw_detail = prov.get_order(seq, pending.get('order_id'))
                    detail = normalize_order_detail(raw_detail or {},
                                                    slot.get('my_ids', ()))
                except Exception as exc:
                    detail = None
                    self._log(ticker, 'cleanup detail failed: '
                                      f'{type(exc).__name__}: {exc}')
                if detail and detail.get('terminal'):
                    self._finish_cleanup(ticker, slot)
                    return
                if detail and _as_float(detail.get('qty_open'), 0.0) > 0:
                    owned.append(detail)
                else:
                    slot['cleanup_after'] = now + _CLEANUP_RETRY_S
                    return
            elif pending.get('accepted') and pending.get('client_order_id'):
                # A lost POST response can leave only the durable idempotency
                # key.  First exhaust CLOSED history for that exact key.  If
                # it is absent, repeat the identical request while Toss still
                # guarantees idempotency; this may create the never-received
                # original, but it can never create a second identity and is
                # cancelled below in the same cleanup pass.
                slot['cleanup_client_empty_reads'] = int(
                    slot.get('cleanup_client_empty_reads') or 0) + 1
                coid = pending.get('client_order_id')
                submitted = (_broker_epoch(pending.get('submitted_at'))
                             or _broker_epoch(pending.get('ts'))
                             or float(slot.get('cleanup_started_at') or now))
                closed_detail = None
                get_closed = getattr(prov, 'get_closed_orders', None)
                if callable(get_closed):
                    try:
                        try:
                            closed = get_closed(
                                seq, ticker, limit=100,
                                from_date=_closed_from_date(ticker, submitted))
                        except TypeError:
                            closed = get_closed(seq, ticker)
                        raw_closed = next(
                            (o for o in (closed or [])
                             if str(_client_order_id(o) or '') == str(coid)),
                            None)
                        if raw_closed is not None:
                            candidate = normalize_order_detail(
                                raw_closed, slot.get('my_ids', ()))
                            if candidate and candidate.get('order_id'):
                                closed_detail = candidate
                    except Exception as exc:
                        # The provider is all-pages-or-error, so no prefix from
                        # an incomplete CLOSED scan is consumed here.
                        self._log(ticker, 'cleanup CLOSED lookup failed: '
                                          f'{type(exc).__name__}: {exc}')

                if closed_detail is not None:
                    oid = closed_detail.get('order_id')
                    slot['my_ids'].add(oid)
                    self._note_order_accepted(
                        slot.get('engine'), pending.get('side'),
                        pending.get('price'), pending.get('qty'), oid, coid)
                    if not self._save_state(ticker, slot.get('engine')):
                        slot['cleanup_after'] = now + _CLEANUP_RETRY_S
                        return
                    if closed_detail.get('terminal'):
                        self._finish_cleanup(ticker, slot)
                        return
                    if _as_float(closed_detail.get('qty_open'), 0.0) > 0:
                        owned.append(closed_detail)
                    else:
                        slot['cleanup_after'] = now + _CLEANUP_RETRY_S
                        return
                elif now - submitted < _IDEMPOTENCY_TTL_S:
                    placed, _message = self._place_real(
                        ticker, slot, prov, seq, slot.get('engine'),
                        pending.get('side'), pending.get('price'),
                        pending.get('qty'), 'cleanup identity recovery',
                        client_id=coid, cleanup_recovery=True)
                    refreshed = (getattr(slot.get('engine'), '_pending', None)
                                 or {})
                    if placed and refreshed.get('order_id'):
                        oid = refreshed.get('order_id')
                        # Persist the broker id too.  Even if this second write
                        # fails, the pre-POST client id is durable and the live
                        # order is still cancelled immediately below.
                        self._save_state(ticker, slot.get('engine'))
                        owned.append({
                            'id': oid, 'side': refreshed.get('side'),
                            'price': refreshed.get('price'),
                            'qty_open': refreshed.get('qty') or 1,
                            'mine': True,
                        })
                    elif not refreshed:
                        # A definite non-ambiguous rejection proves that this
                        # idempotent request did not leave a working order.
                        if self._save_state(ticker, slot.get('engine')):
                            self._finish_cleanup(ticker, slot)
                        else:
                            slot['cleanup_retire_pending_save'] = True
                            slot['cleanup_after'] = now + _CLEANUP_RETRY_S
                        return
                    else:
                        slot['cleanup_after'] = now + _CLEANUP_RETRY_S
                        return
                elif slot.get('cleanup_client_empty_reads', 0) >= 3:
                    # OPEN is an exhaustive non-paginated read.  After the
                    # idempotency window and repeated successful empty reads,
                    # no working order with this lost identity remains. Retire
                    # the ambiguous marker durably; a later WATCH still adopts
                    # any broker holdings change as truth.
                    self._note_order_failed(
                        slot.get('engine'), pending.get('side'),
                        pending.get('price'), pending.get('qty'))
                    retired = not getattr(
                        slot.get('engine'), '_pending', None)
                    if (retired
                            and self._save_state(ticker, slot.get('engine'))):
                        self._log(ticker, 'client-id-only cleanup retired after '
                                          'the idempotency window and repeated '
                                          'empty OPEN reads')
                        self._finish_cleanup(ticker, slot)
                    else:
                        if retired:
                            slot['cleanup_retire_pending_save'] = True
                        slot['cleanup_after'] = now + _CLEANUP_RETRY_S
                    return
                else:
                    slot['cleanup_after'] = now + _CLEANUP_RETRY_S
                    return
            else:
                self._finish_cleanup(ticker, slot)
                return

        for order in owned:
            oid = order.get('id')
            if not oid:
                continue
            self._log(ticker, f'cleanup cancel bot order {oid}')
            try:
                st, body = prov.cancel_order(oid, seq)
                if not (isinstance(st, int) and 200 <= st < 300):
                    body = body if isinstance(body, dict) else {}
                    code = (body.get('error') or {}).get('code') or st
                    self._log(ticker, f'cleanup cancel rejected: {code}')
            except Exception as exc:
                self._log(ticker, 'cleanup cancel failed: '
                                  f'{type(exc).__name__}: {exc}')
        # Confirm the orders have disappeared on a later broker read. Never
        # infer success merely from the cancel response.
        slot['cleanup_after'] = now + _CLEANUP_RETRY_S

    def _cycle(self, ticker, slot):
        prov = self.app._toss_provider()
        if prov is None:
            if not slot.get('stopping'):
                self._push_ui(ticker, slot, status='Toss unavailable')
            return
        seq = self.app._account_seq(prov)
        if not seq:
            if not slot.get('stopping'):
                self._push_ui(ticker, slot, status='no Toss account')
            return
        if slot.get('stopping'):
            self._cleanup_cycle(ticker, slot, prov, seq)
            return

        # LIVE runs only during regular hours; drop to WATCH at the close.
        phase = market_phase(ticker)
        dropped_to_watch = False
        with self._lock:
            if slot['mode'] == 'LIVE' and phase != 'REGULAR':
                slot['mode'] = 'WATCH'
                dropped_to_watch = True
            mode = slot['mode']
        if dropped_to_watch:
            self._log(ticker, 'market left regular hours — LIVE → WATCH')

        # Persisted quantity is not an external-trade history watermark. Start
        # ordinary WATCH from the broker position seen now. The one exception
        # is a durable accepted bot intent: its saved position is needed to
        # attribute a fill that completed while this process was off.
        engine_lock = slot['engine_lock']
        with engine_lock:
            saved = self._saved_for(ticker) if slot['engine'] is None else None
            pending = (dict(getattr(slot.get('engine'), '_pending', None) or {})
                       if slot.get('engine') is not None
                       else dict(((saved or {}).get('pending') or {})))
        previous_shares = slot.get('broker_shares')
        if (previous_shares is None and isinstance(saved, dict)
                and isinstance(pending, dict) and pending.get('accepted')):
            saved_q = saved.get('q')
            if saved_q is not None:
                try:
                    previous_shares = int(round(float(saved_q)))
                except (TypeError, ValueError):
                    previous_shares = None
        pending_order_id = ((pending or {}).get('order_id')
                            if isinstance(pending, dict) else None)
        pending_client_id = ((pending or {}).get('client_order_id')
                             if isinstance(pending, dict) else None)
        snap = self._real_snapshot(
            prov, seq, ticker, slot['my_ids'], previous_shares,
            pending_order_id=pending_order_id,
            pending_client_order_id=pending_client_id)
        if snap['price'] is not None:
            slot['ticks'].append((time.time(), snap['price']))
            del slot['ticks'][:-_TICKS_KEPT]
            self._data_recovered(ticker, slot)
        else:
            self._data_failure(ticker, slot, 'no price from Toss')

        ccy = 'KRW' if ticker.endswith('.KS') else 'USD'
        snap['unit_cash'] = self._units.get(ccy) or 0.0
        snap['trading_date'] = self._trading_date(ticker)
        snap['prev_close'], snap['high5'] = self._daily_vantage(
            prov, ticker, slot)
        snap['can_trade'] = (mode == 'LIVE')
        snap['phase'] = phase
        self._refresh_ohlc(prov, ticker, slot, snap)
        snap['highs'] = self._session_highs(slot, snap)

        # AUTO must use the volatility calculated from this same live-folded
        # candle snapshot. Otherwise a volatility threshold and a price line
        # can cross together while the engine still trades yesterday's Gear.
        with self._lock:
            card = dict(slot['card']) if slot.get('card') else None
        if card and card.get('auto') and slot.get('vol5') is not None:
            if int(snap.get('shares') or 0) > 0:
                card['gear'] = select_auto_gear(slot['vol5'])
            else:
                card['gear'] = effective_entry_gear(
                    slot['vol5'], snap.get('price'), snap.get('unit_cash'))
            with self._lock:
                if self._slots.get(ticker) is slot:
                    slot['card'] = dict(card)
        snap['card'] = card

        with engine_lock:
            if slot['engine'] is None:
                slot['engine'] = CampaignEngine(
                    ticker, trading_date=snap['trading_date'], saved=saved,
                    log=lambda m, t=ticker: self._log(t, m))
                self._log(ticker, 'campaign engine armed'
                                  + (' (state restored)' if saved else ''))
            engine = slot['engine']
            detail = snap.get('pending_order')
            live_pending = getattr(engine, '_pending', None) or pending
            if (isinstance(detail, dict) and isinstance(live_pending, dict)
                    and self._evidence_matches_pending(detail, live_pending)):
                detail_id = detail.get('order_id') or detail.get('id')
                detail_client = (detail.get('client_order_id')
                                 or live_pending.get('client_order_id'))
                if detail_id is not None:
                    slot['my_ids'].add(detail_id)
                self._note_order_accepted(
                    engine, live_pending.get('side'),
                    live_pending.get('price'), live_pending.get('qty'),
                    detail_id or live_pending.get('order_id'), detail_client)
            self._apply_vantage_request(ticker, slot, engine, snap=snap)

            acts = engine.poll(snap)
            # Each successfully accepted broker delta advances exactly once.
            # Later history/detail may resolve order lifecycle safety, but it
            # never rewrites or replays this position observation.
            slot['broker_shares'] = snap['shares']
            self._execute(ticker, slot, prov, seq, engine, acts, snap=snap)
            self._retry_unresolved_submission(
                ticker, slot, prov, seq, engine, snap, mode, phase)
            if getattr(engine, 'dirty', False):
                # Keep dirty on a failed write so campaign fills and one-time
                # migrations retry next poll.
                if self._save_state(ticker, engine):
                    engine.dirty = False
            self._push_ui(ticker, slot, snap=snap)

    # ── Action executor ───────────────────────────────────────────────────────

    @staticmethod
    def _note_order_failed(engine, side, price, qty):
        note = getattr(engine, 'note_order_failed', None)
        if callable(note):
            note(side, price, qty)

    @staticmethod
    def _note_order_accepted(engine, side, price, qty, order_id, client_id):
        note = getattr(engine, 'note_order_accepted', None)
        if callable(note):
            note(side, price, qty, order_id=order_id,
                 client_order_id=client_id)

    @staticmethod
    def _note_order_submitted(engine, side, price, qty, client_id):
        note = getattr(engine, 'note_order_submitted', None)
        return bool(note and note(side, price, qty, client_id))

    @staticmethod
    def _side_backoff(slot, side):
        value = slot.get('backoff_until', 0.0)
        if isinstance(value, dict):
            value = value.get(str(side or '').upper(), 0.0)
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _set_side_backoff(slot, side, seconds, reason=None):
        value = slot.get('backoff_until')
        if not isinstance(value, dict):
            value = {'BUY': 0.0, 'SELL': 0.0}
            slot['backoff_until'] = value
        key = str(side or '').upper()
        value[key] = time.time() + float(seconds)
        reasons = slot.get('backoff_reason')
        if not isinstance(reasons, dict):
            reasons = {'BUY': None, 'SELL': None}
            slot['backoff_reason'] = reasons
        reasons[key] = reason if float(seconds) > 0 else None

    def _place_real(self, ticker, slot, prov, seq, engine,
                    side, price, qty, label, client_id=None,
                    cleanup_recovery=False):
        """Send one LIMIT/DAY order behind one atomic identity gate.

        A normal placement may proceed only while this exact slot is still
        LIVE. Slot membership/mode and the durable client id are checked and
        committed under ``_lock`` so ``disable()`` cannot remove a bare slot
        and then let an already-decided POST escape. Cleanup identity recovery
        is deliberately allowed for its retained stopping slot in WATCH.
        """
        wire = fmt_order_price(ticker, price)
        symbol = re.sub(r'[^A-Za-z0-9_-]', '', ticker)[:10]
        # time_ns keeps same-side retries unique while the 36-char truncation
        # keeps the Toss idempotency key within its documented boundary.
        coid = client_id or (f'{_BOT_CLIENT_ID_PREFIX}{symbol}-{side[:1]}-'
                             f'{time.time_ns()}')[:36]
        with self._lock:
            # Recovery/retry enters with an identity that was already made
            # durable.  A concurrent WATCH reopen (or a failed repeat save)
            # must not turn that accepted, outcome-ambiguous identity back
            # into a bare line: doing so could permit a duplicate order later.
            pending = getattr(engine, '_pending', None)
            identity_was_accepted = bool(
                isinstance(pending, dict) and pending.get('accepted'))
            current = self._slots.get(ticker)
            permitted = (
                current is slot
                and ((cleanup_recovery and slot.get('stopping'))
                     or (not cleanup_recovery
                         and slot.get('mode') == 'LIVE'
                         and not slot.get('stopping'))))
            if not permitted:
                if not identity_was_accepted:
                    self._note_order_failed(engine, side, price, qty)
                return False, 'autopilot is no longer allowed to submit'
            if not self._note_order_submitted(
                    engine, side, price, qty, coid):
                return False, 'intent changed before submission'
            # Commit the idempotency key before the network call while
            # disable() is excluded by this same lock. A failed first write
            # proves no POST happened, so that new intent is retired. A failed
            # repeat write keeps the identity that was durable beforehand.
            if not self._save_state(ticker, engine):
                if not identity_was_accepted:
                    self._note_order_failed(engine, side, price, qty)
                # _save_state updates the in-memory store before its disk
                # write. Mirror the safe engine state into that cache: either
                # the new unposted identity was retired above, or the prior
                # accepted recovery identity was deliberately retained.
                serialize = getattr(engine, 'to_dict', None)
                if isinstance(getattr(self, '_store', None), dict) \
                        and callable(serialize):
                    self._store[self._store_key(ticker)] = serialize()
                return False, 'state persistence failed; order not transmitted'
        self._log(ticker, f'place [{label}] {side} {qty} @ {wire}')
        try:
            st, body = prov.place_limit_order(
                ticker, side, wire, qty, seq, client_order_id=coid)
        except Exception as e:
            self._log(ticker, f'place error: {e}')
            self._set_side_backoff(
                slot, side, _SUBMISSION_RETRY_S, reason='transport')
            unresolved = getattr(engine, 'note_order_unresolved', None)
            if callable(unresolved):
                unresolved(side, price, qty, reason=f'transport: {e}',
                           client_order_id=coid)
            return False, f'order outcome unresolved: {e}'
        body = body if isinstance(body, dict) else {}
        result = body.get('result') or body
        order_id = result.get('orderId')
        accepted_coid = result.get('clientOrderId') or coid
        if isinstance(st, int) and 200 <= st < 300 and order_id:
            slot['my_ids'].add(order_id)
            slot['insuff_warned'] = False
            self._set_side_backoff(slot, side, 0)
            self._note_order_accepted(
                engine, side, price, qty, order_id, accepted_coid)
            return True, f'{side} {qty} @ {wire} placed'
        code = str((body.get('error') or {}).get('code') or st)
        self._log(ticker, f'place rejected: {code}')
        code_lower = code.lower()
        duplicate_client_id = (
            st == 409 and (
                'duplicate' in code_lower or 'idempot' in code_lower
                or ('client' in code_lower and 'order' in code_lower)))
        ambiguous = ((isinstance(st, int) and st >= 500)
                     or st in (None, 0)
                     or (isinstance(st, int) and 200 <= st < 300)
                     or duplicate_client_id)
        if ambiguous:
            self._set_side_backoff(
                slot, side, _SUBMISSION_RETRY_S,
                reason='ambiguous submission')
            unresolved = getattr(engine, 'note_order_unresolved', None)
            if callable(unresolved):
                unresolved(side, price, qty,
                           reason=f'broker response {code}',
                           client_order_id=coid)
            return False, f'order outcome unresolved: {code}'
        self._note_order_failed(engine, side, price, qty)
        if 'insufficient' in code and 'buying' in code:
            # Army state is broker data, not a timer. Mute this BUY now; the
            # engine re-checks fresh buying power on the next poll and can fire
            # immediately when the current line is fundable again.
            self._set_side_backoff(slot, 'BUY', 0)
            slot['insuff_warned'] = True
            if engine is not None:
                engine.buy_state = 'EXHAUSTED'
        elif 'hours' in code or 'closed' in code:
            self._set_side_backoff(
                slot, side, _BACKOFF_HOURS_CLOSED, reason='market hours')
        elif 'opposite' in code:
            pass           # a foreign order blocks the side; retry next poll
        else:
            self._set_side_backoff(
                slot, side, _BACKOFF_OTHER, reason='broker rejection')
        return False, f'rejected: {code}'

    def _retry_unresolved_submission(self, ticker, slot, prov, seq, engine,
                                     snap, mode, phase):
        """Recover an ambiguous POST with the same ten-minute idempotency key."""
        p = getattr(engine, '_pending', None)
        if (not isinstance(p, dict) or not p.get('unresolved')
                or p.get('order_id') is not None
                or not p.get('client_order_id')
                or snap.get('pending_order') is not None
                or mode != 'LIVE' or phase != 'REGULAR'):
            return
        with self._lock:
            if (self._slots.get(ticker) is not slot
                    or slot.get('mode') != 'LIVE' or slot.get('stopping')):
                return
        now = time.time()
        submitted = float(p.get('submitted_at') or p.get('ts') or now)
        if now - submitted >= _IDEMPOTENCY_TTL_S:
            engine.status = ('submission remains unresolved after the Toss '
                             'idempotency window — manual broker review needed')
            return
        if (now < float(slot.get('submission_retry_after') or 0.0)
                or now < self._side_backoff(slot, p.get('side'))):
            return
        if any(_as_float(o.get('qty_open'), 0.0) > 0
               for o in (snap.get('orders') or [])):
            return
        live = snap.get('price')
        crossed = (live is not None and
                   (live <= p.get('price') if p.get('side') == 'BUY'
                    else live >= p.get('price')))
        if not crossed:
            return
        slot['submission_retry_after'] = now + _SUBMISSION_RETRY_S
        self._log(ticker, 'retry unresolved submission with the same '
                          f'clientOrderId {p.get("client_order_id")}')
        self._place_real(
            ticker, slot, prov, seq, engine, p.get('side'), p.get('price'),
            p.get('qty'), f'{p.get("kind") or p.get("side")} reconcile',
            client_id=p.get('client_order_id'))

    def _execute(self, ticker, slot, prov, seq, engine, acts, snap=None):
        now = time.time()
        has_cancel = any(act and act[0] == 'cancel' for act in acts)
        open_by_id = {
            order.get('id'): order
            for order in ((snap or {}).get('orders') or [])
            if order.get('id')
        }
        for act in acts:
            kind = act[0]
            if kind == 'notify':
                # Reserved for explicit one-time engine warnings. Reserve
                # exhaustion itself is visual-only in the campaign window.
                self._popup(ticker, act[1])
            elif kind == 'stand_down':
                # The position closed. A campaign is a deliberate act, so the
                # bot does not start another one on its own: LIVE goes off and
                # the commander arms it again when they mean to.
                if slot['mode'] == 'LIVE':
                    slot['mode'] = 'WATCH'
                    self._log(ticker, f'LIVE → WATCH: {act[1]}')
                self._alert(ticker, slot, act[1])
            elif kind == 'cancel':
                _, oid, label = act
                order = open_by_id.get(oid)
                pending = getattr(engine, '_pending', None) or {}
                stable_pending = (pending.get('accepted')
                                  and pending.get('order_id') is not None
                                  and str(pending.get('order_id')) == str(oid))
                detail = (snap or {}).get('pending_order') or {}
                stable_detail = (detail.get('order_id') is not None
                                 and str(detail.get('order_id')) == str(oid)
                                 and (detail.get('mine') or stable_pending))
                visible_owned = bool(order and order.get('mine'))
                if not (visible_owned or stable_pending or stable_detail):
                    self._log(ticker, f'skip unsafe cancel [{label}] {oid}')
                    continue
                if order and _as_float(order.get('qty_open'), 0.0) <= 0:
                    continue
                self._log(ticker, f'cancel [{label}] {oid}')
                try:
                    st, body = prov.cancel_order(oid, seq)
                    if not (isinstance(st, int) and 200 <= st < 300):
                        body = body if isinstance(body, dict) else {}
                        code = (body.get('error') or {}).get('code') or st
                        self._log(ticker, f'cancel rejected: {code}')
                except Exception as e:
                    self._log(ticker, f'cancel error: {e}')
            elif kind == 'place':
                _, side, price, qty, label = act
                # Even if an engine regression emits cancel+place together,
                # never race a replacement/opposite order against a cancel
                # whose result is not yet visible in this broker snapshot.
                if has_cancel:
                    self._log(ticker, f'skip place [{label}] — waiting for '
                                      'cancel confirmation')
                    self._note_order_failed(engine, side, price, qty)
                    engine.status = (f'{side} not sent — waiting for the bot '
                                     'order cancellation to be confirmed')
                    continue
                # Re-read immediately before the broker call. A mode captured
                # when the poll began must not let LIVE → WATCH slip through
                # while fill reconciliation and cancellation were running.
                with self._lock:
                    live_now = (self._slots.get(ticker) is slot
                                and slot.get('mode') == 'LIVE'
                                and not slot.get('stopping')
                                and market_phase(ticker) == 'REGULAR')
                if not live_now or not (snap or {}).get('can_trade', True):
                    self._note_order_failed(engine, side, price, qty)
                    continue
                backoff_until = self._side_backoff(slot, side)
                if now < backoff_until:
                    self._log(ticker, f'skip place [{label}] (backing off '
                                      f'{backoff_until - now:.0f}s)')
                    self._note_order_failed(engine, side, price, qty)
                    engine.status = (f'{side} not sent — broker retry in '
                                     f'{backoff_until - now:.0f}s')
                    continue
                with self._lock:
                    live_now = (self._slots.get(ticker) is slot
                                and slot.get('mode') == 'LIVE'
                                and not slot.get('stopping')
                                and market_phase(ticker) == 'REGULAR')
                if not live_now or not (snap or {}).get('can_trade', True):
                    self._note_order_failed(engine, side, price, qty)
                    continue
                ok, msg = self._place_real(ticker, slot, prov, seq, engine,
                                           side, price, qty, label)
                if not ok:
                    engine.status = f'{side} not sent — {msg}'

    def _alert(self, ticker, slot, msg):
        """Say something urgent WITHOUT taking the application hostage.

        Everything here runs on the poll thread. `messagebox` from a
        background thread opens an application-modal window; if it happens to
        appear behind the cockpit, every other window — including the card
        grid — stops accepting clicks and there is nothing visible to dismiss.
        That is the "sometimes the cards are not clickable" bug.

        So: no dialogs from the poll. The message goes to the log and to the
        slot's alert line, which the cockpit shows in red."""
        self._log(ticker, f'ALERT: {msg}')
        if slot is not None:
            slot['alert'] = msg

    def _popup(self, ticker, msg):
        slot = self._slots.get(ticker)
        self._alert(ticker, slot, msg)

    # ── UI push (marshaled to the tk thread) ──────────────────────────────────

    def _push_ui(self, ticker, slot, snap=None, status=None):
        """One flat dict describing the campaign right now, handed to the
        window on the tk thread. Every field is read defensively so a poll can
        never die on the way to the screen."""
        engine = slot.get('engine')
        badge_key = slot['mode']
        ccy = 'KRW' if ticker.endswith('.KS') else 'USD'
        shares = (snap or {}).get('shares') or 0
        avg = (snap or {}).get('avg_cost') or 0.0
        ui = {
            'ticker': ticker,
            'state': engine.state if engine else 'ARMING',
            'campaign_state': getattr(engine, 'campaign_state', None),
            'status': status or (engine.status if engine else 'arming…'),
            'campaign': (engine.summary(shares, avg, (snap or {}).get('price'))
                         if engine is not None else None),
            'lines': {k: dict(v) for k, v in
                      (getattr(engine, 'lines', {}) or {}).items()},
            'crossed': dict(getattr(engine, 'crossed', {}) or {}),
            'events': list(getattr(engine, 'events', []) or []),
            'buy_state': getattr(engine, 'buy_state', 'OK') or 'OK',
            'vol5': slot.get('vol5'),
            'alert': slot.get('alert'),
            'bars': list(slot.get('ohlc_view') or slot.get('bars') or []),
            'card_auto': bool((slot.get('card') or {}).get('auto', True)),
            'mode': slot['mode'],
            'badge_key': badge_key,
            'phase': market_phase(ticker),
            'price': (snap or {}).get('price'),
            'shares': (snap or {}).get('shares'),
            'avg_cost': (snap or {}).get('avg_cost'),
            'buying_power': (snap or {}).get('buying_power'),
            'unit_cash': self._units.get(ccy) or 0.0,
            'orders': list((snap or {}).get('orders') or []),
            'recent_fills': list((snap or {}).get('recent_fills') or []),
            'ticks': list(slot.get('ticks') or []),
            'ts': datetime.now().strftime('%H:%M:%S'),
        }
        slot['ui'] = ui

        def apply():
            current = self._slots.get(ticker)
            if current is not slot or slot.get('stopping'):
                return
            self._apply_row_badge(ticker, badge_key)
            self._sync_card_from_ui(ticker, ui)
            self._notify(ticker, ui)
        try:
            self.root.after(0, apply)
        except (RuntimeError, tk.TclError):
            pass          # app shutting down / no mainloop
