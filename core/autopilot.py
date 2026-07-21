"""Daily v^ Linear Weighted Grid engine — the Daily Adventure autopilot.

The bot no longer follows the card gear lines. It runs the grid strategy
(manual: "Daily v^ Grid Autopilot Manual.md"): every trading day builds ONE
fixed coordinate system — the ADVENTURE — around a session anchor, and the
bot mechanically rebalances inventory whenever price crosses a grid level.
Both sides of a wobble are harvested: V (fall→BUY, rebound→SELL) and
^ (rise→SELL, dip→REBUY). The cards keep their gear system as the manual
trading aid; this engine ignores them.

The grid (defaults, all module constants below):

    levels   -5 … +5           (cap 5: OUTSIDE the zone nothing is chased)
    offsets  3% arithmetic     level ±n = anchor × (1 ± 0.03·n)
    weights  [1, 2, 3, 4, 5]   cumulative W(n) = 1, 3, 6, 10, 15

Target inventory (the whole state machine):

    target(-n) = base + W(n)·unit      target(+n) = base − W(n)·unit
    clamped to [MIN_INVENTORY, MAX_INVENTORY]; order = target − actual.

Rules that make it run unattended:

    * Adjacent transitions only, ONE strategy order at a time, and the
      level advances ONLY on a broker-confirmed fill reaching the target.
    * Nothing to BUY (reserve army can't fund the delta): the transition is
      simply NOT taken — the level stays, the line stays crossed, and the
      buy fires by itself the moment cash returns (e.g. after a sell).
    * Nothing to SELL (target clamps to actual, e.g. inventory at the
      lower bound): the level advances SILENTLY so the grid keeps tracking
      price; the first downward crossing buys, and the upper levels can
      sell again after that.
    * Partial fills self-heal: the level does not advance, so the next
      touch of the same line re-orders exactly the remainder
      (delta = target − actual).
    * Manual (external) fills are FOLDED INTO THE BASE: a buy/sell the bot
      did not place shifts base_inventory by the same amount, so every
      level target shifts with it and the bot's own units stay untouched —
      manual trading and the bot coexist without fighting each other.
      (Sold below the bot's deployed depth, the base clamps at 0 and the
      bot simply rebuilds on its normal lines.)
    * Opening gap ≥ first offset: compressed one-level init — the ANCHOR
      IS the opening price itself, labeled L∓1 (A); every level is spaced
      3%-of-the-open from there, and only the minimum weight-1 action
      trades (skipped levels are never chased). On a normal day the anchor
      is the previous close, labeled L+0 (A).
    * Daily rebase: each new trading date starts a fresh adventure — new
      anchor (previous regular close), base inventory = actual shares,
      unit re-sized, level 0, day log cleared. P&L history lives in the
      log file; the rebase never rewrites it.

WATCH mode places nothing; a due transition is still exposed as `trigger`
(the controller keeps manual-fire support for a future UI — the current
window has no manual buttons).

This module has NO network and NO tkinter. State survives restarts via
to_dict()/`saved` (the controller persists it).
"""

import time
from datetime import datetime

from core.calc import trim_buy_price, trim_sell_price, round_half_up

POLL_SECONDS = 5         # watcher tick (4 light reads per watched ticker)

# ── Grid configuration (v0.2 defaults — edit here, then restart the bot) ────
GRID_STEP_PCT = 0.03             # arithmetic level spacing (3% of anchor)
LEVEL_CAP = 5                    # zone = -5 … +5; outside is not chased
GRID_WEIGHTS = (1, 2, 3, 4, 5)   # linear level weights (units per step)
GAP_THRESHOLD = GRID_STEP_PCT    # opening gap ≥ 1 level → compressed init

# Absolute inventory bounds (shares). CORE is never sold; MAX None = only
# the cap bounds accumulation (W(5)=15 units above base).
CORE_INVENTORY = 0
MIN_INVENTORY = 0
MAX_INVENTORY = None

_PENDING_STALE_S = 90    # forget an order intent this long after placing it
                         # if nothing rests and nothing filled

