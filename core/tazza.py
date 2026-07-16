"""TAZZA_DOUBLE engine — 타짜: 묻고 더블로 가 (pure logic).

Same WATCHER protocol as Daily443Engine: feed `poll(snap)` every ~10 s and
execute the returned actions in order. Nothing rests before a trigger.

The meta (TAZZA manual v0.1.0):

    LOAD      empty → BUY 1 unit at anchor −3% (reload after a full exit:
              final sell fill −3%; next day: prev close −3%).
    DOUBLE    price ≤ Toss avg × (1 − ladder) → buy the SAME SHARE COUNT
              again (6 held → buy 6 → 12; user simplification: value-exact
              doubling would need slightly more shares as the line is
              lower, but share-count doubling keeps the intuition and the
              avg lands a touch better than −ladder). All or nothing: if
              the full double is not affordable, 밑장 빼기 instead. A double
              while in an emergency episode is a RECOVERY_BUY → back to
              normal 3/6.
    밑장 빼기   (SKIM) sell 1/3 of the holding at market, lock the proceeds,
              rebuy the SAME qty at the fill −3%. Success → back to 3/6,
              stage 0. Rebound (upper tier fills first) → failure: lock is
              released, the lowered ladder mode stays.
    EXIT      two-tier sell from the campaign ledger, never from Toss avg:
                  X = (B + g·K − S) / ((1+r1)·q1 + (1+r2)·q2)
                  P1 = (1+r1)·X,  P2 = (1+r2)·X,  q1 = half-up(Q/2)
              after tier 1 fills: P_remaining = (B + g·K − S) / Q.
              deployed ≤ 1 unit or Q ≤ 1 → single full exit at g = r1.
    STAGES    skim #1 → stage 1, ladder mode 2/4; 5 idle trading days or a
              second unaffordable double → skim #2 → stage 2, mode 1/2;
              5 more idle days or a third unaffordable double → FINAL_OUT
              (손절): sell everything at the first valid price. 군대 회수는
              규칙이다.
    LADDER    −6/−7/−8 % auto-picked once per campaign from how chunky one
              share is against the unit:
                  share < 1.2 u → −6 %,  1.2–2 u → −7 %,  > 2 u → −8 %

Ledger vs broker (manual PART VI): the DOWN line uses the live Toss avg
cost; the UP lines use only this campaign's B (buy turnover), S (sell
turnover), K (max cash sunk = max(B−S) over time) and the real share count
Q — so skims, partial exits and rebuys are all priced back in
automatically.

`projection` carries the numbers for manual entry into the Toss app: the
next 3 double lines (sizes ×1, ×2, ×4 of the current deployment) and the
two sell tiers.

This module has NO network and NO tkinter. State survives restarts via
to_dict()/from_dict-style `saved` (the controller persists LIVE campaigns).
"""

import time
from datetime import datetime

from core.calc import trim_buy_price, trim_sell_price, round_half_up

STRATEGY = 'TAZZA_DOUBLE'

LADDER_MODES = {
    'NORMAL_3_6':  {'r1': 0.03, 'r2': 0.06, 'g': 0.045, 'label': '3/6'},
    'CAUTION_2_4': {'r1': 0.02, 'r2': 0.04, 'g': 0.030, 'label': '2/4'},
    'FINAL_1_2':   {'r1': 0.01, 'r2': 0.02, 'g': 0.015, 'label': '1/2'},
}
_STAGE_MODE = {0: 'NORMAL_3_6', 1: 'CAUTION_2_4', 2: 'FINAL_1_2'}

LOAD_DROP_PCT = 3        # LOAD / RELOAD: anchor −3%
SKIM_REBUY_DROP_PCT = 3  # rebuy the skimmed qty at the sell fill −3%
IDLE_LIMIT_DAYS = 5      # stage 1/2: idle trading days before escalating
PROJECTION_DEPTH = 3     # future double lines shown for manual entry

_PENDING_STALE_S = 90    # forget an order intent this long after placing
                         # it if nothing rests and nothing filled (DAY
                         # order died / was cancelled outside)

