"""V_COMMANDOS_GEARBOX — the campaign engine (pure logic, no network, no tk).

Written to the shape of the old v^ grid engine, which was dependable because
it was small: read the broker, draw the lines, take at most one action, and
remember almost nothing.

THE WHOLE MECHANIC
------------------
    1. The broker says how many shares are held, and at what average cost.
    2. The gear draws the lines from that, and from nothing else:

           EMPTY     LOAD  = vantage × (1 - gear.load%)   ≈ one unit of cash
           DEPLOYED  CHASE = avg     × (1 - gear.chase%)  shares × gear.ratio
                     EXIT  = avg     × (1 + tier%)        split across the
                                                          armed tiers

    3. Watch the price. When it crosses a line, send that order.
    4. If it does not fill it rests. If the price then reaches the line on the
       OTHER side, cancel the resting order and send the other one instead.
    5. A buy fills → the broker's average moves → every line moves with it.
       Back to 3.

That is the entire strategy. Everything below is that loop, plus the honest
minimum needed to avoid sending a duplicate order.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
    * No campaign object, no campaign id, no lifecycle states. "A campaign" is
      just the fills since the holding was last zero. shares > 0 is DEPLOYED,
      shares == 0 is EMPTY, and the broker is the only authority on which.
    * No same-day reload. When a position closes the bot stands down and LIVE
      goes off; arm it again to start another campaign.
    * No history replay, no reconstructing an exit it did not witness, no
      statistics kept in fields that can drift away from the account.
    * External trades need no special handling at all. A hand buy moves the
      average, so the lines move. A hand sell to zero empties the position, so
      it goes back to the LOAD rule. Both fall out of "read the broker" free.

SETTINGS ARE THE BOT'S OWN
    Gear, exit tiers and AUTO live here and persist here. The stock card keeps
    its own copy for hand trading, and the two are NOT kept in step — the card
    is a worksheet, this is what trades. The card's `sync to autopilot` button
    copies its settings across when the commander means it.

VANTAGE
    Needed only by the LOAD, and only while EMPTY. Automatic is the Dynamic
    High5 — the highest of five sessions with today's live high included, so a
    fresh peak lifts the line at once — or it can be pinned by hand. Once a
    position exists the vantage is irrelevant: the average cost is the
    reference for every line.

Persisted state is `_SAVE_FIELDS` and is deliberately tiny. The less that is
remembered, the less there is to go stale while you trade in the app.
"""

from datetime import datetime

from core.calc import (DEFAULT_EXIT_TIER, DEFAULT_GEAR, calc_chase_price,
                       calc_chase_shares, calc_load_price, calc_sell_tiers,
                       chase_drop, clamp_gear, effective_entry_gear,
                       gear_params, load_drop, round_half_up,
                       select_auto_gear, tier_pcts, trim_buy_price,
                       trim_sell_price)

POLL_SECONDS = 5
STRATEGY_ID = 'V_COMMANDOS_GEARBOX'
PROJECTED_CHASES = 2       # extra buy lines drawn ahead; never ordered

_SAVE_FIELDS = ('gear', 'auto', 'exit_tiers', 'tier_done', 'vantage',
                'vantage_manual', 'vantage_manual_label', 'trading_date',
                'events')


def norm_tiers(value):
    """Three booleans, at least one armed. A single tier number works too."""
    if isinstance(value, (list, tuple)) and len(value) == 3:
        out = [bool(v) for v in value]
    else:
        try:
            t = max(1, min(3, int(value)))
        except (TypeError, ValueError):
            t = DEFAULT_EXIT_TIER
        out = [i == t - 1 for i in range(3)]
    return out if any(out) else [i == DEFAULT_EXIT_TIER - 1 for i in range(3)]