_CROSS_EPS = 1e-9        # relative tolerance so an exact touch of a level
                         # (e.g. 103 vs 100×1.03) counts as crossed despite
                         # binary floating-point noise

_SAVE_FIELDS = ('trading_date', 'anchor', 'anchor_level', 'reference_close',
                'opening_price', 'gap_mode', 'gap_pending', 'base_inventory',
                'unit_qty', 'current_level', 'grid_ready', 'events',
                'buy_value', 'sell_value', 'fills', 'last_shares')


def grid_offsets(cap=LEVEL_CAP, step=GRID_STEP_PCT):
    """Arithmetic offsets [step, 2·step, …] out to the cap."""
    return [step * n for n in range(1, cap + 1)]


def cum_weight(n, weights=GRID_WEIGHTS):
    """W(n): total units between the anchor and absolute level n."""
    n = min(abs(int(n)), len(weights))
    return sum(weights[:n])


def level_raw_price(anchor, level, anchor_level=0, step=GRID_STEP_PCT):
    """Untrimmed grid price for a level (crossings compare against this;
    orders are tick-trimmed per side at order time).

    The ANCHOR is the price that sits at `anchor_level`: the previous
    close at L0 on a normal day, or the opening price itself at L∓1 on a
    compressed-gap day. Every level is `step`-of-the-anchor away from it:

        price(k) = anchor × (1 + step × (k − anchor_level))
    """
    return anchor * (1 + step * (level - anchor_level))


def target_inventory(level, base, unit_qty,
                     minimum=None, maximum=None, weights=GRID_WEIGHTS):
    """Bounded target share count at a grid level."""
    w = cum_weight(level, weights)
    raw = base + w * unit_qty if level < 0 else (
        base - w * unit_qty if level > 0 else base)
    lo = max(CORE_INVENTORY, MIN_INVENTORY if minimum is None else minimum)
    raw = max(lo, raw)
    hi = MAX_INVENTORY if maximum is None else maximum
    if hi is not None:
        raw = min(hi, raw)
    return int(raw)


