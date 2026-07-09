"""Daily 443 V-Commandos autopilot engine (pure logic) — WATCHER model.

The bot pre-places NOTHING (§30.8). It watches the live price against the
pedal's lines and fires a real LIMIT order only at the moment a line is
crossed:

    EMPTY:     price <= LOAD  → BUY one unit       (load  = anchor − load%)
    DEPLOYED:  price <= CHASE → BUY the chase qty  (chase = avg − chase%)
               price >= SELL  → SELL everything    (sell  = avg + sell%)

While an order of OURS rests (just fired, not yet filled) the watcher waits —
no duplicate requests. If the opposite line triggers while our unfilled order
rests, ours is cancelled and the new side fires (the exit has priority).
Foreign orders (placed manually in the app/web) are never cancelled — the
watcher just waits, and external fills flow back through the account data,
so app/API trading coexist without friction.

Pedals (§30.9) — selectable, same ~7% zone:

    '443' (default):  load 4 / chase 4 / sell 3, chase qty = floor(n/2)
    '352' (safe):     load 3 / chase 5 / sell 2, chase qty = ceil(n/2)

Anchor (§6/§30.7): prev close at the day start; after an intraday full sell
the anchor becomes the sell price and the reload sits sell% below it
(re-enter near the old cost); next day reverts to prev close − load%.

This module has NO network and NO tkinter — the controller feeds it one
snapshot per poll and executes the actions it returns.
"""

from datetime import datetime

from core.calc import trim_buy_price, trim_sell_price

# ── Pedals ───────────────────────────────────────────────────────────────────
PEDALS = {
    '443': {'load': 4, 'chase': 4, 'sell': 3, 'reload': 3, 'qty': 'floor'},
    '352': {'load': 3, 'chase': 5, 'sell': 2, 'reload': 2, 'qty': 'ceil'},
}
DEFAULT_PEDAL = '443'

POLL_SECONDS = 10        # watcher tick (§30.8: 15s was too dull)
FEE_BUFFER   = 1.005     # reserve must cover price*qty*buffer before a buy

# §30.7 test phase: the reserve pre-check is UNLOCKED (dry-run keeps trading
# on paper; live mode relies on Toss rejecting unaffordable buys — that broker
# rejection still stops the bot). Flip to True to restore the self-stop gate.
RESERVE_GATE = False

_PRICE_EPS_US = 0.005


def chase_qty(shares: int, rounding: str = 'floor') -> int:
    """Chase size = half the position (§30.2/§30.9): '443' takes the smaller
    half (floor, loosen the chase), '352' the bigger half (ceil, aggressive):
    9 → 4 vs 5, 13 → 6 vs 7. Minimum 1."""
    shares = int(shares)
    if shares <= 0:
        return 0
    q = (shares + 1) // 2 if rounding == 'ceil' else shares // 2
    return max(1, q)