class CampaignEngine:
    """One watched stock. Feed `poll(snap)` each cycle; execute what it returns.

    snap = {
        'price':        float|None,
        'shares':       int,          broker holding — the authority
        'avg_cost':     float,        broker average
        'orders':       [{'id','side','price','qty_open','filled','mine'}],
        'buying_power': float|None,
        'unit_cash':    float,
        'trading_date': 'YYYY-MM-DD',
        'highs':        [(iso_date, high)]  recent sessions, today included
        'prev_close':   float|None,
        'can_trade':    bool,         False in WATCH — lines are drawn, but
                                      nothing is sent
        'vol5':         float|None,   the 5-day range, for AUTO gear
    }

    actions:
        ('cancel', order_id, why)           only ever our own orders
        ('place', side, price, qty, label)
        ('stand_down', why)                 sold out; drop LIVE back to WATCH
    """

    def __init__(self, ticker, trading_date=None, log=None, saved=None):
        self.ticker = ticker
        self.currency = 'KRW' if ticker.endswith('.KS') else 'USD'
        self.strategy = STRATEGY_ID

        # -- persisted --------------------------------------------------------
        # The bot's OWN settings. The card has its own, and they are not
        # synced: the card is a worksheet for hand trading, this is what
        # actually trades. `sync to autopilot` on the card copies one into
        # the other, deliberately, when the commander asks for it.
        self.auto = True                 # gear follows V while EMPTY
        self.gear = DEFAULT_GEAR
        self.exit_tiers = norm_tiers(DEFAULT_EXIT_TIER)
        self.tier_done = [False, False, False]
        self.vantage = None
        self.vantage_manual = None
        self.vantage_manual_label = ''
        self.trading_date = trading_date
        self.events = []                 # fills since the holding was zero

        # -- runtime, rebuilt on every poll -----------------------------------
        self.state = 'ARMING'            # ARMING | FLAT | DEPLOYED
                                         # (the cockpit prints FLAT as EMPTY)
        self.campaign_state = 'FLAT'     # the label the cockpit colours
        self.status = 'arming…'
        self.lines = {}
        self.crossed = {'BUY': None, 'SELL': None}
        self.buy_state = 'OK'            # OK | EXHAUSTED
        self.vantage_src = 'high5'

        self.dirty = False
        self._pending = None             # {'side','price','qty','tiers'}
        self._pending_tiers = []
        self._prev_shares = None
        self._prev_avg = 0.0
        self._log = log or (lambda msg: None)
        if saved:
            self._restore(saved)

    # ── Persistence ──────────────────────────────────────────────────────────

    def to_dict(self):
        d = {f: getattr(self, f) for f in _SAVE_FIELDS}
        d['strategy'] = STRATEGY_ID
        d['q'] = self._prev_shares
        d['saved_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return d

    def _restore(self, saved):
        for f in _SAVE_FIELDS:
            if f in saved:
                setattr(self, f, saved[f])
        self.gear = clamp_gear(self.gear)
        self.exit_tiers = norm_tiers(self.exit_tiers)
        self.tier_done = list(self.tier_done or [False, False, False])
        self.events = [e for e in (self.events or []) if isinstance(e, dict)]

    # ── Campaign figures, all derived (nothing to drift) ─────────────────────

    @property
    def chase_count(self):
        return sum(1 for e in self.events if e.get('kind') == 'CHASE')

    @property
    def campaign_low(self):
        buys = [e['price'] for e in self.events
                if (e.get('qty') or 0) > 0 and e.get('price')]
        return min(buys) if buys else None

    @property
    def campaign_start(self):
        return self.events[0]['ts'] if self.events else None

    @property
    def campaign_id(self):
        """There is no campaign object to have an id. This is a label derived
        from the log — truthy while a position is held, None once it is not —
        so callers can still ask "is a campaign open?" without the engine
        having to remember one."""
        return (f'{self.ticker} · {len(self.events)} fills'
                if self.events else None)

    # ── Small helpers ────────────────────────────────────────────────────────

    def _fp(self, p):
        return f'{p:,.0f}' if self.currency == 'KRW' else f'{p:,.2f}'

    def _tier_text(self, tiers=None):
        tiers = self.exit_tiers if tiers is None else tiers
        on = [f'T{i + 1}' for i, a in enumerate(tiers) if a]
        return '+'.join(on) if on else '—'

    def _event(self, kind, qty, price, shares, avg, source='BOT'):
        """The fill log: trades only, and only while a position is held. It is
        cleared the moment the holding reaches zero, exactly as the v^ engine
        cleared its day log."""
        self.events.append({'date': self.trading_date or '',
                            'ts': datetime.now().strftime('%m/%d %H:%M'),
                            'kind': kind, 'qty': qty, 'price': price,
                            'shares': shares, 'avg': avg, 'source': source,
                            'note': ''})
        del self.events[:-200]
        self.dirty = True

    @staticmethod
    def _mine(orders, side):
        return [o for o in orders if o.get('side') == side and o.get('mine')]

    @staticmethod
    def _foreign(orders, side):
        return [o for o in orders
                if o.get('side') == side and not o.get('mine')]

    def _same(self, a, b):
        if a is None or b is None:
            return False
        tol = 0.0 if self.ticker.endswith('.KS') else 0.005
        return abs(float(a) - float(b)) <= tol

    # ── Order-outcome hooks (the controller calls these) ─────────────────────

    def note_order_accepted(self, side, price, qty, order_id=None,
                            client_id=None):
        return True

    def note_order_failed(self, side, price, qty):
        self._pending = None

    # ── Vantage: used by the LOAD line, and nothing else ─────────────────────

    def set_manual_vantage(self, price, label=''):
        if not price or price <= 0:
            return False
        self.vantage_manual = float(price)
        self.vantage_manual_label = str(label or '').strip()
        self.vantage = float(price)
        self.dirty = True
        self._log(f'vantage pinned: {self._fp(price)} {label}'.strip())
        return True

    def clear_manual_vantage(self):
        self.vantage_manual = None
        self.vantage_manual_label = ''
        self.dirty = True
        self._log('vantage released — back to the Dynamic High5')
        return True

    def _refresh_vantage(self, snap):
        """Dynamic High5: the highest of five sessions, the live one included,
        so a peak made this morning lifts the LOAD line straight away.

        Five means five. When today has no bar yet — a weekend, or before the
        open — the fifth slot falls back to the fifth completed session rather
        than silently vanishing. Dropping it takes the OLDEST session out of
        the window, and that is often the one holding the peak."""
        if self.vantage_manual:
            self.vantage = self.vantage_manual
            self.vantage_src = 'manual'
            return
        today = snap.get('trading_date')
        highs = snap.get('highs') or []
        done = [h for d, h in highs if h and d != today]
        live = next((h for d, h in reversed(highs) if h and d == today), None)
        window = (done[-4:] + [live]) if live else done[-5:]
        v = max(window) if window else snap.get('prev_close')
        if not v or v <= 0:
            return
        self.vantage_src = 'high5' if window else 'close'
        if v != self.vantage:
            self.vantage, self.dirty = v, True

    # ── The bot's own settings ───────────────────────────────────────────────

    def set_gear(self, gear):
        """Pick a gear by hand. That is a decision, so AUTO steps aside."""
        gear = clamp_gear(gear)
        self.auto = False
        if gear != self.gear:
            self._log(f'gear G{self.gear} → G{gear} (manual)')
            self.gear = gear
        self.dirty = True
        return True

    def set_auto(self, on=True):
        self.auto = bool(on)
        self._log(f'gear selection → {"AUTO" if self.auto else "MANUAL"}')
        self.dirty = True
        return True

    def set_tiers(self, tiers):
        tiers = norm_tiers(tiers)
        if tiers != self.exit_tiers:
            self._log(f'exits {self._tier_text()} → {self._tier_text(tiers)}')
            # A newly armed tier starts unspent; the ladder re-splits below.
            self.tier_done = [d and a for d, a in zip(self.tier_done, tiers)]
            self.exit_tiers = tiers
            self.dirty = True
        return True

    def apply_card_config(self, cfg):
        """The card's `sync to autopilot` button, and nothing else. The two
        sides are otherwise independent on purpose."""
        cfg = dict(cfg or {})
        if 'exit_tiers' in cfg:
            self.set_tiers(cfg['exit_tiers'])
        if cfg.get('auto'):
            self.set_auto(True)
        elif 'gear' in cfg:
            self.set_gear(cfg['gear'])
        self._log(f'synced from the card: G{self.gear} {self._tier_text()} '
                  f'({"AUTO" if self.auto else "MANUAL"})')
        return True

    def _apply_auto(self, snap):
        """While AUTO is on the gear follows the 5-day range — the bot's own
        copy of the rule, so it holds whether or not a card exists. While
        EMPTY the heavy-unit entry floor applies too (manual §6.2): a stock
        whose one share eats a big bite of a unit cannot open on a shallow
        ladder — exactly the rule the card's ▲heavy tag shows. Deployed, the
        floor drops away and V alone drives."""
        if not self.auto:
            return
        v = snap.get('vol5')
        if v is None:
            return
        base = select_auto_gear(v)
        if int(snap.get('shares') or 0) > 0:
            gear, heavy = base, ''
        else:
            gear = effective_entry_gear(v, snap.get('price'),
                                        snap.get('unit_cash'))
            heavy = ' ▲heavy' if gear > base else ''
        if gear != self.gear:
            self._log(f'gear G{self.gear} → G{gear} '
                      f'(AUTO, V {v:.2f}%{heavy})')
            self.gear, self.dirty = gear, True

    # ── Fills, read straight off the broker's share count ────────────────────

    def _detect_fills(self, snap, shares, avg):
        prev = self._prev_shares
        if prev is None:
            # First poll. Whatever the broker holds is the truth; a saved log
            # describing a position we no longer have is simply dropped.
            if shares == 0 and self.events:
                self._log(f'{len(self.events)} saved fills but the broker '
                          f'holds nothing — clearing the log, back to empty')
                self.events, self.dirty = [], True
            elif shares > 0:
                self._log(f'adopted {shares} @ {self._fp(avg)} '
                          f'(G{self.gear} {self._tier_text()})')
            return None
        if shares > prev:
            return self._on_buy(prev, shares, avg)
        if shares < prev:
            return self._on_sell(snap, prev, shares, avg)
        return None

    def _take_pending(self, side):
        p = self._pending
        if p and p['side'] == side:
            self._pending = None
            return p
        return None

    def _on_buy(self, prev, shares, avg):
        qty = shares - prev
        pend = self._take_pending('BUY')
        if pend:
            price, source = pend['price'], 'BOT'
        else:
            price = ((avg * shares - self._prev_avg * prev) / qty
                     if avg > 0 and self._prev_avg > 0 and prev > 0 else avg)
            source = 'EXT'
        if prev == 0:
            self.events = []             # a new campaign starts clean
        # More shares to sell: every armed tier arms again on the new holding.
        self.tier_done = [False, False, False]
        self._event('LOAD' if prev == 0 else 'CHASE', qty, price, shares, avg,
                    source)
        self._log(f'{"LOAD" if prev == 0 else "CHASE"} {source}: '
                  f'{prev} → {shares} @ {self._fp(avg)}')
        return None

    def _on_sell(self, snap, prev, shares, avg):
        qty = prev - shares
        pend = self._take_pending('SELL')
        if pend:
            price, source = pend['price'], 'BOT'
            for i in pend.get('tiers') or []:
                if 0 <= i < 3:
                    self.tier_done[i] = True
        else:
            price, source = (snap.get('price') or self._prev_avg), 'EXT'

        if shares > 0:
            kind = ('T' + '/'.join(str(i + 1) for i in pend['tiers'])
                    if pend and pend.get('tiers') else 'SELL')
            self._event(kind, -qty, price, shares, avg, source)
            self._log(f'{kind} {source}: {prev} → {shares} left')
            return None

        # Sold out: the campaign is over. Clear the log, disarm, stand down.
        n = len(self.events) + 1
        self._log(f'EXIT {source}: {prev} → 0 @ {self._fp(price or 0)} — '
                  f'campaign closed after {n} fills')
        self.events = []
        self.tier_done = [False, False, False]
        self.dirty = True
        return ('stand_down',
                f'sold out — LIVE off. Arm it again to start a new campaign.')

    # ── The lines ────────────────────────────────────────────────────────────

    def _ladder(self, lines, start_qty, start_price, first):
        """The chases after `first`, each folded into the running average
        exactly as the campaign would run them. Drawn, never ordered."""
        q, avg = start_qty, start_price
        for n in range(1, first + PROJECTED_CHASES):
            p = calc_chase_price(avg, self.gear)
            add = calc_chase_shares(q, self.gear)
            if p <= 0 or add <= 0:
                break
            if n >= first:
                lines[f'chase{n}'] = {'kind': f'chase{n}', 'armed': False,
                                      'price': p, 'qty': add,
                                      'label': f'chase {n}'}
            avg = (avg * q + p * add) / (q + add)
            q += add

    def _exits(self, lines, shares, avg, armed):
        """The holding split across the tiers that are armed and not spent.
        When every armed tier is spent but shares remain, the ladder restarts
        on what is left — the lines always describe the real position."""
        live = [a and not d for a, d in zip(self.exit_tiers, self.tier_done)]
        if not any(live):
            self.tier_done = [False, False, False]
            live = list(self.exit_tiers)
        pcts = tier_pcts(self.gear)
        for i, e in enumerate(calc_sell_tiers(shares, avg, pcts, live)):
            if e['price'] is None:
                continue
            key = f'exit{i + 1}' if armed else f'pexit{i + 1}'
            lines[key] = {'kind': key, 'tier': i, 'armed': armed,
                          'price': trim_sell_price(self.ticker, e['price']),
                          'qty': e['qty'],
                          'label': (f'EXIT T{i + 1} +{pcts[i]}%' if armed
                                    else f'exit if loaded T{i + 1} '
                                         f'+{pcts[i]}%')}

    def _draw_deployed(self, shares, avg):
        g = gear_params(self.gear)
        lines = {}
        qty = calc_chase_shares(shares, self.gear)
        if qty > 0:
            lines['chase'] = {
                'kind': 'chase', 'armed': True, 'qty': qty,
                'price': trim_buy_price(self.ticker,
                                        calc_chase_price(avg, self.gear)),
                'label': f'CHASE -{chase_drop(self.gear)}% ×{g["frac"]}'}
        self._ladder(lines, shares, avg, first=2)   # chase 1 is the armed one
        self._exits(lines, shares, avg, armed=True)
        return lines

    def _draw_empty(self, snap):
        if not self.vantage or self.vantage <= 0:
            return {}
        price = trim_buy_price(self.ticker,
                               calc_load_price(self.vantage, self.gear))
        unit = snap.get('unit_cash') or 0.0
        qty = (max(1, round_half_up(unit / price))
               if unit > 0 and price > 0 else 0)
        if qty <= 0:
            return {}
        lines = {'load': {'kind': 'load', 'armed': True, 'price': price,
                          'qty': qty,
                          'label': f'LOAD -{load_drop(self.gear)}%'}}
        self._ladder(lines, qty, price, first=1)    # the LOAD is the armed one
        self._exits(lines, qty, price, armed=False)
        return lines

    # ── The loop ─────────────────────────────────────────────────────────────

    def poll(self, snap: dict) -> list:
        d = snap.get('trading_date')
        if d and d != self.trading_date:
            self.trading_date, self.dirty = d, True

        shares = int(snap.get('shares') or 0)
        avg = float(snap.get('avg_cost') or 0)
        self._apply_auto(snap)

        acts = []
        closed = self._detect_fills(snap, shares, avg)
        if closed:
            acts.append(closed)
        self._prev_shares, self._prev_avg = shares, avg
        self.crossed = {'BUY': None, 'SELL': None}

        if shares > 0:
            self.state = self.campaign_state = 'DEPLOYED'
            if avg <= 0:
                self.lines = {}
                self.status = ('deployed, but the broker reports no average '
                               'cost — waiting rather than guessing')
                return acts
            self.lines = self._draw_deployed(shares, avg)
        else:
            self.state = self.campaign_state = 'FLAT'
            self._refresh_vantage(snap)
            self.lines = self._draw_empty(snap)
            if not self.lines:
                self.status = 'empty — waiting for a vantage'
                return acts

        return acts + self._act(snap, shares)

    def _act(self, snap, shares):
        """One decision per poll: which line is crossed, and what rests where."""
        price = snap.get('price')
        if price is None:
            self.status = 'no price — watching paused'
            return []

        orders = snap.get('orders') or []
        tag = f'G{self.gear} {self._tier_text()}'

        # The EXIT has priority: taking money off beats putting more in.
        want = self._crossed_sell(price, shares) or self._crossed_buy(price)
        if not want:
            self.status = f'[{tag}] {self._watching_text(price)}'
            return self._retire_stale(orders)

        self.crossed[want['side']] = {'price': want['price'],
                                      'qty': want['qty']}
        acts = []
        other = 'BUY' if want['side'] == 'SELL' else 'SELL'

        # The price reached the other side while our order sat there: pull it,
        # so the side we actually want is free.
        for o in self._mine(orders, other):
            acts.append(('cancel', o['id'],
                         f'the {want["side"].lower()} line was reached first'))

        # Already working this side?
        for o in self._mine(orders, want['side']):
            if float(o.get('filled') or 0) > 0:
                self.status = f'[{tag}] partly filled — waiting for the rest'
                return acts
            if (self._same(o.get('price'), want['price'])
                    and int(o.get('qty_open') or 0) == want['qty']):
                self.status = (f'[{tag}] {want["side"]} resting {want["qty"]} '
                               f'@ {self._fp(want["price"])}')
                return acts
            acts.append(('cancel', o['id'], 'the line moved'))
            self.status = f'[{tag}] re-pricing the {want["side"].lower()}'
            return acts

        if self._foreign(orders, want['side']):
            self.status = (f'[{tag}] another order is working this side — '
                           f'leaving it alone')
            return acts

        if want['side'] == 'BUY' and not self._affordable(snap, want):
            self.status = (f'[{tag}] {want["label"]} reached, but the army '
                           f'cannot fund it — watching the exits')
            return acts
        self.buy_state = 'OK'

        if not snap.get('can_trade', True):
            self.status = (f'[{tag}] {want["label"]} reached @ '
                           f'{self._fp(price)} — WATCH mode, not sent')
            return acts

        self._pending_tiers = list(want.get('tiers') or [])
        self._pending = {'side': want['side'], 'price': want['price'],
                         'qty': want['qty'], 'tiers': self._pending_tiers}
        acts.append(('place', want['side'], want['price'], want['qty'],
                     want['label']))
        self.status = (f'[{tag}] {want["label"]} sent: {want["qty"]} @ '
                       f'{self._fp(want["price"])}')
        return acts

    def _crossed_buy(self, price):
        for key in ('load', 'chase'):
            e = self.lines.get(key)
            if e and e.get('armed') and price <= e['price']:
                return dict(e, side='BUY')
        return None

    def _crossed_sell(self, price, shares):
        hit = [e for k, e in self.lines.items()
               if k.startswith('exit') and e.get('armed')
               and price >= e['price']]
        if not hit:
            return None
        # Gapped through several tiers: everything they cover leaves in ONE
        # order, at the highest line that was actually reached.
        return {'side': 'SELL', 'price': max(e['price'] for e in hit),
                'qty': min(shares, sum(e['qty'] for e in hit)),
                'tiers': [e['tier'] for e in hit],
                'label': 'EXIT ' + '+'.join(f'T{e["tier"] + 1}' for e in hit)}

    def _affordable(self, snap, want):
        bp = snap.get('buying_power')
        if bp is None or want['price'] * want['qty'] <= bp:
            return True
        if self.buy_state != 'EXHAUSTED':
            self._log(f'army short: {want["label"]} needs '
                      f'{self._fp(want["price"] * want["qty"])}')
        self.buy_state = 'EXHAUSTED'
        return False

    def _retire_stale(self, orders):
        """No line is crossed, so nothing of ours should be resting. An order
        left over from a line that has since moved gets cancelled; the next
        crossing re-sends it at the current price."""
        acts = [('cancel', o['id'], 'no line is crossed any more')
                for side in ('BUY', 'SELL')
                for o in self._mine(orders, side)
                if float(o.get('filled') or 0) <= 0]
        if acts:
            self._pending = None
        return acts

    def _watching_text(self, price):
        lo = next((self.lines[k]['price'] for k in ('load', 'chase')
                   if self.lines.get(k)), None)
        ups = [e['price'] for k, e in self.lines.items()
               if k.startswith('exit') and e.get('armed')]
        hi = min(ups) if ups else None
        return (f'watching: {self._fp(lo) if lo else "--"} < '
                f'{self._fp(price)} < {self._fp(hi) if hi else "--"}')

    # ── What the cockpit reads ───────────────────────────────────────────────

    def summary(self, shares=0, avg=0.0, price=None):
        pcts = tier_pcts(self.gear)
        target = sum((e['price'] - avg) * e['qty']
                     for k, e in self.lines.items()
                     if k.startswith('exit') and e.get('armed')) if avg else 0
        return {
            'strategy': STRATEGY_ID,
            'gear': self.gear,
            'gear_name': gear_params(self.gear)['name'],
            'load_pct': load_drop(self.gear),
            'chase_pct': chase_drop(self.gear),
            'add_frac': gear_params(self.gear)['frac'],
            'exit_tiers': list(self.exit_tiers),
            'tier_done': list(self.tier_done),
            'tier_pcts': list(pcts),
            'tier_text': self._tier_text(),
            'vantage': self.vantage,
            'vantage_src': self.vantage_src,
            'vantage_manual': self.vantage_manual,
            'vantage_manual_label': self.vantage_manual_label,
            'auto': self.auto,
            'campaign_id': self.campaign_id,
            'campaign_start': self.campaign_start,
            'chase_count': self.chase_count,
            'campaign_low': self.campaign_low,
            'max_cost': (shares * avg) or None,
            'gross_target': target or None,
        }
