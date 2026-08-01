"""Offline simulation of the Gearbox V-Commandos campaign engine.

Run:  python scripts/test_vcommandos.py     (no network, no tkinter)

Part 1 checks the deterministic calculator against the manual's own tables
(gear parameters, the volatility→gear rule, and the normalized Part II
ladders — final average, capital used, and rebound-to-tier).

Part 2 runs whole price paths through the real engine behind a FakeBroker
that fills resting limit orders when the simulated price crosses them, so
the campaign lifecycle is exercised exactly as the live watcher would see
it: LOAD → CHASE → full EXIT → same-day reload, plus manual app trades,
adopted positions, the army wall, gear shifts and KR tick trimming.
"""

import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.calc import (GEARS, RELOAD_DROP_PCT, add_ratio, calc_chase_cascade,
                       calc_exit, calc_exit_lines, calc_load_ladder,
                       calc_load_price, calc_load_shares, calc_reload_price,
                       calc_sell_tiers, calc_volatility, chase_drop,
                       effective_entry_gear, exit_pct, load_drop,
                       normalize_gear, select_auto_gear, tier_for_exit_pct,
                       tier_pcts, weight_min_gear)
from core.vcommandos import CampaignEngine

T = 'TEST'                 # USD-style ticker: cent trims keep prices exact
KR = '005930.KS'
D1, D2 = '2026-07-21', '2026-07-22'
UNIT = 1000.0
AMPLE_CASH = 1_000_000_000.0

passed = 0


def ok(cond, name, info=''):
    global passed
    assert cond, f'FAIL: {name} {info}'
    passed += 1
    print(f'  ok  {name}')


def near(a, b, tol=0.01):
    return a is not None and abs(a - b) <= tol


# ════════════════════════════════════════════════════════════════════════════
# Part 1 — the deterministic calculator
# ════════════════════════════════════════════════════════════════════════════
print('— gear table (manual §6) —')

# gear: (load%, chase%, add ratio, tiers)
MANUAL_GEARS = {
    1: (6, 4, 1 / 2, (1, 3, 5)),
    2: (7, 5, 2 / 3, (2, 4, 6)),
    3: (8, 6, 3 / 4, (3, 5, 7)),
    4: (9, 7, 4 / 5, (4, 6, 8)),
    5: (10, 8, 1.0, (5, 7, 9)),
}
for g, (ld, ch, ratio, tiers) in MANUAL_GEARS.items():
    ok(load_drop(g) == ld and chase_drop(g) == ch
       and abs(add_ratio(g) - ratio) < 1e-9
       and GEARS[g]['tiers'] == tiers,
       f'G{g} matches the manual ({ld}/{ch}/×{ratio:.3f}/{tiers})')

ok(all(exit_pct(g, 2) == MANUAL_GEARS[g][3][1] for g in MANUAL_GEARS),
   'tier 2 is the default exit of every gear')
ok(tier_for_exit_pct(3, 5) == 2 and tier_for_exit_pct(3, 99) is None,
   'a stored exit percent maps back to its tier')

print('— volatility → gear (manual §17) —')
# V = 100 × (High5 − Low5) / High5 — the span of the whole 5-day window.
CUTS = [(0.0, 1), (9.9, 1), (10.0, 1), (10.1, 2), (15.0, 2), (15.1, 3),
        (20.0, 3), (20.1, 4), (25.0, 4), (25.1, 5), (55.0, 5)]
for v, g in CUTS:
    ok(select_auto_gear(v) == g, f'V {v}% → G{g}')
ok(select_auto_gear(None) == 1,
   'unknown volatility never guesses its way into the ×1.0 gear')
ok(select_auto_gear(25.1) == 5 and select_auto_gear(25.0) == 4,
   'G5 ×1.0 arms only above a 25% range')
ok(near(calc_volatility(104.0, 92.0), 11.538, 0.001)
   and select_auto_gear(calc_volatility(104.0, 92.0)) == 2,
   'V is computed from the 5-day high and low, then read off the ladder')

print('— heavy-unit entry floor —')
ok(weight_min_gear(900, 1000) == 1 and weight_min_gear(1300, 1000) == 2
   and weight_min_gear(2600, 1000) == 5,
   'one chunky share floors the entry gear')
