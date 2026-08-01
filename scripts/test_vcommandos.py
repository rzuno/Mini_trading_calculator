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
                       calc_volatility, chase_drop, effective_entry_gear,
                       exit_pct, load_drop, normalize_gear, select_auto_gear,
                       tier_for_exit_pct, weight_min_gear)
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
   'the EXIT is the WHOLE position at the selected tier')
tiers = calc_exit_lines(11, 92.0, 3)
ok([t['qty'] for t in tiers] == [11, 11, 11],
   'every displayed tier carries the whole position — no 33/33/34 split')

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
         card=None, can_trade=True):
    return {'price': price, 'shares': b.shares, 'avg_cost': b.avg,
            'orders': b.open_orders(), 'buying_power': b.cash,
            'unit_cash': unit, 'trading_date': date,
            'high5': high5, 'prev_close': prev_close or high5,
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


def fresh(ticker=T, shares=0, avg=0.0, cash=None, gear=3, tier=2):
    b = FakeBroker(shares, avg, cash)
    e = CampaignEngine(ticker, trading_date=D1)
    return e, b, {'gear': gear, 'exit_tier': tier}


def line(e, key):
    """(price, qty) of a published line, or (None, None)."""
    v = e.lines.get(key)
    return (v['price'], v['qty']) if v else (None, None)


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
ok(near(line(e, 'exit')[0], 92.0 * 1.05, 0.02)
   and line(e, 'exit')[1] == b.shares,
   'the EXIT is the whole position at T2 +5%', str(e.lines.get('exit')))

chase_p, chase_q = line(e, 'chase')
ok(near(chase_p, 92.0 * 0.94, 0.01) and chase_q == 8,
   'the CHASE hangs -6% below the broker average, ×3/4')
settle(e, b, chase_p, card=card)
ok(b.shares == 19 and e.chase_count == 1,
   'the chase fills and the average drops', f'{b.shares} sh @ {b.avg:.2f}')
ok(near(line(e, 'exit')[0], b.avg * 1.05, 0.02),
   'the EXIT re-aims off the NEW broker average')

exit_p = line(e, 'exit')[0]
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
ok(flat_e.lines['load']['armed'] and not flat_e.lines['pexit']['armed'],
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
exit_p = line(e, 'exit')[0]
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

print('— the reload never crosses into a new day —')
e, b, card = fresh(gear=3)
settle(e, b, 92.0, card=card)
settle(e, b, line(e, 'exit')[0], card=card)
ok(e.vantage_src == 'reload', 'reload armed at the close of day 1')
settle(e, b, 95.0, date=D2, high5=101.0, card=card)
ok(e.vantage_src == 'high5' and near(e.vantage, 101.0),
   'day 2 discards the reload and returns to the rolling High5')

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
ok('exit' in e.lines, 'an exhausted campaign keeps watching its EXIT')

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
ok(any(ev['kind'] == 'ADOPT' for ev in e.events),
   'the adoption is the first row of the campaign log')
ok(near(line(e, 'exit')[0], 90.0 * 1.05, 0.02),
   'the adopted campaign computes its EXIT from the broker average alone')

b.external_buy(85.0, 10)                     # a second hand buy
settle(e, b, 86.0, card=card)
ok(any(ev['source'] == 'EXT' for ev in e.events),
   'a hand buy is recorded as an external fill')
ok(near(line(e, 'exit')[0], b.avg * 1.05, 0.02)
   and near(line(e, 'chase')[0], b.avg * 0.94, 0.01),
   'both lines re-aim off the new broker average')

b.external_sell(89.0, 5)                     # a hand partial sell
settle(e, b, 89.0, card=card)                # still between the two lines
ok(e.manually_modified,
   'an external PARTIAL sell flags the campaign MANUALLY_MODIFIED')
ok(line(e, 'exit')[1] == b.shares,
   'the EXIT still covers the whole REMAINING position')

b.external_sell(95.0, b.shares)              # closed by hand
settle(e, b, 95.0, card=card)                # above the reload line
ok(e.campaign_id is None and not e.manually_modified,
   'a hand full-sell completes the campaign and clears the flag')

print('— the gear shifts freely, and is logged —')
e, b, card = fresh(gear=3)
settle(e, b, 92.0, card=card)
before_shares, before_avg, cid = b.shares, b.avg, e.campaign_id
card = {'gear': 5, 'exit_tier': 3}
settle(e, b, 92.0, card=card)
kinds = [ev['kind'] for ev in e.events]
ok('GEAR' in kinds and 'TIER' in kinds,
   'shifting gear or tier mid-campaign writes a log row', str(kinds))
ok(near(line(e, 'chase')[0], b.avg * 0.92, 0.01)
   and near(line(e, 'exit')[0], b.avg * 1.09, 0.02),
   'the new gear replaces both lines immediately')
ok(e.campaign_id == cid and b.shares == before_shares
   and near(b.avg, before_avg, 1e-9),
   'the shift keeps the holding, the average and the campaign id')
ok(near(line(e, 'chase2')[0], calc_chase_cascade(b.shares, b.avg, 5, 2)[1]['price'],
        0.02),
   'and the projected ladder follows the new gear too')

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
