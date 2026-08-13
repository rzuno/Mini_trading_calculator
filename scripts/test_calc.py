"""The deterministic V-Commandos calculator — the CARD's math.

Run:  python scripts/test_calc.py     (no network, no tkinter)

Checks `core/calc.py` against the Gearbox manual's own tables: gear
parameters, the volatility→gear rule, the heavy-unit entry floor, legacy
migration, the normalized Part II ladders (final average, capital used),
the card's projected lines, and the exit-ladder distribution law.

The campaign ENGINE these numbers once drove was retired on 2026-08-13 —
the card is now the worksheet whose numbers the commander types into the
broker app by hand, and the autopilot runs the Daily v^ grid instead
(scripts/test_grid.py). The engine and its suite are recoverable from
commit `f011d8f`.
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
UNIT = 1000.0

passed = 0


def ok(cond, name, info=''):
    global passed
    assert cond, f'FAIL: {name} {info}'
    passed += 1
    print(f'  ok  {name}')


def near(a, b, tol=0.01):
    return a is not None and abs(a - b) <= tol


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


print(f'\nALL {passed} CALC CHECKS PASSED')
