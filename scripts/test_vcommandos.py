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
   'unknown volatility never guesses its way into the doubling gear')
ok(select_auto_gear(25.1) == 5 and select_auto_gear(25.0) == 4,
   'G5 — the share-doubling gear — arms only above a 25% range')
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

    def __init__(self, shares=0, avg=0.0, cash=None):
        self.shares = int(shares)
        self.avg = float(avg)
        self.cash = cash                 # None = unknown (unlimited)
        self.orders = {}
        self._next = 1
        self.fills = []

    def place(self, side, price, qty):
        oid = f'f{self._next}'
        self._next += 1
        self.orders[oid] = {'side': side, 'price': price, 'qty': int(qty)}
        return oid

    def cancel(self, oid):
        self.orders.pop(oid, None)

    def open_orders(self):
        return [{'id': oid, 'side': o['side'], 'price': o['price'],
                 'qty_open': o['qty'], 'filled': 0, 'mine': True}
                for oid, o in self.orders.items()]

    def on_price(self, price):
        for oid, o in list(self.orders.items()):
            hit = (price <= o['price'] if o['side'] == 'BUY'
                   else price >= o['price'])
            if hit:
                self.orders.pop(oid)
                self._fill(o['side'], o['price'], o['qty'])

    def _fill(self, side, price, qty):
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
        self.fills.append((side, price, qty))

    # A manual trade made in the broker app, invisible to the engine.
    external_buy = lambda self, price, qty: self._fill('BUY', price, qty)
    external_sell = lambda self, price, qty: self._fill('SELL', price, qty)


def snap(b, price, date=D1, high5=100.0, prev_close=None, unit=UNIT,
         card=None, can_trade=True, highs=None):
    if highs is None:
        # One flat window of sessions at `high5`, dated up to `date`.
        highs = [(date, high5)]
    return {'price': price, 'shares': b.shares, 'avg_cost': b.avg,
            'orders': b.open_orders(), 'buying_power': b.cash,
            'unit_cash': unit, 'trading_date': date, 'highs': highs,
            'prev_close': prev_close or high5,
            'can_trade': can_trade, 'card': card}


def settle(e, b, price, rounds=4, **kw):
    """Poll → execute → fill → poll again, until the engine is quiet."""
    placed = []
    for _ in range(rounds):
        for act in e.poll(snap(b, price, **kw)):
            if act[0] == 'place':
                _, side, p, q, _label = act
                b.place(side, p, q)
                placed.append((side, p, q))
            elif act[0] == 'cancel':
                b.cancel(act[1])
        b.on_price(price)
    return placed


def fresh(ticker=T, shares=0, avg=0.0, cash=None, gear=3, tiers=(0, 1, 0)):
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


print('— campaign lifecycle —')
e, b, card = fresh(gear=3)
settle(e, b, 95.0, card=card)
ok(e.state == 'FLAT' and near(line(e, 'load')[0], 92.0),
   'flat: one LOAD line at High5 -8%', str(e.lines))
ok(b.shares == 0, 'above the LOAD nothing is bought')

settle(e, b, 92.0, card=card)
ok(b.shares == 11 and e.campaign_id,
   'the LOAD fills one unit and opens a campaign', f'{b.shares} sh')
ok(e.events and e.events[0]['kind'] == 'LOAD',
   'the campaign log opens with a LOAD row')
ok(e.events[0]['date'] == D1,
   'every log row records the trading day, not only the clock')
ok(near(exits(e)[0][0], 92.0 * 1.05, 0.02)
   and exits(e)[0][1] == b.shares,
   'the EXIT is the whole position at T2 +5%', str(e.lines.get('exit')))

chase_p, chase_q = line(e, 'chase')
ok(near(chase_p, 92.0 * 0.94, 0.01) and chase_q == 8,
   'the CHASE hangs -6% below the broker average, ×3/4')
settle(e, b, chase_p, card=card)
ok(b.shares == 19 and e.chase_count == 1,
   'the chase fills and the average drops', f'{b.shares} sh @ {b.avg:.2f}')
ok(near(exits(e)[0][0], b.avg * 1.05, 0.02),
   'the EXIT re-aims off the NEW broker average')

exit_p = exits(e)[0][0]
settle(e, b, exit_p, card=card)
ok(b.shares == 0, 'the EXIT sells every share in one order')
ok(e.campaign_id is None and e.campaign_state == 'RELOAD_ARMED',
   'the campaign completes and arms the same-day reload')

