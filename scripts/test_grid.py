"""Offline simulation of the Daily v^ Linear Weighted Grid engine.

Run:  python scripts/test_grid.py     (no network, no tkinter)

A FakeBroker fills resting limit orders whenever the simulated price
crosses them, so whole price paths run through the real engine exactly as
the live watcher would see them (one action per poll, fill-confirmed level
advancement, share-diff detection).

Covers the manual's deterministic paths (flat, V tooth, ^ tooth, deep V,
deep ^, repeated tooth, one-way moves), the cap-5 zone edges, opening
gaps, the nothing-to-buy / nothing-to-sell rules, partial-fill self-heal,
foreign-order pause, WATCH triggers + manual fire, the daily rebase,
persistence, KR tick trimming, and a random-walk fuzz with invariants.
"""

import os
import random
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.autopilot import (GridEngine, LEVEL_CAP, GRID_STEP_PCT,
                            grid_offsets, cum_weight, level_raw_price,
                            target_inventory)

T = 'TEST'                 # USD-style ticker: cent trims keep prices exact
D1, D2 = '2026-07-21', '2026-07-22'

passed = 0


def ok(cond, name, info=''):
    global passed
    assert cond, f'FAIL: {name} {info}'
    passed += 1
    print(f'  ok  {name}')


# ── Fake broker: resting limits fill on a price cross ────────────────────────

class FakeBroker:
    def __init__(self, shares=0, cash=None):
        self.shares = int(shares)
        self.cash = cash            # None = unlimited (bp unknown)
        self.orders = {}
        self._next = 1

    def place(self, side, price, qty):
        oid = f'f{self._next}'
        self._next += 1
        self.orders[oid] = {'side': side, 'price': price, 'qty': int(qty)}
        return oid

    def on_price(self, price):
        for oid, o in list(self.orders.items()):
            if o['side'] == 'BUY' and price <= o['price']:
                self.shares += o['qty']
                if self.cash is not None:
                    self.cash -= o['price'] * o['qty']
                    assert self.cash > -1e-6, 'broker cash went negative!'
                del self.orders[oid]
            elif o['side'] == 'SELL' and price >= o['price']:
                assert self.shares >= o['qty'], 'oversold!'
                self.shares -= o['qty']
                if self.cash is not None:
                    self.cash += o['price'] * o['qty']
                del self.orders[oid]

    def partial_fill(self, qty):
        """Fill only `qty` of the single resting order, then kill it."""
        oid, o = next(iter(self.orders.items()))
        if o['side'] == 'BUY':
            self.shares += qty
            if self.cash is not None:
                self.cash -= o['price'] * qty
        else:
            self.shares -= qty
            if self.cash is not None:
                self.cash += o['price'] * qty
        del self.orders[oid]

    def open_orders(self):
        return [{'id': oid, 'side': o['side'], 'price': o['price'],
                 'qty_open': o['qty'], 'filled': 0, 'mine': True}
                for oid, o in self.orders.items()]


def make_snap(price, broker, date=D1, prev_close=100.0, unit_cash=100.0,
              phase='REGULAR', can=True, extra_orders=()):
    return {'price': price, 'shares': broker.shares, 'avg_cost': 0.0,
            'orders': broker.open_orders() + list(extra_orders),
            'buying_power': broker.cash, 'unit_cash': unit_cash,
            'trading_date': date, 'prev_close': prev_close,
            'phase': phase, 'can_trade': can}


def settle(engine, broker, price, max_polls=40, **kw):
    """Poll at one price until nothing more happens (the watcher would do
    the same over consecutive 10-second ticks). A resting unfilled limit —
    e.g. a KR BUY trimmed one tick below the raw line — is a valid steady
    state: the bot waits for the fill. Returns the placed acts."""
    placed = []
    quiet = 0
    for _ in range(max_polls):
        before = (broker.shares, len(broker.orders))
        acts = engine.poll(make_snap(price, broker, **kw))
        for a in acts:
            assert a[0] == 'place', f'unexpected act {a[0]}'
            placed.append(a)
            broker.place(a[1], a[2], a[3])
        broker.on_price(price)
        quiet = (quiet + 1 if (not acts
                               and (broker.shares, len(broker.orders))
                               == before) else 0)
        if quiet >= 2:
            break
    else:
        raise AssertionError('settle() did not converge')
    return placed


def run_path(engine, broker, prices, **kw):
    """[(price, inventory_after, orders_placed)] for a whole path."""
    out = []
    for p in prices:
        placed = settle(engine, broker, p, **kw)
        out.append((p, broker.shares, placed))
    return out


