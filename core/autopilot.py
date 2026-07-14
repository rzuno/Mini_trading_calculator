"""Daily V-Commandos autopilot engine (pure logic) — ADAPTIVE GEARS + WATCHER.

WATCHER model (manual §30.8): the bot pre-places NOTHING. It watches the
live price every poll and fires a real LIMIT order only when a line is
crossed. While our own unfilled order rests it waits; if the opposite line
triggers, ours is cancelled and the new side fires (exit first). Foreign
app/web orders are never cancelled.

ADAPTIVE GEARS (manual PART II): the fixed 443/352 pedals are replaced by
independent BUY and SELL gears chosen automatically from the stock's
deployment ratio = actual cost basis ÷ total army (both in units):

    BUY:   <25% → B1 (−4%, ×1/2)   <50% → B2 (−5%, ×2/3)   ≥50% → B3 (−6%, ×3/4)
    SELL:  <25% → S3 (+3%)          <50% → S2 (+2.5%)        ≥50% → S1 (+2%)

Early integer overrides (exact, PART II §7): 1 share → B3, 3 or 5 → B2.
Quantities round HALF-UP (7×1/2=3.5→4), minimum 1.

LOAD is always anchor −4% for ~1 unit; the same-day RELOAD after a full
exit is the sell price −3%; next day back to prev close −4%.

Global Army Manager (PART II §12-§16, active only when RESERVE_GATE=True):
tradable army = buying power − 0.5-unit permanent safety reserve (Toss
buying power already nets out resting BUY reservations). If the normal
chase is unaffordable, fall back to B1 (−4%) with the maximum affordable
quantity when that is ≥30% of current shares; below 30% the buy side goes
EXHAUSTED (one notification; the SELL stays managed; recovers by itself
when army returns).

This module has NO network and NO tkinter.
"""

from datetime import datetime

from core.calc import trim_buy_price, trim_sell_price, round_half_up

# ── Adaptive gears (PART II §5/§9) ───────────────────────────────────────────
BUY_GEARS = {
    1: {'drop': 4, 'frac': 1 / 2, 'label': 'B1 (-4% ×1/2)'},
    2: {'drop': 5, 'frac': 2 / 3, 'label': 'B2 (-5% ×2/3)'},
    3: {'drop': 6, 'frac': 3 / 4, 'label': 'B3 (-6% ×3/4)'},
}
SELL_GEARS = {
    1: {'gain': 2.0, 'label': 'S1 (+2%)'},
    2: {'gain': 2.5, 'label': 'S2 (+2.5%)'},
    3: {'gain': 3.0, 'label': 'S3 (+3%)'},
}

LOAD_DROP_PCT   = 4      # first load of the day: prev close −4%
RELOAD_DROP_PCT = 3      # same-day reload after a full exit: sell −3%

POLL_SECONDS = 10        # watcher tick
SAFETY_RESERVE_UNITS = 0.5   # permanent, never spent (PART II §12)
FALLBACK_MIN_RATIO = 0.30    # affordable/current shares to allow B1 fallback

# Test phase: the Global Army Manager is UNLOCKED (affordability ignored;
# gears stay purely deployment-based). Live mode then relies on Toss
# rejecting unaffordable buys (announced once + backoff, not a hard stop).
# Flip to True when real cash is back to enable §12-§16 fully.
RESERVE_GATE = False

_PRICE_EPS_US = 0.005


def buy_gear_for(deploy_ratio: float, shares: int) -> int:
    """Deployment-zone gear with the exact early integer overrides."""
    if shares == 1:
        return 3
    if shares in (3, 5):
        return 2
    if deploy_ratio < 0.25:
        return 1
    if deploy_ratio < 0.50:
        return 2
    return 3


def sell_gear_for(deploy_ratio: float) -> int:
    if deploy_ratio < 0.25:
        return 3
    if deploy_ratio < 0.50:
        return 2
    return 1


def chase_qty(shares: int, frac: float) -> int:
    """Gear ratio of the position, HALF-UP rounded (PART II §8), min 1."""
    shares = int(shares)
    if shares <= 0:
        return 0
    return max(1, round_half_up(shares * frac))


def load_qty(unit_cash: float, load_price: float) -> int:
    """The integer share count closest to one unit (PART II §4), min 1."""
    if not unit_cash or not load_price or load_price <= 0:
        return 0
    return max(1, round_half_up(unit_cash / load_price))


