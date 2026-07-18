"""Unified autopilot engine — the line watcher (pure logic).

ONE strategy for everything now: the bot follows exactly the lines the stock
CARD computes — same gear tables, same tier splits (core.calc). It watches
the live price every poll and fires a real LIMIT order only when a line is
crossed; after a fill the position changes, every line is recomputed from the
new reality, and it goes back to watching. No strategic play beyond the
lines: no 밑장 빼기, no stages, no idle-day escalation, no gear shifting by
the bot. When the army can't fund a buy the buy side simply stops, its line is
shown muted in the charts, and the SELL keeps being watched — 밑장 빼기 / gear
lowering is the COMMANDER's manual decision.

Lines (all from core.calc, identical to the cards):

    EMPTY     LOAD at vantage × (1 − pct), sized to ~1 unit.
              vantage = previous session close (normal), or — after a
              same-day full exit — the actual sell fill (RE-BAIT exception:
              the bait hangs −4% below the fill, gear 1 regardless of V,
              and dies at the session end / day roll).
    DEPLOYED  CHASE at avg × (1 − pct), sized by the gear ratio
              (-4% ×1/2 … -8% ×1.0 = the whole position again);
              SELL tiers at avg × (1 + tier%), the held shares split across
              the ACTIVE tiers (middle tier alone is the default full exit).
              A buy fill restarts the tier ladder on the whole holding.

Gear source: the card pushes its current config on every compute →
    snap['card'] = {'pct', 'gear', 'tier_pcts', 'tier_actives'}
Exception 1 (same-day re-bait): a campaign opened by the re-bait is PINNED
to gear 1 (chase −4% ×1/2, tiers +1/+3/+5) until its full exit.
Exception 2 (heavy-unit minimum entry gear) lives in the card's auto-gear
logic, so the pct arrives here with it already applied.

WATCH mode never places orders; instead the crossed line is exposed as
`trigger` so the Autopilot window's Buy/Sell buttons can fire it manually.

This module has NO network and NO tkinter. State survives restarts via
to_dict()/`saved` (the controller persists it).
"""

import time
from datetime import datetime

from core.calc import (AUTO_GEARS, RE_BAIT_GEAR, RE_BAIT_PCT,
                       calc_buy_shares, calc_sell_tiers,
                       trim_buy_price, trim_sell_price, round_half_up)

POLL_SECONDS = 10        # watcher tick

_PENDING_STALE_S = 90    # forget an order intent this long after placing it
                         # if nothing rests and nothing filled (DAY order
                         # died / was cancelled outside)

_SAVE_FIELDS = ('anchor', 'anchor_source', 'trading_date', 'gear1_pinned',
                'tier_done', 'chase_count', 'events')

_DEFAULT_ACTIVES = (False, True, False)     # middle tier is the default exit


def default_card_config() -> dict:
    """Fallback when the card has not pushed a config yet (gear 1)."""
    return {'gear': 1, 'pct': AUTO_GEARS[1]['pct'],
            'tier_pcts': list(AUTO_GEARS[1]['tiers']),
            'tier_actives': list(_DEFAULT_ACTIVES)}


