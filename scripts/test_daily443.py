"""Offline simulation of the ADAPTIVE-GEAR watcher engine (no network/GUI).

Covers: gear selection by deployment ratio, the exact 1/3/5-share integer
overrides, half-up quantities, load sizing, the watcher cycle (fire only on
touch), gear movement as the position grows, the Global Army Manager
(0.5-unit reserve, B1 fallback, EXHAUSTED + recovery + single notify),
campaign log clearing, -3% reload, day rollover, and market phases.

Run:  python scripts/test_daily443.py
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.autopilot as ap
from core.autopilot import (Daily443Engine, buy_gear_for, sell_gear_for,
                            chase_qty, load_qty)
from core.calc import trim_buy_price, trim_sell_price
from gui.autopilot_ctrl import market_phase

T = '000660.KS'
LOG = []


def snap(price, shares=0, avg=0.0, orders=(), bp=50_000_000, unit=1_000_000,
         army=30.0, date='2026-07-09', prev_close=None, can_trade=True):
    return {'price': price, 'shares': shares, 'avg_cost': avg,
            'orders': list(orders), 'buying_power': bp, 'unit_cash': unit,
            'army_units': army, 'trading_date': date,
            'prev_close': prev_close, 'can_trade': can_trade}


def order(oid, side, price, qty, filled=0, mine=True):
    return {'id': oid, 'side': side, 'price': price, 'qty_open': qty,
            'filled': filled, 'mine': mine}


def places(acts):   return [a for a in acts if a[0] == 'place']
def cancels(acts):  return [a for a in acts if a[0] == 'cancel']
def notifies(acts): return [a for a in acts if a[0] == 'notify']


def check(name, cond, detail=''):
    status = 'ok' if cond else 'FAIL'
    detail = str(detail).encode('ascii', 'replace').decode()  # cp949 console
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ''))
    if not cond:
        raise SystemExit(f"FAILED: {name} {detail}")


print("-- gear selection by deployment ratio (PART II S6/S10) --")
check('buy: 10% -> B1', buy_gear_for(0.10, 20) == 1)
check('buy: 25% -> B2', buy_gear_for(0.25, 20) == 2)
check('buy: 49% -> B2', buy_gear_for(0.49, 20) == 2)
check('buy: 50% -> B3', buy_gear_for(0.50, 20) == 3)
check('sell: 10% -> S3', sell_gear_for(0.10) == 3)
check('sell: 30% -> S2', sell_gear_for(0.30) == 2)
check('sell: 60% -> S1', sell_gear_for(0.60) == 1)

print("-- exact early integer overrides (PART II S7) --")
check('1 share -> B3 (big stock buys 1 deeper at -6%)',
      buy_gear_for(0.05, 1) == 3)
check('3 shares -> B2 (3x2/3 = exact 2)', buy_gear_for(0.05, 3) == 2)
check('5 shares -> B2 (3 at deeper price)', buy_gear_for(0.60, 5) == 2)
check('2 shares follow the zone', buy_gear_for(0.05, 2) == 1)
check('7 shares follow the zone', buy_gear_for(0.60, 7) == 3)

print("-- half-up quantities (PART II S8) --")
check('7 x1/2 = 3.5 -> 4', chase_qty(7, 1 / 2) == 4)
check('7 x2/3 = 4.67 -> 5', chase_qty(7, 2 / 3) == 5)
check('7 x3/4 = 5.25 -> 5', chase_qty(7, 3 / 4) == 5)
check('1 share -> min 1', chase_qty(1, 1 / 2) == 1)
check('load: closest to one unit (10.4 -> 10)',
      load_qty(1_000_000, 96_000) == 10)
check('load: 1.6 -> 2 shares', load_qty(1_000_000, 640_000) == 2)
check('load: big stock min 1', load_qty(1_000_000, 2_900_000) == 1)

print("-- market phases --")


def utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


check('KR 10:00 KST = REGULAR',
      market_phase(T, utc(2026, 7, 14, 1, 0)) == 'REGULAR')
check('KR Saturday = CLOSED',
      market_phase(T, utc(2026, 7, 18, 1, 0)) == 'CLOSED')
check('US Jul 10:00 ET = REGULAR',
      market_phase('NVDA', utc(2026, 7, 14, 14, 0)) == 'REGULAR')

print("-- light position: B1/S3 zone (the old 443) --")
eng = Daily443Engine(T, anchor=100_000, trading_date='2026-07-09',
                     log=lambda m: LOG.append(m))
acts = eng.poll(snap(price=99_000))
check('EMPTY watches load -4% (96,000 x10)',
      not acts and eng.lines['load'] == (96_000, 10), str(eng.lines))
eng.poll(snap(price=95_900))                       # load fires
acts = eng.poll(snap(price=95_000, shares=10, avg=96_000))
check('deploy 0.96u/30u = 3% -> B1/S3',
      eng.buy_gear == 1 and eng.sell_gear == 3,
      f'B{eng.buy_gear}/S{eng.sell_gear} {eng.deploy_ratio:.2%}')
check('sell +3% (98,900 x10), chase -4% (92,100 x5 half-up)',
      eng.lines['sell'] == (98_900, 10) and eng.lines['chase'] == (92_100, 5),
      str(eng.lines))
check('nothing fired between the lines', not acts, str(acts))

print("-- heavier position: gears move to B2/S2 then B3/S1 --")
eng2 = Daily443Engine(T, anchor=100_000, trading_date='2026-07-09')
eng2.poll(snap(price=95_000, shares=20, avg=96_000, army=5.0))
check('deploy 1.92u/5u = 38% -> B2/S2',
      eng2.buy_gear == 2 and eng2.sell_gear == 2,
      f'B{eng2.buy_gear}/S{eng2.sell_gear} {eng2.deploy_ratio:.2%}')
check('chase -5% (91,200 x13: 20x2/3=13.33)',
      eng2.lines['chase'] == (91_200, 13), str(eng2.lines))
check('sell +2.5% (98,400)', eng2.lines['sell'][0] == 98_400,
      str(eng2.lines))
eng3 = Daily443Engine(T, anchor=100_000, trading_date='2026-07-09')
eng3.poll(snap(price=95_000, shares=20, avg=96_000, army=3.0))
check('deploy 64% -> B3/S1',
      eng3.buy_gear == 3 and eng3.sell_gear == 1,
      f'B{eng3.buy_gear}/S{eng3.sell_gear}')
check('chase -6% (90,200 x15), sell +2% (98,000: 97,920 ceiled)',
      eng3.lines['chase'] == (90_200, 15)
      and eng3.lines['sell'][0] == 98_000, str(eng3.lines))

print("-- 1-share override in action (stock bigger than one unit) --")
eng4 = Daily443Engine(T, anchor=3_000_000, trading_date='2026-07-09')
eng4.poll(snap(price=2_900_000, shares=1, avg=2_900_000, army=30.0,
               unit=1_000_000))
check('1 share -> B3: chase -6% x1',
      eng4.buy_gear == 3 and eng4.lines['chase'][1] == 1,
      f"B{eng4.buy_gear} {eng4.lines['chase']}")
check('chase price = avg x0.94 floored',
      eng4.lines['chase'][0] == trim_buy_price(T, 2_900_000 * 0.94),
      str(eng4.lines['chase'][0]))

print("-- watcher triggers fire on touch --")
acts = eng.poll(snap(price=92_100, shares=10, avg=96_000))
check('CHASE fired (B1: 5 @ 92,100)',
      places(acts)[0][1:4] == ('BUY', 92_100, 5), str(acts))
acts = eng.poll(snap(price=99_000, shares=10, avg=96_000,
                     orders=[order('b1', 'BUY', 92_100, 5)]))
check('exit first: cancel our buy + SELL (S3)',
      cancels(acts) and places(acts)[0][1:4] == ('SELL', 98_900, 10),
      str(acts))

print("-- sell gear from ACTUAL fills only (PART II S11) --")
eng5 = Daily443Engine(T, anchor=100_000, trading_date='2026-07-09')
eng5.poll(snap(price=95_000, shares=7, avg=96_000, army=3.0))
sell_before = eng5.lines['sell'][0]
eng5.poll(snap(price=95_000, shares=7, avg=96_000, army=3.0,
               orders=[order('bx', 'BUY', 91_200, 5)]))
check('resting unfilled BUY does not move the sell line',
      eng5.lines['sell'][0] == sell_before, str(eng5.lines))

print("-- full sell -> campaign cleared, reload -3% --")
eng.poll(snap(price=99_000, shares=0, avg=0.0))
check('campaign log cleared', eng.events == [], str(eng.events))
check('anchor = sell 98,900; reload -3% (95,900)',
      eng.anchor == 98_900 and eng.lines['load'][0] == 95_900,
      f'{eng.anchor} {eng.lines}')

print("-- Global Army Manager (gate ON) --")
ap.RESERVE_GATE = True
# shares 10 @96,000, army 5u -> 19% ... use army small for gear but bp low.
# Normal B1 chase: 5 @ 92,100 = 460,500. unit 1M -> safety 500k.
eng6 = Daily443Engine(T, anchor=100_000, trading_date='2026-07-09')
eng6.poll(snap(price=95_000, shares=10, avg=96_000, bp=2_000_000))
check('affordable: normal gear, buy_state OK',
      eng6.buy_state == 'OK' and eng6.lines['chase'] == (92_100, 5),
      f'{eng6.buy_state} {eng6.lines}')
# bp 900k -> tradable 400k -> max at B1 92,100 = 4 -> 4/10 = 0.4 >= 0.3.
acts = eng6.poll(snap(price=95_000, shares=10, avg=96_000, bp=900_000))
check('B1 fallback with max affordable (4)',
      eng6.buy_state == 'FALLBACK' and eng6.lines['chase'] == (92_100, 4),
      f'{eng6.buy_state} {eng6.lines}')
# bp 700k -> tradable 200k -> max 2 -> 0.2 < 0.3 -> EXHAUSTED + notify once.
acts = eng6.poll(snap(price=95_000, shares=10, avg=96_000, bp=700_000))
check('EXHAUSTED below the 30% ratio',
      eng6.buy_state == 'EXHAUSTED' and 'chase' not in eng6.lines,
      f'{eng6.buy_state} {eng6.lines}')
check('notify emitted once', len(notifies(acts)) == 1, str(acts))
acts = eng6.poll(snap(price=95_000, shares=10, avg=96_000, bp=700_000))
check('no repeated notify', not notifies(acts), str(acts))
check('sell still watched while exhausted', eng6.lines['sell'][0] == 98_900,
      str(eng6.lines))
acts = eng6.poll(snap(price=99_000, shares=10, avg=96_000, bp=700_000))
check('exhausted still fires the EXIT',
      places(acts) and places(acts)[0][1] == 'SELL', str(acts))
acts = eng6.poll(snap(price=95_000, shares=10, avg=96_000, bp=2_000_000))
check('recovers automatically when army returns',
      eng6.buy_state == 'OK' and eng6.lines['chase'] == (92_100, 5),
      f'{eng6.buy_state} {eng6.lines}')

print("-- army gate on the LOAD --")
eng7 = Daily443Engine(T, anchor=100_000, trading_date='2026-07-09')
acts = eng7.poll(snap(price=99_000, bp=800_000))
check('load reduced to the affordable 3 shares (>=30% of target)',
      eng7.buy_state == 'FALLBACK' and eng7.lines['load'] == (96_000, 3),
      f'{eng7.buy_state} {eng7.lines}')
acts = eng7.poll(snap(price=99_000, bp=600_000))
check('load EXHAUSTED below 30% of target (1/10)',
      eng7.buy_state == 'EXHAUSTED' and not eng7.lines.get('load'),
      f'{eng7.buy_state} {eng7.lines}')
check('notify emitted', len(notifies(acts)) == 1, str(acts))
ap.RESERVE_GATE = False

print("-- gate OFF (test phase): affordability ignored --")
eng8 = Daily443Engine(T, anchor=100_000, trading_date='2026-07-09')
acts = eng8.poll(snap(price=95_900, bp=1_000))
check('load fires regardless of reserve', len(places(acts)) == 1, str(acts))
check('buy_state OK with the gate off', eng8.buy_state == 'OK')

print("-- day rollover: campaign survives, anchor back to prev close --")
eng9 = Daily443Engine(T, anchor=100_000, trading_date='2026-07-09')
eng9.poll(snap(price=95_900))
eng9.poll(snap(price=95_900, shares=10, avg=96_000))
eng9.poll(snap(price=95_000, shares=10, avg=96_000,
               date='2026-07-10', prev_close=95_500))
check('campaign log kept across days',
      [e['kind'] for e in eng9.events] == ['LOAD'], str(eng9.events))
eng10 = Daily443Engine(T, anchor=98_900, trading_date='2026-07-09')
eng10.anchor_source = 'sell'
eng10.poll(snap(price=99_000, date='2026-07-10', prev_close=99_000))
check('empty next day: prev close, -4% again',
      eng10.anchor == 99_000 and eng10.lines['load'][0] == 95_000,
      f'{eng10.anchor} {eng10.lines}')

print("\nAll adaptive-gear watcher checks passed.")
