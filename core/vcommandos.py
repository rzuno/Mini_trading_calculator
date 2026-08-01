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
from zoneinfo import ZoneInfo

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
                'vantage_manual', 'vantage_manual_label', 'gear', 'exit_tiers',
                'tier_done', 'trading_date', 'chase_count', 'max_qty',
                'max_cost', 'campaign_low', 'campaign_start',
                'campaign_vantage', 'load_price', 'last_exit_date',
                'last_exit_price', 'manually_modified', 'events')

_LEGACY_NON_TRADE_EVENTS = {
    'ADOPT', 'ADOPT_POSITION', 'GEAR', 'TIER', 'TIERS', 'HOLD'
}

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
        'recent_fills': [{'side','qty','price','filled_at','order_id',
                          'client_order_id','mine'}],
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
        self.vantage_manual_label = ''   # e.g. "07/31 high" for the chart
        self.last_exit_date = None
        self.last_exit_price = None

        # -- Campaign metrics ---------------------------------------------------
        self.chase_count = 0
        self.max_qty = 0
        self.max_cost = 0.0              # peak cash deployed, own currency
        self.campaign_low = None
        self.manually_modified = False

        self.buy_state = 'OK'            # OK | EXHAUSTED | UNAVAILABLE
        self.crossed = {'BUY': None, 'SELL': None}

        self.dirty = False
        self._exhaust_logged = False
        self._pending = None             # {'side','price','qty','kind','ts'}
        self.consumed_fill_keys = set()  # evidence accepted by the last poll
        self._prev_shares = None
        self._prev_avg = 0.0
        self._restored_q = None           # persisted broker baseline; the first
        self._restored_avg = 0.0          # live poll reconciles without a fill
        self._log = log or (lambda msg: None)
        if saved:
            self._restore(saved)

    # ── Persistence ───────────────────────────────────────────────────────────

    def to_dict(self):
        d = {f: getattr(self, f) for f in _SAVE_FIELDS}
        d['strategy'] = STRATEGY_ID
        d['q'] = (self._prev_shares if self._prev_shares is not None
                  else self._restored_q)
        d['avg'] = (self._prev_avg if self._prev_shares is not None
                    else self._restored_avg)
        d['pending'] = (self._safe_pending(self._pending)
                        if self._pending and self._pending.get('accepted')
                        else None)
        d['saved_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return d

    def _restore(self, saved):
        for f in _SAVE_FIELDS:
            if f in saved:
                setattr(self, f, saved[f])
        old_events = list(self.events or [])
        self.events = [e for e in old_events if isinstance(e, dict) and not (
            str(e.get('kind') or '').upper() in _LEGACY_NON_TRADE_EVENTS
            and not self._nonzero_number(e.get('qty')))]
        if len(self.events) != len(old_events):
            self.dirty = True
        self.gear = clamp_gear(self.gear)
        self.exit_tiers = _norm_tiers(self.exit_tiers)
        self.tier_done = (list(self.tier_done or [False, False, False])
                          + [False, False, False])[:3]
        self.vantage_manual_label = str(self.vantage_manual_label or '')
        try:
            self._restored_q = (None if saved.get('q') is None
                                else max(0, int(saved.get('q'))))
        except (TypeError, ValueError):
            self._restored_q = None
        try:
            self._restored_avg = max(0.0, float(saved.get('avg') or 0.0))
        except (TypeError, ValueError):
            self._restored_avg = 0.0
        self._pending = self._safe_pending(saved.get('pending'))

    @staticmethod
    def _positive_number(value):
        try:
            return float(value) > 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _nonzero_number(value):
        try:
            return float(value) != 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _safe_pending(value):
        """Return a JSON-safe, validated pending-order record.

        Old or half-written state must not manufacture ownership. In
        particular, a restored intent is trusted only when it was accepted by
        the broker and carries a stable broker/client order id.
        """
        if not isinstance(value, dict):
            return None
        side = str(value.get('side') or '').upper()
        try:
            price = float(value.get('price'))
            qty = int(value.get('qty'))
        except (TypeError, ValueError):
            return None
        if side not in ('BUY', 'SELL') or price <= 0 or qty <= 0:
            return None
        order_id = value.get('order_id')
        client_order_id = value.get('client_order_id')
        accepted = bool(value.get('accepted'))
        if accepted and order_id is None and client_order_id is None:
            # Legacy pending data had no stable ownership proof. It is safer
            # to let the open-order snapshot classify it than to restore it as
            # a bot order merely because price and quantity look familiar.
            return None
        try:
            ts = float(value.get('ts') or time.time())
        except (TypeError, ValueError):
            ts = time.time()
        try:
            filled_seen = max(0, int(value.get('filled_seen') or 0))
        except (TypeError, ValueError):
            filled_seen = 0
        try:
            submitted_at = float(value.get('submitted_at') or ts)
        except (TypeError, ValueError):
            submitted_at = ts
        try:
            unresolved_event_qty = int(value.get('unresolved_event_qty') or 0)
        except (TypeError, ValueError):
            unresolved_event_qty = 0
        tiers = []
        for tier in value.get('tiers') or []:
            try:
                tier = int(tier)
            except (TypeError, ValueError):
                continue
            if 0 <= tier < 3 and tier not in tiers:
                tiers.append(tier)
        return {
            'side': side, 'price': price, 'qty': qty,
            'kind': str(value.get('kind') or side).upper(),
            'tiers': tiers, 'ts': ts,
            'order_id': order_id, 'client_order_id': client_order_id,
            'accepted': accepted,
            'filled_seen': min(qty, filled_seen),
            'step_counted': bool(value.get('step_counted')),
            'cancelling': bool(value.get('cancelling')),
            # ``unresolved`` means a request may have reached Toss, or a
            # holdings change outran its execution detail.  It deliberately
            # survives restarts: forgetting it could create a duplicate.
            'unresolved': bool(value.get('unresolved')),
            'terminal_status': str(value.get('terminal_status') or '').upper(),
            'submitted_at': submitted_at,
            'unresolved_event_index': value.get('unresolved_event_index'),
            'unresolved_event_qty': unresolved_event_qty,
        }

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
        orders = [o for o in (snap.get('orders') or [])
                  if float(o.get('qty_open') or 0) > 0]
        buys = [o for o in orders
                if str(o.get('side') or '').upper() == 'BUY']
        sells = [o for o in orders
                 if str(o.get('side') or '').upper() == 'SELL']
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
                         'kind': (kind or side).upper(),
                         'tiers': list(tiers or []), 'ts': time.time(),
                         'order_id': None, 'client_order_id': None,
                         'accepted': False, 'filled_seen': 0,
                         'step_counted': False, 'cancelling': False,
                         'unresolved': False, 'terminal_status': '',
                         'submitted_at': None,
                         'unresolved_event_index': None,
                         'unresolved_event_qty': 0}

    def _pending_matches_intent(self, side, price, qty):
        p = self._pending
        if not p or p.get('side') != str(side or '').upper():
            return False
        try:
            return (self._same_price(p.get('price'), price)
                    and int(p.get('qty') or 0) == int(qty))
        except (TypeError, ValueError):
            return False

    def note_order_submitted(self, side, price, qty, client_order_id):
        """Bind the idempotency key before the network request is sent.

        A transport timeout is not a rejection: Toss may already have accepted
        the request.  Persisting the client id makes the intent ownable and
        lets the controller safely repeat the *same* idempotent request.
        """
        if not client_order_id or not self._pending_matches_intent(
                side, price, qty):
            return False
        p = self._pending
        submitted_at = p.get('submitted_at') or time.time()
        p.update({'accepted': True, 'client_order_id': client_order_id,
                  'ts': time.time(), 'unresolved': True,
                  'terminal_status': '', 'submitted_at': submitted_at})
        self.campaign_state = 'PAUSED_RECONCILE'
        self.status = ('order submission awaiting broker confirmation — '
                       'no replacement will be sent')
        self.dirty = True
        return True

    def note_order_accepted(self, side, price, qty, order_id=None,
                            client_order_id=None):
        """Bind broker acceptance/detail to the already persisted intent.

        The client id is persisted before transmission; this callback adds the
        broker id and clears the outcome-ambiguous guard after a definite
        successful ``place`` response.
        """
        if not self._pending_matches_intent(side, price, qty):
            return False
        p = self._pending
        p.update({'accepted': True, 'order_id': order_id,
                  'client_order_id': client_order_id, 'ts': time.time(),
                  'cancelling': False, 'unresolved': False,
                  'terminal_status': ''})
        self.dirty = True
        return True

    def note_order_failed(self, side, price, qty):
        """Forget a proposed placement that was skipped or rejected."""
        if not self._pending_matches_intent(side, price, qty):
            return False
        self._pending = None
        self.campaign_state = ('DEPLOYED' if (self._prev_shares or 0) > 0
                               else ('RELOAD_ARMED'
                                     if self.vantage_src == 'reload'
                                     else 'FLAT'))
        self.status = 'order was not accepted — line re-armed'
        self.dirty = True
        return True

    def note_order_unresolved(self, side=None, price=None, qty=None, reason='',
                              order_id=None, client_order_id=None):
        """Keep an accepted intent whose broker outcome stayed ambiguous.

        This is deliberately different from ``note_order_failed``: the broker
        may have accepted or filled it, so the engine pauses reconciliation and
        makes no claim about rejection. Optional side/price/qty guards prevent
        a late callback from clearing a newer intent.
        """
        p = self._pending
        if not p or not p.get('accepted'):
            return False
        if side is not None and p.get('side') != str(side).upper():
            return False
        if price is not None and not self._same_price(p.get('price'), price):
            return False
        if qty is not None:
            try:
                if int(p.get('qty') or 0) != int(qty):
                    return False
            except (TypeError, ValueError):
                return False
        if order_id is not None:
            p['order_id'] = order_id
        if client_order_id is not None:
            p['client_order_id'] = client_order_id
        p['unresolved'] = True
        p['ts'] = time.time()
        self.campaign_state = 'PAUSED_RECONCILE'
        suffix = f' ({reason})' if reason else ''
        self.status = ('broker order outcome unresolved — reconciliation '
                       f'paused without guessing{suffix}')
        self.dirty = True
        return True

    @staticmethod
    def _order_id(order):
        return order.get('id') if order.get('id') is not None \
            else order.get('order_id')

    def _pending_matches_order(self, pending, order):
        if not pending or not order:
            return False
        if str(order.get('side') or '').upper() != pending.get('side'):
            return False
        oid = self._order_id(order)
        if pending.get('order_id') is not None and oid is not None:
            return str(pending['order_id']) == str(oid)
        coid = order.get('client_order_id')
        if pending.get('client_order_id') is not None and coid is not None:
            return str(pending['client_order_id']) == str(coid)
        # Price/quantity matching is allowed only after the controller has
        # independently labelled the order as ours.
        if not order.get('mine'):
            return False
        total = int(order.get('qty_open') or 0) + int(order.get('filled') or 0)
        return (self._same_price(pending.get('price'), order.get('price'))
                and total == int(pending.get('qty') or 0))

    def _order_owned(self, order):
        """Ownership requires the controller's flag or a stable saved id."""
        if order.get('mine'):
            return True
        p = self._pending
        if not p or not p.get('accepted'):
            return False
        oid = self._order_id(order)
        if p.get('order_id') is not None and oid is not None:
            return str(p['order_id']) == str(oid)
        coid = order.get('client_order_id')
        return (p.get('client_order_id') is not None and coid is not None
                and str(p['client_order_id']) == str(coid))

    def _matching_pending_order(self, snap, pending=None):
        p = pending or self._pending
        return next((o for o in (snap.get('orders') or [])
                     if float(o.get('qty_open') or 0) > 0
                     and self._pending_matches_order(p, o)), None)

    def _mark_cancelling(self, order=None):
        p = self._pending
        if not p:
            return
        if order is None or self._pending_matches_order(p, order):
            p['cancelling'] = True
            p['ts'] = time.time()
            self.dirty = True

    def _pending_state(self, fallback='DEPLOYED'):
        kind = str((self._pending or {}).get('kind') or '').upper()
        return {'LOAD': 'ARMED_LOAD', 'RELOAD': 'ARMED_LOAD',
                'CHASE': 'CHASE_PENDING', 'EXIT': 'EXIT_PENDING'}.get(
                    kind, fallback)

    def _restale(self, acts, orders, price, qty, what):
        """Reconcile bot-owned orders with one currently desired line.

        ``resting`` means an exact or partially filled bot order is still
        alive. ``cancelling`` means this poll emits cancellation only; a
        replacement may be considered after a later broker snapshot confirms
        the old order is gone. ``clear`` means the side is unobstructed.
        """
        alive = False
        cancelling = False
        side = 'SELL' if what == 'exit' else 'BUY'
        for o in orders:
            if not self._order_owned(o):
                return 'foreign'          # normally caught ticker-wide first
            if float(o.get('filled') or 0) > 0:
                alive = True              # never disturb a partial fill
                continue
            if (self._same_price(o.get('price'), price)
                    and int(o.get('qty_open') or 0) == int(qty)):
                alive = True
                continue
            acts.append(('cancel', self._order_id(o), f'stale {what}'))
            self._log(f'stale {what} cancelled: was '
                      f"{o.get('qty_open')} @ {self._fp(o.get('price') or 0)}, "
                      f'line is now {qty} @ {self._fp(price)}')
            self._mark_cancelling(o)
            cancelling = True

        # The accepted order can briefly be absent from the open-order feed.
        # Keep it pending rather than fire a duplicate. If its desired line
        # changed and a stable id exists, request cancellation and still wait
        # for a later snapshot before replacing it.
        p = self._pending
        if not orders and p and p.get('side') == side:
            same = (self._same_price(p.get('price'), price)
                    and int(p.get('qty') or 0) == int(qty))
            if p.get('cancelling'):
                return 'cancelling'
            if same:
                return 'resting'
            if p.get('accepted') and p.get('order_id') is not None:
                acts.append(('cancel', p['order_id'], f'stale {what}'))
                self._mark_cancelling()
                return 'cancelling'
            if p.get('accepted') and p.get('client_order_id') is not None:
                # A transport-ambiguous submission has a durable client id but
                # may not have yielded its broker id yet.  It cannot be safely
                # replaced or cancelled by price/quantity.
                return 'resting'
            if not p.get('accepted'):
                # The executor has not acknowledged the just-emitted action
                # yet. It will call accepted or failed before another cycle.
                return 'resting'
        if cancelling:
            return 'cancelling'
        return 'resting' if alive else 'clear'

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
        5-day chart). A live campaign or resting LOAD cannot be moved from
        underneath an order; the user chooses a Vantage only while FLAT."""
        if (not price or price <= 0 or self.campaign_id
                or (self._prev_shares or 0) > 0
                or self.campaign_state != 'FLAT' or self._pending):
            return False
        self.vantage_manual = float(price)
        self.vantage_manual_label = (str(label or '').strip()
                                     or f'Manual {self._fp(price)}')
        self.vantage = float(price)
        self.vantage_src = 'manual'
        self.dirty = True
        self._log(f'vantage pinned by hand: {self._fp(price)} '
                  f'({self.vantage_manual_label})')
        return True

    def clear_manual_vantage(self):
        if (not self.vantage_manual or self.campaign_id
                or (self._prev_shares or 0) > 0
                or self.campaign_state != 'FLAT' or self._pending):
            return False
        self.vantage_manual = None
        self.vantage_manual_label = ''
        self.vantage_src = 'high5'
        self.vantage = None
        self.dirty = True
        self._log('manual vantage cleared — Automatic Dynamic High5 restored')
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

    def _expire_pending(self, snap, shares):
        p = self._pending
        if not p:
            return
        detail = snap.get('pending_order')
        if isinstance(detail, dict) and self._pending_matches_order(p, detail):
            status = str(detail.get('status') or '').upper()
            if detail.get('order_id') is not None:
                p['order_id'] = detail.get('order_id')
            if detail.get('client_order_id') is not None:
                p['client_order_id'] = detail.get('client_order_id')
            if detail.get('terminal'):
                p['terminal_status'] = status
            else:
                # Exact detail proves that the submission was accepted.  A
                # prior transport ambiguity no longer needs to pause it.
                p['unresolved'] = False
                p['terminal_status'] = ''

        resting = self._matching_pending_order(snap, p)
        unchanged = shares == self._prev_shares
        terminal = bool(isinstance(detail, dict) and detail.get('terminal')
                        and self._pending_matches_order(p, detail))
        detail_filled = (int(float(detail.get('filled') or 0))
                         if isinstance(detail, dict) else 0)
        seen = int(p.get('filled_seen') or 0)
        # A same-side holdings change may already have been recorded UNKNOWN
        # while execution detail lagged.  Let delayed reconciliation inspect
        # even a zero-fill CANCELED/REJECTED detail before the pending identity
        # is released; that exact exclusion proof turns the UNKNOWN row EXT.
        has_unresolved_event = p.get('unresolved_event_index') is not None
        if (terminal and unchanged and detail_filled <= seen
                and not has_unresolved_event):
            self._log(f"pending {p['side']} terminal state confirmed: "
                      f"{p.get('terminal_status') or 'CLOSED'}")
            self._pending = None
            self.dirty = True
            return

        # OPEN-list absence is not terminal evidence.  Once an accepted intent
        # becomes stale, retain its stable identity and pause reconciliation
        # until exact order detail proves FILLED/CANCELED/REJECTED.  This is the
        # central no-duplicate invariant.
        if (p.get('accepted') and not resting and not terminal
                and time.time() - float(p.get('ts') or 0) >= _PENDING_STALE_S):
            p['unresolved'] = True
            self.campaign_state = 'PAUSED_RECONCILE'
            self.status = ('accepted order is absent from OPEN but not proven '
                           'terminal — no replacement will be sent')
            self.dirty = True
            return
        if (not p.get('accepted') and unchanged
                and time.time() - float(p.get('ts') or 0) >= _PENDING_STALE_S):
            self._log(f"unsubmitted {p['side']} intent expired")
            self._pending = None
            self.dirty = True

    @staticmethod
    def _fill_qty(fill):
        if not isinstance(fill, dict):
            return 0
        for key in ('qty', 'quantity', 'filled_qty', 'actual_fill_qty'):
            try:
                qty = int(round(float(fill.get(key) or 0)))
            except (TypeError, ValueError):
                continue
            if qty > 0:
                return qty
        return 0

    @staticmethod
    def _fill_price(fill):
        if not isinstance(fill, dict):
            return None
        for key in ('price', 'actual_fill_price'):
            try:
                price = float(fill.get(key))
            except (TypeError, ValueError):
                continue
            if price > 0:
                return price
        return None

    @staticmethod
    def _fill_stamp(fill):
        return fill.get('filled_at') or fill.get('fill_time') or ''

    @classmethod
    def fill_evidence_key(cls, fill):
        """Stable JSON-safe identity for controller-side evidence de-duplication."""
        if not isinstance(fill, dict):
            return None
        oid = cls._fill_order_id(fill)
        coid = fill.get('client_order_id')
        side = str(fill.get('side') or '').upper()
        qty = cls._fill_qty(fill)
        stamp = str(cls._fill_stamp(fill) or '')
        if not (oid or coid or stamp):
            return None
        return '|'.join(str(v or '') for v in (oid, coid, side, qty, stamp))

    def _consume_fill(self, fill):
        key = self.fill_evidence_key(fill)
        if key:
            self.consumed_fill_keys.add(key)

    def _fill_date(self, fill):
        stamp = self._fill_stamp(fill)
        if stamp is None:
            return None
        text = str(stamp).strip()
        market_tz = ZoneInfo('Asia/Seoul' if self.ticker.endswith('.KS')
                             else 'America/New_York')
        if len(text) >= 10 and text[4:5] == '-' and text[7:8] == '-':
            try:
                parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
                if parsed.tzinfo is not None:
                    parsed = parsed.astimezone(market_tz)
                return parsed.strftime('%Y-%m-%d')
            except ValueError:
                return text[:10]
        try:
            epoch = float(stamp)
            if epoch > 10_000_000_000:   # milliseconds
                epoch /= 1000.0
            return datetime.fromtimestamp(epoch, market_tz).strftime('%Y-%m-%d')
        except (TypeError, ValueError, OSError, OverflowError):
            return None

    @staticmethod
    def _fill_order_id(fill):
        return (fill.get('order_id') if fill.get('order_id') is not None
                else fill.get('id'))

    def _fill_matches_pending(self, fill, pending):
        if not fill or not pending:
            return False
        oid = self._fill_order_id(fill)
        if pending.get('order_id') is not None and oid is not None:
            return str(pending['order_id']) == str(oid)
        coid = fill.get('client_order_id')
        if pending.get('client_order_id') is not None and coid is not None:
            return str(pending['client_order_id']) == str(coid)
        return bool(fill.get('mine')) and self._same_price(
            self._fill_price(fill), pending.get('price'))

    def _recent_fill(self, snap, side, qty=0, pending=None,
                     foreign_only=False):
        """Best normalized completed-fill evidence for the observed delta.

        Stable ids outrank all fallbacks. Otherwise prefer the matching
        quantity and the newest timestamp. Missing price remains missing: this
        function never substitutes the live quote or average cost.
        """
        side = str(side or '').upper()
        candidates = []
        for index, fill in enumerate(snap.get('recent_fills') or []):
            if not isinstance(fill, dict):
                continue
            if str(fill.get('side') or '').upper() != side:
                continue
            if foreign_only and fill.get('mine') is not False:
                continue
            fq = self._fill_qty(fill)
            id_match = self._fill_matches_pending(fill, pending)
            exact_qty = bool(qty and fq == int(qty))
            stamp = str(self._fill_stamp(fill) or '')
            candidates.append(((2 if id_match else 0,
                                1 if exact_qty else 0, stamp, index), fill))
        return max(candidates, key=lambda item: item[0])[1] \
            if candidates else None

    def _pending_detail_evidence(self, snap, pending=None):
        """Convert exact cumulative order detail into fill evidence.

        CLOSED-history rows may be de-duplicated after the first scan, while
        ``pending_order`` remains authoritative on every poll.  Keeping this
        independent view lets a holdings snapshot that lags cumulative broker
        execution catch up later without losing ownership or the actual fill
        price.
        """
        p = pending or self._pending
        detail = snap.get('pending_order')
        if (not isinstance(detail, dict)
                or not self._pending_matches_order(p, detail)):
            return None
        try:
            filled = max(0, int(round(float(detail.get('filled') or 0))))
        except (TypeError, ValueError):
            filled = 0
        if filled <= 0:
            return None
        return {
            'side': str(detail.get('side') or p.get('side') or '').upper(),
            'qty': filled,
            'price': detail.get('actual_fill_price'),
            'actual_fill_price': detail.get('actual_fill_price'),
            'filled_at': detail.get('filled_at'),
            'fill_time': detail.get('filled_at'),
            'order_id': self._order_id(detail),
            'id': self._order_id(detail),
            'client_order_id': detail.get('client_order_id'),
            'mine': True,
            'status': detail.get('status'),
            'terminal': bool(detail.get('terminal')),
        }

    def _pending_fill_evidence(self, snap, pending, side, qty):
        """Prefer the strongest exact cumulative evidence for ``pending``."""
        recent = self._recent_fill(snap, side, qty, pending)
        detail = self._pending_detail_evidence(snap, pending)
        if detail is None:
            return recent
        if recent is None or not self._fill_matches_pending(recent, pending):
            return detail
        if self._fill_qty(detail) < self._fill_qty(recent):
            return recent
        # Preserve actual price/time from CLOSED history when a provider's
        # exact-detail endpoint omits either field.
        merged = dict(detail)
        if not self._fill_price(merged):
            merged['price'] = self._fill_price(recent)
            merged['actual_fill_price'] = self._fill_price(recent)
        if not self._fill_stamp(merged):
            merged['filled_at'] = self._fill_stamp(recent)
            merged['fill_time'] = self._fill_stamp(recent)
        return merged

    def _pending_for_fill(self, snap, side, qty):
        p = self._pending
        evidence = self._pending_fill_evidence(snap, p, side, qty)
        if not p or not p.get('accepted') or p.get('side') != side:
            return None, evidence, 0
        if (evidence and evidence.get('mine') is False
                and not self._fill_matches_pending(evidence, p)):
            return None, evidence, 0
        old_filled = int(p.get('filled_seen') or 0)
        order = self._matching_pending_order(snap, p)
        open_total = (int(float(order.get('filled') or 0))
                      if order is not None else 0)
        open_progress = max(0, open_total - old_filled)
        fill_qty = self._fill_qty(evidence)
        expected_remainder = max(0, int(p.get('qty') or 0) - old_filled)
        evidence_progress = 0
        if evidence is not None and self._fill_matches_pending(evidence, p):
            if fill_qty > old_filled:
                # Toss order detail reports cumulative filled quantity.
                evidence_progress = fill_qty - old_filled
            elif (order is None and int(qty) == expected_remainder
                  and fill_qty >= int(qty)):
                # Defensive support for a feed that reports the final
                # execution fragment instead of the cumulative order total.
                evidence_progress = fill_qty
        # Execution detail is cumulative and can lead the holdings endpoint.
        # Attribute at most the position movement actually visible now; the
        # unobserved cumulative remainder stays pending for a later snapshot.
        proven = min(int(qty), expected_remainder,
                     max(open_progress, evidence_progress))
        if proven > 0:
            return p, evidence, proven
        # A same-side manual trade can happen while our order merely rests.
        # Intent alone is not attribution proof; prefer explicit foreign fill
        # evidence, otherwise leave the actual price/source unverified.
        return (None,
                self._recent_fill(snap, side, qty, foreign_only=True), 0)

    def _advance_pending_fill(self, snap, pending, qty):
        """Keep a pending order through partial fills.

        Returns True only when its full requested quantity filled. A partially
        filled order whose remainder was cancelled is resolved and forgotten,
        but its exit tier is not spent.  ``filled_seen`` is deliberately the
        quantity reflected in holdings, not the possibly-ahead cumulative
        quantity reported by order detail.
        """
        if not pending:
            return False
        requested = int(pending.get('qty') or 0)
        pending['filled_seen'] = min(
            requested,
            int(pending.get('filled_seen') or 0) + int(qty))
        order = self._matching_pending_order(snap, pending)
        detail = snap.get('pending_order')
        detail_matches = (isinstance(detail, dict)
                          and self._pending_matches_order(pending, detail))
        detail_terminal = bool(detail_matches and detail.get('terminal'))
        detail_filled = (min(requested,
                             int(float(detail.get('filled') or 0)))
                         if detail_matches else 0)
        if (order is None and detail_matches and not detail_terminal
                and int(float(detail.get('qty_open') or 0)) > 0):
            # OPEN can lag while exact detail still proves that a partially
            # filled remainder is working. Keep it pending; OPEN absence is
            # never permission to create a replacement.
            order = detail
        fully_filled = pending['filled_seen'] >= requested
        detail_ahead = detail_filled > pending['filled_seen']
        if detail_ahead:
            # The order endpoint has proved more execution than holdings has
            # exposed.  Preserve the identity and pause instead of inventing a
            # negative external quantity or releasing a replacement order.
            pending['unresolved'] = True
            self.campaign_state = 'PAUSED_RECONCILE'
            self.status = ('order execution is ahead of broker holdings — '
                           'waiting for the position snapshot to catch up')
            resolved = False
        elif fully_filled:
            resolved = True
        elif detail_terminal:
            # A terminal partial fill has now been fully reflected; its
            # cancelled/rejected remainder is not a completed tier.
            resolved = True
        elif order is not None and int(order.get('qty_open') or 0) > 0:
            resolved = False
        elif pending.get('accepted'):
            # OPEN absence without exact terminal evidence is ambiguous.
            pending['unresolved'] = True
            resolved = False
        else:
            resolved = True
        if resolved and self._pending is pending:
            self._pending = None
        self.dirty = True
        return fully_filled

    def _legitimate_reload(self, snap):
        return (self.campaign_id is None
                and self.campaign_state == 'RELOAD_ARMED'
                and self.vantage_src == 'reload'
                and self.last_exit_date == snap.get('trading_date')
                and self._positive_number(self.last_exit_price)
                and self._positive_number(self.vantage))

    def _clear_stale_campaign(self):
        """Reset stale holdings history without fabricating a closing trade."""
        self.campaign_id = None
        self.campaign_state = 'FLAT'
        self.campaign_start = None
        self.campaign_vantage = None
        self.load_price = None
        self.chase_count = 0
        self.max_qty = 0
        self.max_cost = 0.0
        self.campaign_low = None
        self.manually_modified = False
        self.tier_done = [False, False, False]
        self.events = []
        self._pending = None
        self.vantage = None
        self.vantage_src = 'high5'
        self.vantage_manual = None
        self.vantage_manual_label = ''
        self.last_exit_date = None
        self.last_exit_price = None
        self.dirty = True

    def _first_snapshot(self, snap, shares, avg):
        """Reconcile persisted state to broker truth without inventing fills."""
        saved_q, saved_avg = self._restored_q, self._restored_avg
        if shares > 0:
            if not self.campaign_id:
                self._adopt(shares, avg)
                return
            qty_changed = saved_q is None or int(saved_q) != shares
            avg_tol = 0.5 if self.ticker.endswith('.KS') else 0.005
            avg_changed = (saved_avg <= 0 < avg or
                           (saved_avg > 0 and abs(saved_avg - avg) > avg_tol))
            self._prev_shares, self._prev_avg = shares, avg
            self.max_qty = max(self.max_qty, shares)
            self.max_cost = max(self.max_cost, shares * avg)
            if qty_changed or avg_changed:
                self.manually_modified = True
                self.tier_done = [False, False, False]
                self._log('RECONCILE_POSITION: saved '
                          f'{saved_q if saved_q is not None else "?"} @ '
                          f'{self._fp(saved_avg)} → broker {shares} @ '
                          f'{self._fp(avg)}; exit tiers re-armed')
                p = self._pending
                if p and saved_q is not None:
                    # Quantity direction alone is not ownership proof.  Keep a
                    # restored accepted intent until OPEN or exact detail
                    # resolves it; do not silently consume it as the offline
                    # change or forget it because one OPEN page omitted it.
                    if not self._matching_pending_order(snap, p):
                        p['unresolved'] = True
                        self.campaign_state = 'PAUSED_RECONCILE'
                self.dirty = True
            return

        # A completed same-day reload already proven and persisted is valid.
        if self._legitimate_reload(snap):
            self._prev_shares, self._prev_avg = 0, 0.0
            return

        # A broker-accepted LOAD can legitimately survive a restart while the
        # holding is still zero. Persistence exists specifically so this order
        # remains attributable and is not replaced by a duplicate.
        if (self.campaign_id is None and self._pending
                and self._pending.get('accepted')
                and int(saved_q or 0) == 0):
            self._prev_shares, self._prev_avg = 0, 0.0
            return

        # If the bot was offline over an actual full sell, only today's broker
        # fill evidence can recover the reload. Otherwise discard the stale
        # campaign with no synthetic EXIT row.
        active = bool(self.campaign_id or self.campaign_state in (
            'DEPLOYED', 'CHASE_PENDING', 'EXIT_PENDING',
            'CHASE_CANCELLING', 'EXIT_CANCELLING', 'ORDER_CANCELLING',
            'PAUSED_RECONCILE'))
        fill = self._recent_fill(snap, 'SELL', saved_q or 0, self._pending)
        fill_today = fill and self._fill_date(fill) == snap.get('trading_date')
        fill_covers_position = fill and self._fill_qty(fill) >= int(saved_q or 0)
        if active and saved_q and fill_today and fill_covers_position:
            self._prev_shares = int(saved_q)
            self._prev_avg = float(saved_avg or 0.0)
            self._on_sell_fill(snap, int(saved_q), 0, 0.0)
            self._prev_shares, self._prev_avg = 0, 0.0
            return
        if active:
            self._log('RECONCILE_FLAT: broker holds zero; stale campaign '
                      'discarded without inventing an exit')
            self._clear_stale_campaign()
        elif self.campaign_state not in ('FLAT', 'RELOAD_ARMED'):
            self.campaign_state = 'FLAT'
            self._pending = None
            self.dirty = True
        self._prev_shares, self._prev_avg = 0, 0.0

    def _replay_correlated_fills(self, snap, start_shares, start_avg,
                                 final_shares):
        """Replay broker executions when their gross net proves the position.

        Holdings remain authoritative.  The replay is used only when every
        time-correlated execution in the interval nets *exactly* from the last
        accepted holding to the new one.  This lets a BUY+SELL round trip (net
        zero), or opposing bot/app executions, enter the campaign log without
        guessing from an incomplete history page.
        """
        pending_evidence = None
        if self._pending:
            # Exact detail remains available even after the controller has
            # de-duplicated its CLOSED-history twin.  Collapse any pending-
            # order rows to the strongest cumulative view so restart/catch-up
            # replay cannot lose the remaining bot fragment.
            pending_evidence = self._pending_fill_evidence(
                snap, self._pending, self._pending.get('side'),
                self._pending.get('qty') or 0)
            if (pending_evidence is not None
                    and not self._fill_matches_pending(
                        pending_evidence, self._pending)):
                pending_evidence = None
        if not snap.get('fills_correlated') and pending_evidence is None:
            return False
        indexed = [(i, f) for i, f in enumerate(snap.get('recent_fills') or [])
                   if snap.get('fills_correlated') and isinstance(f, dict)
                   and self._fill_qty(f) > 0]
        if self._pending:
            if pending_evidence is not None:
                pending_indexes = [
                    i for i, fill in indexed
                    if self._fill_matches_pending(fill, self._pending)
                ]
                indexed = [
                    (i, fill) for i, fill in indexed
                    if not self._fill_matches_pending(fill, self._pending)
                ]
                indexed.append((min(pending_indexes) if pending_indexes else
                                len(snap.get('recent_fills') or []),
                                pending_evidence))
        if not indexed:
            return False
        indexed.sort(key=lambda pair: (str(self._fill_stamp(pair[1]) or ''),
                                       pair[0]))

        virtual_seen = int((self._pending or {}).get('filled_seen') or 0)
        steps = []
        for _index, fill in indexed:
            side = str(fill.get('side') or '').upper()
            if side not in ('BUY', 'SELL'):
                continue
            qty = self._fill_qty(fill)
            if self._pending and self._fill_matches_pending(fill, self._pending):
                # Exact order detail is cumulative.  Convert it to the new
                # fragment so a prior partial fill cannot be counted twice.
                qty = max(0, qty - virtual_seen)
                virtual_seen += qty
            if qty > 0:
                steps.append((side, qty, fill))
        if not steps:
            return False

        net = sum(qty if side == 'BUY' else -qty
                  for side, qty, _fill in steps)
        if int(start_shares) + int(net) != int(final_shares):
            return False

        # Validate the chronological path before mutating campaign state.
        check = int(start_shares)
        for side, qty, _fill in steps:
            check += qty if side == 'BUY' else -qty
            if check < 0:
                return False

        cur_shares, cur_avg = int(start_shares), float(start_avg or 0.0)
        if cur_shares > 0 and not self.campaign_id:
            self._adopt(cur_shares, cur_avg)
        self._prev_shares, self._prev_avg = cur_shares, cur_avg
        final_avg = float(snap.get('avg_cost') or 0.0)
        for side, qty, fill in steps:
            price = self._fill_price(fill)
            next_shares = cur_shares + (qty if side == 'BUY' else -qty)
            if side == 'BUY':
                if price and next_shares > 0:
                    next_avg = ((cur_avg * cur_shares + price * qty)
                                / next_shares)
                elif next_shares == final_shares and final_avg > 0:
                    next_avg = final_avg
                else:
                    next_avg = cur_avg
            else:
                next_avg = cur_avg if next_shares > 0 else 0.0
            step_snap = dict(snap)
            step_snap['shares'] = next_shares
            step_snap['avg_cost'] = next_avg
            step_snap['recent_fills'] = [fill]
            if side == 'BUY':
                self._on_buy_fill(step_snap, cur_shares, next_shares, next_avg)
            else:
                self._on_sell_fill(step_snap, cur_shares, next_shares, next_avg)
            cur_shares, cur_avg = next_shares, next_avg
            self._prev_shares, self._prev_avg = cur_shares, cur_avg
        return True

    def _reconcile_delayed_pending_fill(self, snap, shares):
        """Repair an UNKNOWN row when exact detail arrives after retry timeout."""
        p = self._pending
        if not p or p.get('unresolved_event_index') is None:
            return False
        try:
            index = int(p.get('unresolved_event_index'))
            event = self.events[index]
        except (TypeError, ValueError, IndexError):
            return False
        recorded = abs(int(event.get('qty') or 0))
        if recorded <= 0:
            return False

        detail = snap.get('pending_order')
        detail_matches = (isinstance(detail, dict)
                          and self._pending_matches_order(p, detail))
        terminal = bool(detail_matches and detail.get('terminal'))
        status = str(detail.get('status') or '').upper() \
            if detail_matches else ''
        try:
            detail_filled = (max(0, int(float(detail.get('filled') or 0)))
                             if detail_matches else 0)
        except (TypeError, ValueError):
            detail_filled = 0

        # Exact zero-fill terminal detail excludes the bot order as the cause
        # of this same-side holdings movement.  Repair UNKNOWN to EXT before
        # releasing the pending identity; CANCELED and REJECTED are both
        # positive non-fill evidence.
        if terminal and detail_filled == 0 and status in (
                'CANCELED', 'REJECTED'):
            event['source'] = 'EXT'
            proof = f'bot order {status.lower()} with 0 filled'
            event['note'] = ((str(event.get('note') or '') + '; ')
                             if event.get('note') else '') + proof
            self.manually_modified = shares > 0
            self._pending = None
            self.dirty = True
            return True

        evidence = self._pending_fill_evidence(
            snap, p, p.get('side'), p.get('qty') or 0)
        if not evidence or not self._fill_matches_pending(evidence, p):
            return False
        total = self._fill_qty(evidence)
        old = int(p.get('filled_seen') or 0)
        available = max(0, min(int(p.get('qty') or 0), total) - old)
        # As in the immediate path, cumulative order detail may be ahead of
        # holdings.  It can explain no more than the UNKNOWN movement already
        # recorded; the rest remains pending until holdings catches up.
        progress = min(recorded, available)
        if progress <= 0:
            return False

        source = 'BOT' if progress == recorded else 'MIXED'
        event['source'] = source
        external = recorded - progress
        if source == 'MIXED':
            event['note'] = (str(event.get('note') or '') + '; '
                             if event.get('note') else '') + (
                                 f'bot {progress}, external/net {external}')
            self.manually_modified = shares > 0
        actual = self._fill_price(evidence)
        if actual and source == 'BOT':
            event['price'] = actual

        p['filled_seen'] = min(
            int(p.get('qty') or 0), old + progress)
        complete = p['filled_seen'] >= int(p.get('qty') or 0)
        if p.get('side') == 'SELL' and shares == 0 and actual and source == 'BOT':
            # The earlier UNKNOWN full exit deliberately refused to guess a
            # reload. Exact delayed execution evidence can now restore it.
            self.last_exit_price = actual
            self.vantage = actual
            self.vantage_src = 'reload'
            self.campaign_state = 'RELOAD_ARMED'
        if complete:
            for tier in p.get('tiers') or []:
                if 0 <= tier < 3:
                    self.tier_done[tier] = True
        cumulative_seen = min(int(p.get('qty') or 0), total)
        detail_ahead = cumulative_seen > p['filled_seen']
        p['unresolved_event_index'] = None
        p['unresolved_event_qty'] = 0
        if complete or (terminal and not detail_ahead):
            self._pending = None
        elif detail_ahead:
            p['unresolved'] = True
            self.campaign_state = 'PAUSED_RECONCILE'
            self.status = ('order execution is ahead of broker holdings — '
                           'waiting for the position snapshot to catch up')
        else:
            p['unresolved'] = False
        self._consume_fill(evidence)
        self.dirty = True
        return True

    def _detect_fills(self, snap, shares):
        prev = self._prev_shares
        avg = float(snap.get('avg_cost') or 0)
        if prev is None:
            restored = self._restored_q
            if (restored is not None and self._replay_correlated_fills(
                    snap, int(restored), self._restored_avg, shares)):
                return
            # Exact stable-id evidence can be cumulatively ahead on the first
            # snapshot after restart.  Even when the full cumulative quantity
            # does not yet net to holdings (so gross replay declines), adopt
            # only the visible delta through the normal capped fill path.
            if restored is not None and int(restored) != int(shares):
                p = self._pending
                side = 'BUY' if int(shares) > int(restored) else 'SELL'
                evidence = self._pending_fill_evidence(
                    snap, p, side, abs(int(shares) - int(restored)))
                if (p and p.get('accepted') and p.get('side') == side
                        and evidence is not None
                        and self._fill_matches_pending(evidence, p)):
                    total = min(int(p.get('qty') or 0),
                                self._fill_qty(evidence))
                    if total > int(p.get('filled_seen') or 0):
                        self._prev_shares = int(restored)
                        self._prev_avg = float(self._restored_avg or 0.0)
                        if side == 'BUY':
                            self._on_buy_fill(
                                snap, int(restored), shares, avg)
                        else:
                            self._on_sell_fill(
                                snap, int(restored), shares, avg)
                        return
            self._first_snapshot(snap, shares, avg)
            return
        if shares == prev and self._reconcile_delayed_pending_fill(snap, shares):
            return
        if self._replay_correlated_fills(
                snap, int(prev), self._prev_avg, shares):
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
        pend, evidence, bot_qty = self._pending_for_fill(
            snap, 'BUY', qty)
        ambiguous_pending = (not pend and not evidence and self._pending
                             and self._pending.get('accepted')
                             and self._pending.get('side') == 'BUY')
        mixed = bool(pend and bot_qty != qty)
        if pend:
            price = self._fill_price(evidence) or pend['price']
            source = 'MIXED' if mixed else 'BOT'
            if mixed:
                price = ((avg * shares - self._prev_avg * prev) / qty
                         if avg > 0 and self._prev_avg > 0 and prev > 0
                         else (avg or None))
                self.manually_modified = True
        else:
            price = self._fill_price(evidence)
            if not price:
                price = ((avg * shares - self._prev_avg * prev) / qty
                         if avg > 0 and self._prev_avg > 0 and prev > 0
                         else (avg or None))
            source = ('BOT' if evidence and evidence.get('mine') else
                      ('UNKNOWN' if ambiguous_pending else 'EXT'))
        self._consume_fill(evidence)
        order_kind = str((pend or {}).get('kind') or '').upper()
        if pend and order_kind == 'CHASE' and not pend.get('step_counted'):
            self.chase_count += 1
            pend['step_counted'] = True
        self._advance_pending_fill(snap, pend, bot_qty)
        if prev == 0:
            self._open_campaign(price)
            if mixed:
                self.manually_modified = True
            kind = (order_kind if order_kind in ('LOAD', 'RELOAD') else
                    ('RELOAD' if self.vantage_src == 'reload' else 'LOAD'))
            mixed_note = (f'bot {bot_qty}, external/net {qty - bot_qty}'
                          if mixed else '')
            self._event(kind, qty, price, shares, avg, source,
                        note=mixed_note)
            self._log(f'{kind} filled ({source}): 0 → {shares} shares '
                      f'— campaign {self.campaign_id} G{self.gear} '
                      f'{self._tier_text()}')
            self.vantage_src = 'high5' if not self.vantage_manual else 'manual'
        else:
            # More shares to sell: every armed tier re-arms on the new holding.
            self.tier_done = [False, False, False]
            kind = order_kind if order_kind in ('LOAD', 'RELOAD') else 'CHASE'
            if kind == 'CHASE' and not pend:
                self.chase_count += 1
            note = (f'#{self.chase_count}' if kind == 'CHASE'
                    else 'order remainder')
            if mixed:
                note += f'; bot {bot_qty}, external/net {qty - bot_qty}'
            self._event(kind, qty, price, shares, avg, source, note=note)
            self._log(f'{kind} filled ({source}): {prev} → {shares} shares '
                      + (f'(chase #{self.chase_count}) — '
                         if kind == 'CHASE' else '— ')
                      + 'exit ladder re-armed')
        if price:
            self.campaign_low = (price if self.campaign_low is None
                                 else min(self.campaign_low, price))
        if source == 'UNKNOWN' and self._pending and self._pending.get(
                'unresolved'):
            self._pending['unresolved_event_index'] = len(self.events) - 1
            self._pending['unresolved_event_qty'] = qty
        self.max_qty = max(self.max_qty, shares)
        self.max_cost = max(self.max_cost, shares * avg)
        self.dirty = True

    def _on_sell_fill(self, snap, prev, shares, avg):
        qty = prev - shares
        pend, evidence, bot_qty = self._pending_for_fill(
            snap, 'SELL', qty)
        ambiguous_pending = (not pend and not evidence and self._pending
                             and self._pending.get('accepted')
                             and self._pending.get('side') == 'SELL')
        mixed = bool(pend and bot_qty != qty)
        actual_price = self._fill_price(evidence)
        if pend:
            # A partial row may use the known limit when execution detail has
            # not arrived yet. A full exit/reload anchor may not: a LIMIT can
            # improve, so only the broker's completed-fill price is actual.
            price = (None if mixed else
                     (actual_price or (pend['price'] if shares > 0 else None)))
            source = 'MIXED' if mixed else 'BOT'
        else:
            price = actual_price
            source = ('BOT' if evidence and evidence.get('mine') else
                      ('UNKNOWN' if ambiguous_pending else 'EXT'))
        self._consume_fill(evidence)
        complete = self._advance_pending_fill(snap, pend, bot_qty)
        if pend and complete:
            for i in (pend.get('tiers') or []):
                if 0 <= i < 3:
                    self.tier_done[i] = True

        gain = ((price - self._prev_avg) * qty
                if price and self._prev_avg else None)
        note = self._fp(gain) + ' gross' if gain is not None else ''

        if shares > 0:
            # A partial exit is normal when several tiers are armed; a hand
            # trim is not, and is flagged so the log stays honest.
            if source in ('EXT', 'MIXED', 'UNKNOWN'):
                self.manually_modified = True
            kind = ('T' + '/'.join(str(i + 1) for i in pend['tiers'])
                    if pend and pend.get('tiers') else 'SELL')
            if mixed:
                note = (note + '; ' if note else '') + (
                    f'bot {bot_qty}, external/net {qty - bot_qty}')
            self._event(kind, -qty, price, shares, avg, source, note=note)
            if source == 'UNKNOWN' and self._pending and self._pending.get(
                    'unresolved'):
                self._pending['unresolved_event_index'] = len(self.events) - 1
                self._pending['unresolved_event_qty'] = -qty
            self._log(f'{kind} filled ({source}): {prev} → {shares} shares '
                      f'— {self._tier_text()} armed, campaign still open')
            self.dirty = True
            return

        if (self._pending and self._pending.get('side') == 'SELL'
                and not (source == 'UNKNOWN'
                         and self._pending.get('unresolved'))):
            self._pending = None
        if mixed:
            note = (note + '; ' if note else '') + (
                f'bot {bot_qty}, external/net {qty - bot_qty}')
        self._event('EXIT', -qty, price, shares, avg, source, note=note)
        if source == 'UNKNOWN' and self._pending and self._pending.get(
                'unresolved'):
            self._pending['unresolved_event_index'] = len(self.events) - 1
            self._pending['unresolved_event_qty'] = -qty
        at = f' @ {self._fp(price)}' if price else ' (fill price unavailable)'
        self._log(f'EXIT ({source}): campaign {self.campaign_id} closed — '
                  f'-{qty}{at}')
        self.campaign_state = 'COMPLETED'
        self.campaign_id = None
        self.manually_modified = False
        self.tier_done = [False, False, False]
        self.last_exit_date = self.trading_date
        self.last_exit_price = price
        self.vantage_manual = None
        self.vantage_manual_label = ''
        if price:
            # Rest of this session: one fast reload at the actual sell fill -3%.
            self.vantage = price
            self.vantage_src = 'reload'
            self.campaign_state = 'RELOAD_ARMED'
            self._log(f'RELOAD_ARMED: {self._fp(calc_reload_price(price))} '
                      f'(sell fill -{RELOAD_DROP_PCT}%) — this session only; '
                      f'tomorrow returns to the Dynamic High5 LOAD')
        else:
            # A live quote or prior average is not execution evidence. Keep the
            # audit row honest and use the normal Dynamic High5 LOAD instead of
            # inventing a sell anchor and a guessed -3% reload.
            self.vantage = None
            self.vantage_src = 'high5'
            self.campaign_state = 'FLAT'
            self._log('full sell detected but its fill price is unavailable — '
                      'Dynamic High5 restored; no reload was guessed')
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

    def _foreign_order_pause(self, snap, tag):
        """Yield the whole ticker to an order that is not owned by this bot.

        Manual trading and automation may coexist, but they must never race.
        A foreign order therefore pauses both sides. Stable bot-owned ids are
        cancelled so manual control gets a clean lane; foreign ids are never
        touched and campaign history is left intact.
        """
        orders = [o for o in (snap.get('orders') or [])
                  if float(o.get('qty_open') or 0) > 0]
        foreign = [o for o in orders if not self._order_owned(o)]
        if not foreign:
            return None
        acts = []
        for order in orders:
            if not self._order_owned(order):
                continue
            acts.append(('cancel', self._order_id(order),
                         'yield to manual order'))
            self._mark_cancelling(order)
        p = self._pending
        if (p and p.get('accepted') and p.get('order_id') is not None
                and not any(self._pending_matches_order(p, o)
                            for o in orders) and not p.get('cancelling')
                and not p.get('terminal_status')):
            acts.append(('cancel', p['order_id'], 'yield to manual order'))
            self._mark_cancelling()
        self.campaign_state = 'PAUSED_MANUAL_ORDER'
        sides = '/'.join(sorted({str(o.get('side') or '').upper()
                                 for o in foreign}))
        tail = '; bot order cancelling first' if acts else ''
        self.status = (f'[{tag}] manual/foreign {sides} order resting — '
                       f'autopilot paused for this ticker{tail}')
        return acts

    def _watch_order_cleanup(self, snap, tag):
        """WATCH owns no live orders: cancel ours, preserve everyone else's."""
        if snap.get('can_trade', True):
            return None
        orders = [o for o in (snap.get('orders') or [])
                  if float(o.get('qty_open') or 0) > 0 and self._order_owned(o)]
        acts = []
        for order in orders:
            acts.append(('cancel', self._order_id(order),
                         'WATCH mode owns no resting orders'))
            self._mark_cancelling(order)
        p = self._pending
        if (p and p.get('accepted') and p.get('order_id') is not None
                and not any(self._pending_matches_order(p, o)
                            for o in orders) and not p.get('cancelling')
                and not p.get('terminal_status')):
            acts.append(('cancel', p['order_id'],
                         'WATCH mode owns no resting orders'))
            self._mark_cancelling()
        if acts or (p and p.get('cancelling')):
            self.campaign_state = 'WATCH_CANCELLING'
            self.status = (f'[{tag}] WATCH mode — cancelling bot-owned order; '
                           'no replacement will be sent')
            return acts
        return None

    def _set_order_wait(self, tag, fallback, noun='order'):
        self.campaign_state = self._pending_state(fallback)
        kind = str((self._pending or {}).get('kind') or noun).lower()
        suffix = ' cancellation pending' if (self._pending or {}).get(
            'cancelling') else ' resting — waiting for the fill'
        self.status = f'[{tag}] {kind}{suffix}'

    def _cancel_owned(self, acts, orders, label):
        """Request cancellations and mark matching pending state."""
        for order in orders:
            if not self._order_owned(order):
                continue
            acts.append(('cancel', self._order_id(order), label))
            self._mark_cancelling(order)
        return bool(acts)

    # ── Main decision cycle ───────────────────────────────────────────────────

    def poll(self, snap: dict) -> list:
        self.consumed_fill_keys = set()
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
        guarded = bool(self._pending and self._pending.get('unresolved'))
        decision_snap = snap
        if guarded and snap.get('can_trade', True):
            decision_snap = dict(snap)
            decision_snap['can_trade'] = False
        acts = (self._poll_deployed(decision_snap, shares) if shares > 0
                else self._poll_flat(decision_snap))
        if self._pending and self._pending.get('unresolved'):
            self.campaign_state = 'PAUSED_RECONCILE'
            self.status = ('broker order outcome unresolved — exact terminal '
                           'confirmation required; no replacement will be sent')
        return acts

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
        reserve_known = bp is not None
        affordable = reserve_known and need <= bp
        if not reserve_known:
            self.buy_state = 'UNAVAILABLE'
            self._exhaust_logged = False
        elif not affordable:
            self._exhaust(f'next chase needs {chase_q} @ {self._fp(chase_p)}')
        else:
            self._buy_ok()

        buys, sells = self._split_orders(snap)
        tag = f'G{self.gear} {self._tier_text()}'
        foreign = self._foreign_order_pause(snap, tag)
        if foreign is not None:
            return foreign
        watch_cleanup = self._watch_order_cleanup(snap, tag)
        if watch_cleanup is not None:
            return watch_cleanup
        price = snap.get('price')
        if price is None:
            self.status = 'no price — watching paused'
            return acts

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
            order_state = self._restale(acts, sells, line, qty, 'exit')
            if order_state == 'cancelling':
                self.campaign_state = 'EXIT_CANCELLING'
                self.status = (f'[{tag}] stale exit cancelling — replacement '
                               'waits for the next broker snapshot')
                return acts
            if order_state == 'resting':
                self._set_order_wait(tag, 'EXIT_PENDING', 'exit')
                return acts
            if not snap.get('can_trade', True):
                self.status = (f'[{tag}] exit line crossed @ '
                               f'{self._fp(price)} — WATCH mode, not sent')
                return acts
            if self._cancel_owned(acts, buys, 'our chase (exit first)'):
                self.campaign_state = 'EXIT_CANCELLING'
                self.status = (f'[{tag}] exit crossed — cancelling the bot '
                               'buy first; sell waits for the next snapshot')
                return acts
            self._place(acts, 'SELL', line, qty, label, kind='EXIT',
                        tiers=tiers)
            self.campaign_state = 'EXIT_PENDING'
            self.status = f'[{tag}] {label} fired: {qty} @ {self._fp(line)}'
            return acts

        # 2) CHASE — while the army can fund it.
        if chase_q > 0 and price <= chase_p:
            self.crossed['BUY'] = {'price': chase_p, 'qty': chase_q}
            order_state = self._restale(acts, buys, chase_p, chase_q, 'chase')
            if order_state == 'cancelling':
                self.campaign_state = 'CHASE_CANCELLING'
                self.status = (f'[{tag}] stale chase cancelling — replacement '
                               'waits for the next broker snapshot')
                return acts
            if order_state == 'resting':
                self._set_order_wait(tag, 'CHASE_PENDING', 'chase')
                return acts
            if not reserve_known:
                self.status = (f'[{tag}] CHASE crossed but reserve data is '
                               'unavailable — watching the exits only')
                return acts
            if not affordable:
                self.status = (f'[{tag}] CHASE crossed but no reserve army '
                               f'remains — watching the exits only')
                return acts
            if not snap.get('can_trade', True):
                self.status = (f'[{tag}] CHASE line crossed @ '
                               f'{self._fp(price)} — WATCH mode, not sent')
                return acts
            if self._cancel_owned(acts, sells, 'our exit (chase first)'):
                self.campaign_state = 'CHASE_CANCELLING'
                self.status = (f'[{tag}] chase crossed — cancelling the bot '
                               'sell first; buy waits for the next snapshot')
                return acts
            self._place(acts, 'BUY', chase_p, chase_q,
                        f'CHASE -{chase_drop(self.gear)}% ×{g["frac"]} '
                        f'(G{self.gear})', kind='CHASE')
            self.campaign_state = 'CHASE_PENDING'
            self.status = f'[{tag}] CHASE fired: {chase_q} @ {self._fp(chase_p)}'
            return acts

        # 3) Nothing crossed — retire any order of ours still sitting out here.
        buy_state = self._restale(acts, buys, chase_p, chase_q, 'chase')
        sell_state = 'clear'
        if exits:
            sell_p, sell_q = exits[0]['price'], exits[0]['qty']
            pending = self._pending
            if pending and pending.get('side') == 'SELL':
                wanted = [e for e in exits
                          if e.get('tier') in (pending.get('tiers') or [])]
                if wanted:
                    sell_p = max(e['price'] for e in wanted)
                    sell_q = sum(e['qty'] for e in wanted)
            sell_state = self._restale(acts, sells, sell_p, sell_q, 'exit')
        if 'cancelling' in (buy_state, sell_state):
            self.campaign_state = 'ORDER_CANCELLING'
            self.status = (f'[{tag}] stale bot order cancelling — waiting for '
                           'the next broker snapshot')
            return acts
        if 'resting' in (buy_state, sell_state):
            self._set_order_wait(tag, 'DEPLOYED')
            return acts

        self.campaign_state = 'DEPLOYED'
        lo = self._fp(chase_p) if chase_q > 0 else '--'
        hi = self._fp(min(e['price'] for e in exits)) if exits else '--'
        tail = ''
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
        reserve_known = bp is not None
        affordable = reserve_known and load_p * load_q <= bp
        if not reserve_known:
            self.buy_state = 'UNAVAILABLE'
            self._exhaust_logged = False
        elif not affordable:
            self._exhaust(f'load needs {load_q} @ {self._fp(load_p)}')
        else:
            self._buy_ok()

        buys, sells = self._split_orders(snap)
        tag = f'G{self.gear}' + (' reload' if reload_mode else '')
        foreign = self._foreign_order_pause(snap, tag)
        if foreign is not None:
            return foreign
        watch_cleanup = self._watch_order_cleanup(snap, tag)
        if watch_cleanup is not None:
            return watch_cleanup
        if self._cancel_owned(acts, sells, 'stray sell (no shares)'):
            self.campaign_state = 'LOAD_CANCELLING'
            self.status = (f'[{tag}] clearing the bot sell before a new load — '
                           'waiting for the next broker snapshot')
            return acts
        pending_kind = str((self._pending or {}).get('kind') or '').upper()
        if pending_kind not in ('', 'LOAD', 'RELOAD') and buys:
            if self._cancel_owned(acts, buys, 'old campaign buy (flat)'):
                self.campaign_state = 'LOAD_CANCELLING'
                self.status = (f'[{tag}] clearing the old bot buy before a new '
                               'load — waiting for the next snapshot')
                return acts
        price = snap.get('price')
        if price is None:
            self.status = 'no price — watching paused'
            return acts

        if price <= load_p:
            self.crossed['BUY'] = {'price': load_p, 'qty': load_q}
            order_state = self._restale(acts, buys, load_p, load_q,
                                        kind.lower())
            if order_state == 'cancelling':
                self.campaign_state = 'LOAD_CANCELLING'
                self.status = (f'[{tag}] stale load cancelling — replacement '
                               'waits for the next broker snapshot')
                return acts
            if order_state == 'resting':
                self._set_order_wait(tag, 'ARMED_LOAD', 'load')
                return acts
            if not reserve_known:
                self.status = (f'[{tag}] LOAD crossed but reserve data is '
                               'unavailable')
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

        order_state = self._restale(acts, buys, load_p, load_q, kind.lower())
        if order_state == 'cancelling':
            self.campaign_state = 'LOAD_CANCELLING'
            self.status = (f'[{tag}] stale load cancelling — waiting for the '
                           'next broker snapshot')
            return acts
        if order_state == 'resting':
            self._set_order_wait(tag, 'ARMED_LOAD', 'load')
            return acts
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
            'vantage_manual_label': self.vantage_manual_label,
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