ok(effective_entry_gear(5.0, 2600, 1000) == 5,
   'a calm but chunky stock still enters on G5')
ok(effective_entry_gear(30.0, 100, 1000) == 5,
   'a violent stock reaches G5 on volatility alone')

print('— legacy migration —')
ok(normalize_gear(4) == 4 and normalize_gear('G2') == 2,
   'a gear number passes through')
ok(normalize_gear(8) == 5 and normalize_gear(6) == 3,
   'a legacy bait drop percent becomes its gear')
ok(normalize_gear('L1') == 1 and normalize_gear('L4') == 5,
   'the oldest L1-L7 keys still migrate')

print('— normalized ladders (manual Part II, fractional sizing) —')
# vantage 100, 1 unit at LOAD, no fees, fractional shares. The 32-unit figure
# is the table's reference scale, NOT a cap the engine enforces.
MANUAL_TABLE = {           # gear: (rows, final avg, capital used)
    1: (8, 84.43, 23.02),
    2: (7, 80.74, 31.01),
    3: (6, 78.69, 24.57),
    4: (6, 75.28, 28.14),
    5: (5, 73.38, 26.09),
}
for g, (rows, final_avg, capital) in MANUAL_TABLE.items():
    price = calc_load_price(100.0, g)
    qty, avg, spent = 1.0, price, 1.0
    for _ in range(rows):
        buy_p = avg * (1 - chase_drop(g) / 100.0)
        add = qty * add_ratio(g)
        spent += add * buy_p * (1.0 / price)     # cash, in units
        avg = (avg * qty + buy_p * add) / (qty + add)
        qty += add
    ok(near(avg, final_avg, 0.02), f'G{g} final average {final_avg}',
       f'got {avg:.2f}')
    ok(near(spent, capital, 0.02), f'G{g} capital used {capital}u',
       f'got {spent:.2f}u')
    ok(GEARS[g]['max_chase'] == rows,
       f'G{g} reference table runs {rows} chases')

print('— card lines —')
ladder, load_p, load_q = calc_load_ladder(100.0, 3, UNIT, chases=2)
ok(near(load_p, 92.0) and load_q == 11,
   'G3 LOAD sits at vantage -8% and buys one unit', f'{load_p} × {load_q}')
ok(near(ladder[1]['price'], 92.0 * 0.94) and ladder[1]['qty'] == 8,
   'chase 1 hangs -6% below the load price, ×3/4 of the shares')
cascade = calc_chase_cascade(11, 92.0, 3, 3)
ok(cascade[0]['price'] == ladder[1]['price']
   and cascade[1]['price'] == ladder[2]['price'],
   'the flat card projects exactly the deployed cascade')

ex = calc_exit(11, 92.0, 3, 2)
ok(ex['qty'] == 11 and near(ex['price'], 92.0 * 1.05),
   'one armed tier carries the WHOLE position')

print('— the exit ladder (the old distribution law) —')
pcts = tier_pcts(3)
one = calc_sell_tiers(11, 92.0, pcts, [False, True, False])
ok([e['qty'] for e in one] == [None, 11, None],
   'one tier armed → everything leaves there', str([e['qty'] for e in one]))
two = calc_sell_tiers(10, 92.0, pcts, [True, False, True])
ok([e['qty'] for e in two] == [5, None, 5],
   'two armed → the holding halves', str([e['qty'] for e in two]))
three = calc_sell_tiers(9, 92.0, pcts, [True, True, True])
ok([e['qty'] for e in three] == [3, 3, 3],
   'three armed → the holding thirds', str([e['qty'] for e in three]))
ok([e['qty'] for e in calc_sell_tiers(5, 92.0, pcts, [True] * 3)] == [2, 2, 1],
   'the remainder goes to the MIDDLE tier first (5 → 2/2/1)')
ok([e['qty'] for e in calc_sell_tiers(4, 92.0, pcts, [True] * 3)] == [1, 2, 1],
   'then the centre never ends up smaller than the outsides (4 → 1/2/1)')
ok([e['qty'] for e in calc_sell_tiers(1, 92.0, pcts, [True] * 3)]
   == [None, 1, None],
   'a single share goes to the middle tier alone (1 → 0/1/0)')
