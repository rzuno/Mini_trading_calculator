"""V_COMMANDOS_GEARBOX — the campaign engine (pure logic, no network, no tk).

The bot watches exactly the lines the stock CARD draws: same gearbox, same
vantage, same exit tiers (`core/calc.py`). A campaign begins when the LOAD
fills and ends only when the broker says the holding is zero.

    FLAT ─ LOAD ─▶ DEPLOYED ─ CHASE… ─ EXIT tier(s) ─▶ COMPLETED ─▶ FLAT
                                                    └▶ RELOAD_ARMED (same day)

WATCHER MODEL — nothing is placed in advance
    The engine sends NO order until the price actually crosses a line. Until
    then the lines are drawn and watched, and that is all. When a line is
    crossed it sends one LIMIT order AT that line.

    A limit order can rest unfilled (the price ticked through, or away). While
    it rests it BLOCKS its side, so every poll the engine re-checks it against
    the line it is supposed to be at: if the price or quantity no longer
    matches — because the gear shifted, the average moved, or a tier was
    re-armed — it cancels its own stale order and re-arms the current line.
    The commander never has to clear the way by hand. (This self-healing came
    from the 443 engine's 'stale LOAD' / 'stale SELL' rule; the first Gearbox
    build lost it, which is what made a resting order look like a dead end.)

EXITS — one tier or several
    Each gear has three tiers. Arm one and the whole position leaves at that
    line. Arm two or three and the holding is split across them; a tier that
    fills is spent, the rest stay armed, and the campaign is over only when
    the holding is actually zero. A CHASE fill re-arms every tier on the new,
    larger holding — the ladder always describes what is held right now.

VANTAGE — where the LOAD hangs from (manual Appendix A.4/A.5)
    normally         DYNAMIC HIGH5: max(the previous FOUR completed-session
                     highs, today's high so far). A five-session window whose
                     fifth session is live, so a peak made this morning lifts
                     the LOAD line the moment it happens.
    same session
    after a full EXIT the actual final sell fill, with the LOAD a flat -3%
                     under it. This session only; the two entry systems are
                     never active at once.
    manual           a day the commander picked off the 5-day chart; frozen
                     until released.

    The bot never tries to date an old campaign's end (Appendix A.2 Case 3):
    no shares and no full sell today means FLAT and Dynamic High5, full stop.

Everything else is recomputed from the BROKER's quantity and average cost
after every fill, so the bot can adopt a position traded by hand and keep
going. There is no capital cap: the army is the only wall.

State survives restarts through to_dict()/`saved` (the controller persists it).
"""

import time
from datetime import datetime

from core.calc import (DEFAULT_EXIT_TIER, DEFAULT_GEAR, RELOAD_DROP_PCT,
                       calc_chase_price, calc_chase_shares, calc_exit_price,
                       calc_load_price, calc_reload_price, calc_sell_tiers,
                       chase_drop, clamp_gear, exit_pct, gear_params,
                       load_drop, round_half_up, tier_pcts, trim_buy_price,
                       trim_sell_price)

POLL_SECONDS = 5         # watcher tick (4 light reads per watched ticker)

_PENDING_STALE_S = 90    # forget an order intent this long after placing it
                         # if nothing rests and nothing filled (a DAY order
                         # died, or it was cancelled outside)

_SAVE_FIELDS = ('campaign_id', 'campaign_state', 'vantage', 'vantage_src',
                'vantage_manual', 'gear', 'exit_tiers',
                'tier_done', 'trading_date', 'chase_count', 'max_qty',
                'max_cost', 'campaign_low', 'campaign_start',
                'campaign_vantage', 'load_price', 'last_exit_date',
                'last_exit_price', 'manually_modified', 'events')

STRATEGY_ID = 'V_COMMANDOS_GEARBOX'

PROJECTED_CHASES = 2     # chase lines published beyond the armed one


def default_card_config() -> dict:
    """Fallback when the card has not pushed its config yet."""
    return {'gear': DEFAULT_GEAR, 'exit_tiers': [False, True, False]}