class GridEngine:
    """One adventure battlefield. Feed `poll(snap)` every cycle; execute the
    returned actions in order.

    snap = {
        'price':        float|None   live price,
        'shares':       int          held shares (source of truth: broker),
        'avg_cost':     float        broker average cost (accounting only),
        'orders':       [{'id','side','price','qty_open','filled','mine'}],
        'buying_power': float|None   cash reserve in this stock's currency,
        'unit_cash':    float        1 unit in this stock's currency,
        'trading_date': str          market-local date 'YYYY-MM-DD',
        'prev_close':   float|None   previous completed regular close,
        'phase':        str          'REGULAR'|'PRE'|'AFTER'|'CLOSED',
        'can_trade':    bool         False in WATCH mode → the due transition
                                     is exposed as `trigger`, NOT fired,
    }

    actions: ('place', side, price, qty, label), ('notify', message).
    The grid bot never auto-cancels: it keeps ONE strategy order and waits.
    """

    def __init__(self, ticker, trading_date=None, log=None, saved=None):
        self.ticker = ticker
        self.currency = 'KRW' if ticker.endswith('.KS') else 'USD'
        self.state = 'ARMING'     # ARMING|WAIT_OPEN|WATCHING|ORDER_PENDING
        self.status = 'arming…'
        self.trading_date = trading_date

        # ── Adventure (per-day session) state ────────────────────────────
        self.anchor = None            # THE anchor price: prev close (L0) or
                                      # the opening price itself (gap, L∓1)
        self.anchor_level = 0         # the level the anchor sits at
        self.reference_close = None   # previous regular close
        self.opening_price = None     # first regular quote the bot saw
        self.gap_mode = 'NONE'        # 'NONE' | 'DOWN' | 'UP'
        self.gap_pending = False      # the minimum weight-1 gap action is due
        self.base_inventory = 0       # actual shares when the grid was built
        self.unit_qty = 0             # frozen share size of one unit
        self.current_level = 0
        self.grid_ready = False

        # Day log + accounting-lite (cleared at the daily rebase).
        self.events = []              # today's actual fills
        self.buy_value = 0.0
        self.sell_value = 0.0
        self.fills = 0

        # UI exposure (rebuilt every poll).
        self.grid = []                # [{'level','price','target'}] cap→-cap
        self.trigger = {'BUY': None, 'SELL': None}
        self.trigger_note = None
        self.buy_state = 'OK'         # 'OK' | 'EXHAUSTED' (down-line muted)

        self.dirty = False
        self._exhaust_logged = False
        self._pending = None          # {'side','price','qty','level','target','ts'}
        self._prev_shares = None
        self.last_shares = None       # persisted share count (restart bookkeeping)
        self._log = log or (lambda msg: None)
        if saved:
            self._restore(saved)

    # ── Persistence ───────────────────────────────────────────────────────────

    def to_dict(self):
        d = {f: getattr(self, f) for f in _SAVE_FIELDS}
        # The unresolved order intent survives restarts so a fill that lands
        # while the program is off can be attributed to the bot on re-arm.
        d['pending'] = dict(self._pending) if self._pending else None
        d['saved_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return d

    def _restore(self, saved):
        try:
            for f in _SAVE_FIELDS:
                if f in saved:
                    setattr(self, f, saved[f])
            p = saved.get('pending')
            self._pending = (dict(p) if isinstance(p, dict)
                             and p.get('side') in ('BUY', 'SELL') else None)
            self.events = list(self.events or [])
            self.current_level = int(self.current_level or 0)
            self.base_inventory = int(self.base_inventory or 0)
            self.unit_qty = int(self.unit_qty or 0)
            if self.last_shares is not None:
                self.last_shares = int(self.last_shares)
            if self.grid_ready and (not self.anchor or self.unit_qty <= 0):
                self.grid_ready = False     # old/foreign store format
        except (TypeError, ValueError):
            self.__init__(self.ticker, trading_date=self.trading_date,
                          log=self._log)

    # ── Small helpers ─────────────────────────────────────────────────────────

    def _fp(self, p):
        return f'{p:,.0f}' if self.currency == 'KRW' else f'{p:,.2f}'

    def _event(self, kind, qty, price, shares):
        self.events.append({'ts': datetime.now().strftime('%m/%d %H:%M'),
                            'kind': kind, 'qty': qty, 'price': price,
                            'shares': shares})
        del self.events[:-300]
        self.dirty = True

    def _raw(self, level):
        # Round away binary float noise (100×1.03 = 103.000…01) BEFORE any
        # tick trim, so an exact touch crosses and the ceil/floor trims
        # don't drift a cent off the strategy line.
        return round(level_raw_price(self.anchor, level, self.anchor_level),
                     6)

    def _target(self, level):
        return target_inventory(level, self.base_inventory, self.unit_qty)

    def _order_price(self, side, level):
        raw = self._raw(level)
        return (trim_sell_price(self.ticker, raw) if side == 'SELL'
                else trim_buy_price(self.ticker, raw))

    @staticmethod
    def _split_orders(snap):
        buys = [o for o in snap.get('orders', []) if o.get('side') == 'BUY']
        sells = [o for o in snap.get('orders', []) if o.get('side') == 'SELL']
        return buys, sells

    def _place(self, acts, side, price, qty, label, level, target):
        acts.append(('place', side, price, qty, label))
        self._pending = {'side': side, 'price': price, 'qty': qty,
                         'level': level, 'target': target, 'ts': time.time()}

    def note_manual_order(self, side, price, qty, level=None, target=None):
        """Register a manually-fired order (the window's Buy/Sell buttons in
        WATCH mode) so its fill advances the level exactly like a bot order."""
        self._pending = {'side': side, 'price': price, 'qty': qty,
                         'level': level, 'target': target, 'ts': time.time()}

    # ── Daily rebase: every date is a fresh adventure ────────────────────────

    def _roll_day(self, snap):
        d = snap.get('trading_date')
        if not d or d == self.trading_date:
            return
        first = self.trading_date is None
        self.trading_date = d
        if not first and self.grid_ready:
            net = self.sell_value - self.buy_value
            self._log(f'adventure closed: {self.fills} fills, '
                      f'buy {self._fp(self.buy_value)} / '
                      f'sell {self._fp(self.sell_value)} '
                      f'(net {self._fp(net)}), end level '
                      f'{self.current_level:+d}')
        self.anchor = None
        self.anchor_level = 0
        self.reference_close = None
        self.opening_price = None
        self.gap_mode = 'NONE'
        self.gap_pending = False
        self.current_level = 0
        self.grid_ready = False
        self.events = []
        self.buy_value = 0.0
        self.sell_value = 0.0
        self.fills = 0
        self._pending = None
        self.dirty = True
        if not first:
            self._log(f'new adventure day {d} — grid rebuilds at the '
                      f'regular open')

    # ── Fill detection (broker share diffs are the truth) ────────────────────

    def _expire_pending(self, snap, shares):
        """Drop a stale intent: nothing rests on that side, nothing filled."""
        p = self._pending
        if not p or time.time() - p['ts'] < _PENDING_STALE_S:
            return
        resting = [o for o in snap.get('orders', [])
                   if o.get('side') == p['side']]
        if not resting and shares == self._prev_shares:
            self._log(f"pending {p['side']} expired unfilled — forgotten")
            self._pending = None

    def _arm_attribution(self, snap, shares):
        """First poll after (re)arming: explain any inventory change that
        happened while the watcher was OFF, using the persisted bookkeeping
        (last_shares + the unresolved order intent).

        1. A diff in the pending order's direction is credited to the BOT
           (its DAY limit filled after shutdown): the level advances when
           the full order is covered, exactly like live fill detection.
        2. Whatever remains is a MANUAL trade → folded into the base, the
           same as it would have been while polling.
        """
        known = self.last_shares
        if known is None:                     # first-ever arm on this ticker
            if shares > 0:
                self._log(f'armed with {shares} shares held')
            return
        diff = shares - int(known)
        if diff == 0:
            if shares > 0:
                self._log(f're-armed consistent: {shares} shares, '
                          f'level {self.current_level:+d}')
            return
        residual = diff
        pend = self._pending
        if pend and self.grid_ready:
            sign = 1 if pend['side'] == 'BUY' else -1
            moved = diff * sign               # shares moved the pending way?
            if moved > 0:
                take = min(moved, int(pend['qty']))
                price = pend['price']
                value = take * (price or 0.0)
                if pend['side'] == 'BUY':
                    self.buy_value += value
                else:
                    self.sell_value += value
                self.fills += 1
                lvl = pend.get('level')
                kind = (f"{pend['side']} L{lvl:+d}" if lvl is not None
                        else pend['side'])
                self._event(kind, sign * take, price,
                            int(known) + sign * take)
                self._log(f"{pend['side']} filled while off: {take} @ "
                          f'{self._fp(price or 0)} (attributed to the bot)')
                if take == int(pend['qty']) and lvl is not None:
                    self.current_level = lvl
                    self._log(f'level {lvl:+d} confirmed from the '
                              f'restored intent')
                residual = diff - sign * take
                self._pending = None
        if residual:
            if self.grid_ready:
                old = self.base_inventory
                self.base_inventory = max(0, old + residual)
                self._log(f'manual trade while off: {residual:+d} shares — '
                          f'folded into base ({old} → '
                          f'{self.base_inventory}); bot units unchanged')
                self._event(('BUY ext' if residual > 0 else 'SELL ext'),
                            residual, snap.get('price'), shares)
            else:
                self._log(f'inventory changed while off: {residual:+d} '
                          f'shares (before the grid — base follows at init)')
        self.dirty = True

    def _detect_fills(self, snap, shares):
        prev = self._prev_shares
        if prev is None:
            self._arm_attribution(snap, shares)
            return
        if shares == prev:
            return
        qty = abs(shares - prev)
        side = 'BUY' if shares > prev else 'SELL'
        pend = self._pending if (self._pending
                                 and self._pending['side'] == side) else None
        price = (pend['price'] if pend
                 else (snap.get('price') or snap.get('avg_cost') or 0.0))
        value = qty * (price or 0.0)
        if side == 'BUY':
            self.buy_value += value
        else:
            self.sell_value += value
        self.fills += 1

        if pend and pend.get('level') is not None:
            kind = f'{side} L{pend["level"]:+d}'
        elif pend:
            kind = side
        else:
            kind = f'{side} ext'
            if self.grid_ready:
                # Fold the commander's manual trade into the base so every
                # level target shifts with it: the bot's own units stay the
                # same size and manual/bot trading never fight each other.
                old = self.base_inventory
                self.base_inventory = max(0, old + (shares - prev))
                self._log(f'external {side.lower()}: {prev} → {shares} '
                          f'shares — folded into base ({old} → '
                          f'{self.base_inventory}); bot units unchanged')
            else:
                self._log(f'external {side.lower()}: {prev} → {shares} '
                          f'shares (before the grid — base follows at init)')
        self._event(kind, shares - prev, price, shares)

        if pend:
            target = pend.get('target')
            if target is not None and shares == target:
                self.current_level = pend['level']
                self._pending = None
                self._log(f'level {pend["level"]:+d} confirmed: '
                          f'inventory {shares}')
            else:
                resting = [o for o in snap.get('orders', [])
                           if o.get('side') == side]
                if not resting:
                    self._pending = None
                    self._log(f'partial fill done at {shares}/{target} — '
                              f'level stays {self.current_level:+d}; the '
                              f'remainder re-orders on the next touch')
                else:
                    self._log(f'partial fill: {shares}/{target} — order '
                              f'still resting')
        self.dirty = True

    # ── Session (adventure) initialization ───────────────────────────────────

    def _ensure_session(self, snap, shares):
        if self.grid_ready:
            return True
        ref = snap.get('prev_close')
        price = snap.get('price')
        unit = snap.get('unit_cash') or 0.0
        if not ref or ref <= 0:
            self.state = 'ARMING'
            self.status = 'adventure waiting — no previous close yet'
            self.grid = []
            return False
        self.reference_close = ref
        if price is None:
            self.state = 'ARMING'
            self.status = 'adventure waiting — no quote yet'
            self._preview_grid(ref, shares, unit)
            return False
        if snap.get('phase') != 'REGULAR':
            self.state = 'WAIT_OPEN'
            self.status = (f'adventure starts at the regular open '
                           f'(prev close {self._fp(ref)})')
            self._preview_grid(ref, shares, unit)
            return False
        if unit <= 0:
            self.state = 'ARMING'
            self.status = 'adventure waiting — unit cash unknown'
            return False

        # First regular quote of the day → fix the coordinate system.
        # Normal day: the anchor IS the previous close, sitting at L0.
        # Gap day (open beyond ±3% of it): the anchor IS the opening price
        # itself, sitting at L∓1 — every level is 3%-of-the-open away.
        self.opening_price = price
        gap = price / ref - 1.0
        if gap <= -GAP_THRESHOLD:
            self.anchor = price
            self.anchor_level = -1
            self.current_level = -1
            self.gap_mode = 'DOWN'
            self.gap_pending = True     # the minimum weight-1 BUY is due
        elif gap >= GAP_THRESHOLD:
            self.anchor = price
            self.anchor_level = +1
            self.current_level = +1
            self.gap_mode = 'UP'
            self.gap_pending = True     # the minimum weight-1 SELL is due
        else:
            self.anchor = ref
            self.anchor_level = 0
            self.current_level = 0
            self.gap_mode = 'NONE'
            self.gap_pending = False
        self.base_inventory = shares
        self.unit_qty = max(1, round_half_up(unit / self.anchor))
        self.grid_ready = True
        self.dirty = True
        self._log(f'adventure {self.trading_date}: anchor '
                  f'{self._fp(self.anchor)} at L{self.anchor_level:+d} '
                  f'({self.gap_mode.lower()} gap {gap * 100:+.2f}%), '
                  f'base {shares} sh, unit {self.unit_qty} sh, '
                  f'level {self.current_level:+d}')
        return True

    def _preview_grid(self, ref, shares, unit):
        """Display-only grid around the prev close before the open."""
        u = max(1, round_half_up(unit / ref)) if unit > 0 else 0
        self.grid = [{'level': k, 'price': level_raw_price(ref, k),
                      'target': target_inventory(k, shares, u),
                      'anchor': k == 0}
                     for k in range(LEVEL_CAP, -LEVEL_CAP - 1, -1)]

    def _refresh_grid(self):
        self.grid = [{'level': k, 'price': self._raw(k),
                      'target': self._target(k),
                      'anchor': k == self.anchor_level}
                     for k in range(LEVEL_CAP, -LEVEL_CAP - 1, -1)]

    # ── Main decision cycle ───────────────────────────────────────────────────

    def poll(self, snap: dict) -> list:
        self._roll_day(snap)
        shares = int(snap.get('shares') or 0)
        self._expire_pending(snap, shares)
        self._detect_fills(snap, shares)
        self._prev_shares = shares
        if self.last_shares != shares:
            self.last_shares = shares         # persisted restart bookkeeping
            self.dirty = True
        self.trigger = {'BUY': None, 'SELL': None}
        self.trigger_note = None
        if not self._ensure_session(snap, shares):
            return []
        self._refresh_grid()
        return self._watch(snap, shares)

    def _gap_action(self, snap, shares):
        """The one-time minimum weight-1 action of a compressed gap open:
        reconcile inventory to the target of the level the opening price
        became (∓1). Cleared once inventory matches; while it cannot run
        (no cash / WATCH mode) it simply stays due and retries."""
        lvl = self.current_level
        target = self._target(lvl)
        delta = target - shares
        acts = []
        if delta == 0:
            self.gap_pending = False
            self.dirty = True
            self._log(f'gap action done — inventory {shares} matches '
                      f'level {lvl:+d}')
            return acts
        side = 'BUY' if delta > 0 else 'SELL'
        qty = abs(delta)
        line = self._order_price(side, lvl)
        label = f'gap {side} L{lvl:+d} → {target}'
        can = snap.get('can_trade', True)
        if side == 'BUY':
            bp = snap.get('buying_power')
            cost = qty * line
            if bp is not None and cost > bp:
                self.trigger_note = (f'▼ gap BUY due at L{lvl:+d} — needs '
                                     f'{self._fp(cost)} but reserve '
                                     f'{self._fp(bp)}: skipped until the '
                                     f'army returns')
                self.status = (f'[L{lvl:+d}] gap BUY unfunded '
                               f'({self._fp(cost)} > {self._fp(bp)})')
                return acts
        self.trigger[side] = {'side': side, 'price': line, 'qty': qty,
                              'label': label, 'level': lvl, 'target': target}
        if not can:
            self.status = (f'[L{lvl:+d}] gap {side} due: {qty} @ '
                           f'{self._fp(line)} (watching only)')
            return acts
        self._place(acts, side, line, qty, label, lvl, target)
        self.state = 'ORDER_PENDING'
        self.status = (f'[L{lvl:+d}] gap {side} fired: {qty} @ '
                       f'{self._fp(line)}')
        return acts

    def _watch(self, snap, shares):
        price = snap.get('price')
        lvl = self.current_level
        tag = f'L{lvl:+d}'
        if price is None:
            self.state = 'WATCHING'
            self.status = f'[{tag}] no price — watching paused'
            return []

        # Down-side affordability every poll (muted line + note in the UI).
        down = lvl - 1 if lvl - 1 >= -LEVEL_CAP else None
        if down is not None:
            d_delta = self._target(down) - shares
            if d_delta > 0:
                cost = d_delta * self._order_price('BUY', down)
                bp = snap.get('buying_power')
                if bp is not None and cost > bp:
                    if self.buy_state != 'EXHAUSTED':
                        self.buy_state = 'EXHAUSTED'
                        if not self._exhaust_logged:
                            self._exhaust_logged = True
                            self._log(f'army short for level {down:+d} '
                                      f'(needs {self._fp(cost)})')
                else:
                    self.buy_state = 'OK'
                    self._exhaust_logged = False
            else:
                self.buy_state = 'OK'

        buys, sells = self._split_orders(snap)
        if self._pending:
            resting = buys if self._pending['side'] == 'BUY' else sells
            if resting:
                self.state = 'ORDER_PENDING'
                self.status = (f"[{tag}] {self._pending['side']} "
                               f"{self._pending['qty']} @ "
                               f"{self._fp(self._pending['price'])} resting "
                               f"— waiting for the fill")
                return []
        elif buys or sells:
            # Foreign order on a bot-exclusive ticker: no new transitions.
            self.state = 'WATCHING'
            sides = '/'.join(sorted({o.get('side') or '?'
                                     for o in buys + sells}))
            self.status = (f'[{tag}] {sides} order resting on Toss (not '
                           f'mine) — adventure paused until it clears')
            return []

        self.state = 'WATCHING'
        if snap.get('phase') != 'REGULAR':
            self.status = (f'[{tag}] session over — adventure sleeps '
                           f'(inventory {shares})')
            return []

        up = lvl + 1 if lvl + 1 <= LEVEL_CAP else None
        transition = None
        if up is not None and price >= self._raw(up) * (1 - _CROSS_EPS):
            transition = up
        elif down is not None and price <= self._raw(down) * (1 + _CROSS_EPS):
            transition = down
        if transition is None:
            if self.gap_pending:
                return self._gap_action(snap, shares)
            lo = self._fp(self._raw(down)) if down is not None else 'edge'
            hi = self._fp(self._raw(up)) if up is not None else 'edge'
            self.status = (f'[{tag}] watching: {lo} < now {self._fp(price)} '
                           f'< {hi} · inv {shares}')
            return []

        target = self._target(transition)
        delta = target - shares
        can = snap.get('can_trade', True)
        acts = []

        if delta == 0:
            # Bound-clamped (e.g. nothing to sell): the grid keeps tracking
            # price so the way back down can trade again.
            self.current_level = transition
            self.gap_pending = False
            self.dirty = True
            self._log(f'level {transition:+d} reached — target equals '
                      f'inventory ({shares}), nothing to trade')
            self.status = (f'[L{transition:+d}] level advanced without a '
                           f'trade (bounds) · inv {shares}')
            return acts

        if delta < 0:
            qty = -delta
            line = self._order_price('SELL', transition)
            label = f'grid SELL L{transition:+d} → {target}'
            self.trigger['SELL'] = {'side': 'SELL', 'price': line,
                                    'qty': qty, 'label': label,
                                    'level': transition, 'target': target}
            if not can:
                self.status = (f'[{tag}] SELL due → L{transition:+d}: {qty} '
                               f'@ {self._fp(line)} (watching only)')
                return acts
            self.gap_pending = False    # the transition supersedes it
            self._place(acts, 'SELL', line, qty, label, transition, target)
            self.state = 'ORDER_PENDING'
            self.status = (f'[{tag}] SELL fired → L{transition:+d}: {qty} '
                           f'@ {self._fp(line)}')
            return acts

        qty = delta
        line = self._order_price('BUY', transition)
        cost = qty * line
        bp = snap.get('buying_power')
        if bp is not None and cost > bp:
            # The transition is NOT taken: the level stays, and this buy
            # fires by itself whenever the army returns (e.g. after a sell).
            self.trigger_note = (f'▼ L{transition:+d} crossed — needs '
                                 f'{self._fp(cost)} but reserve '
                                 f'{self._fp(bp)}: buy skipped until the '
                                 f'army returns')
            self.status = (f'[{tag}] L{transition:+d} crossed but no army '
                           f'({self._fp(cost)} > {self._fp(bp)}) — '
                           f'sell side stays watched')
            return acts
        label = f'grid BUY L{transition:+d} → {target}'
        self.trigger['BUY'] = {'side': 'BUY', 'price': line, 'qty': qty,
                               'label': label, 'level': transition,
                               'target': target}
        if not can:
            self.status = (f'[{tag}] BUY due → L{transition:+d}: {qty} '
                           f'@ {self._fp(line)} (watching only)')
            return acts
        self.gap_pending = False        # the transition supersedes it
        self._place(acts, 'BUY', line, qty, label, transition, target)
        self.state = 'ORDER_PENDING'
        self.status = (f'[{tag}] BUY fired → L{transition:+d}: {qty} '
                       f'@ {self._fp(line)}')
        return acts