# ── Pure grid math ───────────────────────────────────────────────────────────
print('— grid math (cap 5, 3% arithmetic, weights 1..5) —')
ok(LEVEL_CAP == 5 and abs(GRID_STEP_PCT - 0.03) < 1e-12,
   'cap 5, 3% step')
ok(grid_offsets() == [0.03, 0.06, 0.09, 0.12, 0.15],
   'offsets 3/6/9/12/15%')
ok([cum_weight(n) for n in range(6)] == [0, 1, 3, 6, 10, 15],
   'W(n) = 0,1,3,6,10,15')
def near(a, b, eps=1e-6):
    return abs(a - b) < eps


ok(near(level_raw_price(100, 1), 103) and near(level_raw_price(100, -3), 91)
   and near(level_raw_price(100, 5), 115)
   and near(level_raw_price(100, -5), 85),
   'level prices at anchor 100')
tg = [target_inventory(k, 10, 1) for k in range(-5, 6)]
ok(tg == [25, 20, 16, 13, 11, 10, 9, 7, 4, 0, 0],
   'targets base 10 u1: -5→25 … +4/+5 clamp at 0', str(tg))
ok(target_inventory(-5, 10, 1, maximum=18) == 18, 'maximum clamp')
ok(target_inventory(5, 10, 1, minimum=4) == 4, 'minimum clamp')

# ── Session init ─────────────────────────────────────────────────────────────
print('— adventure init —')
e = GridEngine(T, trading_date=D1)
b = FakeBroker(shares=10)
settle(e, b, 100.0)
ok(e.grid_ready and e.anchor == 100.0 and e.current_level == 0
   and e.base_inventory == 10 and e.unit_qty == 1,
   'no gap: anchor = prev close, level 0, unit 1')
ok([g['level'] for g in e.grid] == list(range(5, -6, -1)),
   'grid exposes +5 … -5')

e2 = GridEngine(T, trading_date=D1)
b2 = FakeBroker(shares=10)
acts = e2.poll(make_snap(99.0, b2, phase='PRE'))
ok(not e2.grid_ready and e2.state == 'WAIT_OPEN' and not acts,
   'before the regular open: grid waits (preview only)')

# ── Deterministic paths (manual §26) ────────────────────────────────────────
print('— deterministic paths —')


def fresh(shares=10, cash=None):
    return GridEngine(T, trading_date=D1), FakeBroker(shares, cash)


e, b = fresh()
run_path(e, b, [100, 102.9, 97.1, 100])
ok(b.shares == 10 and e.fills == 0, 'flat market inside ±1: no orders')

e, b = fresh()
r = run_path(e, b, [100, 97, 100])
ok([x[1] for x in r] == [10, 11, 10], 'V tooth: BUY 1 then SELL 1')
ok(e.sell_value - e.buy_value == 100 - 97, 'V tooth harvests the gap')

e, b = fresh()
r = run_path(e, b, [100, 103, 100])
ok([x[1] for x in r] == [10, 9, 10], '^ tooth: SELL 1 then REBUY 1')
ok(e.sell_value - e.buy_value == 3, '^ tooth harvests the gap too')

e, b = fresh()
r = run_path(e, b, [100, 97, 94, 91, 94, 97, 100])
ok([x[1] for x in r] == [10, 11, 13, 16, 13, 11, 10],
   'deep V: 10→11→13→16→13→11→10')

e, b = fresh()
r = run_path(e, b, [100, 103, 106, 109, 106, 103, 100])
ok([x[1] for x in r] == [10, 9, 7, 4, 7, 9, 10],
   'deep ^: 10→9→7→4→7→9→10')

e, b = fresh()
run_path(e, b, [100, 97, 94, 91])
r = run_path(e, b, [94, 91, 94, 91, 94])
ok([x[1] for x in r] == [13, 16, 13, 16, 13],
   'repeated -2↔-3 tooth trades 3 units every pass')

# ── Cap 5: the zone edge ─────────────────────────────────────────────────────
print('— cap 5 zone —')
e, b = fresh()
r = run_path(e, b, [100, 97, 94, 91, 88, 85])
ok([x[1] for x in r] == [10, 11, 13, 16, 20, 25],
   'full depth: +1+2+3+4+5 units down to L-5')
placed = settle(e, b, 80.0)
ok(not placed and e.current_level == -5 and b.shares == 25,
   'below L-5 nothing is chased (outside the zone)')
placed = settle(e, b, 88.0)
ok(b.shares == 20 and e.current_level == -4,
   'back inside: L-4 sells 5 again')

e, b = fresh()
r = run_path(e, b, [100, 103, 106, 109, 112, 115, 118])
ok([x[1] for x in r] == [10, 9, 7, 4, 0, 0, 0],
   'up: sells stop at zero inventory, levels advance silently')