ok(all(e['qty'] is None for e in calc_sell_tiers(9, 92.0, pcts, [False] * 3)),
   'no tier armed → no exit line at all')
ok(near(calc_sell_tiers(9, 92.0, pcts, [True] * 3)[2]['price'], 92.0 * 1.07),
   'each tier prices off its own gear percent')
ok([e['qty'] for e in calc_exit_lines(9, 92.0, 3, [True, True, True])]
   == [3, 3, 3],
   'calc_exit_lines with actives gives the real ladder')
ok([e['qty'] for e in calc_exit_lines(9, 92.0, 3)] == [9, 9, 9],
   'and without them, the price-only reference')

ok(near(calc_reload_price(100.0), 97.0),
   f'the same-day reload is a flat -{RELOAD_DROP_PCT}% off the sell fill')
ok(calc_load_shares(100.0, 5, 50.0) == 1,
   'a share bigger than a unit still loads the minimum 1 share')


# ════════════════════════════════════════════════════════════════════════════
# Part 2 — the campaign engine behind a fake broker
# ════════════════════════════════════════════════════════════════════════════

class FakeBroker:
    """Resting limit orders fill the moment the price crosses them."""

    def __init__(self, shares=0, avg=0.0, cash=AMPLE_CASH):
        self.shares = int(shares)
        self.avg = float(avg)
        self.cash = cash                 # None = unknown (unlimited)
        self.orders = {}
        self.closed = {}
        self._next = 1
        self.fills = []

    def place(self, side, price, qty):
        oid = f'f{self._next}'
        self._next += 1
        self.orders[oid] = {'side': side, 'price': price, 'qty': int(qty),
                            'filled': 0, 'mine': True}
        return oid

    def cancel(self, oid):
        order = self.orders.pop(oid, None)
        if order:
            self.closed[oid] = dict(order, status='CANCELED')

    def order_detail(self, oid):
        order = self.orders.get(oid) or self.closed.get(oid)
        if not order:
            return None
        filled = int(order.get('filled') or 0)
        status = order.get('status') or (
            'PARTIAL_FILLED' if filled else 'PENDING')
        return {'id': oid, 'order_id': oid, 'side': order['side'],
                'price': order['price'], 'qty': order['qty'],
                'qty_open': max(0, order['qty'] - filled),
                'filled': filled, 'mine': order.get('mine', True),
                'status': status,
                'terminal': status in ('FILLED', 'CANCELED', 'REJECTED')}

    def open_orders(self):
        return [{'id': oid, 'side': o['side'], 'price': o['price'],
                 'qty_open': o['qty'] - o.get('filled', 0),
                 'filled': o.get('filled', 0),
                 'mine': o.get('mine', True)}
                for oid, o in self.orders.items()]

    def on_price(self, price):
        for oid, o in list(self.orders.items()):
            hit = (price <= o['price'] if o['side'] == 'BUY'
                   else price >= o['price'])
            if hit:
                self.partial_fill(oid, o['qty'] - o.get('filled', 0))

    def partial_fill(self, oid, qty, price=None):
        o = self.orders[oid]
        qty = min(int(qty), o['qty'] - o.get('filled', 0))
        if qty <= 0:
            return
        o['filled'] = o.get('filled', 0) + qty
        self._fill(o['side'], price or o['price'], qty,
                   mine=o.get('mine', True), order_id=oid)
        if o['filled'] >= o['qty']:
            self.closed[oid] = dict(o, status='FILLED')
            self.orders.pop(oid, None)

    def _fill(self, side, price, qty, mine, order_id=None):
        if side == 'BUY':
            total = self.avg * self.shares + price * qty
            self.shares += qty
            self.avg = total / self.shares
            if self.cash is not None:
                self.cash -= price * qty
        else:
            self.shares -= qty
            if self.cash is not None:
                self.cash += price * qty
            if self.shares == 0:
                self.avg = 0.0
        self.fills.append({'side': side, 'price': price, 'qty': qty,
                           'filled_at': D1 + 'T12:00:00',
                           'order_id': order_id, 'mine': bool(mine)})

    # A manual trade made in the broker app, invisible to the engine.
    def external_buy(self, price, qty):
        self._fill('BUY', price, qty, mine=False)

    def external_sell(self, price, qty):
        self._fill('SELL', price, qty, mine=False)


