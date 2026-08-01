"""V_COMMANDOS_GEARBOX — the campaign engine (pure logic, no network, no tk).

The bot watches exactly the lines the stock CARD draws: same gearbox, same
vantage, same exit tier (`core/calc.py`). A campaign begins when the LOAD
fills and ends only when the broker says the holding is zero.

    FLAT ─ LOAD ─▶ DEPLOYED ─ CHASE… ─▶ full EXIT ─▶ COMPLETED ─▶ FLAT
                                                  └▶ RELOAD_ARMED (same day)

Two lines are watched at a time, never more:

    next CHASE BUY   avg × (1 - gear.chase%)  ×  shares × gear.ratio
    full EXIT SELL   avg × (1 + tier%)        ×  every share held

Everything is recomputed from the BROKER's quantity and average cost after
every fill, so the bot can adopt a position that was traded by hand in the
app and keep going — it never needs to reconstruct the historical ladder.

Gear and exit tier are fixed for the campaign's life. The card may override
them mid-campaign; that is an explicit, logged event (GEAR_OVERRIDE /
EXIT_TIER_OVERRIDE), not adaptive behaviour.

WATCH mode never places orders: a crossed line is exposed as `trigger` so the
campaign window's Buy/Sell buttons can fire it by hand. State survives
restarts through to_dict()/`saved` (the controller persists it).
"""

import time
from datetime import datetime

from core.calc import (CAMPAIGN_CAP_UNITS, DEFAULT_EXIT_TIER, DEFAULT_GEAR,
                       RELOAD_DROP_PCT, calc_chase_price, calc_chase_shares,
                       calc_exit_price, calc_load_price, calc_reload_price,
                       chase_drop, clamp_gear, clamp_tier, exit_pct,
                       gear_params, load_drop, round_half_up, trim_buy_price,
                       trim_sell_price)

POLL_SECONDS = 10        # watcher tick

_PENDING_STALE_S = 90    # forget an order intent this long after placing it
                         # if nothing rests and nothing filled (a DAY order
                         # died, or it was cancelled outside)

_SAVE_FIELDS = ('campaign_id', 'campaign_state', 'vantage', 'vantage_src',
                'gear', 'exit_tier', 'cap_units', 'trading_date',
                'chase_count', 'max_qty', 'max_cost', 'campaign_low',
                'campaign_start', 'campaign_vantage', 'load_price',
                'manually_modified', 'events', 'overrides')

STRATEGY_ID = 'V_COMMANDOS_GEARBOX'


def default_card_config() -> dict:
    """Fallback when the card has not pushed its config yet."""
    return {'gear': DEFAULT_GEAR, 'exit_tier': DEFAULT_EXIT_TIER,
            'cap_units': CAMPAIGN_CAP_UNITS}


