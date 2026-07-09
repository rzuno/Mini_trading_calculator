"""Offline simulation of the Daily 443 autopilot engine (no network, no GUI).

Walks a full V-Commandos campaign on a fake KR stock:
    arm empty → load rests → load fills → sell rests → chase hits →
    chase fills → new sell → bounce → full sell → anchor reset → re-arm →
    reserve too low → STOP.
Run:  python scripts/test_daily443.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.autopilot as ap443
from core.autopilot import Daily443Engine, chase_qty, load_qty
from core.calc import trim_buy_price, trim_sell_price

T = '000660.KS'
LOG = []


def snap(price, shares=0, avg=0.0, orders=(), bp=5_000_000, unit=1_000_000,
         date='2026-07-09', prev_close=None):
    return {'price': price, 'shares': shares, 'avg_cost': avg,
            'orders': list(orders), 'buying_power': bp, 'unit_cash': unit,
            'trading_date': date, 'prev_close': prev_close}


def order(oid, side, price, qty, filled=0):
    return {'id': oid, 'side': side, 'price': price, 'qty_open': qty,
            'filled': filled}


def places(acts):  return [a for a in acts if a[0] == 'place']
def cancels(acts): return [a for a in acts if a[0] == 'cancel']
def stops(acts):   return [a for a in acts if a[0] == 'stop']


def check(name, cond, detail=''):
    status = 'ok' if cond else 'FAIL'
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ''))
    if not cond:
        raise SystemExit(f"FAILED: {name} {detail}")


print("-- quantity rules (S30.2) --")
seq = {1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 3, 7: 3, 9: 4, 13: 6}
for held, want in seq.items():
    check(f'chase_qty({held}) == {want}', chase_qty(held) == want,
          f'got {chase_qty(held)}')
check('chase_qty(0) == 0', chase_qty(0) == 0)
check('load_qty: 1 unit floors', load_qty(1_000_000, 96_000) == 10)
check('load_qty: min 1 share', load_qty(1_000_000, 2_900_000) == 1)

print("-- price trimming --")
check('KR buy floors to tick', trim_buy_price(T, 92_160) == 92_100)
check('KR sell ceils to tick', trim_sell_price(T, 98_880) == 98_900)
check('KR big-band buy floor', trim_buy_price(T, 1_550_433.14) == 1_550_000)
check('US buy floors to cent', trim_buy_price('NVDA', 187.6789) == 187.67)
check('US sell ceils to cent', trim_sell_price('NVDA', 187.6712) == 187.68)

print("-- campaign: arm empty → load ladder --")
eng = Daily443Engine(T, anchor=100_000, trading_date='2026-07-09',
                     log=lambda m: LOG.append(m))
acts = eng.poll(snap(price=99_000))
check('state EMPTY', eng.state == 'EMPTY')
check('places one LOAD buy', len(places(acts)) == 1 and not cancels(acts))
_, side, p, q, lbl = places(acts)[0]
check('load = anchor×0.96 floored', (side, p, q) == ('BUY', 96_000, 10),
      f'got {side} {p} ×{q}')

print("-- load resting: no churn --")
acts = eng.poll(snap(price=97_000, orders=[order('b1', 'BUY', 96_000, 10)]))
check('no actions while resting', not acts)

print("-- anchor changed → load replaced --")
eng.anchor = 98_000   # e.g. intraday re-arm at a lower anchor
acts = eng.poll(snap(price=97_000, orders=[order('b1', 'BUY', 96_000, 10)]))
check('stale load cancelled+replaced',
      len(cancels(acts)) == 1 and len(places(acts)) == 1)
check('new load price = 98,000×0.96 floored',
      places(acts)[0][2] == trim_buy_price(T, 98_000 * 0.96))
eng.anchor = 100_000  # back to the scenario

print("-- load fills → DEPLOYED, sell goes up --")
acts = eng.poll(snap(price=95_900, shares=10, avg=96_000))
check('state DEPLOYED', eng.state == 'DEPLOYED')
check('LOAD fill logged', any('LOAD filled' in m for m in LOG))
check('places SELL all', places(acts)[0][1:4] == ('SELL', 98_900, 10),
      f'got {places(acts)}')

print("-- sell resting, price between lines: no churn --")
s1 = order('s1', 'SELL', 98_900, 10)
acts = eng.poll(snap(price=95_000, shares=10, avg=96_000, orders=[s1]))
check('no actions between lines', not acts)
check('chase line computed', eng.lines['chase'] == (92_100, 5),
      f"got {eng.lines['chase']}")

print("-- chase hit → swap sides --")
acts = eng.poll(snap(price=92_000, shares=10, avg=96_000, orders=[s1]))
check('cancels sell + places chase buy',
      len(cancels(acts)) == 1 and places(acts)[0][1:4] == ('BUY', 92_100, 5),
      f'got {acts}')

print("-- chase fills → new sell from the new avg --")
acts = eng.poll(snap(price=92_300, shares=15, avg=94_700))
check('CHASE fill logged + counted',
      eng.chase_count == 1 and any('CHASE filled' in m for m in LOG))
check('new sell = 94,700×1.03 ceiled ×15',
      places(acts)[0][1:4] == ('SELL', 97_600, 15), f'got {places(acts)}')

print("-- bounce while a buy rests: exit takes priority --")
eng2 = Daily443Engine(T, anchor=100_000, trading_date='2026-07-09')
eng2.poll(snap(price=95_000, shares=10, avg=96_000))          # arms DEPLOYED
b2 = order('b2', 'BUY', 92_100, 5)
acts = eng2.poll(snap(price=99_000, shares=10, avg=96_000, orders=[b2]))
check('cancels buy, places sell',
      cancels(acts)[0][1] == 'b2' and places(acts)[0][1] == 'SELL',
      f'got {acts}')

print("-- full sell → anchor = sell price, RELOAD at -3% (S30.7) --")
s2 = order('s2', 'SELL', 97_600, 15)
eng.poll(snap(price=97_000, shares=15, avg=94_700, orders=[s2]))  # adopt sell
acts = eng.poll(snap(price=97_700, shares=0, avg=0.0))
check('anchor = last sell price', eng.anchor == 97_600, f'got {eng.anchor}')
check('anchor source = sell', eng.anchor_source == 'sell',
      eng.anchor_source)
check('re-arms EMPTY and places new load',
      eng.state == 'EMPTY' and places(acts)[0][1] == 'BUY')
check('reload at -3% of the sell (94,600), not -4%',
      places(acts)[0][2] == trim_buy_price(T, 97_600 * 0.97) == 94_600,
      f'got {places(acts)[0][2]}')
check('pseudo sell line above the load (97,500)',
      eng.lines['psell'][0] == trim_sell_price(T, 94_600 * 1.03) == 97_500,
      f"got {eng.lines.get('psell')}")

print("-- campaign fill log (events) --")
kinds = [e['kind'] for e in eng.events]
check('events LOAD → CHASE → SELL', kinds == ['LOAD', 'CHASE', 'SELL'],
      str(kinds))
check('load event: +10 @ 96,000',
      eng.events[0]['qty'] == 10 and eng.events[0]['price'] == 96_000,
      str(eng.events[0]))
check('chase event price backed out of avg (92,100)',
      abs(eng.events[1]['price'] - 92_100) < 1, str(eng.events[1]))
check('sell event: -15 @ 97,600',
      eng.events[2]['qty'] == -15 and eng.events[2]['price'] == 97_600,
      str(eng.events[2]))

print("-- day rollover (S6): next day anchor = prev close, -4% again --")
acts = eng.poll(snap(price=98_000, date='2026-07-10', prev_close=99_000,
                     orders=[order('b3', 'BUY', 94_600, 10)]))
check('anchor = prev close', eng.anchor == 99_000, f'got {eng.anchor}')
check('anchor source back to close', eng.anchor_source == 'close')
check('chase count reset', eng.chase_count == 0)
check('events cleared for the new day', eng.events == [])
check('stale -3% load replaced by the -4% day load (95,000)',
      len(cancels(acts)) == 1
      and places(acts)[0][2] == trim_buy_price(T, 99_000 * 0.96) == 95_000,
      f'got {acts}')

print("-- reserve gate UNLOCKED (S30.7): low reserve does NOT stop --")
check('gate is off by default', ap443.RESERVE_GATE is False)
acts = eng.poll(snap(price=98_000, bp=100_000))
check('keeps placing despite low reserve',
      not stops(acts) and eng.state == 'EMPTY', f'got {acts}')

print("-- reserve stop still works when the gate is re-enabled --")
ap443.RESERVE_GATE = True
acts = eng.poll(snap(price=98_000, bp=100_000))
check('stop emitted with gate on', len(stops(acts)) == 1, f'got {acts}')
check('state STOPPED', eng.state == 'STOPPED')
check('no more actions after stop', eng.poll(snap(price=90_000)) == [])

print("-- reserve stop on chase, sell left resting (gate on) --")
eng3 = Daily443Engine(T, anchor=100_000, trading_date='2026-07-09')
eng3.poll(snap(price=95_000, shares=10, avg=96_000))
s3 = order('s3', 'SELL', 98_900, 10)
acts = eng3.poll(snap(price=92_000, shares=10, avg=96_000, orders=[s3],
                      bp=10_000))
check('stops instead of chasing', len(stops(acts)) == 1 and not places(acts),
      f'got {acts}')
check('sell NOT cancelled (exit door stays)', not cancels(acts))
ap443.RESERVE_GATE = False

print("\nAll Daily 443 engine checks passed.")