def snap(b, price, date=D1, high5=100.0, prev_close=None, unit=UNIT,
         card=None, can_trade=True, highs=None, recent_fills=None):
    if highs is None:
        # One flat window of sessions at `high5`, dated up to `date`.
        highs = [(date, high5)]
    return {'price': price, 'shares': b.shares, 'avg_cost': b.avg,
            'orders': b.open_orders(), 'buying_power': b.cash,
            'unit_cash': unit, 'trading_date': date, 'highs': highs,
            'prev_close': prev_close or high5,
            'can_trade': can_trade, 'card': card,
            'recent_fills': (list(b.fills) if recent_fills is None
                             else list(recent_fills))}


def settle(e, b, price, rounds=4, **kw):
    """Poll → execute → fill → poll again, until the engine is quiet."""
    placed = []
    for _ in range(rounds):
        broker_snap = snap(b, price, **kw)
        pending = getattr(e, '_pending', None) or {}
        if pending.get('order_id'):
            broker_snap['pending_order'] = b.order_detail(
                pending.get('order_id'))
        for act in e.poll(broker_snap):
            if act[0] == 'place':
                _, side, p, q, _label = act
                oid = b.place(side, p, q)
                e.note_order_accepted(side, p, q, order_id=oid)
                placed.append((side, p, q))
            elif act[0] == 'cancel':
                b.cancel(act[1])
        b.on_price(price)
    return placed


def fresh(ticker=T, shares=0, avg=0.0, cash=AMPLE_CASH, gear=3,
          tiers=(0, 1, 0)):
    b = FakeBroker(shares, avg, cash)
    e = CampaignEngine(ticker, trading_date=D1)
    return e, b, {'gear': gear, 'exit_tiers': [bool(t) for t in tiers]}


def line(e, key):
    """(price, qty) of a published line, or (None, None)."""
    v = e.lines.get(key)
    return (v['price'], v['qty']) if v else (None, None)


def exits(e):
    """The armed exit ladder as (price, qty), low tier first."""
    return [(v['price'], v['qty']) for k, v in sorted(e.lines.items())
            if k.startswith('exit')]


print('— the loop: LOAD, CHASE, EXIT —')
e, b, card = fresh(gear=3)
settle(e, b, 95.0, card=card)
ok(e.state == 'FLAT' and near(line(e, 'load')[0], 92.0),
   'empty: one LOAD line at the vantage -8%', str(e.lines))
ok(b.shares == 0, 'above the LOAD nothing is bought')

settle(e, b, 92.0, card=card)
ok(b.shares == 11, 'the LOAD fills one unit', f'{b.shares} sh')
ok([ev['kind'] for ev in e.events] == ['LOAD'],
   'and writes one row to the log', str(e.events))
ok(e.state == 'DEPLOYED', 'the stock is deployed because the broker says so')

chase_p, chase_q = line(e, 'chase')
ok(near(chase_p, 92.0 * 0.94, 0.02) and chase_q == 8,
   'the CHASE hangs -6% under the broker average, x3/4 of the shares')
ok(near(exits(e)[0][0], 92.0 * 1.05, 0.02) and exits(e)[0][1] == 11,
   'and the EXIT is +5% above it, carrying the whole holding')

settle(e, b, chase_p, card=card)
ok(b.shares == 19, 'the chase fills', f'{b.shares} sh @ {b.avg:.2f}')
ok(near(line(e, 'chase')[0], b.avg * 0.94, 0.02)
   and near(exits(e)[0][0], b.avg * 1.05, 0.02),
   'the average moved, so BOTH lines moved with it — that is the whole '
   'mechanic')

settle(e, b, exits(e)[0][0], card=card)
ok(b.shares == 0, 'the EXIT empties the position')
ok(e.state == 'FLAT' and e.events == [],
   'the log is cleared and the stock is empty again, exactly as the v^ '
   'engine cleared its day log', str(e.events))