class CampaignEngine:
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
        'high5':        float|None   highest completed-session high, 5 days,
        'prev_close':   float|None   fallback vantage when High5 is missing,
        'can_trade':    bool         False in WATCH mode → the crossed line
                                     is exposed as `trigger`, NOT fired,
        'card':         dict|None    {'gear','exit_tier','cap_units'},
    }

    actions: ('cancel', order_id, label) — only ever our own orders —
             ('place', side, price, qty, label), ('notify', message).
    """

    def __init__(self, ticker, trading_date=None, log=None, saved=None):
        self.ticker = ticker
        self.currency = 'KRW' if ticker.endswith('.KS') else 'USD'
        self.strategy = STRATEGY_ID
        self.state = 'ARMING'            # coarse state for the card badge
        self.campaign_state = 'FLAT'     # the manual's §13 state machine
        self.status = 'arming…'
        self.lines = {}                  # 'load'|'chase'|'exit'|'pexit'
        self.events = []                 # THIS campaign's fills
        self.overrides = []              # GEAR_OVERRIDE / EXIT_TIER_OVERRIDE
        self.trading_date = trading_date

        # -- Campaign header (manual §14.1) ------------------------------------
        self.campaign_id = None
        self.campaign_start = None
        self.campaign_vantage = None     # the vantage that generated the LOAD
        self.load_price = None
        self.gear = DEFAULT_GEAR
        self.exit_tier = DEFAULT_EXIT_TIER
        self.cap_units = CAMPAIGN_CAP_UNITS

        # -- Live vantage (flat state) -----------------------------------------
        self.vantage = None
        self.vantage_src = 'high5'       # 'high5' | 'close' | 'reload'

        # -- Campaign metrics ---------------------------------------------------
        self.chase_count = 0
        self.max_qty = 0
        self.max_cost = 0.0              # peak cash deployed, own currency
        self.campaign_low = None
        self.manually_modified = False

        self.buy_state = 'OK'            # 'OK' | 'EXHAUSTED' | 'CAPPED'
        self.trigger = {'BUY': None, 'SELL': None}
        self.trigger_note = None

        self.dirty = False
        self._exhaust_logged = False
        self._pending = None             # {'side','price','qty','kind','ts'}
        self._prev_shares = None
        self._prev_avg = 0.0
        self._log = log or (lambda msg: None)
        if saved:
            self._restore(saved)

    # ── Persistence ───────────────────────────────────────────────────────────

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
        self.events = list(self.events or [])
        self.overrides = list(self.overrides or [])
        self.gear = clamp_gear(self.gear)
        self.exit_tier = clamp_tier(self.exit_tier)

    # ── Small helpers ─────────────────────────────────────────────────────────

    def _fp(self, p):
        return f'{p:,.0f}' if self.currency == 'KRW' else f'{p:,.2f}'

    def _now(self):
        return datetime.now().strftime('%m/%d %H:%M')

    def _event(self, kind, qty, price, shares, avg, source='BOT'):
        self.events.append({'ts': self._now(), 'kind': kind, 'qty': qty,
                            'price': price, 'shares': shares, 'avg': avg,
                            'source': source})
        del self.events[:-300]
        self.dirty = True

    def _override(self, kind, old, new, reason=''):
        self.overrides.append({'ts': self._now(), 'kind': kind,
                               'old': old, 'new': new, 'reason': reason})
        del self.overrides[:-50]
        self._log(f'{kind}: {old} → {new}' + (f' ({reason})' if reason else ''))
        self.dirty = True

    def _exhaust(self, what, kind='EXHAUSTED'):
        """Buy side out of army (or at the campaign cap): no popup — the
        window shows the muted buy line. Logged once per transition."""
        self.buy_state = kind
        if not self._exhaust_logged:
            self._exhaust_logged = True
            self._log(f'{kind}: {what}')

    def _buy_ok(self):
        if self.buy_state != 'OK' and self._exhaust_logged:
            self._log('buying resumes')
        self.buy_state = 'OK'
        self._exhaust_logged = False

    @staticmethod
    def _split_orders(snap):
        buys = [o for o in snap.get('orders', []) if o.get('side') == 'BUY']
        sells = [o for o in snap.get('orders', []) if o.get('side') == 'SELL']
        return buys, sells

    def _place(self, acts, side, price, qty, label, kind=None):
        acts.append(('place', side, price, qty, label))
        self._pending = {'side': side, 'price': price, 'qty': qty,
                         'kind': kind or side, 'ts': time.time()}

    def note_manual_order(self, side, price, qty, kind=None):
        """Register a manually-fired order (the window's Buy/Sell buttons in
        WATCH mode) so its fill is attributed exactly like a bot order."""
        self._pending = {'side': side, 'price': price, 'qty': qty,
                         'kind': kind or side, 'ts': time.time()}

    # ── Gear / tier resolution (the card is the source; changes are logged) ──

    def _apply_card(self, snap):
        card = dict(snap.get('card') or default_card_config())
        gear = clamp_gear(card.get('gear', self.gear))
        tier = clamp_tier(card.get('exit_tier', self.exit_tier))
        try:
            cap = float(card.get('cap_units') or self.cap_units)
        except (TypeError, ValueError):
            cap = self.cap_units

        deployed = (self._prev_shares or 0) > 0
        if gear != self.gear:
            if deployed:
                self._override('GEAR_OVERRIDE', self.gear, gear,
                               'card gear changed mid-campaign')
            self.gear = gear
            self.dirty = True
        if tier != self.exit_tier:
            if deployed:
                self._override('EXIT_TIER_OVERRIDE', self.exit_tier, tier,
                               'card tier changed mid-campaign')
            self.exit_tier = tier
            self.dirty = True
        if cap != self.cap_units:
            if deployed:
                self._override('CAP_OVERRIDE', self.cap_units, cap, '')
            self.cap_units = cap
            self.dirty = True

    # ── Day rollover: the reload dies, the vantage returns to High5 ──────────

    def _roll_day(self, snap):
        d = snap.get('trading_date')
        if not d or d == self.trading_date:
            return
        first = self.trading_date is None
        self.trading_date = d
        if first:
            return
        self.dirty = True
        if (snap.get('shares') or 0) > 0:
            return                       # a campaign simply continues
        if self.vantage_src == 'reload':
            self._log('same-day reload expired unfilled — not carried into '
                      'the new day')
            self.vantage = None
            self.vantage_src = 'high5'
            self.campaign_state = 'FLAT'
        self._refresh_flat_vantage(snap, announce=True)

    def _refresh_flat_vantage(self, snap, announce=False):
        """While flat the bot keeps the rolling High5 fresh (manual §9.1)."""
        if self.vantage_src == 'reload':
            return
        v = snap.get('high5') or snap.get('prev_close')
        if not v or v <= 0:
            return
        src = 'high5' if snap.get('high5') else 'close'
        if v != self.vantage or src != self.vantage_src:
            self.vantage, self.vantage_src = v, src
            self.dirty = True
            if announce:
                self._log(f'vantage = {src} {self._fp(v)}')

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
            if shares > 0:
                self._adopt(shares, avg)
            return
        if shares > prev:
            self._on_buy_fill(snap, prev, shares, avg)
        elif shares < prev:
            self._on_sell_fill(snap, prev, shares, avg)

    def _adopt(self, shares, avg):
        """ADOPT_POSITION (manual §12.2): shares exist but no live campaign in
        this process. The fixed-gear design needs nothing but the broker's
        quantity and average — no Step history to reconstruct."""
        if not self.campaign_id:
            self._open_campaign(avg, adopted=True)
        self.max_qty = max(self.max_qty, shares)
        self.max_cost = max(self.max_cost, shares * avg)
        self._log(f'ADOPT_POSITION: {shares} @ {self._fp(avg)} '
                  f'(G{self.gear}, T{self.exit_tier})')

    def _open_campaign(self, price, adopted=False):
        self.campaign_id = f"{self.ticker}-{datetime.now():%Y%m%d-%H%M%S}"
        self.campaign_start = datetime.now().strftime('%Y-%m-%d %H:%M')
        self.campaign_vantage = self.vantage
        self.load_price = price
        self.chase_count = 0
        self.max_qty = 0
        self.max_cost = 0.0
        self.campaign_low = price
        self.manually_modified = bool(adopted)
        self.events = []
        self.overrides = []
        self.dirty = True

    def _on_buy_fill(self, snap, prev, shares, avg):
        qty = shares - prev
        pend = self._take_pending('BUY')
        if pend:
            price = pend['price']
            source = 'BOT'
        else:
            price = ((avg * shares - self._prev_avg * prev) / qty
                     if avg > 0 and self._prev_avg > 0 and prev > 0
                     else (avg or None))
            source = 'EXTERNAL'
        if prev == 0:
            self._open_campaign(price)
            kind = 'RELOAD' if self.vantage_src == 'reload' else 'LOAD'
            self._event(kind, qty, price, shares, avg, source)
            self._log(f'{kind} filled ({source}): 0 → {shares} shares '
                      f'— campaign {self.campaign_id} G{self.gear} '
                      f'T{self.exit_tier}')
            self.vantage_src = 'high5'
        else:
            self.chase_count += 1
            self._event('CHASE', qty, price, shares, avg, source)
            self._log(f'CHASE filled ({source}): {prev} → {shares} shares '
                      f'(chase #{self.chase_count})')
            if source == 'EXTERNAL':
                self._log('EXTERNAL_BUY — lines recalculated from the new '
                          'broker average')
        if price:
            self.campaign_low = (price if self.campaign_low is None
                                 else min(self.campaign_low, price))
        self.max_qty = max(self.max_qty, shares)
        self.max_cost = max(self.max_cost, shares * avg)
        self.dirty = True

    def _on_sell_fill(self, snap, prev, shares, avg):
        qty = prev - shares
        pend = self._take_pending('SELL')
        if pend:
            price = pend['price']
            source = 'BOT'
        else:
            price = snap.get('price') or self._prev_avg or None
            source = 'EXTERNAL'

        if shares > 0:
            # A partial sell is not part of the strategy (manual §12.4).
            self.manually_modified = True
            self._event('PARTIAL', -qty, price, shares, avg, source)
            self._log(f'EXTERNAL_PARTIAL_SELL: {prev} → {shares} shares — '
                      f'campaign flagged MANUALLY_MODIFIED; lines recalculated '
                      f'from the remainder')
            self.dirty = True
            return

        self._event('EXIT', -qty, price, shares, avg, source)
        tag = 'EXIT' if source == 'BOT' else 'EXTERNAL_FULL_EXIT'
        self._log(f'{tag}: campaign {self.campaign_id} closed — '
                  f'-{qty} @ {self._fp(price or 0)}')
        self.campaign_state = 'COMPLETED'
        self.campaign_id = None
        self.manually_modified = False
        if price:
            # Same-day fast reload: one bait at the actual final sell fill
            # -3%, valid only for the rest of this session (manual §9.3).
            self.vantage = price
            self.vantage_src = 'reload'
            self.campaign_state = 'RELOAD_ARMED'
            self._log(f'RELOAD_ARMED: {self._fp(calc_reload_price(price))} '
                      f'(sell fill -{RELOAD_DROP_PCT}%) until the session ends')
        self.dirty = True

    # ── Main decision cycle ───────────────────────────────────────────────────

    def poll(self, snap: dict) -> list:
        self._roll_day(snap)
        shares = int(snap.get('shares') or 0)
        self._expire_pending(snap, shares)
        self._apply_card(snap)
        self._detect_fills(snap, shares)
        self._prev_shares = shares
        self._prev_avg = float(snap.get('avg_cost') or 0)
        self.trigger = {'BUY': None, 'SELL': None}
        self.trigger_note = None
        price = snap.get('price')
        if price and shares > 0 and self.campaign_low is not None:
            self.campaign_low = min(self.campaign_low, price)
        return (self._poll_deployed(snap, shares) if shares > 0
                else self._poll_flat(snap))

    # ── DEPLOYED: exactly two lines — next CHASE, full EXIT ──────────────────

    def _poll_deployed(self, snap, shares):
        self.state = 'DEPLOYED'
        avg = float(snap.get('avg_cost') or 0)
        if avg <= 0:
            self.campaign_state = 'PAUSED_RECONCILE'
            self.status = ('deployed but the broker reports no average cost — '
                           'paused instead of guessing')
            self.lines = {}
            return []

        g = gear_params(self.gear)
        chase_p = trim_buy_price(self.ticker, calc_chase_price(avg, self.gear))
        chase_q = calc_chase_shares(shares, self.gear)
        exit_p = trim_sell_price(self.ticker,
                                 calc_exit_price(avg, self.gear, self.exit_tier))

        self.lines = {'exit': (exit_p, shares)}
        if chase_q > 0:
            self.lines['chase'] = (chase_p, chase_q)

        acts = []
        # Campaign cap and army are both checked every poll so the window can
        # flag an unaffordable chase before the line is crossed.
        unit = snap.get('unit_cash') or 0.0
        spent = shares * avg
        need = chase_p * chase_q
        cap_room = (self.cap_units * unit - spent) if unit > 0 else None
        bp = snap.get('buying_power')
        capped = cap_room is not None and need > cap_room
        broke = bp is not None and need > bp
        if capped:
            self._exhaust(f'next chase needs {self._fp(need)}, campaign cap '
                          f'leaves {self._fp(max(0.0, cap_room))}', 'CAPPED')
        elif broke:
            self._exhaust(f'next chase needs {chase_q} @ {self._fp(chase_p)}')
        else:
            self._buy_ok()

        price = snap.get('price')
        if price is None:
            self.status = 'no price — watching paused'
            return acts
        buys, sells = self._split_orders(snap)
        tag = (f'G{self.gear} T{self.exit_tier} +'
               f'{exit_pct(self.gear, self.exit_tier)}%')

        # 1) EXIT first — the campaign always prefers to finish.
        if price >= exit_p:
            if sells:
                self.campaign_state = 'EXIT_PENDING'
                self.status = f'[{tag}] EXIT resting — waiting for the fill'
                return acts
            label = f'EXIT T{self.exit_tier} (full)'
            self.trigger['SELL'] = {'side': 'SELL', 'price': exit_p,
                                    'qty': shares, 'label': label,
                                    'kind': 'EXIT'}
            if not snap.get('can_trade', True):
                self.status = (f'[{tag}] EXIT trigger met @ {self._fp(price)} '
                               f'(watching only)')
                return acts
            acts += [('cancel', o['id'], 'our chase (exit first)')
                     for o in buys if o.get('mine')]
            self._place(acts, 'SELL', exit_p, shares, label, kind='EXIT')
            self.campaign_state = 'EXIT_PENDING'
            self.status = f'[{tag}] EXIT fired: {shares} @ {self._fp(exit_p)}'
            return acts

        # 2) CHASE — only when both the army and the campaign cap allow it.
        if chase_q > 0 and price <= chase_p:
            if buys:
                self.campaign_state = 'CHASE_PENDING'
                self.status = f'[{tag}] chase resting — waiting for the fill'
                return acts
            if capped or broke:
                why = ('campaign cap reached' if capped
                       else 'no reserve army')
                self.trigger_note = (f'▼ CHASE crossed — {why}; the manual '
                                     f'Buy button is off')
                self.status = (f'[{tag}] CHASE crossed but {why} — watching '
                               f'EXIT only')
                return acts
            label = (f'CHASE -{chase_drop(self.gear)}% ×{g["frac"]} '
                     f'(G{self.gear})')
            self.trigger['BUY'] = {'side': 'BUY', 'price': chase_p,
                                   'qty': chase_q, 'label': label,
                                   'kind': 'CHASE'}
            if not snap.get('can_trade', True):
                self.status = (f'[{tag}] CHASE trigger met @ '
                               f'{self._fp(price)} (watching only)')
                return acts
            acts += [('cancel', o['id'], 'our exit (chase first)')
                     for o in sells if o.get('mine')]
            self._place(acts, 'BUY', chase_p, chase_q, label, kind='CHASE')
            self.campaign_state = 'CHASE_PENDING'
            self.status = f'[{tag}] CHASE fired: {chase_q} @ {self._fp(chase_p)}'
            return acts

        self.campaign_state = 'DEPLOYED'
        lo = self._fp(chase_p) if chase_q > 0 else '--'
        tail = ''
        if self.buy_state == 'CAPPED':
            tail = ' · CAP reached (chase off)'
        elif self.buy_state == 'EXHAUSTED':
            tail = ' · EXHAUSTED (chase off)'
        if self.manually_modified:
            tail += ' · MANUALLY_MODIFIED'
        self.status = (f'[{tag}] watching: {lo} < now {self._fp(price)} '
                       f'< {self._fp(exit_p)}{tail}')
        return acts

    # ── FLAT: one line — the LOAD hanging off the vantage ────────────────────

    def _poll_flat(self, snap):
        self.state = 'FLAT'
        self._refresh_flat_vantage(snap)
        if not self.vantage or self.vantage <= 0:
            self.campaign_state = 'FLAT'
            self.status = 'flat — waiting for the vantage (High5)'
            self.lines = {}
            return []

        reload_mode = (self.vantage_src == 'reload')
        if reload_mode:
            raw = calc_reload_price(self.vantage)
            drop = RELOAD_DROP_PCT
        else:
            raw = calc_load_price(self.vantage, self.gear)
            drop = load_drop(self.gear)
        load_p = trim_buy_price(self.ticker, raw)
        unit = snap.get('unit_cash') or 0.0
        # One full unit of cash, sized off the TRIMMED (actually orderable)
        # load price so the placed order matches what the card shows.
        load_q = (max(1, round_half_up(unit / load_p))
                  if unit > 0 and load_p > 0 else 0)
        if load_q <= 0:
            self.lines = {}
            self.campaign_state = 'FLAT'
            self.status = 'flat — unit cash unknown'
            return []

        # Projected exit if the load filled here (informational only).
        pexit = trim_sell_price(
            self.ticker, calc_exit_price(load_p, self.gear, self.exit_tier))
        self.lines = {'load': (load_p, load_q), 'pexit': (pexit, load_q)}

        acts = []
        bp = snap.get('buying_power')
        need = load_p * load_q
        affordable = bp is None or need <= bp
        if not affordable:
            self._exhaust(f'load needs {load_q} @ {self._fp(load_p)}')
        else:
            self._buy_ok()

        price = snap.get('price')
        if price is None:
            self.status = 'no price — watching paused'
            return acts
        buys, _sells = self._split_orders(snap)
        label = (f'RELOAD -{drop}%' if reload_mode
                 else f'LOAD -{drop}% (G{self.gear})')
        tag = f'G{self.gear}' + (' reload' if reload_mode else '')

        if price <= load_p:
            if buys:
                self.campaign_state = 'ARMED_LOAD'
                self.status = f'[{tag}] load resting — waiting for the fill'
                return acts
            if not affordable:
                self.trigger_note = ('▼ LOAD crossed — no reserve army; the '
                                     'manual Buy button is off')
                self.status = (f'[{tag}] LOAD crossed but no reserve army '
                               f'remains')
                return acts
            self.trigger['BUY'] = {'side': 'BUY', 'price': load_p,
                                   'qty': load_q, 'label': label,
                                   'kind': 'RELOAD' if reload_mode else 'LOAD'}
            if not snap.get('can_trade', True):
                self.status = (f'[{tag}] LOAD trigger met @ '
                               f'{self._fp(price)} (watching only)')
                return acts
            self._place(acts, 'BUY', load_p, load_q, label,
                        kind='RELOAD' if reload_mode else 'LOAD')
            self.campaign_state = 'ARMED_LOAD'
            self.status = f'[{tag}] LOAD fired: {load_q} @ {self._fp(load_p)}'
            return acts

        self.campaign_state = ('RELOAD_ARMED' if reload_mode else 'FLAT')
        src = 'reload' if reload_mode else self.vantage_src
        self.status = (f'[{tag}] watching: load {self._fp(load_p)} < now '
                       f'{self._fp(price)} (vantage {self._fp(self.vantage)}, '
                       f'{src})')
        return acts

    # ── Read-only campaign summary (the window's header) ─────────────────────

    def summary(self, shares=0, avg=0.0, price=None):
        exit_p = (calc_exit_price(avg, self.gear, self.exit_tier)
                  if avg > 0 else None)
        return {
            'strategy': STRATEGY_ID,
            'campaign_id': self.campaign_id,
            'campaign_state': self.campaign_state,
            'gear': self.gear,
            'gear_name': gear_params(self.gear)['name'],
            'exit_tier': self.exit_tier,
            'exit_pct': exit_pct(self.gear, self.exit_tier),
            'load_pct': load_drop(self.gear),
            'chase_pct': chase_drop(self.gear),
            'add_frac': gear_params(self.gear)['frac'],
            'vantage': self.vantage,
            'vantage_src': self.vantage_src,
            'campaign_vantage': self.campaign_vantage,
            'campaign_start': self.campaign_start,
            'campaign_low': self.campaign_low,
            'chase_count': self.chase_count,
            'max_qty': self.max_qty,
            'max_cost': self.max_cost,
            'cap_units': self.cap_units,
            'manually_modified': self.manually_modified,
            'exit_price': exit_p,
            'gross_target': ((exit_p - avg) * shares
                             if exit_p and shares else None),
        }