ok(e.current_level == 5, 'level parked at +5 above the zone')
r = run_path(e, b, [112, 109])
ok(b.shares == 4 and e.current_level == 3,
   'coming down: +4 silent (target 0), +3 REBUYS 4')

# ── The user's nothing-to-sell scenario (base 0) ────────────────────────────
print('— nothing to sell / nothing to buy —')
e, b = fresh(shares=0)
r = run_path(e, b, [100, 103, 106, 103, 100, 97, 100])
ok([x[1] for x in r] == [0, 0, 0, 0, 0, 1, 0],
   '+1/+2 do nothing, -1 buys, back at 0 the sell works again')

e, b = fresh(shares=10, cash=50.0)          # cannot fund BUY 1 @ 97
settle(e, b, 100.0)                          # normal init at level 0
placed = settle(e, b, 97.0)
ok(not placed and e.current_level == 0 and b.shares == 10,
   'no cash: the -1 transition is NOT taken (level stays)')
ok(e.trigger_note and 'buy skipped' in e.trigger_note,
   'trigger note explains the skipped buy', str(e.trigger_note))
placed = settle(e, b, 103.0)                 # sell side still watched
ok(b.shares == 9 and e.current_level == 1,
   'sell still fires while the buy side is broke')
ok(b.cash == 50.0 + 103.0, 'the sell refills the army')
placed = settle(e, b, 100.0)
ok(b.shares == 10 and e.current_level == 0,
   'with cash back, the next buy fires by itself')

# ── One poll = one action (gap through several levels) ──────────────────────
print('— multi-level drop —')
e, b = fresh()
settle(e, b, 100.0)
placed = settle(e, b, 91.0)
ok(len(placed) == 3 and [a[3] for a in placed] == [1, 2, 3],
   'a straight drop to L-3 steps one adjacent order at a time')
ok(b.shares == 16 and e.current_level == -3, 'ends reconciled at L-3')

# ── Opening gaps (compressed one-level) ─────────────────────────────────────
print('— opening gaps —')
e, b = fresh()
settle(e, b, 90.0)                           # first regular quote -10%
ok(e.gap_mode == 'DOWN' and e.current_level == -1,
   'down gap: opening price becomes L-1')
ok(abs(e.anchor - 90.0 / 0.97) < 1e-9, 'compressed anchor 90/0.97')
ok(b.shares == 11, 'only the minimum weight-1 BUY (no catch-up)')

e, b = fresh()
settle(e, b, 110.0)
ok(e.gap_mode == 'UP' and e.current_level == 1
   and abs(e.anchor - 110.0 / 1.03) < 1e-9 and b.shares == 9,
   'up gap: L+1, anchor 110/1.03, minimum SELL 1')

e, b = fresh()
settle(e, b, 98.0)
ok(e.gap_mode == 'NONE' and e.current_level == 0 and b.shares == 10,
   'small gap (<3%): normal init, no order')

e, b = fresh(shares=10, cash=10.0)           # gap BUY unaffordable
settle(e, b, 90.0)
ok(b.shares == 10 and e.gap_pending and e.current_level == -1,
   'gap BUY without army stays due (no fake fill, no level lie)')
b.cash = 500.0
settle(e, b, 90.0)
ok(b.shares == 11 and not e.gap_pending,
   'gap BUY fires by itself when the army returns')

# ── Partial fill self-heal ───────────────────────────────────────────────────
print('— partial fills —')
e, b = fresh()
settle(e, b, 100.0)
acts = e.poll(make_snap(94.0, b))            # L-1 BUY 1 fires
b.place(acts[0][1], acts[0][2], acts[0][3])
b.partial_fill(0)                            # order dies unfilled
settle(e, b, 96.9)                           # still below the -1 line
ok(b.shares == 11 and e.current_level == -1,
   'a dead order re-fires on the next touch')
acts = e.poll(make_snap(94.0, b))            # L-2 BUY 2 fires
b.place(acts[0][1], acts[0][2], acts[0][3])
b.partial_fill(1)                            # HALF fill, order gone
e.poll(make_snap(94.0, b))                   # detect the partial
ok(e.current_level == -1 and b.shares == 12,
   'partial fill does NOT advance the level')
settle(e, b, 94.0)
ok(b.shares == 13 and e.current_level == -2,
   'the remainder re-orders and completes the level')

# ── Foreign order pause ──────────────────────────────────────────────────────
print('— foreign orders —')
e, b = fresh()
settle(e, b, 100.0)
foreign = [{'id': 'web1', 'side': 'SELL', 'price': 105.0, 'qty_open': 2,
            'filled': 0, 'mine': False}]