print('— the next lines, and the ones after —')
e, b, card = fresh(gear=3)
settle(e, b, 92.0, card=card)
ok(e.lines['chase']['armed'] and not e.lines['chase2']['armed'],
   'exactly one buy line is armed; the rest are projections')
ok('chase2' in e.lines and 'chase3' in e.lines,
   'two chases beyond the armed one are published', str(list(e.lines)))
c1, c2, c3 = (line(e, k)[0] for k in ('chase', 'chase2', 'chase3'))
ok(c1 > c2 > c3, 'the projected ladder descends', f'{c1} {c2} {c3}')
q1, q2 = line(e, 'chase')[1], line(e, 'chase2')[1]
ok(q2 > q1, 'each deeper chase adds more shares', f'{q1} then {q2}')
proj = calc_chase_cascade(b.shares, b.avg, 3, 2)
ok(near(c2, proj[1]['price'], 0.02),
   'the projection folds each fill into the running average, as the '
   'campaign would')
flat_e, flat_b, flat_card = fresh(gear=3)
settle(flat_e, flat_b, 95.0, card=flat_card)
ok(flat_e.lines['load']['armed'] and not flat_e.lines['pexit2']['armed'],
   "a flat card arms the LOAD and marks its exit a projection")
ok('chase1' in flat_e.lines and 'chase2' in flat_e.lines,
   'a FLAT stock publishes chase 1 AND chase 2 — the LOAD is the armed line '
   'there, so chase 1 is a projection like the rest',
   str(list(flat_e.lines)))
ok('chase' not in flat_e.lines,
   "and no bare 'chase' key, which only a deployed campaign arms")
fl_load = line(flat_e, 'load')
fl_c1, fl_c2 = line(flat_e, 'chase1'), line(flat_e, 'chase2')
ok(fl_load[0] > fl_c1[0] > fl_c2[0],
   'the flat ladder descends LOAD → chase 1 → chase 2',
   f'{fl_load[0]} {fl_c1[0]} {fl_c2[0]}')
card_ladder, _lp, _lq = calc_load_ladder(100.0, 3, UNIT, chases=2)
ok(near(fl_c1[0], card_ladder[1]['price'], 0.02)
   and fl_c1[1] == card_ladder[1]['qty'],
   "and matches the flat CARD's own Chase 1 line exactly",
   f"{fl_c1} vs {card_ladder[1]}")
ok(near(fl_c2[0], card_ladder[2]['price'], 0.02)
   and fl_c2[1] == card_ladder[2]['qty'],
   "and its Chase 2 line too")

print('— same-day reload —')
e, b, card = fresh(gear=3)
settle(e, b, 92.0, card=card)
exit_p = exits(e)[0][0]
settle(e, b, exit_p, card=card)
ok(near(e.vantage, exit_p) and e.vantage_src == 'reload',
   'the reload anchors on the ACTUAL final sell fill')
settle(e, b, exit_p * 0.99, card=card)
ok(b.shares == 0, 'a 1% dip does not trigger the -3% reload')
reload_p = line(e, 'load')[0]
ok(near(reload_p, exit_p * 0.97, 0.02),
   f'the reload sits at the sell fill -{RELOAD_DROP_PCT}%', str(reload_p))
settle(e, b, reload_p, card=card)
ok(b.shares > 0 and e.events[0]['kind'] == 'RELOAD',
   'the reload opens a new campaign, logged as RELOAD')

print('— the DYNAMIC HIGH5 vantage (manual Appendix A.4/A.5) —')
e, b, card = fresh(gear=3)
settle(e, b, 92.0, card=card)
sell_at = exits(e)[0][0]
settle(e, b, sell_at, card=card)
ok(e.vantage_src == 'reload' and e.last_exit_date == D1,
   'the exit records the day it happened and arms the same-day reload',
   f'{e.vantage_src} {e.last_exit_date}')

# Day 2: the reload expires. Appendix A.2 Case 3 — the bot does NOT try to
# date the old campaign; it just uses the Dynamic High5 window again.
# FIVE completed sessions + today, so the oldest must fall out of the window.
window = [('2026-07-15', 130.0), ('2026-07-16', 110.0), ('2026-07-17', 104.0),
          ('2026-07-20', 103.0), (D1, 102.0), (D2, 101.0)]
