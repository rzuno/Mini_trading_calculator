"""Daily 443 V-Commandos autopilot engine (pure logic).

Implements the state machine of "Daily 443 V-Commandos Autopilot Manual v001"
(§7-§18, §30 addendum). This module has NO network and NO tkinter — the GUI
controller feeds it one snapshot per poll cycle and executes the actions it
returns, so the whole strategy is testable offline.

Strategy constants (the "443 rule"):
    LOAD  = anchor   × 0.96   (enter after a -4% daily pullback)
    CHASE = avg_cost × 0.96   (chase the average -4% down)
    SELL  = avg_cost × 1.03   (escape everything on a +3% bounce)

Order policy (§30.1): only ONE side can rest on Toss at a time
(opposite-pending). The SELL is the resting exit door; the buy side is
watched by the poll and fired on touch (cancel SELL → place BUY → after the
fill the new SELL is placed). A BUY that doesn't fill keeps resting and the
sell side becomes the watched one — symmetric harpooning.

Stop rule (§30.4): the engine stops itself ONLY when the reserve cannot fund
the next buy. Everything else is the controller's problem (log / backoff).
"""

from core.calc import trim_buy_price, trim_sell_price

# ── 443 constants ────────────────────────────────────────────────────────────
LOAD_DROP_PCT   = 4
CHASE_DROP_PCT  = 4
SELL_GAIN_PCT   = 3
RELOAD_DROP_PCT = 3      # §30.7: reload after an intraday full sell sits at
                         # -3% of the sell price (re-enter near the old cost)

POLL_SECONDS   = 15      # per-stock poll interval (§30.3)
MAX_AUTOPILOT  = 2       # hard cap on simultaneously autopiloted stocks (§30.6)
FEE_BUFFER     = 1.005   # reserve must cover price*qty*buffer before a buy

# §30.7 test phase: the reserve pre-check is UNLOCKED (dry-run keeps trading
# on paper; live mode relies on Toss rejecting unaffordable buys — that broker
# rejection still stops the bot). Flip to True to restore the self-stop gate.
RESERVE_GATE = False

# US prices come back with float noise; KR prices are ints so exact compare.
_PRICE_EPS_US = 0.005