ok(line(e, 'load')[0] is not None,
   'and it is back on the LOAD rule, not the chase rule')

print('— selling out stands the bot down —')
e, b, card = fresh(gear=3)
settle(e, b, 92.0, card=card)
target = exits(e)[0][0]
acts = []
for _ in range(4):                      # settle(), but keeping every action
    out = list(e.poll(snap(b, target, card=card)))
    acts += out
    for a in out:
        if a[0] == 'place':
            oid = b.place(a[1], a[2], a[3])
            e.note_order_accepted(a[1], a[2], a[3], order_id=oid)
        elif a[0] == 'cancel':
            b.cancel(a[1])
    b.on_price(target)
ok(any(a[0] == 'stand_down' for a in acts),
   'closing the position asks the controller to drop LIVE',
   str([a[0] for a in acts]))

print('— the other side cancels the pending order —')
e, b, card = fresh(gear=3)
settle(e, b, 92.0, card=card)
chase_p = line(e, 'chase')[0]
exit_p = exits(e)[0][0]
e.poll(snap(b, chase_p, card=card))
b.place('BUY', chase_p, 8)
acts = e.poll(snap(b, exit_p, card=card))
ok(any(a[0] == 'cancel' for a in acts),
   'the resting BUY is cancelled when the price reaches the sell side',
   str(acts))
for a in acts:
    if a[0] == 'cancel':
        b.cancel(a[1])
acts = e.poll(snap(b, exit_p, card=card))
ok(any(a[0] == 'place' and a[1] == 'SELL' for a in acts),
   'and the SELL goes out on the next poll', str(acts))

print('— a resting order is left alone, or re-priced —')
e, b, card = fresh(gear=3)
e.poll(snap(b, 92.0, card=card))
b.place('BUY', 92.0, 11)
acts = e.poll(snap(b, 92.0, card=card))
ok(not [a for a in acts if a[0] == 'place'],
   'an order already resting on the right line is left to work')
card5 = {'gear': 5, 'exit_tiers': [False, True, False]}
acts = e.poll(snap(b, 90.0, card=card5))
ok(any(a[0] == 'cancel' for a in acts),
   'a gear change moves the line, so the stale order is pulled', str(acts))

print('— external trading needs no special case —')
e, b, card = fresh(gear=3)
b.external_buy(90.0, 20)
settle(e, b, 91.0, card=card)
ok(e.state == 'DEPLOYED',
   'a hand buy makes the stock deployed — the broker is the authority')
ok(near(line(e, 'chase')[0], 90.0 * 0.94, 0.02),
   'and the lines are simply drawn from the new average')

b.external_buy(80.0, 10)
settle(e, b, 86.0, card=card)
ok(near(line(e, 'chase')[0], b.avg * 0.94, 0.02)
   and near(exits(e)[0][0], b.avg * 1.05, 0.02),
   'a second hand buy moves the average again, and the lines follow')

b.external_sell(95.0, b.shares)
settle(e, b, 99.0, card=card)
ok(e.state == 'FLAT' and e.events == [],
   'a hand sell-out empties the stock and clears the log')
ok(line(e, 'load')[0] is not None,
   'and it goes straight back to the LOAD rule')

print('— a stale saved log is dropped on arming —')
e, b, card = fresh(gear=3)
settle(e, b, 92.0, card=card)
saved = e.to_dict()
ok(saved['events'], 'the log is saved while the position is held')
b2 = FakeBroker(0, 0.0, AMPLE_CASH)
e2 = CampaignEngine(T, trading_date=D1, saved=saved)
settle(e2, b2, 99.0, card=card)
ok(e2.events == [] and e2.state == 'FLAT',
   'a saved log the broker no longer backs is dropped, not carried forward',
   str(e2.events))

print('— the tiered exit —')
e, b, card = fresh(gear=3, tiers=(1, 1, 1))
settle(e, b, 92.0, card=card)
ok([q for _p, q in exits(e)] == [4, 4, 3],
   '11 shares split across three tiers, the middle taking the remainder',
   str(exits(e)))
settle(e, b, exits(e)[0][0], card=card)
ok(b.shares == 7, 'T1 takes its portion and the rest is still held',
   f'{b.shares} sh')