settle(e, b, 105.0, date=D2, card=card, highs=window)  # above the 101.2 LOAD
ok(e.vantage_src == 'high5',
   'day 2 drops the reload and returns to the Dynamic High5 LOAD',
   e.vantage_src)
ok(near(e.vantage, 110.0),
   'the window is the last FOUR completed sessions plus today — the 130 from '
   'five sessions back has aged out, the 110 has not', str(e.vantage))
ok(near(line(e, 'load')[0], 110.0 * 0.92, 0.02),
   'and the LOAD hangs under it')

# A.5: a peak made THIS session lifts the vantage immediately.
settle(e, b, 118.0, date=D2, card=card,
       highs=window[:-1] + [(D2, 120.0)])
ok(near(e.vantage, 120.0),
   "today's high so far is part of the window, so a fresh peak lifts the "
   'vantage in the same session', str(e.vantage))
ok(near(line(e, 'load')[0], 120.0 * 0.92, 0.02),
   'and the LOAD line is recalculated live under it',
   str(line(e, 'load')[0]))

print('— the vantage can be pinned by hand —')
e2, b2, card2 = fresh(gear=3)
settle(e2, b2, 99.0, card=card2, highs=[(D1, 100.0)])
ok(near(e2.vantage, 100.0) and e2.vantage_src == 'high5',
   'it starts on the rolling high')
ok(e2.set_manual_vantage(140.0, '(07/18 high)'),
   'a day picked off the 5-day chart pins the vantage')
settle(e2, b2, 135.0, date=D2, card=card2, highs=[(D2, 100.0)])
ok(near(e2.vantage, 140.0) and e2.vantage_src == 'manual',
   'and it survives the day roll — the rolling high does not overwrite it',
   f'{e2.vantage} {e2.vantage_src}')
ok(near(line(e2, 'load')[0], 140.0 * 0.92, 0.02),
   'the LOAD hangs under the pinned price')
e2.clear_manual_vantage()
settle(e2, b2, 99.0, date=D2, card=card2, highs=[(D2, 100.0)])
ok(near(e2.vantage, 100.0) and e2.vantage_src == 'high5',
   'releasing it hands the vantage back to the rolling high')

print('— the army is the only wall —')
e, b, card = fresh(gear=5, cash=None)        # unlimited cash
settle(e, b, 90.0, card=card)
ok(b.shares == 11, 'G5 loads at High5 -10%')
for _ in range(6):
    p = line(e, 'chase')[0]
    if p is None:
        break
    settle(e, b, p, card=card)
ok(b.shares > 350 and e.chase_count == 6,
   'with cash on hand the ladder keeps going — no 32-unit cap stops it',
   f'{b.shares} sh after {e.chase_count} chases')
ok(e.buy_state == 'OK', 'and the buy side never reports a cap')

e, b, card = fresh(gear=3, cash=1500.0)      # 1.5 units of cash
settle(e, b, 92.0, card=card)
ok(b.shares == 11, 'the LOAD fits in the army')
settle(e, b, line(e, 'chase')[0], card=card)
ok(e.buy_state == 'EXHAUSTED' and b.shares == 11,
   'the next chase is refused when the cash is not there',
   f'{b.shares} sh, {e.buy_state}')
ok(any(k.startswith('exit') for k in e.lines), 'an exhausted campaign keeps watching its EXIT')

e, b, card = fresh(gear=3, cash=500.0)       # half a unit
settle(e, b, 92.0, card=card)
ok(b.shares == 0 and e.buy_state == 'EXHAUSTED',
   'no army → the LOAD is not fired and the state says so')

print('— manual app trading, adopted and reconciled —')
e, b, card = fresh(gear=3)
b.external_buy(90.0, 20)                     # bought by hand in the app
settle(e, b, 91.0, card=card)
ok(e.state == 'DEPLOYED' and e.campaign_id,
   'ADOPT_POSITION: the bot takes over a hand-bought holding')
ok(e.events == [],
   'adopting writes NOTHING to the campaign log — the log is a record of '
   'trades, and adopting is not a trade', str(e.events))
ok(near(exits(e)[0][0], 90.0 * 1.05, 0.02),
   'the adopted campaign computes its EXIT from the broker average alone')

b.external_buy(85.0, 10)                     # a second hand buy
settle(e, b, 86.0, card=card)
ok(any(ev['source'] == 'EXT' for ev in e.events),
   'a hand buy is recorded as an external fill')