acts = e.poll(make_snap(97.0, b, extra_orders=foreign))
ok(not acts and 'not mine' not in e.status and 'paused' in e.status,
   'foreign resting order pauses new transitions', e.status)
settle(e, b, 97.0)
ok(b.shares == 11, 'clears and resumes when the foreign order is gone')

# ── WATCH mode triggers + manual fire ───────────────────────────────────────
print('— WATCH / manual —')
e, b = fresh()
settle(e, b, 100.0)
acts = e.poll(make_snap(97.0, b, can=False))
ok(not acts and e.trigger['BUY']
   and e.trigger['BUY']['level'] == -1 and e.trigger['BUY']['qty'] == 1,
   'WATCH exposes the due transition instead of placing')
t = e.trigger['BUY']
b.place('BUY', t['price'], t['qty'])
e.note_manual_order('BUY', t['price'], t['qty'],
                    level=t['level'], target=t['target'])
b.on_price(97.0)
e.poll(make_snap(97.0, b, can=False))
ok(e.current_level == -1 and b.shares == 11,
   'manual fire advances the level exactly like a bot order')

# ── Daily rebase (adventure rollover) ───────────────────────────────────────
print('— daily rebase —')
e, b = fresh()
run_path(e, b, [100, 97, 94, 91])
ok(b.shares == 16 and e.fills == 3 and len(e.events) == 3,
   'day 1 ends at L-3 with 16 sh')
settle(e, b, 91.0, date=D2, prev_close=91.0)
ok(e.grid_ready and e.anchor == 91.0 and e.current_level == 0
   and e.base_inventory == 16 and e.events == [] and e.fills == 0,
   'day 2: fresh adventure — base 16, level 0, day log cleared')
settle(e, b, 93.73, date=D2, prev_close=91.0)   # 91×1.03 = 93.73
ok(b.shares < 16, "yesterday's deep buys sell on today's upper grid")

# ── Persistence ──────────────────────────────────────────────────────────────
print('— persistence —')
e, b = fresh()
run_path(e, b, [100, 97, 94])
d = e.to_dict()
e2 = GridEngine(T, saved=d)
ok(e2.grid_ready and e2.anchor == 100.0 and e2.current_level == -2
   and e2.base_inventory == 10 and e2.unit_qty == 1,
   'restart mid-adventure restores anchor/level/base/unit')
settle(e2, b, 91.0)
ok(b.shares == 16 and e2.current_level == -3,
   'restored engine keeps trading the same grid')
legacy = {'anchor': 98800, 'anchor_source': 'sell', 'tier_done': [0, 0, 0]}
e3 = GridEngine(T, saved=legacy)
settle(e3, FakeBroker(5), 100.0)
ok(e3.grid_ready and e3.anchor == 100.0,
   'legacy (line-watcher) store entries fall back to a fresh init')

# ── KR tick trimming ────────────────────────────────────────────────────────
print('— KR ticks —')
ek = GridEngine('005930.KS', trading_date=D1)
bk = FakeBroker(shares=10)
placed = []
# 70,050×0.97 = 67,948.5 → the BUY trims DOWN to the 67,900 tick and rests
# until price actually reaches it (never bids above the strategy line).
for p in (70050.0, 67940.0, 67900.0):
    placed += settle(ek, bk, p, prev_close=70050.0, unit_cash=70050.0)
ok(placed and all(pl[2] % 100 == 0 for pl in placed),
   'KR order prices snap to the tick grid', str(placed))
ok(bk.shares == 11 and ek.current_level == -1,
   'the resting tick-trimmed BUY fills when its tick trades')

# ── Random-walk fuzz with invariants ────────────────────────────────────────
print('— fuzz —')
random.seed(20260721)
for trial, cash in ((1, None), (2, 1500.0)):
    e, b = fresh(shares=10, cash=cash)
    start_cash = cash
    price = 100.0
    settle(e, b, price)
    for i in range(1500):
        price = max(60.0, min(140.0, price * (1 + random.uniform(-0.012,
                                                                 0.012))))
        settle(e, b, round(price, 2))
        assert -LEVEL_CAP <= e.current_level <= LEVEL_CAP
        assert 0 <= b.shares <= target_inventory(-LEVEL_CAP, 10, e.unit_qty)
        if cash is not None:
            assert b.cash > -1e-6
    if cash is not None:
        drift = abs((start_cash + e.sell_value - e.buy_value) - b.cash)
        ok(drift < 1e-6, f'fuzz {trial}: engine ledger matches broker cash')
    ok(True, f'fuzz {trial}: 1500 random steps, all invariants held '
             f'({e.fills} fills, end L{e.current_level:+d}, '
             f'{b.shares} sh)')

print(f'\nALL {passed} CHECKS PASSED')