def chase_qty(shares: int) -> int:
    """Conservative half (§30.2): floor(shares/2), minimum 1.
    Sequence: 1→1, 2→1, 3→1, 4→2, 6→3, 9→4 (buy amount for each holding)."""
    shares = int(shares)
    if shares <= 0:
        return 0
    return max(1, shares // 2)


def load_qty(unit_cash: float, load_price: float) -> int:
    """One unit of cash at the load price, floored (conservative), min 1."""
    if not unit_cash or not load_price or load_price <= 0:
        return 0
    return max(1, int(unit_cash // load_price))


class Daily443Engine:
    """One autopiloted stock. Feed `poll(snap)` every cycle; execute the
    returned actions in order.

    snap = {
        'price':        float|None   live price,
        'shares':       int          held shares (source of truth: broker),
        'avg_cost':     float        broker average cost,
        'orders':       [{'id', 'side', 'price', 'qty_open', 'filled'}],
        'buying_power': float|None   cash reserve in this stock's currency,
        'unit_cash':    float        1 unit in this stock's currency,
        'trading_date': str          market-local date 'YYYY-MM-DD',
        'prev_close':   float|None   previous completed close (for anchor),
    }

    actions = list of tuples:
        ('cancel', order_id, label)
        ('place',  side, price, qty, label)     price already trimmed
        ('stop',   reason)                       engine stopped itself
    """

    def __init__(self, ticker: str, anchor=None, trading_date=None, log=None):
        self.ticker  = ticker
        self.currency = 'KRW' if ticker.endswith('.KS') else 'USD'
        self.state   = 'ARMING'     # ARMING → EMPTY / DEPLOYED → (STOPPED)
        self.anchor  = anchor       # LOAD reference while EMPTY (§6)
        self.anchor_source = 'close'   # 'close' (-4% load) | 'sell' (-3% reload)
        self.trading_date = trading_date
        self.stopped_reason = None
        self.chase_count = 0        # chase fills today (log/telemetry only)
        self.lines = {}             # {'load'|'chase'|'sell'|'psell': (price, qty)}
        self.status = 'arming'      # one-line human status for the UI
        self.events = []            # today's fills: {ts, kind, qty, price, shares, avg}
        self._prev_shares = None
        self._prev_avg = 0.0
        self._my_sell_price = None  # anchor source after a full sell (§11)
        self._log = log or (lambda msg: None)

    # ── Small helpers ─────────────────────────────────────────────────────────

    def _eps(self) -> float:
        return 0.0 if self.currency == 'KRW' else _PRICE_EPS_US

    def _same_price(self, a, b) -> bool:
        return a is not None and b is not None and abs(a - b) <= self._eps()

    def stop(self, reason: str):
        self.state = 'STOPPED'
        self.stopped_reason = reason
        self.status = f'STOPPED — {reason}'
        self._log(f'STOP: {reason}')

    # ── Fill detection (broker is the source of truth, §17) ──────────────────

    def _event(self, kind, qty, price, shares, avg):
        from datetime import datetime
        self.events.append({'ts': datetime.now().strftime('%H:%M:%S'),
                            'kind': kind, 'qty': qty, 'price': price,
                            'shares': shares, 'avg': avg})
        del self.events[:-300]

    def _detect_fills(self, snap, shares: int):
        prev = self._prev_shares
        if prev is None:
            return
        avg = float(snap.get('avg_cost') or 0)
        if shares > prev:
            qty = shares - prev
            if prev == 0:
                # The broker avg IS the actual load fill price.
                price = avg or (self.lines.get('load') or (None,))[0]
                self._event('LOAD', qty, price, shares, avg)
                self._log(f'LOAD filled: 0 → {shares} shares')
            else:
                self.chase_count += 1
                # Actual chase fill price backed out of the avg change.
                price = ((avg * shares - self._prev_avg * prev) / qty
                         if avg > 0 and self._prev_avg > 0 else None)
                self._event('CHASE', qty, price, shares, avg)
                self._log(f'CHASE filled: {prev} → {shares} shares '
                          f'(chase #{self.chase_count} today)')
        elif prev > 0 and shares == 0:
            # Full sell → the next load anchors on the sell price, at the
            # closer -3% reload so a repeated dip re-enters near the old
            # cost (§30.7). Next day it reverts to prev close / -4%.
            self._event('SELL', -prev, self._my_sell_price, 0, 0.0)
            if self._my_sell_price:
                self.anchor = self._my_sell_price
                self.anchor_source = 'sell'
                self._log(f'SELL filled: anchor reset to {self.anchor:,.0f} '
                          f'(-{RELOAD_DROP_PCT}% reload)')
            else:
                self._log('SELL filled (external?) — anchor kept')

    # ── Day rollover (§6): new day → anchor = previous close ─────────────────

    def _roll_day(self, snap):
        d = snap.get('trading_date')
        if not d or d == self.trading_date:
            return
        self.trading_date = d
        self.chase_count = 0
        self.events = []
        if snap['shares'] <= 0 and snap.get('prev_close'):
            self.anchor = snap['prev_close']
            self.anchor_source = 'close'      # -4% load again (§6/§30.7)
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

        price = snap.get('price')
        acts = (self._poll_deployed(snap, shares, price) if shares > 0
                else self._poll_empty(snap, price))
        return acts

    def _reserve_ok(self, snap, price, qty) -> bool:
        if not RESERVE_GATE:
            return True          # §30.7 test phase: gate unlocked
        bp = snap.get('buying_power')
        if bp is None:
            return True          # unknown reserve: let the broker be the judge
        return bp >= price * qty * FEE_BUFFER

    # ── DEPLOYED (§10): resting SELL, watched CHASE ───────────────────────────

    def _poll_deployed(self, snap, shares, price):
        self.state = 'DEPLOYED'
        avg = float(snap.get('avg_cost') or 0)
        if avg <= 0:
            self.status = 'deployed — waiting for avg cost'
            return []

        sell_p  = trim_sell_price(self.ticker, avg * (1 + SELL_GAIN_PCT / 100.0))
        chase_p = trim_buy_price(self.ticker, avg * (1 - CHASE_DROP_PCT / 100.0))
        cq = chase_qty(shares)
        self.lines = {'sell': (sell_p, shares), 'chase': (chase_p, cq)}

        buys  = [o for o in snap.get('orders', []) if o.get('side') == 'BUY']
        sells = [o for o in snap.get('orders', []) if o.get('side') == 'SELL']
        acts = []

        if buys:
            # A chase/load BUY is resting. Leave it alone (partial fills fold in
            # via the broker avg) — unless the bounce arrives first: then the
            # exit takes priority (cancel BUY → SELL everything).
            for s in sells:      # both sides resting should be impossible
                acts.append(('cancel', s['id'], 'stray SELL'))
            if price is not None and price >= sell_p:
                for b in buys:
                    acts.append(('cancel', b['id'], 'chase (bounce hit)'))
                acts.append(('place', 'SELL', sell_p, shares, f'+{SELL_GAIN_PCT}% exit'))
                self._my_sell_price = sell_p
                self.status = f'bounce hit — selling all {shares} @ {sell_p:,.0f}'
            else:
                self.status = (f'chase resting {buys[0].get("qty_open")} @ '
                               f'{buys[0].get("price"):,.0f}; sell watched @ {sell_p:,.0f}')
            return acts

        if sells:
            s0 = sells[0]
            for s in sells[1:]:
                acts.append(('cancel', s['id'], 'duplicate SELL'))
            if price is not None and price <= chase_p:
                # Chase touched: reserve gate first (§30.4), then swap sides.
                if not self._reserve_ok(snap, chase_p, cq):
                    acts.append(('stop',
                                 f'reserve cannot fund next chase '
                                 f'({cq} @ {chase_p:,.0f})'))
                    self.stop(f'reserve cannot fund next chase ({cq} @ {chase_p:,.0f})')
                    return acts
                acts.append(('cancel', s0['id'], 'sell (chase hit)'))
                acts.append(('place', 'BUY', chase_p, cq, f'-{CHASE_DROP_PCT}% chase'))
                self.status = f'chase hit — buying {cq} @ {chase_p:,.0f}'
                return acts
            # Maintain the exit door (§16): replace only on a real mismatch and
            # never a partially-filled order.
            if (float(s0.get('filled') or 0) <= 0
                    and (not self._same_price(s0.get('price'), sell_p)
                         or int(s0.get('qty_open') or 0) != shares)):
                acts.append(('cancel', s0['id'], 'stale SELL'))
                acts.append(('place', 'SELL', sell_p, shares, f'+{SELL_GAIN_PCT}% exit'))
                self._my_sell_price = sell_p
                self.status = f'sell refreshed: {shares} @ {sell_p:,.0f}'
            else:
                self._my_sell_price = s0.get('price') or sell_p
                self.status = (f'sell resting {shares} @ {self._my_sell_price:,.0f}; '
                               f'chase watched @ {chase_p:,.0f}')
            return acts

        # Nothing resting → the exit door goes up.
        acts.append(('place', 'SELL', sell_p, shares, f'+{SELL_GAIN_PCT}% exit'))
        self._my_sell_price = sell_p
        self.status = f'placing sell {shares} @ {sell_p:,.0f}'
        return acts

    # ── ARMED_EMPTY (§9): one resting LOAD ────────────────────────────────────

    def _poll_empty(self, snap, price):
        self.state = 'EMPTY'
        if not self.anchor or self.anchor <= 0:
            if snap.get('prev_close'):
                self.anchor = snap['prev_close']
                self.anchor_source = 'close'
                self._log(f'anchor = prev close {self.anchor:,.0f}')
            else:
                self.status = 'empty — waiting for anchor (prev close)'
                self.lines = {}
                return []

        # -4% from the day anchor; -3% when re-arming off an intraday sell.
        drop = RELOAD_DROP_PCT if self.anchor_source == 'sell' else LOAD_DROP_PCT
        load_p = trim_buy_price(self.ticker, self.anchor * (1 - drop / 100.0))
        lq = load_qty(snap.get('unit_cash'), load_p)
        # 'psell' = the pseudo exit the daily chart draws above the load.
        self.lines = {'load': (load_p, lq),
                      'psell': (trim_sell_price(
                          self.ticker, load_p * (1 + SELL_GAIN_PCT / 100.0)), lq)}
        if lq <= 0:
            self.status = 'empty — unit cash unknown'
            return []

        buys  = [o for o in snap.get('orders', []) if o.get('side') == 'BUY']
        sells = [o for o in snap.get('orders', []) if o.get('side') == 'SELL']
        acts = [('cancel', s['id'], 'stray SELL (no shares)') for s in sells]

        if buys:
            b0 = buys[0]
            for b in buys[1:]:
                acts.append(('cancel', b['id'], 'duplicate LOAD'))
            if (float(b0.get('filled') or 0) <= 0
                    and (not self._same_price(b0.get('price'), load_p)
                         or int(b0.get('qty_open') or 0) != lq)):
                acts.append(('cancel', b0['id'], 'stale LOAD'))
                acts.append(('place', 'BUY', load_p, lq, f'-{drop}% load'))
                self.status = f'load refreshed: {lq} @ {load_p:,.0f}'
            else:
                self.status = (f'load resting {lq} @ {b0.get("price"):,.0f} '
                               f'(anchor {self.anchor:,.0f}, -{drop}%)')
            return acts

        if not self._reserve_ok(snap, load_p, lq):
            acts.append(('stop', f'reserve cannot fund the load ({lq} @ {load_p:,.0f})'))
            self.stop(f'reserve cannot fund the load ({lq} @ {load_p:,.0f})')
            return acts
        acts.append(('place', 'BUY', load_p, lq, f'-{drop}% load'))
        self.status = f'placing load {lq} @ {load_p:,.0f}'
        return acts