class Daily443Engine:
    """One watched stock. Feed `poll(snap)` every cycle; execute the returned
    actions in order.

    snap = {
        'price':        float|None   live price,
        'shares':       int          held shares (source of truth: broker),
        'avg_cost':     float        broker average cost,
        'orders':       [{'id','side','price','qty_open','filled','mine'}],
        'buying_power': float|None   cash reserve in this stock's currency,
        'unit_cash':    float        1 unit in this stock's currency,
        'army_units':   float        total army in units (global N),
        'trading_date': str          market-local date 'YYYY-MM-DD',
        'prev_close':   float|None   previous completed close (anchor),
        'can_trade':    bool         False in WATCH mode → triggers reported
                                     in the status but NOT fired,
    }

    actions: ('cancel', order_id, label) — only ever our own orders —
             ('place', side, price, qty, label), ('notify', message).
    """

    def __init__(self, ticker: str, anchor=None, trading_date=None, log=None):
        self.ticker = ticker
        self.currency = 'KRW' if ticker.endswith('.KS') else 'USD'
        self.state = 'ARMING'       # ARMING → EMPTY / DEPLOYED
        self.anchor = anchor
        self.anchor_source = 'close'   # 'close' (−4% load) | 'sell' (−3% reload)
        self.trading_date = trading_date
        self.chase_count = 0
        self.lines = {}             # {'load'|'chase'|'sell'|'psell': (price, qty)}
        self.status = 'arming'
        self.events = []            # THIS campaign's fills; cleared on full sell
        self.buy_gear = None        # 1|2|3 chosen this poll (None while empty)
        self.sell_gear = None
        self.deploy_ratio = 0.0     # cost basis / total army
        self.buy_state = 'OK'       # 'OK' | 'FALLBACK' | 'EXHAUSTED'
        self._exhaust_notified = False
        self._prev_shares = None
        self._prev_avg = 0.0
        self._my_sell_price = None
        self._log = log or (lambda msg: None)

    # ── Small helpers ─────────────────────────────────────────────────────────

    def _eps(self) -> float:
        return 0.0 if self.currency == 'KRW' else _PRICE_EPS_US

    def _tradable(self, snap):
        """Army available for a new buy: buying power minus the permanent
        0.5-unit safety reserve (PART II §12). None = unknown = unlimited.
        Toss buying power already nets out resting BUY-order reservations."""
        if not RESERVE_GATE:
            return None
        bp = snap.get('buying_power')
        if bp is None:
            return None
        unit = snap.get('unit_cash') or 0.0
        return max(0.0, bp - SAFETY_RESERVE_UNITS * unit)

    def _exhaust(self, acts, what):
        """Enter/refresh EXHAUSTED: keep managing the sell, notify ONCE."""
        self.buy_state = 'EXHAUSTED'
        if not self._exhaust_notified:
            self._exhaust_notified = True
            msg = f'army exhausted — {what}. Reinforcement stopped; ' \
                  f'the SELL stays managed and buying resumes when army returns.'
            self._log(f'EXHAUSTED: {what}')
            acts.append(('notify', msg))

    def _buy_ok(self):
        if self.buy_state == 'EXHAUSTED' and self._exhaust_notified:
            self._log('army available again — reinforcement resumes')
        self._exhaust_notified = False

    # ── Campaign fill log (campaign-scoped; cleared on full sell) ────────────

    def _event(self, kind, qty, price, shares, avg):
        self.events.append({'ts': datetime.now().strftime('%m/%d %H:%M'),
                            'kind': kind, 'qty': qty, 'price': price,
                            'shares': shares, 'avg': avg})
        del self.events[:-300]

    def _detect_fills(self, snap, shares: int):
        prev = self._prev_shares
        avg = float(snap.get('avg_cost') or 0)
        if prev is None:
            if shares > 0:
                self._event('HOLD', shares, avg, shares, avg)
                self._log(f'armed with existing position: {shares} @ {avg:,.0f}')
            return
        if shares > prev:
            qty = shares - prev
            if prev == 0:
                price = avg or (self.lines.get('load') or (None,))[0]
                self._event('LOAD', qty, price, shares, avg)
                self._log(f'LOAD filled: 0 → {shares} shares')
            else:
                self.chase_count += 1
                price = ((avg * shares - self._prev_avg * prev) / qty
                         if avg > 0 and self._prev_avg > 0 else None)
                self._event('CHASE', qty, price, shares, avg)
                self._log(f'CHASE filled: {prev} → {shares} shares '
                          f'(chase #{self.chase_count} today)')
        elif shares < prev:
            if shares == 0:
                self._log(f'SELL filled: -{prev} @ '
                          f'{self._my_sell_price or 0:,.0f} — campaign closed')
                self.events = []
                if self._my_sell_price:
                    self.anchor = self._my_sell_price
                    self.anchor_source = 'sell'
                    self._log(f'anchor reset to {self.anchor:,.0f} '
                              f'(-{RELOAD_DROP_PCT}% reload)')
            else:
                self._event('SELL', shares - prev, None, shares, avg)
                self._log(f'partial sell: {prev} → {shares} shares')

    # ── Day rollover: campaign log survives, the anchor resets ───────────────

    def _roll_day(self, snap):
        d = snap.get('trading_date')
        if not d or d == self.trading_date:
            return
        self.trading_date = d
        self.chase_count = 0
        if snap['shares'] <= 0 and snap.get('prev_close'):
            self.anchor = snap['prev_close']
            self.anchor_source = 'close'
            self._log(f'new day {d}: anchor = prev close {self.anchor:,.0f}')

    # ── Main decision cycle ───────────────────────────────────────────────────

    def poll(self, snap: dict) -> list:
        self._roll_day(snap)
        shares = int(snap.get('shares') or 0)
        self._detect_fills(snap, shares)
        self._prev_shares = shares
        self._prev_avg = float(snap.get('avg_cost') or 0)
        return (self._poll_deployed(snap, shares) if shares > 0
                else self._poll_empty(snap))

    @staticmethod
    def _split_orders(snap):
        buys = [o for o in snap.get('orders', []) if o.get('side') == 'BUY']
        sells = [o for o in snap.get('orders', []) if o.get('side') == 'SELL']
        return buys, sells

    def _deployment(self, snap, shares, avg):
        """Actual cost basis ÷ total army, both in units (PART II §3)."""
        unit = snap.get('unit_cash') or 0.0
        army = snap.get('army_units') or 0.0
        if unit <= 0 or army <= 0:
            return 0.0
        return (shares * avg / unit) / army

    # ── DEPLOYED: adaptive lines, fire only on touch ──────────────────────────

    def _poll_deployed(self, snap, shares):
        self.state = 'DEPLOYED'
        avg = float(snap.get('avg_cost') or 0)
        if avg <= 0:
            self.status = 'deployed — waiting for avg cost'
            return []

        # Gears from ACTUAL fills only (PART II §11): a resting, unfilled
        # BUY changes nothing here until the broker shows the new shares.
        ratio = self._deployment(snap, shares, avg)
        self.deploy_ratio = ratio
        bg = buy_gear_for(ratio, shares)
        sg = sell_gear_for(ratio)
        self.buy_gear, self.sell_gear = bg, sg

        sell_gain = SELL_GEARS[sg]['gain']
        sell_p = trim_sell_price(self.ticker, avg * (1 + sell_gain / 100.0))
        chase_p = trim_buy_price(
            self.ticker, avg * (1 - BUY_GEARS[bg]['drop'] / 100.0))
        cq = chase_qty(shares, BUY_GEARS[bg]['frac'])

        acts = []
        chase_label = f"-{BUY_GEARS[bg]['drop']}% chase (B{bg})"
        self.buy_state = 'OK'

        # Global Army Manager (PART II §14-§16), active with the gate on.
        tradable = self._tradable(snap)
        if tradable is not None:
            if cq * chase_p > tradable:
                b1_p = trim_buy_price(self.ticker, avg * 0.96)
                max_aff = int(tradable // b1_p) if b1_p > 0 else 0
                if shares > 0 and max_aff / shares >= FALLBACK_MIN_RATIO:
                    self.buy_state = 'FALLBACK'
                    chase_p, cq = b1_p, max_aff
                    chase_label = f'-4% chase (B1 fallback ×{max_aff})'
                else:
                    self._exhaust(acts,
                                  f'next chase needs {cq} @ {chase_p:,.0f}')
                    chase_p, cq = None, 0
        if self.buy_state != 'EXHAUSTED':
            self._buy_ok()

        self.lines = {'sell': (sell_p, shares)}
        if chase_p:
            self.lines['chase'] = (chase_p, cq)

        price = snap.get('price')
        if price is None:
            self.status = 'no price — watching paused'
            return acts
        buys, sells = self._split_orders(snap)
        can = snap.get('can_trade', True)
        gear_tag = f'B{bg}/S{sg} {ratio * 100:.0f}%'

        if price >= sell_p:
            if sells:
                self.status = f'[{gear_tag}] sell resting — waiting for the fill'
                return acts
            if not can:
                self.status = (f'[{gear_tag}] SELL trigger met @ {price:,.0f} '
                               f'(watching only)')
                return acts
            acts += [('cancel', b['id'], 'our buy (exit first)')
                     for b in buys if b.get('mine')]
            acts.append(('place', 'SELL', sell_p, shares,
                         f'+{sell_gain:g}% exit (S{sg})'))
            self._my_sell_price = sell_p
            self.status = f'[{gear_tag}] SELL fired: {shares} @ {sell_p:,.0f}'
            return acts

        if chase_p and price <= chase_p:
            if buys:
                self.status = f'[{gear_tag}] buy resting — waiting for the fill'
                return acts
            if not can:
                self.status = (f'[{gear_tag}] CHASE trigger met @ {price:,.0f} '
                               f'(watching only)')
                return acts
            acts += [('cancel', s['id'], 'our sell (chase first)')
                     for s in sells if s.get('mine')]
            acts.append(('place', 'BUY', chase_p, cq, chase_label))
            self.status = f'[{gear_tag}] CHASE fired: {cq} @ {chase_p:,.0f}'
            return acts

        if self.buy_state == 'EXHAUSTED':
            self.status = (f'[{gear_tag}] EXHAUSTED — watching sell '
                           f'{sell_p:,.0f} only')
        else:
            tail = ' (B1 fallback)' if self.buy_state == 'FALLBACK' else ''
            self.status = (f'[{gear_tag}] watching: {chase_p:,.0f} '
                           f'< now {price:,.0f} < {sell_p:,.0f}{tail}')
        return acts

    # ── EMPTY: watch the −4% load (−3% reload) only ───────────────────────────

    def _poll_empty(self, snap):
        self.state = 'EMPTY'
        self.buy_gear, self.sell_gear = None, None
        self.deploy_ratio = 0.0
        if not self.anchor or self.anchor <= 0:
            if snap.get('prev_close'):
                self.anchor = snap['prev_close']
                self.anchor_source = 'close'
                self._log(f'anchor = prev close {self.anchor:,.0f}')
            else:
                self.status = 'empty — waiting for anchor (prev close)'
                self.lines = {}
                return []

        drop = (RELOAD_DROP_PCT if self.anchor_source == 'sell'
                else LOAD_DROP_PCT)
        load_p = trim_buy_price(self.ticker, self.anchor * (1 - drop / 100.0))
        unit = snap.get('unit_cash')
        lq = load_qty(unit, load_p)
        acts = []
        self.buy_state = 'OK'

        # Army gate for the load (extension of PART II §15 to the LOAD).
        tradable = self._tradable(snap)
        if lq > 0 and tradable is not None and lq * load_p > tradable:
            max_aff = int(tradable // load_p) if load_p > 0 else 0
            if max_aff >= 1 and max_aff / lq >= FALLBACK_MIN_RATIO:
                self.buy_state = 'FALLBACK'
                lq = max_aff
            else:
                self._exhaust(acts, f'load needs {lq} @ {load_p:,.0f}')
                lq = 0
        if self.buy_state != 'EXHAUSTED':
            self._buy_ok()

        if lq <= 0:
            self.lines = {}
            if self.buy_state != 'EXHAUSTED':
                self.status = 'empty — unit cash unknown'
            else:
                self.status = 'EXHAUSTED — cannot fund the load'
            return acts

        # Pseudo exit above the load, using the sell gear the position WOULD
        # have right after the load fills (usually S3 at ~1 unit deployed).
        unit_val = unit or 0.0
        army = snap.get('army_units') or 0.0
        pratio = ((lq * load_p / unit_val) / army
                  if unit_val > 0 and army > 0 else 0.0)
        psg = sell_gear_for(pratio)
        psell = trim_sell_price(
            self.ticker, load_p * (1 + SELL_GEARS[psg]['gain'] / 100.0))
        self.lines = {'load': (load_p, lq), 'psell': (psell, lq)}

        price = snap.get('price')
        if price is None:
            self.status = 'no price — watching paused'
            return acts
        buys, _sells = self._split_orders(snap)

        if price <= load_p:
            if buys:
                self.status = 'load buy resting — waiting for the fill'
                return acts
            if not snap.get('can_trade', True):
                self.status = (f'LOAD trigger met @ {price:,.0f} '
                               f'(watching only)')
                return acts
            self.status = f'LOAD fired: {lq} @ {load_p:,.0f}'
            acts.append(('place', 'BUY', load_p, lq, f'-{drop}% load'))
            return acts

        self.status = (f'watching: load {load_p:,.0f} < now {price:,.0f} '
                       f'(anchor {self.anchor:,.0f}, -{drop}%)')
        return acts