class WatcherEngine:
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
        'prev_close':   float|None   previous completed close (vantage),
        'can_trade':    bool         False in WATCH mode → the crossed line
                                     is exposed as `trigger`, NOT fired,
        'card':         dict|None    the card's gear config (see module doc),
    }

    actions: ('cancel', order_id, label) — only ever our own orders —
             ('place', side, price, qty, label), ('notify', message).
    """

    def __init__(self, ticker, trading_date=None, log=None, saved=None):
        self.ticker = ticker
        self.currency = 'KRW' if ticker.endswith('.KS') else 'USD'
        self.state = 'ARMING'          # ARMING → EMPTY / DEPLOYED
        self.status = 'arming…'
        self.lines = {}                # 'load'|'psell'|'chase'|'tier1'|'tier2'|'tier3'
        self.events = []               # THIS campaign's fills; cleared on full sell
        self.trading_date = trading_date

        self.anchor = None             # vantage: prev close or same-day sell fill
        self.anchor_source = 'close'   # 'close' | 'sell' (re-bait)
        self.gear1_pinned = False      # campaign opened by the re-bait → G1
        self.tier_done = [False, False, False]
        self.chase_count = 0

        # Resolved gear of the last poll (for the UI).
        self.gear = None
        self.pct = None
        self.tier_pcts = list(AUTO_GEARS[1]['tiers'])
        self.tier_actives = list(_DEFAULT_ACTIVES)

        self.buy_state = 'OK'          # 'OK' | 'EXHAUSTED'
        self.trigger = {'BUY': None, 'SELL': None}   # crossed lines (manual fire)
        self.trigger_note = None       # why a crossed line is NOT fireable

        self.dirty = False             # state changed since the last save
        self._exhaust_logged = False
        self._pending = None           # {'side','price','qty','tiers','ts'}
        self._prev_shares = None
        self._prev_avg = 0.0
        self._log = log or (lambda msg: None)
        if saved:
            self._restore(saved)

    # ── Persistence ───────────────────────────────────────────────────────────

    def to_dict(self):
        d = {f: getattr(self, f) for f in _SAVE_FIELDS}
        d['q'] = self._prev_shares
        d['saved_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return d

    def _restore(self, saved):
        for f in _SAVE_FIELDS:
            if f in saved:
                setattr(self, f, saved[f])
        restored_events = list(self.events or [])
        # Older builds inserted a synthetic HOLD row every time an Autopilot
        # window armed on an existing position. Campaign fills now mean actual
        # broker-observed BUY/SELL changes only, so discard those legacy rows
        # while preserving every real fill and the rest of the campaign state.
        self.events = [e for e in restored_events
                       if not (isinstance(e, dict)
                               and e.get('kind') == 'HOLD')]
        if len(self.events) != len(restored_events):
            self.dirty = True       # persist the one-time migration next poll
        self.tier_done = list(self.tier_done or [False, False, False])

    # ── Small helpers ─────────────────────────────────────────────────────────

    def _fp(self, p):
        return f'{p:,.0f}' if self.currency == 'KRW' else f'{p:,.2f}'

    def _event(self, kind, qty, price, shares, avg):
        self.events.append({'ts': datetime.now().strftime('%m/%d %H:%M'),
                            'kind': kind, 'qty': qty, 'price': price,
                            'shares': shares, 'avg': avg})
        del self.events[:-300]
        self.dirty = True

    def _exhaust(self, what):
        """Buy side out of army: NO popup — the graph shows the muted buy
        line and the trigger row explains it. Logged once per transition."""
        self.buy_state = 'EXHAUSTED'
        if not self._exhaust_logged:
            self._exhaust_logged = True
            self._log(f'EXHAUSTED: {what}')

    def _buy_ok(self):
        if self.buy_state == 'EXHAUSTED' and self._exhaust_logged:
            self._log('army available again — buying resumes')
        self.buy_state = 'OK'
        self._exhaust_logged = False

    @staticmethod
    def _split_orders(snap):
        buys = [o for o in snap.get('orders', []) if o.get('side') == 'BUY']
        sells = [o for o in snap.get('orders', []) if o.get('side') == 'SELL']
        return buys, sells

    def _place(self, acts, side, price, qty, label, tiers=None):
        acts.append(('place', side, price, qty, label))
        self._pending = {'side': side, 'price': price, 'qty': qty,
                         'tiers': list(tiers or []), 'ts': time.time()}

    def note_manual_order(self, side, price, qty, tiers=None):
        """Register a manually-fired order (the window's Buy/Sell buttons in
        WATCH mode) so its fill is attributed exactly like a bot order —
        tier progress, exit price, same-day re-bait all work the same."""
        self._pending = {'side': side, 'price': price, 'qty': qty,
                         'tiers': list(tiers or []), 'ts': time.time()}

    # ── Gear resolution (card config + the two exceptions) ───────────────────

    def _bundle(self, snap):
        """{'gear','pct','tier_pcts','tier_actives','pinned'} for this poll."""
        card = dict(snap.get('card') or default_card_config())
        actives = list(card.get('tier_actives') or _DEFAULT_ACTIVES)
        if not any(actives):
            actives = list(_DEFAULT_ACTIVES)
        if self.gear1_pinned:
            return {'gear': RE_BAIT_GEAR, 'pct': AUTO_GEARS[RE_BAIT_GEAR]['pct'],
                    'tier_pcts': list(AUTO_GEARS[RE_BAIT_GEAR]['tiers']),
                    'tier_actives': actives, 'pinned': True}
        return {'gear': card.get('gear') or 1,
                'pct': int(card.get('pct') or AUTO_GEARS[1]['pct']),
                'tier_pcts': list(card.get('tier_pcts')
                                  or AUTO_GEARS[1]['tiers']),
                'tier_actives': actives, 'pinned': False}

    # ── Day rollover: the re-bait dies, the anchor resets to prev close ──────

    def _roll_day(self, snap):
        d = snap.get('trading_date')
        if not d or d == self.trading_date:
            return
        first = self.trading_date is None
        self.trading_date = d
        self.chase_count = 0
        if first:
            return
        if (snap.get('shares') or 0) <= 0:
            if self.anchor_source == 'sell':
                # The re-bait NEVER crosses into a new day, even when the
                # fresh prev close is not known yet.
                self._log('re-bait expired unfilled — not carried into the '
                          'new day')
                self.anchor = None
                self.anchor_source = 'close'
            if snap.get('prev_close'):
                self.anchor = snap['prev_close']
                self.anchor_source = 'close'
                self._log(f'new day {d}: vantage = prev close '
                          f'{self._fp(self.anchor)}')
            self.dirty = True

    # ── Fill detection (broker share diffs are the truth) ────────────────────

    def _take_pending(self, side):
        p = self._pending
        if p and p['side'] == side:
            self._pending = None
            return p
        return None

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

    def _detect_fills(self, snap, shares):
        prev = self._prev_shares
        avg = float(snap.get('avg_cost') or 0)
        if prev is None:                       # first poll after (re)arming
            # Log only — the fill list records actual BUY/SELL changes, not
            # every (re)opening of the window.
            if shares > 0:
                self._log(f'armed on an existing position: {shares} @ '
                          f'{self._fp(avg)}')
            return
        if shares > prev:
            self._on_buy_fill(snap, prev, shares, avg)
        elif shares < prev:
            self._on_sell_fill(snap, prev, shares, avg)

    def _on_buy_fill(self, snap, prev, shares, avg):
        qty = shares - prev
        pend = self._take_pending('BUY')
        if pend:
            price = pend['price']
        else:
            price = ((avg * shares - self._prev_avg * prev) / qty
                     if avg > 0 and self._prev_avg > 0 and prev > 0
                     else (avg or None))
        if prev == 0:
            # New campaign. Opened by the same-day re-bait → pinned to G1.
            self.gear1_pinned = (self.anchor_source == 'sell')
            self.tier_done = [False, False, False]
            self._event('LOAD', qty, price, shares, avg)
            self._log(f'LOAD filled: 0 → {shares} shares'
                      + (' — re-bait: campaign pinned to G1'
                         if self.gear1_pinned else ''))
        else:
            self.chase_count += 1
            # Any BUY fill restarts the tier ladder on the whole holding.
            self.tier_done = [False, False, False]
            self._event('CHASE', qty, price, shares, avg)
            self._log(f'CHASE filled: {prev} → {shares} shares '
                      f'(chase #{self.chase_count} today)')
        self.dirty = True

    def _on_sell_fill(self, snap, prev, shares, avg):
        qty = prev - shares
        pend = self._take_pending('SELL')
        if pend:
            price = pend['price']
            for i in (pend.get('tiers') or []):
                if 0 <= i < 3:
                    self.tier_done[i] = True
            kind = ('T' + '/'.join(str(i + 1) for i in pend['tiers'])
                    if pend.get('tiers') else 'SELL')
        else:
            price = snap.get('price') or self._prev_avg or None
            kind = 'SELL'
            self._log(f'external sell: {prev} → {shares} shares')
        self._event(kind, -qty, price, shares, avg)

        if shares == 0:
            self._log(f'campaign closed: -{qty} @ {self._fp(price or 0)}')
            self.events = []
            self.tier_done = [False, False, False]
            self.gear1_pinned = False
            if price:
                # Same-day re-bait: one bait at the exit fill −4% (gear 1),
                # valid only for the rest of this session.
                self.anchor = price
                self.anchor_source = 'sell'
                self._log(f're-bait armed: {self._fp(price)} '
                          f'-{RE_BAIT_PCT}% (G1) until the session ends')
        self.dirty = True

    # ── Main decision cycle ───────────────────────────────────────────────────

    def poll(self, snap: dict) -> list:
        self._roll_day(snap)
        shares = int(snap.get('shares') or 0)
        self._expire_pending(snap, shares)
        self._detect_fills(snap, shares)
        self._prev_shares = shares
        self._prev_avg = float(snap.get('avg_cost') or 0)
        self.trigger = {'BUY': None, 'SELL': None}
        self.trigger_note = None
        return (self._poll_deployed(snap, shares) if shares > 0
                else self._poll_empty(snap))

    # ── DEPLOYED: chase + sell tiers, fire only on touch ─────────────────────

    def _poll_deployed(self, snap, shares):
        self.state = 'DEPLOYED'
        avg = float(snap.get('avg_cost') or 0)
        if avg <= 0:
            self.status = 'deployed — waiting for avg cost'
            return []

        b = self._bundle(snap)
        self.gear, self.pct = b['gear'], b['pct']
        self.tier_pcts, self.tier_actives = b['tier_pcts'], b['tier_actives']
        pct = b['pct']

        chase_p = trim_buy_price(self.ticker, avg * (1 - pct / 100.0))
        cq = calc_buy_shares(shares, pct)
        size_tag = ' x1.0' if pct == 8 else ''
        chase_label = f'-{pct}%{size_tag} chase (G{b["gear"]})'

        # Sell tiers: split the holding across the active, not-yet-done tiers.
        # If every active tier is already done but shares remain (e.g. a
        # partial fill), the ladder restarts on the remainder.
        actives_now = [a and not d
                       for a, d in zip(b['tier_actives'], self.tier_done)]
        if not any(actives_now) and any(b['tier_actives']):
            self.tier_done = [False, False, False]
            actives_now = list(b['tier_actives'])
        split = calc_sell_tiers(shares, avg, b['tier_pcts'], actives_now)
        sell_lines = [(i, trim_sell_price(self.ticker, e['price']), e['qty'])
                      for i, e in enumerate(split) if e['price'] is not None]

        self.lines = {}
        if cq > 0:
            self.lines['chase'] = (chase_p, cq)
        for i, p, q in sell_lines:
            self.lines[f'tier{i + 1}'] = (p, q)

        acts = []
        # Army check every poll so the UI can flag exhaustion early.
        bp = snap.get('buying_power')
        affordable = bp is None or cq * chase_p <= bp
        if not affordable:
            self._exhaust(f'next buy needs {cq} @ {self._fp(chase_p)}')
        else:
            self._buy_ok()

        price = snap.get('price')
        if price is None:
            self.status = 'no price — watching paused'
            return acts
        buys, sells = self._split_orders(snap)
        can = snap.get('can_trade', True)
        tag = (f'G{b["gear"]}' + (' pinned' if b['pinned'] else '')
               + f' -{pct}%')

        # 1) SELL first (exit has priority). If the price gapped through
        #    several tiers, everything they cover goes in ONE order at the
        #    highest crossed line.
        crossed = [(i, p, q) for i, p, q in sell_lines if price >= p]
        if crossed:
            tqty = sum(q for _i, _p, q in crossed)
            tprice = max(p for _i, p, _q in crossed)
            tiers = [i for i, _p, _q in crossed]
            tlabel = '매도 ' + '+'.join(f'T{i + 1}' for i in tiers)
            if sells:
                # An order already rests on this side — no trigger either,
                # so the manual button cannot double-order.
                self.status = f'[{tag}] sell resting — waiting for the fill'
                return acts
            self.trigger['SELL'] = {'side': 'SELL', 'price': tprice,
                                    'qty': tqty, 'label': tlabel,
                                    'tiers': tiers}
            if not can:
                self.status = (f'[{tag}] SELL trigger met @ {self._fp(price)} '
                               f'(watching only)')
                return acts
            acts += [('cancel', o['id'], 'our buy (exit first)')
                     for o in buys if o.get('mine')]
            self._place(acts, 'SELL', tprice, tqty, tlabel, tiers=tiers)
            self.status = f'[{tag}] SELL fired: {tqty} @ {self._fp(tprice)}'
            return acts

        # 2) BUY (chase) — only when the army can fund it.
        if cq > 0 and price <= chase_p:
            if buys:
                self.status = f'[{tag}] buy resting — waiting for the fill'
                return acts
            if not affordable:
                self.trigger_note = ('▼ BUY crossed — no reserve army; '
                                     'manual Buy button is off')
                self.status = (f'[{tag}] BUY line crossed, but no reserve '
                               f'army remains — watching SELL only')
                return acts
            self.trigger['BUY'] = {'side': 'BUY', 'price': chase_p,
                                   'qty': cq, 'label': chase_label}
            if not can:
                self.status = (f'[{tag}] BUY trigger met @ {self._fp(price)} '
                               f'(watching only)')
                return acts
            acts += [('cancel', o['id'], 'our sell (chase first)')
                     for o in sells if o.get('mine')]
            self._place(acts, 'BUY', chase_p, cq, chase_label)
            self.status = f'[{tag}] BUY fired: {cq} @ {self._fp(chase_p)}'
            return acts

        lo = self._fp(chase_p) if cq > 0 else '--'
        hi = (self._fp(min(p for _i, p, _q in sell_lines))
              if sell_lines else '--')
        tail = ' · EXHAUSTED (buy off)' if self.buy_state == 'EXHAUSTED' else ''
        self.status = (f'[{tag}] watching: {lo} < now {self._fp(price)} '
                       f'< {hi}{tail}')
        return acts

    # ── EMPTY: watch the load line hanging off the vantage point ─────────────

    def _poll_empty(self, snap):
        self.state = 'EMPTY'
        if not self.anchor or self.anchor <= 0:
            if snap.get('prev_close'):
                self.anchor = snap['prev_close']
                self.anchor_source = 'close'
                self._log(f'vantage = prev close {self._fp(self.anchor)}')
                self.dirty = True
            else:
                self.status = 'empty — waiting for the vantage (prev close)'
                self.lines = {}
                return []

        rebait = (self.anchor_source == 'sell')
        b = self._bundle(snap)
        if rebait:                      # exception 1: gear 1 for the day
            b = {'gear': RE_BAIT_GEAR, 'pct': RE_BAIT_PCT,
                 'tier_pcts': list(AUTO_GEARS[RE_BAIT_GEAR]['tiers']),
                 'tier_actives': b['tier_actives'], 'pinned': False}
        self.gear, self.pct = b['gear'], b['pct']
        self.tier_pcts, self.tier_actives = b['tier_pcts'], b['tier_actives']
        pct = b['pct']

        load_p = trim_buy_price(self.ticker,
                                self.anchor * (1 - pct / 100.0))
        unit = snap.get('unit_cash') or 0.0
        lq = (max(1, round_half_up(unit / load_p))
              if unit > 0 and load_p > 0 else 0)
        if lq <= 0:
            self.lines = {}
            self.status = 'empty — unit cash unknown'
            return []

        # Pseudo exit (informational): the lowest active tier as if loaded.
        psplit = calc_sell_tiers(lq, load_p, b['tier_pcts'],
                                 b['tier_actives'])
        psell = next(((trim_sell_price(self.ticker, e['price']), e['qty'])
                      for e in psplit if e['price'] is not None), None)
        self.lines = {'load': (load_p, lq)}
        if psell:
            self.lines['psell'] = psell

        acts = []
        bp = snap.get('buying_power')
        affordable = bp is None or lq * load_p <= bp
        if not affordable:
            self._exhaust(f'load needs {lq} @ {self._fp(load_p)}')
        else:
            self._buy_ok()

        price = snap.get('price')
        if price is None:
            self.status = 'no price — watching paused'
            return acts
        buys, _sells = self._split_orders(snap)
        label = f'-{pct}% load' + (' (re-bait)' if rebait else '')
        tag = f'G{b["gear"]}' + (' re-bait' if rebait else '')

        if price <= load_p:
            if buys:
                self.status = 'load buy resting — waiting for the fill'
                return acts
            if not affordable:
                self.trigger_note = ('▼ LOAD crossed — no reserve army; '
                                     'manual Buy button is off')
                self.status = (f'[{tag}] LOAD line crossed, but no reserve '
                               f'army remains')
                return acts
            self.trigger['BUY'] = {'side': 'BUY', 'price': load_p,
                                   'qty': lq, 'label': label}
            if not snap.get('can_trade', True):
                self.status = (f'[{tag}] LOAD trigger met @ '
                               f'{self._fp(price)} (watching only)')
                return acts
            self._place(acts, 'BUY', load_p, lq, label)
            self.status = f'[{tag}] LOAD fired: {lq} @ {self._fp(load_p)}'
            return acts

        src = '재입질' if rebait else 'prev close'
        self.status = (f'[{tag}] watching: load {self._fp(load_p)} < now '
                       f'{self._fp(price)} (vantage {self._fp(self.anchor)}, '
                       f'{src})')
        return acts