_SAVE_FIELDS = (
    'anchor', 'anchor_source', 'trading_date', 'campaign_active',
    'B', 'S', 'K', 'ladder_pct', 'ladder_mode', 'emergency_stage',
    'tier_progress', 'skim_pending', 'skim_rebuy_line', 'skim_rebuy_qty',
    'skim_lock_amount', 'stage_idle_days', 'final_out', 'double_count',
    'events',
)


def ladder_pct_for(share_price, unit_cash) -> float:
    """−6/−7/−8 % by single-share chunkiness vs the unit (user rule)."""
    if not share_price or not unit_cash or unit_cash <= 0:
        return 0.07
    r = share_price / unit_cash
    if r < 1.2:
        return 0.06
    if r < 2.0:
        return 0.07
    return 0.08


class TazzaEngine:
    """One battlefield. Same snap contract as Daily443Engine plus:
        'skim_locks'  float  other battlefields' 밑장 lock total (same ccy)
        'phase'       str    market phase ('REGULAR' | ...) for time-based
                             escalation (second skim / FINAL_OUT fire only
                             in the regular session)
    """

    def __init__(self, ticker, trading_date=None, log=None, saved=None):
        self.ticker = ticker
        self.currency = 'KRW' if ticker.endswith('.KS') else 'USD'
        self.state = 'ARMING'
        self.status = 'arming…'
        self.lines = {}          # 'load'|'psell'|'lower'|'rebuy'|'tier1'|'tier2'
        self.events = []
        self.projection = None   # {'buys': [(p, q)…], 'sells': [(p, q)…]}
        self.trading_date = trading_date
        self.anchor = None
        self.anchor_source = 'close'

        # Campaign ledger (PART VI) — survives partial exits and skims.
        self.campaign_active = False
        self.B = 0.0             # campaign buy turnover
        self.S = 0.0             # campaign sell turnover
        self.K = 0.0             # max cash sunk: max over time of (B − S)
        self.ladder_pct = None   # 0.06 | 0.07 | 0.08, fixed per campaign
        self.ladder_mode = 'NORMAL_3_6'
        self.emergency_stage = 0
        self.tier_progress = 'TIER_1'
        self.skim_pending = False
        self.skim_rebuy_line = None
        self.skim_rebuy_qty = 0
        self.skim_lock_amount = 0.0
        self.stage_idle_days = 0
        self.final_out = False
        self.double_count = 0
        self.deployed_units = 0
        self.next_down_action = None   # DOUBLE | SKIM | REBUY | FINAL (UI)

        self.dirty = False       # ledger changed since the last save
        self._pending = None     # {'intent','side','price','qty','ts'}
        self._skim_notified = False
        self._prev_shares = None
        self._prev_avg = 0.0
        self._restored_q = None
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
        self.events = list(self.events or [])
        self._restored_q = saved.get('q')

    # ── Small helpers ─────────────────────────────────────────────────────────

    def _fp(self, p):
        return f'{p:,.0f}' if self.currency == 'KRW' else f'{p:,.2f}'

    def _event(self, kind, qty, price, shares, avg):
        self.events.append({'ts': datetime.now().strftime('%m/%d %H:%M'),
                            'kind': kind, 'qty': qty, 'price': price,
                            'shares': shares, 'avg': avg})
        del self.events[:-300]
        self.dirty = True

    def _response_fill(self):
        """Any real fill is treated as a market response (§41 simplified)."""
        self.stage_idle_days = 0
        self.dirty = True

    def _set_stage(self, stage):
        self.emergency_stage = stage
        self.ladder_mode = _STAGE_MODE[stage]
        self.tier_progress = 'TIER_1'
        self.dirty = True

    def _clear_skim(self):
        self.skim_pending = False
        self.skim_rebuy_line = None
        self.skim_rebuy_qty = 0
        self.skim_lock_amount = 0.0
        self._skim_notified = False
        self.dirty = True

    def _reset_campaign(self, sell_price=None):
        self.campaign_active = False
        self.B = self.S = self.K = 0.0
        self.ladder_pct = None
        self._set_stage(0)
        self._clear_skim()
        self.stage_idle_days = 0
        self.final_out = False
        self.double_count = 0
        self.deployed_units = 0
        self.events = []
        self._pending = None
        if sell_price:
            self.anchor = sell_price
            self.anchor_source = 'sell'
        self.dirty = True

    def _start_campaign(self, price, qty, snap):
        """First BUY of a campaign (LOAD fill, or arming on a held stock)."""
        val = qty * (price or 0.0)
        self.campaign_active = True
        self.B, self.S, self.K = val, 0.0, val
        self.ladder_pct = ladder_pct_for(price, snap.get('unit_cash'))
        self._set_stage(0)
        self._clear_skim()
        self.stage_idle_days = 0
        self.final_out = False
        self.double_count = 0
        self._log(f'campaign started: {qty} @ {self._fp(price or 0)}, '
                  f'ladder -{self.ladder_pct * 100:.0f}%')

    # ── Day rollover ──────────────────────────────────────────────────────────

    def _roll_day(self, snap):
        d = snap.get('trading_date')
        if not d or d == self.trading_date:
            return
        first = self.trading_date is None
        self.trading_date = d
        if first:
            return
        try:
            weekday = datetime.strptime(d, '%Y-%m-%d').weekday()
        except ValueError:
            weekday = 0
        if weekday < 5 and self.emergency_stage > 0 and not self.final_out:
            self.stage_idle_days += 1
            self.dirty = True
            self._log(f'new day {d}: stage {self.emergency_stage} idle '
                      f'{self.stage_idle_days}/{IDLE_LIMIT_DAYS} days')
        if (snap.get('shares') or 0) <= 0 and snap.get('prev_close'):
            self.anchor = snap['prev_close']
            self.anchor_source = 'close'
            self._log(f'new day {d}: anchor = prev close '
                      f'{self._fp(self.anchor)}')

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
            self._log(f"pending {p['intent']} expired unfilled — forgotten")
            self._pending = None

    def _detect_fills(self, snap, shares):
        prev = self._prev_shares
        avg = float(snap.get('avg_cost') or 0)

        if prev is None:                      # first poll after (re)arming
            if shares > 0:
                if self.campaign_active and self._restored_q == shares:
                    self._log(f'campaign restored: B {self._fp(self.B)} '
                              f'S {self._fp(self.S)} K {self._fp(self.K)} '
                              f'stage {self.emergency_stage} '
                              f'{LADDER_MODES[self.ladder_mode]["label"]}')
                elif self.campaign_active:
                    self._log(f'RECONCILE: broker has {shares} shares, '
                              f'saved campaign had {self._restored_q} — '
                              f'ledger kept; targets follow the ledger, '
                              f'verify them once')
                    self._event('RECON', shares, avg, shares, avg)
                else:
                    self._start_campaign(avg, shares, snap)
                    self._event('HOLD', shares, avg, shares, avg)
                    self._log(f'armed on an existing position: {shares} @ '
                              f'{self._fp(avg)} — ledger seeded from cost')
            elif self.campaign_active:
                self._log('position closed while offline — campaign cleared')
                self._reset_campaign()
            return

        if shares > prev:
            self._on_buy_fill(snap, prev, shares, avg)
        elif shares < prev:
            self._on_sell_fill(snap, prev, shares, avg)

    def _on_buy_fill(self, snap, prev, shares, avg):
        qty = shares - prev
        pend = self._take_pending('BUY')
        if pend:
            price, intent = pend['price'], pend['intent']
        else:
            price = ((avg * shares - self._prev_avg * prev) / qty
                     if avg > 0 and self._prev_avg > 0 and prev > 0
                     else (avg or None))
            intent = 'BUY'                      # external app/web buy
        val = qty * (price or avg or 0.0)

        if not self.campaign_active or intent == 'LOAD':
            self._start_campaign(price or avg, qty, snap)
            self._event('LOAD', qty, price, shares, avg)
            self._log(f'LOAD filled: {prev} → {shares} shares')
            self._response_fill()
            return

        self.B += val
        self.K = max(self.K, self.B - self.S)
        # Any BUY fill restarts the tier ladder on the whole holding (§26).
        self.tier_progress = 'TIER_1'

        if intent == 'DOUBLE':
            self.double_count += 1
            kind = 'DOUBLE'
            if self.emergency_stage > 0:
                self._log('RECOVERY_BUY: full double landed inside the '
                          'emergency episode — back to normal 3/6')
                self._set_stage(0)
                self._clear_skim()
            self._log(f'더블 filled: {prev} → {shares} shares '
                      f'(double #{self.double_count})')
        elif intent == 'REBUY':
            gain = self.skim_lock_amount - val
            kind = 'REBUY'
            self._log(f'밑장 빼기 SUCCESS: rebought {qty} @ '
                      f'{self._fp(price or 0)}, cash edge '
                      f'{self._fp(gain)} freed — back to normal 3/6')
            self._set_stage(0)
            self._clear_skim()
        else:
            kind = 'BUY'
            self._log(f'external buy: {prev} → {shares} shares')

        self._event(kind, qty, price, shares, avg)
        self._response_fill()

    def _on_sell_fill(self, snap, prev, shares, avg):
        qty = prev - shares
        pend = self._take_pending('SELL')
        if pend:
            price, intent = pend['price'], pend['intent']
        else:
            price = snap.get('price') or self._prev_avg or 0.0
            intent = 'SELL'                     # external app/web sell
        val = qty * (price or 0.0)
        self.S += val

        if intent == 'SKIM':
            stage = min(self.emergency_stage + 1, 2)
            self._set_stage(stage)
            self.skim_pending = True
            self.skim_rebuy_line = trim_buy_price(
                self.ticker, price * (1 - SKIM_REBUY_DROP_PCT / 100.0))
            self.skim_rebuy_qty = qty
            self.skim_lock_amount = val
            self.stage_idle_days = 0
            self._skim_notified = False
            self._event('SKIM', -qty, price, shares, avg)
            self._log(f'밑장 빼기 sold {qty} @ {self._fp(price)} → stage '
                      f'{stage} {LADDER_MODES[self.ladder_mode]["label"]}, '
                      f'rebuy line {self._fp(self.skim_rebuy_line)} '
                      f'(lock {self._fp(val)})')
        elif intent in ('TIER1', 'TIER2', 'FINAL'):
            if intent == 'TIER1' and shares > 0:
                self.tier_progress = 'TIER_2'
            if self.skim_pending and intent != 'FINAL':
                self._log('밑장 빼기 FAILURE by rebound: the upper tier '
                          'filled before the rebuy — lock released, '
                          f'{LADDER_MODES[self.ladder_mode]["label"]} stays')
                self._clear_skim()
            self._event(intent, -qty, price, shares, avg)
            self._log(f'{intent} filled: -{qty} @ {self._fp(price)}')
        else:
            self._event('SELL', -qty, price, shares, avg)
            self._log(f'external sell: {prev} → {shares} shares '
                      f'(ledger price estimated at {self._fp(price)})')

        self._response_fill()

        if shares == 0:
            profit = self.S - self.B
            self._log(f'CAMPAIGN COMPLETED: B {self._fp(self.B)} '
                      f'S {self._fp(self.S)} → net {self._fp(profit)} '
                      f'({"손절 FINAL_OUT" if intent == "FINAL" else "exit"})')
            self._reset_campaign(sell_price=price)

    # ── Campaign sell math (PART VI §23-§25) ─────────────────────────────────

    def _sell_targets(self, Q, price_now):
        """[(key, price, qty)] — the first entry is the live trigger."""
        m = LADDER_MODES[self.ladder_mode]
        r1, r2, g = m['r1'], m['r2'], m['g']
        base = self.B + g * self.K - self.S

        def _clamp(p):
            # Target already banked (S covered everything): any price wins —
            # fire at the current market instead of a nonsense line.
            if p <= 0:
                return price_now or 0.01
            return p

        if self.tier_progress == 'TIER_2':
            p = trim_sell_price(self.ticker, _clamp(base / Q))
            return [('tier2', p, Q)]
        if self.deployed_units <= 1 or Q <= 1:
            p = trim_sell_price(
                self.ticker, _clamp((self.B + r1 * self.K - self.S) / Q))
            return [('tier1', p, Q)]
        q1 = round_half_up(Q / 2)
        q2 = Q - q1
        X = base / ((1 + r1) * q1 + (1 + r2) * q2)
        return [('tier1', trim_sell_price(self.ticker, _clamp((1 + r1) * X)), q1),
                ('tier2', trim_sell_price(self.ticker, _clamp((1 + r2) * X)), q2)]

    # ── Projection: the ladder to type into the Toss app by hand ────────────

    def _project(self, avg, shares, sells, free):
        """Future double lines (share count doubling: q, 2q, 4q…). Each
        level is checked against the remaining free army; the first level
        the army cannot fund is shown as 밑장 빼기 (sell 1/3) and the
        projection stops there — after a skim the ladder re-forms around
        the rebuy, so deeper lines would be fiction."""
        d = self.ladder_pct or 0.07
        buys = []
        a, q_tot = avg, shares
        cash = free                       # None = unknown → assume funded
        for _ in range(PROJECTION_DEPTH):
            line = trim_buy_price(self.ticker, a * (1 - d))
            if line <= 0 or q_tot <= 0:
                break
            q = q_tot
            cost = q * line
            if cash is not None and cost > cash:
                skim_q = max(1, round_half_up(q_tot / 3))
                buys.append((line, skim_q, 'SKIM'))
                break
            buys.append((line, q, 'DOUBLE'))
            if cash is not None:
                cash -= cost
            a = (a * q_tot + line * q) / (q_tot + q)
            q_tot += q
        self.projection = {'buys': buys,
                           'sells': [(p, q) for _k, p, q in sells]}

    # ── Main decision cycle ───────────────────────────────────────────────────

    def poll(self, snap: dict) -> list:
        self._roll_day(snap)
        shares = int(snap.get('shares') or 0)
        self._expire_pending(snap, shares)
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

    def _place(self, acts, intent, side, price, qty, label):
        acts.append(('place', side, price, qty, label))
        self._pending = {'intent': intent, 'side': side, 'price': price,
                         'qty': qty, 'ts': time.time()}

    # ── DEPLOYED ──────────────────────────────────────────────────────────────

    def _poll_deployed(self, snap, shares):
        self.state = 'DEPLOYED'
        avg = float(snap.get('avg_cost') or 0)
        if avg <= 0:
            self.status = 'deployed — waiting for avg cost'
            return []
        unit = snap.get('unit_cash') or 0.0
        if not self.campaign_active:          # safety net (seeded on fills)
            self._start_campaign(avg, shares, snap)
        if self.ladder_pct is None:
            self.ladder_pct = ladder_pct_for(avg, unit)
            self.dirty = True
        self.deployed_units = (max(1, round_half_up(shares * avg / unit))
                               if unit > 0 else 1)
        d = self.ladder_pct
        price = snap.get('price')

        sells = self._sell_targets(shares, price)
        lower = trim_buy_price(self.ticker, avg * (1 - d))
        dq = shares                       # 더블 = same share count again

        # Affordability is judged every poll (not only on touch) so the UI
        # can flag 밑장 빼기 BEFORE the price gets there.
        bp = snap.get('buying_power')
        locks = snap.get('skim_locks') or 0.0
        free = None if bp is None else max(0.0, bp - locks)
        affordable = free is None or dq * lower <= free
        if self.skim_pending:
            self.next_down_action = 'REBUY'
        elif affordable:
            self.next_down_action = 'DOUBLE'
        else:
            self.next_down_action = ('FINAL' if self.emergency_stage >= 2
                                     else 'SKIM')

        self.lines = {k: (p, q) for k, p, q in sells}
        if self.skim_pending:
            self.lines['rebuy'] = (self.skim_rebuy_line, self.skim_rebuy_qty)
        else:
            self.lines['lower'] = (lower, dq)
        self._project(avg, shares, sells, free)

        m = LADDER_MODES[self.ladder_mode]
        tag = (f"타짜 -{d * 100:.0f}% {m['label']}"
               + (f' S{self.emergency_stage}' if self.emergency_stage else ''))

        if price is None:
            self.status = f'[{tag}] no price — watching paused'
            return []
        buy_orders, sell_orders = self._split_orders(snap)
        can = snap.get('can_trade', True)
        acts = []

        # FINAL_OUT decided: keep trying to flatten at the market.
        if self.final_out:
            if sell_orders:
                self.status = f'[{tag}] FINAL_OUT sell resting — waiting'
                return acts
            if not can:
                self.status = f'[{tag}] FINAL_OUT pending (watching only)'
                return acts
            out_p = trim_buy_price(self.ticker, price)
            acts += [('cancel', b['id'], 'our buy (final out)')
                     for b in buy_orders if b.get('mine')]
            self._place(acts, 'FINAL', 'SELL', out_p, shares,
                        '손절 FINAL_OUT (sell all)')
            self.status = f'[{tag}] 손절 FINAL_OUT: {shares} @ {self._fp(out_p)}'
            return acts

        # 1) Upper tier (exit first — §36 priority).
        tkey, tprice, tqty = sells[0]
        if price >= tprice:
            if sell_orders:
                self.status = f'[{tag}] sell resting — waiting for the fill'
                return acts
            if not can:
                self.status = (f'[{tag}] SELL trigger met @ '
                               f'{self._fp(price)} (watching only)')
                return acts
            acts += [('cancel', b['id'], 'our buy (exit first)')
                     for b in buy_orders if b.get('mine')]
            intent = 'TIER1' if tkey == 'tier1' else 'TIER2'
            pct = m['r1'] if tkey == 'tier1' else m['r2']
            self._place(acts, intent, 'SELL', tprice, tqty,
                        f'매도 {tkey.upper()} (+{pct * 100:g}% ladder)')
            self.status = (f'[{tag}] SELL fired: {tqty} @ {self._fp(tprice)} '
                           f'({tkey})')
            return acts

        # 2) Down side: rebuy while a 밑장 is out, else double-or-skim.
        if self.skim_pending:
            if price <= self.skim_rebuy_line:
                if buy_orders:
                    self.status = f'[{tag}] rebuy resting — waiting'
                    return acts
                if not can:
                    self.status = (f'[{tag}] REBUY trigger met @ '
                                   f'{self._fp(price)} (watching only)')
                    return acts
                acts += [('cancel', s['id'], 'our sell (rebuy first)')
                         for s in sell_orders if s.get('mine')]
                self._place(acts, 'REBUY', 'BUY', self.skim_rebuy_line,
                            self.skim_rebuy_qty, '밑장 재매수 (-3%)')
                self.status = (f'[{tag}] REBUY fired: {self.skim_rebuy_qty} '
                               f'@ {self._fp(self.skim_rebuy_line)}')
                return acts
        elif price <= lower and dq > 0:
            if buy_orders:
                self.status = f'[{tag}] buy resting — waiting for the fill'
                return acts
            if not can:
                self.status = (f'[{tag}] LOWER trigger met @ '
                               f'{self._fp(price)} (watching only)')
                return acts
            if affordable:
                acts += [('cancel', s['id'], 'our sell (double first)')
                         for s in sell_orders if s.get('mine')]
                self._place(acts, 'DOUBLE', 'BUY', lower, dq,
                            f'더블 BUY +{dq} (shares doubled)')
                self.status = (f'[{tag}] 더블 fired: {dq} @ '
                               f'{self._fp(lower)}')
                return acts
            # Full double unaffordable → 밑장 빼기, or FINAL_OUT at stage 2.
            if self.emergency_stage >= 2:
                self.final_out = True
                self.dirty = True
                acts.append(('notify',
                             '손절 FINAL_OUT: stage 2 lower line touched and '
                             'the full double is unaffordable — selling '
                             'everything (군대 회수는 규칙이다).'))
                self._log('FINAL_OUT: stage 2 + double unaffordable')
                return acts + self._poll_deployed(snap, shares)
            return acts + self._fire_skim(snap, shares, sell_orders, tag,
                                          why=f'double needs {dq} @ '
                                              f'{self._fp(lower)}, free '
                                              f'{self._fp(free)}')

        # 3) Time-based escalation, regular session only (§36/§40).
        if (can and snap.get('phase', 'REGULAR') == 'REGULAR'
                and self.stage_idle_days >= IDLE_LIMIT_DAYS):
            if self.emergency_stage == 1:
                extra = []
                if self.skim_pending:
                    self._log('second 밑장: the first rebuy never filled — '
                              'old plan and lock dropped')
                    extra += [('cancel', b['id'], 'stale rebuy')
                              for b in buy_orders if b.get('mine')]
                    self._clear_skim()
                return acts + extra + self._fire_skim(
                    snap, shares, sell_orders, tag,
                    why=f'{IDLE_LIMIT_DAYS} idle days at stage 1')
            if self.emergency_stage >= 2:
                if self.skim_pending:      # rebuy never came — plan is dead
                    acts += [('cancel', b['id'], 'stale rebuy (final out)')
                             for b in buy_orders if b.get('mine')]
                    self._clear_skim()
                self.final_out = True
                self.dirty = True
                acts.append(('notify',
                             f'손절 FINAL_OUT: {IDLE_LIMIT_DAYS} idle trading '
                             f'days at stage 2 — selling everything.'))
                self._log('FINAL_OUT: stage 2 idle timeout')
                return acts + self._poll_deployed(snap, shares)

        down = (self.skim_rebuy_line if self.skim_pending else lower)
        pend = ' · 밑장 대기' if self.skim_pending else ''
        self.status = (f'[{tag}] 감시: {self._fp(down)} < now '
                       f'{self._fp(price)} < {self._fp(tprice)}{pend}')
        return acts

    def _fire_skim(self, snap, shares, sell_orders, tag, why):
        """Sell 1/3 half-up at the market; the fill arms the −3% rebuy."""
        acts = []
        if sell_orders:
            self.status = f'[{tag}] skim sell resting — waiting'
            return acts
        if not snap.get('can_trade', True):
            self.status = f'[{tag}] 밑장 빼기 due ({why}) — watching only'
            return acts
        price = snap.get('price')
        if not price:
            return acts
        qty = min(shares, max(1, round_half_up(shares / 3)))
        sell_p = trim_buy_price(self.ticker, price)   # cross-the-spread limit
        if not self._skim_notified:
            self._skim_notified = True
            acts.append(('notify',
                         f'밑장 빼기: selling {qty}/{shares} shares now '
                         f'({why}). The same qty is rebought 3% below the '
                         f'fill; the proceeds stay locked for that rebuy.'))
        self._place(acts, 'SKIM', 'SELL', sell_p, qty,
                    f'밑장 빼기 SELL 1/3 ({why})')
        self.status = f'[{tag}] 밑장 빼기 fired: {qty} @ {self._fp(sell_p)}'
        return acts

    # ── EMPTY: watch the −3% load only ────────────────────────────────────────

    def _poll_empty(self, snap):
        self.state = 'EMPTY'
        self.deployed_units = 0
        self.next_down_action = None
        self.projection = None
        if not self.anchor or self.anchor <= 0:
            if snap.get('prev_close'):
                self.anchor = snap['prev_close']
                self.anchor_source = 'close'
                self._log(f'anchor = prev close {self._fp(self.anchor)}')
            else:
                self.status = 'empty — waiting for anchor (prev close)'
                self.lines = {}
                return []

        load_p = trim_buy_price(self.ticker,
                                self.anchor * (1 - LOAD_DROP_PCT / 100.0))
        unit = snap.get('unit_cash') or 0.0
        lq = (max(1, round_half_up(unit / load_p))
              if unit > 0 and load_p > 0 else 0)
        if lq <= 0:
            self.lines = {}
            self.status = 'empty — unit cash unknown'
            return []

        # Pseudo exit: a fresh 1-unit campaign sells whole at +r1 (3%).
        psell = trim_sell_price(self.ticker, load_p * 1.03)
        self.lines = {'load': (load_p, lq), 'psell': (psell, lq)}
        self.projection = {'buys': [(load_p, lq, 'LOAD')],
                           'sells': [(psell, lq)]}

        price = snap.get('price')
        if price is None:
            self.status = 'no price — watching paused'
            return []
        buy_orders, _ = self._split_orders(snap)
        acts = []
        if price <= load_p:
            if buy_orders:
                self.status = 'load buy resting — waiting for the fill'
                return acts
            if not snap.get('can_trade', True):
                self.status = (f'LOAD trigger met @ {self._fp(price)} '
                               f'(watching only)')
                return acts
            self._place(acts, 'LOAD', 'BUY', load_p, lq,
                        f'-{LOAD_DROP_PCT}% load (1 unit)')
            self.status = f'LOAD fired: {lq} @ {self._fp(load_p)}'
            return acts

        self.status = (f'타짜 watching: load {self._fp(load_p)} < now '
                       f'{self._fp(price)} (anchor {self._fp(self.anchor)}, '
                       f'-{LOAD_DROP_PCT}%)')
        return acts