def _norm_tiers(value):
    """Accept [bool, bool, bool] or a single tier number; always return three
    booleans with at least one armed."""
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
        'highs':        [(iso_date, high)]  recent sessions, newest last,
                                     today's bar included and live-folded,
        'prev_close':   float|None   fallback when no highs are known,
        'can_trade':    bool         False in WATCH mode → lines are watched
                                     and drawn, but nothing is placed,
        'card':         dict|None    {'gear','exit_tiers'},
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
        self.lines = {}                  # see _publish_lines
        self.events = []                 # THIS campaign's TRADES, nothing else
        self.trading_date = trading_date

        # -- Campaign header ---------------------------------------------------
        self.campaign_id = None
        self.campaign_start = None
        self.campaign_vantage = None     # the vantage that generated the LOAD
        self.load_price = None
        self.gear = DEFAULT_GEAR
        self.exit_tiers = _norm_tiers(DEFAULT_EXIT_TIER)
        self.tier_done = [False, False, False]

        # -- Vantage -----------------------------------------------------------
        self.vantage = None
        self.vantage_src = 'high5'       # high5 | reload | manual | close
        self.vantage_manual = None       # a price the commander pinned
        self.last_exit_date = None
        self.last_exit_price = None

        # -- Campaign metrics ---------------------------------------------------
        self.chase_count = 0
        self.max_qty = 0
        self.max_cost = 0.0              # peak cash deployed, own currency
        self.campaign_low = None
        self.manually_modified = False

        self.buy_state = 'OK'            # 'OK' | 'EXHAUSTED'
        self.crossed = {'BUY': None, 'SELL': None}

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
        self.gear = clamp_gear(self.gear)
        self.exit_tiers = _norm_tiers(self.exit_tiers)
        self.tier_done = list(self.tier_done or [False, False, False])

    # ── Small helpers ─────────────────────────────────────────────────────────

    def _fp(self, p):
        return f'{p:,.0f}' if self.currency == 'KRW' else f'{p:,.2f}'

    def _event(self, kind, qty, price, shares, avg, source='BOT', note=''):
        """One row of the campaign log. TRADES ONLY — the log is a record of
        what was bought and sold, not of what was configured."""
        self.events.append({'date': self.trading_date or '',
                            'ts': datetime.now().strftime('%m/%d %H:%M'),
                            'kind': kind, 'qty': qty, 'price': price,
                            'shares': shares, 'avg': avg,
                            'source': source, 'note': note})
        del self.events[:-300]
        self.dirty = True

    def _exhaust(self, what):
        self.buy_state = 'EXHAUSTED'
        if not self._exhaust_logged:
            self._exhaust_logged = True
            self._log(f'EXHAUSTED: {what}')

    def _buy_ok(self):
        if self.buy_state != 'OK' and self._exhaust_logged:
            self._log('army available again — buying resumes')
        self.buy_state = 'OK'
        self._exhaust_logged = False

    @staticmethod
    def _split_orders(snap):
        buys = [o for o in snap.get('orders', []) if o.get('side') == 'BUY']
        sells = [o for o in snap.get('orders', []) if o.get('side') == 'SELL']
        return buys, sells

    def _same_price(self, a, b):
        """Broker prices come back with float noise on US names."""
        if a is None or b is None:
            return False
        return abs(float(a) - float(b)) <= (0.0 if self.ticker.endswith('.KS')
                                            else 0.005)

    def _place(self, acts, side, price, qty, label, kind=None, tiers=None):
        acts.append(('place', side, price, qty, label))
        self._pending = {'side': side, 'price': price, 'qty': qty,
                         'kind': kind or side, 'tiers': list(tiers or []),
                         'ts': time.time()}

    def _restale(self, acts, orders, price, qty, what):
        """Our resting order must always sit ON the current line. If it has
        drifted (gear shift, new average, re-armed tier) cancel it so the next
        poll can re-arm; a partially-filled order is never touched.

        Returns True when something still legitimately rests on that side."""
        alive = False
        for o in orders:
            if not o.get('mine'):
                alive = True            # a foreign order blocks the side
                continue
            if float(o.get('filled') or 0) > 0:
                alive = True            # partially filled — leave it alone
                continue
            if (self._same_price(o.get('price'), price)
                    and int(o.get('qty_open') or 0) == int(qty)):
                alive = True            # exactly the line we want
                continue
            acts.append(('cancel', o['id'], f'stale {what}'))
            self._log(f'stale {what} cancelled: was '
                      f"{o.get('qty_open')} @ {self._fp(o.get('price') or 0)}, "
                      f'line is now {qty} @ {self._fp(price)}')
            self._pending = None
        return alive

    # ── Gear / tiers follow the card, always ─────────────────────────────────

    def _apply_card(self, snap):
        """The card is the source of truth for gear and exit tiers — deployed
        or not. Only the average cost is history; the lines are recomputed
        from it on the spot, so a shift is safe. Configuration changes are
        logged to the file, never to the campaign's trade record."""
        card = dict(snap.get('card') or default_card_config())
        gear = clamp_gear(card.get('gear', self.gear))
        tiers = _norm_tiers(card.get('exit_tiers', self.exit_tiers))
        deployed = (self._prev_shares or 0) > 0

        if gear != self.gear:
            old, self.gear, self.dirty = self.gear, gear, True
            if deployed:
                self._log(f'GEAR G{old} → G{gear} (chase -{chase_drop(gear)}% '
                          f'×{gear_params(gear)["frac"]})')
        if tiers != self.exit_tiers:
            old = self.exit_tiers
            self.exit_tiers = tiers
            # A newly armed tier starts fresh; the ladder re-splits next poll.
            self.tier_done = [d and a for d, a in zip(self.tier_done, tiers)]
            self.dirty = True
            if deployed:
                self._log(f'TIERS {self._tier_text(old)} → '
                          f'{self._tier_text(tiers)}')

    def _tier_text(self, tiers=None):
        tiers = self.exit_tiers if tiers is None else tiers
        on = [f'T{i + 1}' for i, a in enumerate(tiers) if a]
        return '+'.join(on) if on else '—'

    # ── Vantage ───────────────────────────────────────────────────────────────

    def set_manual_vantage(self, price, label=''):
        """Pin the vantage to a price the commander picked (a day off the
        5-day chart). Survives day rollovers until cleared."""
        if not price or price <= 0:
            return False
        self.vantage_manual = float(price)
        self.vantage = float(price)
        self.vantage_src = 'manual'
        self.dirty = True
        self._log(f'vantage pinned by hand: {self._fp(price)} {label}'.strip())
        return True

    def clear_manual_vantage(self):
        self.vantage_manual = None
        self.vantage_src = 'high5'
        self.dirty = True
        self._log('manual vantage cleared — back to the rolling high')
        return True

    def _refresh_flat_vantage(self, snap):
        """The DYNAMIC HIGH5 vantage (manual Appendix A.4/A.5), recomputed
        every poll while flat:

            watch_vantage = max(the previous FOUR completed-session highs,
                                today's high so far)

        A five-session window where the fifth session is the live one, so a
        peak made this morning lifts the vantage — and the LOAD line under it
        — the moment it happens. That is the whole point: it lets the bot take
        a left endpoint that could not have been known before the intraday
        high occurred.

        Two things override it:
            reload   the actual final sell fill, for the rest of that session
            manual   a price pinned off the 5-day chart, until released

        Note what is deliberately NOT here: no attempt to date the previous
        campaign's end. Appendix A.2 Case 3 is explicit — with no shares and
        no full sell today, the bot resets to FLAT and uses the normal Dynamic
        High5 window. It never needs to know whether the last campaign closed
        two days ago or five."""
        if self.vantage_src == 'reload':
            return                       # frozen until the session rolls
        if self.vantage_manual:
            if self.vantage != self.vantage_manual:
                self.vantage = self.vantage_manual
                self.dirty = True
            self.vantage_src = 'manual'
            return

        today = snap.get('trading_date')
        highs = snap.get('highs') or []
        completed = [h for d, h in highs if h and d != today]
        today_high = next((h for d, h in reversed(highs)
                           if h and d == today), None)
        window = completed[-4:] + ([today_high] if today_high else [])
        v = max(window) if window else (snap.get('prev_close') or None)
        if not v or v <= 0:
            return
        src = 'high5' if window else 'close'
        if v != self.vantage or src != self.vantage_src:
            if v != self.vantage:
                self._log(f'vantage = {src} {self._fp(v)}')
            self.vantage, self.vantage_src = v, src
            self.dirty = True

    # ── Day rollover ─────────────────────────────────────────────────────────

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
            # Appendix A.2: the reload is a SAME-SESSION rule. Once the day
            # turns it expires and the normal Dynamic High5 LOAD takes over —
            # the two entry systems are never active at once.
            self._log('same-day reload expired unfilled — back to the '
                      'Dynamic High5 LOAD')
            self.vantage = None
            self.vantage_src = 'high5'
            self.campaign_state = 'FLAT'
        self._refresh_flat_vantage(snap)

    # ── Fill detection (broker share diffs are the truth) ────────────────────

    def _take_pending(self, side):
        p = self._pending
        if p and p['side'] == side:
            self._pending = None
            return p
        return None

    def _expire_pending(self, snap, shares):
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
        """Shares exist but no live campaign in this process: take the broker's
        quantity and average and carry on. No Step history to reconstruct."""
        if not self.campaign_id:
            self._open_campaign(avg, adopted=True)
        self._prev_shares, self._prev_avg = shares, avg
        self.max_qty = max(self.max_qty, shares)
        self.max_cost = max(self.max_cost, shares * avg)
        self._log(f'ADOPT_POSITION: {shares} @ {self._fp(avg)} '
                  f'(G{self.gear}, {self._tier_text()})')

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
        self.tier_done = [False, False, False]
        self.events = []
        self.dirty = True

    def _on_buy_fill(self, snap, prev, shares, avg):
        qty = shares - prev
        pend = self._take_pending('BUY')
        if pend:
            price, source = pend['price'], 'BOT'
        else:
            price = ((avg * shares - self._prev_avg * prev) / qty
                     if avg > 0 and self._prev_avg > 0 and prev > 0
                     else (avg or None))
            source = 'EXT'
        if prev == 0:
            self._open_campaign(price)
            kind = 'RELOAD' if self.vantage_src == 'reload' else 'LOAD'
            self._event(kind, qty, price, shares, avg, source)
            self._log(f'{kind} filled ({source}): 0 → {shares} shares '
                      f'— campaign {self.campaign_id} G{self.gear} '
                      f'{self._tier_text()}')
            self.vantage_src = 'high5' if not self.vantage_manual else 'manual'
        else:
            self.chase_count += 1
            # More shares to sell: every armed tier re-arms on the new holding.
            self.tier_done = [False, False, False]
            self._event('CHASE', qty, price, shares, avg, source,
                        note=f'#{self.chase_count}')
            self._log(f'CHASE filled ({source}): {prev} → {shares} shares '
                      f'(chase #{self.chase_count}) — exit ladder re-armed')
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
            price, source = pend['price'], 'BOT'
            for i in (pend.get('tiers') or []):
                if 0 <= i < 3:
                    self.tier_done[i] = True
        else:
            price, source = (snap.get('price') or self._prev_avg or None), 'EXT'

        gain = ((price - self._prev_avg) * qty
                if price and self._prev_avg else None)
        note = self._fp(gain) + ' gross' if gain is not None else ''

        if shares > 0:
            # A partial exit is normal when several tiers are armed; a hand
            # trim is not, and is flagged so the log stays honest.
            if source == 'EXT':
                self.manually_modified = True
            kind = ('T' + '/'.join(str(i + 1) for i in pend['tiers'])
                    if pend and pend.get('tiers') else 'SELL')
            self._event(kind, -qty, price, shares, avg, source, note=note)
            self._log(f'{kind} filled ({source}): {prev} → {shares} shares '
                      f'— {self._tier_text()} armed, campaign still open')
            self.dirty = True
            return

        self._event('EXIT', -qty, price, shares, avg, source, note=note)
        self._log(f'EXIT ({source}): campaign {self.campaign_id} closed — '
                  f'-{qty} @ {self._fp(price or 0)}')
        self.campaign_state = 'COMPLETED'
        self.campaign_id = None
        self.manually_modified = False
        self.tier_done = [False, False, False]
        self.last_exit_date = self.trading_date
        self.last_exit_price = price
        if price and not self.vantage_manual:
            # Rest of this session: one fast reload at the actual sell fill -3%.
            self.vantage = price
            self.vantage_src = 'reload'
            self.campaign_state = 'RELOAD_ARMED'
            self._log(f'RELOAD_ARMED: {self._fp(calc_reload_price(price))} '
                      f'(sell fill -{RELOAD_DROP_PCT}%) — this session only; '
                      f'tomorrow returns to the Dynamic High5 LOAD')
        self.dirty = True

    # ── Line publication ─────────────────────────────────────────────────────

    def _publish_lines(self, armed_buy, projections, sells):
        self.lines = {}
        if armed_buy:
            self.lines[armed_buy['kind']] = armed_buy
        for p in projections:
            self.lines[p['kind']] = p
        for sline in sells:
            self.lines[sline['kind']] = sline

    def _chase_projection(self, shares, avg, start=2, count=PROJECTED_CHASES):
        """`count` projected chase lines, numbered from `start`. A DEPLOYED
        card arms chase 1 separately (projections are 2 and 3); a FLAT card
        arms the LOAD, so its whole ladder — chases 1 and 2, exactly what the
        card prints — is projection. Each is folded into the running average
        as the campaign would run it."""
        out = []
        cur_shares, cur_avg = shares, avg
        for n in range(1, start + count):
            if cur_shares <= 0 or cur_avg <= 0:
                break
            price = trim_buy_price(self.ticker,
                                   calc_chase_price(cur_avg, self.gear))
            qty = calc_chase_shares(cur_shares, self.gear)
            if price <= 0 or qty <= 0:
                break
            if n >= start:
                out.append({'price': price, 'qty': qty, 'kind': f'chase{n}',
                            'label': f'chase {n}', 'armed': False})
            new_shares = cur_shares + qty
            cur_avg = (cur_avg * cur_shares + price * qty) / new_shares
            cur_shares = new_shares
        return out

    def _exit_ladder(self, shares, avg):
        """The armed exit lines: the holding split across the tiers that are
        armed and not yet spent. When every armed tier has been spent but
        shares remain (a partial fill, a hand trade), the ladder restarts on
        what is left — the lines always describe the real position."""
        live = [a and not d for a, d in zip(self.exit_tiers, self.tier_done)]
        if not any(live):
            self.tier_done = [False, False, False]
            live = list(self.exit_tiers)
        pcts = tier_pcts(self.gear)
        split = calc_sell_tiers(shares, avg, pcts, live)
        out = []
        for i, e in enumerate(split):
            if e['price'] is None:
                continue
            out.append({'price': trim_sell_price(self.ticker, e['price']),
                        'qty': e['qty'], 'kind': f'exit{i + 1}', 'tier': i,
                        'label': f'EXIT T{i + 1} +{pcts[i]}%', 'armed': True})
        return out

    # ── Main decision cycle ───────────────────────────────────────────────────

    def poll(self, snap: dict) -> list:
        self._roll_day(snap)
        shares = int(snap.get('shares') or 0)
        self._expire_pending(snap, shares)
        self._apply_card(snap)
        self._detect_fills(snap, shares)
        self._prev_shares = shares
        self._prev_avg = float(snap.get('avg_cost') or 0)
        self.crossed = {'BUY': None, 'SELL': None}
        price = snap.get('price')
        if price and shares > 0 and self.campaign_low is not None:
            self.campaign_low = min(self.campaign_low, price)
        return (self._poll_deployed(snap, shares) if shares > 0
                else self._poll_flat(snap))

    # ── DEPLOYED: armed CHASE + the exit ladder ──────────────────────────────

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
        exits = self._exit_ladder(shares, avg)
        armed_buy = ({'price': chase_p, 'qty': chase_q, 'kind': 'chase',
                      'label': f'CHASE -{chase_drop(self.gear)}% ×{g["frac"]}',
                      'armed': True} if chase_q > 0 else None)
        self._publish_lines(armed_buy, self._chase_projection(shares, avg),
                            exits)

        acts = []
        bp = snap.get('buying_power')
        need = chase_p * chase_q
        affordable = bp is None or need <= bp
        if not affordable:
            self._exhaust(f'next chase needs {chase_q} @ {self._fp(chase_p)}')
        else:
            self._buy_ok()

        price = snap.get('price')
        if price is None:
            self.status = 'no price — watching paused'
            return acts
        buys, sells = self._split_orders(snap)
        tag = f'G{self.gear} {self._tier_text()}'

        # 1) EXIT first — the campaign always prefers to take money off.
        #    A gap through several tiers sells everything they cover, in ONE
        #    order at the highest line crossed.
        hit = [e for e in exits if price >= e['price']]
        if hit:
            qty = sum(e['qty'] for e in hit)
            line = max(e['price'] for e in hit)
            tiers = [e['tier'] for e in hit]
            label = 'EXIT ' + '+'.join(f'T{i + 1}' for i in tiers)
            self.crossed['SELL'] = {'price': line, 'qty': qty}
            if self._restale(acts, sells, line, qty, 'exit'):
                self.campaign_state = 'EXIT_PENDING'
                self.status = f'[{tag}] exit resting — waiting for the fill'
                return acts
            if not snap.get('can_trade', True):
                self.status = (f'[{tag}] exit line crossed @ '
                               f'{self._fp(price)} — WATCH mode, not sent')
                return acts
            acts += [('cancel', o['id'], 'our chase (exit first)')
                     for o in buys if o.get('mine')]
            self._place(acts, 'SELL', line, qty, label, kind='EXIT',
                        tiers=tiers)
            self.campaign_state = 'EXIT_PENDING'
            self.status = f'[{tag}] {label} fired: {qty} @ {self._fp(line)}'
            return acts

        # 2) CHASE — while the army can fund it.
        if chase_q > 0 and price <= chase_p:
            self.crossed['BUY'] = {'price': chase_p, 'qty': chase_q}
            if self._restale(acts, buys, chase_p, chase_q, 'chase'):
                self.campaign_state = 'CHASE_PENDING'
                self.status = f'[{tag}] chase resting — waiting for the fill'
                return acts
            if not affordable:
                self.status = (f'[{tag}] CHASE crossed but no reserve army '
                               f'remains — watching the exits only')
                return acts
            if not snap.get('can_trade', True):
                self.status = (f'[{tag}] CHASE line crossed @ '
                               f'{self._fp(price)} — WATCH mode, not sent')
                return acts
            acts += [('cancel', o['id'], 'our exit (chase first)')
                     for o in sells if o.get('mine')]
            self._place(acts, 'BUY', chase_p, chase_q,
                        f'CHASE -{chase_drop(self.gear)}% ×{g["frac"]} '
                        f'(G{self.gear})', kind='CHASE')
            self.campaign_state = 'CHASE_PENDING'
            self.status = f'[{tag}] CHASE fired: {chase_q} @ {self._fp(chase_p)}'
            return acts

        # 3) Nothing crossed — retire any order of ours still sitting out here.
        self._restale(acts, buys, chase_p, chase_q, 'chase')
        if exits:
            self._restale(acts, sells, exits[0]['price'], exits[0]['qty'],
                          'exit')

        self.campaign_state = 'DEPLOYED'
        lo = self._fp(chase_p) if chase_q > 0 else '--'
        hi = self._fp(min(e['price'] for e in exits)) if exits else '--'
        tail = ' · EXHAUSTED (chase off)' if self.buy_state != 'OK' else ''
        if self.manually_modified:
            tail += ' · MANUALLY_MODIFIED'
        self.status = (f'[{tag}] watching: {lo} < now {self._fp(price)} '
                       f'< {hi}{tail}')
        return acts

    # ── FLAT: the LOAD hanging off the vantage, plus its projected ladder ────

    def _poll_flat(self, snap):
        self.state = 'FLAT'
        self._refresh_flat_vantage(snap)
        if not self.vantage or self.vantage <= 0:
            self.campaign_state = 'FLAT'
            self.status = 'flat — waiting for the vantage'
            self.lines = {}
            return []

        reload_mode = (self.vantage_src == 'reload')
        if reload_mode:
            raw, drop = calc_reload_price(self.vantage), RELOAD_DROP_PCT
        else:
            raw, drop = calc_load_price(self.vantage, self.gear), load_drop(self.gear)
        load_p = trim_buy_price(self.ticker, raw)
        unit = snap.get('unit_cash') or 0.0
        load_q = (max(1, round_half_up(unit / load_p))
                  if unit > 0 and load_p > 0 else 0)
        if load_q <= 0:
            self.lines = {}
            self.campaign_state = 'FLAT'
            self.status = 'flat — unit cash unknown'
            return []

        kind = 'RELOAD' if reload_mode else 'LOAD'
        pcts = tier_pcts(self.gear)
        pexits = [{'price': trim_sell_price(
                       self.ticker, calc_exit_price(load_p, self.gear, i + 1)),
                   'qty': e['qty'], 'kind': f'pexit{i + 1}', 'tier': i,
                   'label': f'exit if loaded T{i + 1} +{pcts[i]}%',
                   'armed': False}
                  for i, e in enumerate(
                      calc_sell_tiers(load_q, load_p, pcts, self.exit_tiers))
                  if e['price'] is not None]
        self._publish_lines(
            {'price': load_p, 'qty': load_q, 'kind': 'load', 'armed': True,
             'label': f'{kind} -{drop}%'},
            self._chase_projection(load_q, load_p, start=1), pexits)

        acts = []
        bp = snap.get('buying_power')
        affordable = bp is None or load_p * load_q <= bp
        if not affordable:
            self._exhaust(f'load needs {load_q} @ {self._fp(load_p)}')
        else:
            self._buy_ok()

        price = snap.get('price')
        if price is None:
            self.status = 'no price — watching paused'
            return acts
        buys, sells = self._split_orders(snap)
        acts += [('cancel', o['id'], 'stray sell (no shares)')
                 for o in sells if o.get('mine')]
        tag = f'G{self.gear}' + (' reload' if reload_mode else '')

        if price <= load_p:
            self.crossed['BUY'] = {'price': load_p, 'qty': load_q}
            if self._restale(acts, buys, load_p, load_q, kind.lower()):
                self.campaign_state = 'ARMED_LOAD'
                self.status = f'[{tag}] load resting — waiting for the fill'
                return acts
            if not affordable:
                self.status = (f'[{tag}] LOAD crossed but no reserve army '
                               f'remains')
                return acts
            if not snap.get('can_trade', True):
                self.status = (f'[{tag}] LOAD line crossed @ '
                               f'{self._fp(price)} — WATCH mode, not sent')
                return acts
            self._place(acts, 'BUY', load_p, load_q,
                        f'{kind} -{drop}% (G{self.gear})', kind=kind)
            self.campaign_state = 'ARMED_LOAD'
            self.status = f'[{tag}] {kind} fired: {load_q} @ {self._fp(load_p)}'
            return acts

        self._restale(acts, buys, load_p, load_q, kind.lower())
        self.campaign_state = ('RELOAD_ARMED' if reload_mode else 'FLAT')
        self.status = (f'[{tag}] watching: load {self._fp(load_p)} < now '
                       f'{self._fp(price)} (vantage {self._fp(self.vantage)}, '
                       f'{self.vantage_src})')
        return acts

    # ── Read-only campaign summary (the window's header) ─────────────────────

    def summary(self, shares=0, avg=0.0, price=None):
        pcts = tier_pcts(self.gear)
        return {
            'strategy': STRATEGY_ID,
            'campaign_id': self.campaign_id,
            'campaign_state': self.campaign_state,
            'gear': self.gear,
            'gear_name': gear_params(self.gear)['name'],
            'exit_tiers': list(self.exit_tiers),
            'tier_done': list(self.tier_done),
            'tier_pcts': list(pcts),
            'tier_text': self._tier_text(),
            'exit_pct': next((p for p, a in zip(pcts, self.exit_tiers) if a),
                             pcts[DEFAULT_EXIT_TIER - 1]),
            'load_pct': load_drop(self.gear),
            'chase_pct': chase_drop(self.gear),
            'add_frac': gear_params(self.gear)['frac'],
            'vantage': self.vantage,
            'vantage_src': self.vantage_src,
            'vantage_manual': self.vantage_manual,
            'last_exit_date': self.last_exit_date,
            'last_exit_price': self.last_exit_price,
            'campaign_vantage': self.campaign_vantage,
            'campaign_start': self.campaign_start,
            'campaign_low': self.campaign_low,
            'chase_count': self.chase_count,
            'max_qty': self.max_qty,
            'max_cost': self.max_cost,
            'manually_modified': self.manually_modified,
            'gross_target': sum(
                (e['price'] - avg) * e['qty']
                for e in self.lines.values()
                if e.get('kind', '').startswith('exit') and avg) or None,
        }