ok(e.state == 'DEPLOYED', 'the campaign is not over — only a portion left')
ok(len(exits(e)) == 2 and [q for _p, q in exits(e)] == [4, 3],
   'the remaining 7 re-split across the tiers still armed', str(exits(e)))
ok(line(e, 'chase')[0] is not None,
   'and the CHASE is still live, so a dip after a partial exit can be bought')

settle(e, b, line(e, 'chase')[0], card=card)
ok(b.shares > 7 and len(exits(e)) == 3,
   'buying more re-arms every tier on the bigger holding', str(exits(e)))

print('— the army is the only wall —')
e, b, card = fresh(gear=3, cash=500.0)
settle(e, b, 92.0, card=card)
ok(b.shares == 0 and e.buy_state == 'EXHAUSTED',
   'no cash, no order — and the state says why')

print('— WATCH draws but never sends —')
e, b, card = fresh(gear=3)
placed = settle(e, b, 92.0, card=card, can_trade=False)
ok(not placed and b.shares == 0, 'WATCH sends nothing')
ok(e.crossed['BUY'] and 'not sent' in e.status,
   'but the crossed line is reported, and the status says plainly why',
   e.status)

print('— KR tick trimming —')
e, b, card = fresh(ticker=KR, gear=3)
placed = settle(e, b, 64_400.0, high5=70_000.0, unit=1_000_000.0, card=card)
ok(placed and all(p % 100 == 0 for _s, p, _q in placed),
   'KR limit prices snap to the tick grid', str(placed))
ok(all(p <= 70_000.0 * 0.92 for _s, p, _q in placed),
   'and a trimmed BUY never bids above the strategy line')

print('— the vantage window is FIVE sessions —')
FIVE = [('2026-07-27', 120.0), ('2026-07-28', 104.0), ('2026-07-29', 103.0),
        ('2026-07-30', 102.0), ('2026-07-31', 101.0)]
e, b, card = fresh(gear=3)
settle(e, b, 115.0, date='2026-08-01', card=card, highs=FIVE)
ok(near(e.vantage, 120.0),
   'with no bar for today the window is the last FIVE completed sessions, '
   "so 07/27's peak still counts", str(e.vantage))
e2, b2, card2 = fresh(gear=3)
settle(e2, b2, 99.0, date='2026-08-03', card=card2,
       highs=FIVE + [('2026-08-03', 106.0)])
ok(near(e2.vantage, 106.0),
   'with a live bar it is four completed + today, and the live high leads',
   str(e2.vantage))

print('— the vantage can be pinned by hand —')
e, b, card = fresh(gear=3)
settle(e, b, 99.0, card=card, highs=[(D1, 100.0)])
ok(near(e.vantage, 100.0), 'it starts on the rolling high')
ok(e.set_manual_vantage(140.0, '07/18 high'), 'a picked day pins it')
settle(e, b, 135.0, date=D2, card=card, highs=[(D2, 100.0)])
ok(near(e.vantage, 140.0) and e.vantage_src == 'manual',
   'and it survives the day roll', f'{e.vantage} {e.vantage_src}')
e.clear_manual_vantage()
settle(e, b, 99.0, date=D2, card=card, highs=[(D2, 100.0)])
ok(near(e.vantage, 100.0), 'releasing hands it back to the rolling high')

print('— persistence stays tiny —')
e, b, card = fresh(gear=4)
settle(e, b, 91.0, card=card)
saved = e.to_dict()
ok(saved['strategy'] == 'V_COMMANDOS_GEARBOX',
   'the saved record names its strategy')
ok(set(saved) - {'strategy', 'q', 'saved_at'} == {
       'gear', 'exit_tiers', 'tier_done', 'vantage', 'vantage_manual',
       'vantage_manual_label', 'trading_date', 'events'},
   'and carries only the handful of fields that cannot be re-read from the '
   'broker', str(sorted(saved)))
e2 = CampaignEngine(T, trading_date=D1, saved=saved)
settle(e2, b, 91.0, card=card)
ok(e2.gear == 4 and len(e2.events) == len(e.events),
   'a restart resumes on the same gear with the same log')

print(f'\nALL {passed} CHECKS PASSED')