ok(near(exits(e)[0][0], b.avg * 1.05, 0.02)
   and near(line(e, 'chase')[0], b.avg * 0.94, 0.01),
   'both lines re-aim off the new broker average')

b.external_sell(89.0, 5)                     # a hand partial sell
settle(e, b, 89.0, card=card)                # still between the two lines
ok(e.manually_modified,
   'an external PARTIAL sell flags the campaign MANUALLY_MODIFIED')
ok(exits(e)[0][1] == b.shares,
   'the EXIT still covers the whole REMAINING position')

b.external_sell(95.0, b.shares)              # closed by hand
settle(e, b, 95.0, card=card)                # above the reload line
ok(e.campaign_id is None and not e.manually_modified,
   'a hand full-sell completes the campaign and clears the flag')

print('— the gear shifts freely, and is logged —')
e, b, card = fresh(gear=3)
settle(e, b, 92.0, card=card)
before_shares, before_avg, cid = b.shares, b.avg, e.campaign_id
card = {'gear': 5, 'exit_tiers': [False, False, True]}
settle(e, b, 92.0, card=card)
kinds = [ev['kind'] for ev in e.events]
ok(kinds == ['LOAD'],
   'shifting gear or tier writes NOTHING to the campaign log — only the '
   'LOAD that actually traded is in it', str(kinds))
ok(near(line(e, 'chase')[0], b.avg * 0.92, 0.01)
   and near(exits(e)[0][0], b.avg * 1.09, 0.02),
   'the new gear replaces both lines immediately')
ok(e.campaign_id == cid and b.shares == before_shares
   and near(b.avg, before_avg, 1e-9),
   'the shift keeps the holding, the average and the campaign id')
ok(near(line(e, 'chase2')[0], calc_chase_cascade(b.shares, b.avg, 5, 2)[1]['price'],
        0.02),
   'and the projected ladder follows the new gear too')

print('— the tiered exit: sell in portions, campaign ends only at zero —')
e, b, card = fresh(gear=3, tiers=(1, 1, 1))       # all three armed
settle(e, b, 92.0, card=card)
ok(b.shares == 11, 'the LOAD fills 11 shares')
ok([q for _p, q in exits(e)] == [4, 4, 3],
   '11 shares split across three tiers, the middle one taking the remainder',
   str(exits(e)))

t1, t2, t3 = (p for p, _q in exits(e))
ok(t1 < t2 < t3, 'the tiers ascend', f'{t1} {t2} {t3}')
settle(e, b, t1, card=card)
ok(b.shares == 7, 'T1 sells its portion and the rest stays held',
   f'{b.shares} sh')
ok(e.campaign_id is not None and e.state == 'DEPLOYED',
   'the campaign is NOT over — a portion left, not the position')
ok(e.tier_done[0] and not e.tier_done[1],
   'T1 is spent; T2 and T3 stay armed')
ok([q for _p, q in exits(e)] == [4, 3],
   'the remaining 7 shares re-split across the two tiers still armed',
   str(exits(e)))
ok(e.lines.get('chase'),
   'and the CHASE line is still there — a dip after a partial exit can still '
   'be bought')

# The dip arrives: buying more re-arms every tier on the bigger holding.
chase_p = line(e, 'chase')[0]
settle(e, b, chase_p, card=card)
ok(b.shares > 7, 'the chase fills after the partial exit', f'{b.shares} sh')
ok(not any(e.tier_done),
   'a chase re-arms EVERY tier — the ladder describes what is held now')
ok(len(exits(e)) == 3, 'all three exit lines are back')

# Take the rest out.
for _ in range(4):
    if b.shares <= 0:
        break
    settle(e, b, max(p for p, _q in exits(e)) if exits(e) else 200.0,
           card=card)
ok(b.shares == 0, 'the holding eventually reaches zero', f'{b.shares} sh')
ok(e.campaign_id is None,
   'and only THEN is the campaign over')
kinds = [ev['kind'] for ev in e.events]
ok(kinds and kinds[-1] == 'EXIT',
   'the last row of the log is the EXIT that emptied the position', str(kinds))

print('— one armed tier is still one clean shot —')
e, b, card = fresh(gear=3, tiers=(0, 1, 0))
settle(e, b, 92.0, card=card)
ok(len(exits(e)) == 1 and exits(e)[0][1] == b.shares,
   'a single armed tier carries the whole position', str(exits(e)))