def load_qty(unit_cash: float, load_price: float) -> int:
    """One unit of cash at the load price, floored (conservative), min 1."""
    if not unit_cash or not load_price or load_price <= 0:
        return 0
    return max(1, int(unit_cash // load_price))


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
        'trading_date': str          market-local date 'YYYY-MM-DD',
        'prev_close':   float|None   previous completed close (anchor),
        'can_trade':    bool         False in WATCH mode → triggers are
                                     reported in the status but NOT fired,
    }

    actions: ('cancel', order_id, label) — only ever our own orders —
             ('place', side, price, qty, label), ('stop', reason).
    """

    def __init__(self, ticker: str, anchor=None, trading_date=None, log=None,
                 pedal: str = DEFAULT_PEDAL):
        self.ticker = ticker
        self.currency = 'KRW' if ticker.endswith('.KS') else 'USD'
        self.state = 'ARMING'       # ARMING → EMPTY / DEPLOYED → (STOPPED)
        self.anchor = anchor
        self.anchor_source = 'close'   # 'close' (day load) | 'sell' (reload)
        self.trading_date = trading_date
        self.pedal_name = pedal if pedal in PEDALS else DEFAULT_PEDAL
        self.stopped_reason = None
        self.chase_count = 0
        self.lines = {}             # {'load'|'chase'|'sell'|'psell': (price, qty)}
        self.status = 'arming'
        self.events = []            # THIS campaign's fills; cleared on full sell
        self._prev_shares = None
        self._prev_avg = 0.0
        self._my_sell_price = None
        self._log = log or (lambda msg: None)

    # ── Pedal ─────────────────────────────────────────────────────────────────

    @property
    def pedal(self) -> dict:
        return PEDALS[self.pedal_name]

    def set_pedal(self, name: str):
        """Switch 443 ↔ 352. Watcher model: nothing to cancel — the lines
        just move and the watcher waits for the new ones (§30.9)."""
        if name in PEDALS and name != self.pedal_name:
            self.pedal_name = name
            p = PEDALS[name]
            self._log(f"pedal → {name} (load {p['load']} / chase {p['chase']}"
                      f" / sell {p['sell']}, qty {p['qty']})")

    # ── Small helpers ─────────────────────────────────────────────────────────

    def _eps(self) -> float:
        return 0.0 if self.currency == 'KRW' else _PRICE_EPS_US

    def stop(self, reason: str):
        self.state = 'STOPPED'
        self.stopped_reason = reason
        self.status = f'STOPPED — {reason}'
        self._log(f'STOP: {reason}')

    def resume(self):
        if self.state == 'STOPPED':
            self.state = 'ARMING'
            self.stopped_reason = None
            self._log('resumed')

    def _reserve_ok(self, snap, price, qty) -> bool:
        if not RESERVE_GATE:
            return True          # §30.7 test phase: gate unlocked
        bp = snap.get('buying_power')
        if bp is None:
            return True
        return bp >= price * qty * FEE_BUFFER

    # ── Campaign fill log (§30.8: campaign-scoped, not daily) ────────────────

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
                # Armed onto an existing position: seed the campaign log.
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
                # Campaign closed: clear the log; the next LOAD starts fresh.
                self._log(f'SELL filled: -{prev} @ '
                          f'{self._my_sell_price or 0:,.0f} — campaign closed')
                self.events = []
                if self._my_sell_price:
                    self.anchor = self._my_sell_price
                    self.anchor_source = 'sell'
                    self._log(f'anchor reset to {self.anchor:,.0f} '
                              f"(-{self.pedal['reload']}% reload)")
            else:
                # Partial / external sell: campaign continues.
                self._event('SELL', shares - prev, None, shares, avg)
                self._log(f'partial sell: {prev} → {shares} shares')

    # ── Day rollover (§6): the campaign log survives, the anchor resets ──────

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
        if self.state == 'STOPPED':
            return []
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

    # ── DEPLOYED: watch both lines, fire only on touch (§30.8) ───────────────

    def _poll_deployed(self, snap, shares):
        self.state = 'DEPLOYED'
        p = self.pedal
        avg = float(snap.get('avg_cost') or 0)
        if avg <= 0:
            self.status = 'deployed — waiting for avg cost'
            return []

        sell_p = trim_sell_price(self.ticker, avg * (1 + p['sell'] / 100.0))
        chase_p = trim_buy_price(self.ticker, avg * (1 - p['chase'] / 100.0))
        cq = chase_qty(shares, p['qty'])
        self.lines = {'sell': (sell_p, shares), 'chase': (chase_p, cq)}

        price = snap.get('price')
        if price is None:
            self.status = 'no price — watching paused'
            return []
        buys, sells = self._split_orders(snap)
        can = snap.get('can_trade', True)

        if price >= sell_p:
            if sells:
                self.status = f'sell resting — waiting for the fill'
                return []
            if not can:
                self.status = (f'SELL trigger met @ {price:,.0f} '
                               f'(watching only)')
                return []
            acts = [('cancel', b['id'], 'our buy (exit first)')
                    for b in buys if b.get('mine')]
            acts.append(('place', 'SELL', sell_p, shares,
                         f"+{p['sell']}% exit"))
            self._my_sell_price = sell_p
            self.status = f'SELL fired: {shares} @ {sell_p:,.0f}'
            return acts

        if price <= chase_p:
            if buys:
                self.status = 'buy resting — waiting for the fill'
                return []
            if not can:
                self.status = (f'CHASE trigger met @ {price:,.0f} '
                               f'(watching only)')
                return []
            if not self._reserve_ok(snap, chase_p, cq):
                self.stop(f'reserve cannot fund next chase ({cq} @ {chase_p:,.0f})')
                return [('stop', self.stopped_reason)]
            acts = [('cancel', s['id'], 'our sell (chase first)')
                    for s in sells if s.get('mine')]
            acts.append(('place', 'BUY', chase_p, cq, f"-{p['chase']}% chase"))
            self.status = f'CHASE fired: {cq} @ {chase_p:,.0f}'
            return acts

        self.status = (f'watching: {chase_p:,.0f} < now {price:,.0f} '
                       f'< {sell_p:,.0f}')
        return []

    # ── EMPTY: watch the load line only (§30.8) ───────────────────────────────

    def _poll_empty(self, snap):
        self.state = 'EMPTY'
        p = self.pedal
        if not self.anchor or self.anchor <= 0:
            if snap.get('prev_close'):
                self.anchor = snap['prev_close']
                self.anchor_source = 'close'
                self._log(f'anchor = prev close {self.anchor:,.0f}')
            else:
                self.status = 'empty — waiting for anchor (prev close)'
                self.lines = {}
                return []

        drop = p['reload'] if self.anchor_source == 'sell' else p['load']
        load_p = trim_buy_price(self.ticker, self.anchor * (1 - drop / 100.0))
        lq = load_qty(snap.get('unit_cash'), load_p)
        self.lines = {'load': (load_p, lq),
                      'psell': (trim_sell_price(
                          self.ticker, load_p * (1 + p['sell'] / 100.0)), lq)}
        if lq <= 0:
            self.status = 'empty — unit cash unknown'
            return []

        price = snap.get('price')
        if price is None:
            self.status = 'no price — watching paused'
            return []
        buys, _sells = self._split_orders(snap)

        if price <= load_p:
            if buys:
                self.status = 'load buy resting — waiting for the fill'
                return []
            if not snap.get('can_trade', True):
                self.status = (f'LOAD trigger met @ {price:,.0f} '
                               f'(watching only)')
                return []
            if not self._reserve_ok(snap, load_p, lq):
                self.stop(f'reserve cannot fund the load ({lq} @ {load_p:,.0f})')
                return [('stop', self.stopped_reason)]
            self.status = f'LOAD fired: {lq} @ {load_p:,.0f}'
            return [('place', 'BUY', load_p, lq, f'-{drop}% load')]

        self.status = (f'watching: load {load_p:,.0f} '
                       f'< now {price:,.0f} (anchor {self.anchor:,.0f}, '
                       f'-{drop}%)')
        return []