settle(e, b, exits(e)[0][0], card=card)
ok(b.shares == 0 and e.campaign_id is None,
   'and empties it in one go')

print('— the bot re-prices its own resting orders —')
e, b, card = fresh(gear=3)
e.poll(snap(b, 92.0, card=card))              # LOAD fires, rests unfilled
b.place('BUY', 92.0, 11)
acts = e.poll(snap(b, 92.0, card=card))
ok(not [a for a in acts if a[0] == 'place'],
   'a resting order at the right line is left alone')

# The gear shifts: the resting order is now at the wrong price.
card = {'gear': 5, 'exit_tiers': [False, True, False]}
acts = e.poll(snap(b, 90.0, card=card))
ok([a for a in acts if a[0] == 'cancel'],
   'a resting order that no longer matches the line is cancelled BY THE BOT',
   str(acts))
for a in acts:
    if a[0] == 'cancel':
        b.cancel(a[1])
acts = e.poll(snap(b, 90.0, card=card))
placed = [a for a in acts if a[0] == 'place']
ok(placed and near(placed[0][2], 90.0, 0.01),
   'and the current line arms on the very next poll — no hand-holding',
   str(placed))

# Same story on the sell side after a chase moves the average.
e, b, card = fresh(gear=3)
settle(e, b, 92.0, card=card)
e.poll(snap(b, exits(e)[0][0], card=card))     # EXIT fires, rests
b.place('SELL', exits(e)[0][0] + 5, 11)        # ... at a stale price
acts = e.poll(snap(b, exits(e)[0][0], card=card))
ok(any(a[0] == 'cancel' and 'stale' in a[2] for a in acts),
   'a stale SELL is retired the same way', str(acts))

print('— a foreign order still blocks its side —')
e, b, card = fresh(gear=3)
b.orders['x1'] = {'side': 'BUY', 'price': 1.0, 'qty': 1}
snap_foreign = snap(b, 92.0, card=card)
for o in snap_foreign['orders']:
    o['mine'] = False
acts = e.poll(snap_foreign)
ok(not [a for a in acts if a[0] in ('place', 'cancel')],
   "someone else's order is never cancelled, and blocks the side", str(acts))

print('— WATCH sends nothing; LIVE sends by itself —')
e, b, card = fresh(gear=3)
placed = settle(e, b, 92.0, card=card, can_trade=False)
ok(not placed and b.shares == 0, 'WATCH never sends an order')
ok(e.crossed['BUY'] and e.crossed['BUY']['qty'] == 11,
   'the crossed line is still reported so the window can show it')
ok('not sent' in e.status, 'and the status says plainly why', e.status)
placed = settle(e, b, 92.0, card=card)
ok(placed and b.shares == 11,
   'LIVE sends the offer the moment the curve touches the line')

print('— one order at a time —')
e, b, card = fresh(gear=3)
e.poll(snap(b, 92.0, card=card))             # places the LOAD, no fill yet
b.place('BUY', 92.0, 11)
placed = [a for a in e.poll(snap(b, 92.0, card=card)) if a[0] == 'place']
ok(not placed, 'no second order while one already rests on that side')

print('— KR tick trimming —')
e, b, card = fresh(ticker=KR, gear=3)
placed = settle(e, b, 64_400.0, high5=70_000.0, unit=1_000_000.0, card=card)
ok(placed and all(p % 100 == 0 for _s, p, _q in placed),
   'KR limit prices snap to the tick grid', str(placed))
ok(all(p <= 70_000.0 * 0.92 for _s, p, _q in placed),
   'a trimmed BUY never bids above the strategy line')

print('— persistence across a restart —')
e, b, card = fresh(gear=4)
settle(e, b, 91.0, card=card)
saved = e.to_dict()
ok(saved['strategy'] == 'V_COMMANDOS_GEARBOX',
   'the saved record names its strategy so nothing else reads it')
e2 = CampaignEngine(T, trading_date=D1, saved=saved)
settle(e2, b, 91.0, card=card)
ok(e2.campaign_id == e.campaign_id and e2.gear == 4,
   'a restart re-arms the same campaign on the same gear')
ok(len(e2.events) == len(e.events),
   'the campaign fill log survives the restart')

print(f'\nALL {passed} CHECKS PASSED')
