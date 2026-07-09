"""Offline simulation of the Daily 443 WATCHER engine (no network, no GUI).

Covers: pedal quantity rules (443 floor / 352 ceil), price trimming, market
phases, the watcher cycle (nothing rests before a trigger; orders fire only
on touch), side-swapping on opposite triggers, foreign-order coexistence,
campaign fill log (cleared on full sell), -3% reload, day rollover, and the
reserve gate in both states.

Run:  python scripts/test_daily443.py
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.autopilot as ap443
from core.autopilot import Daily443Engine, chase_qty, load_qty
from core.calc import trim_buy_price, trim_sell_price
from gui.autopilot_ctrl import market_phase

T = '000660.KS'
LOG = []


def snap(price, shares=0, avg=0.0, orders=(), bp=5_000_000, unit=1_000_000,
         date='2026-07-09', prev_close=None, can_trade=True):
    return {'price': price, 'shares': shares, 'avg_cost': avg,
            'orders': list(orders), 'buying_power': bp, 'unit_cash': unit,
            'trading_date': date, 'prev_close': prev_close,
            'can_trade': can_trade}


def order(oid, side, price, qty, filled=0, mine=True):
    return {'id': oid, 'side': side, 'price': price, 'qty_open': qty,
            'filled': filled, 'mine': mine}


def places(acts):  return [a for a in acts if a[0] == 'place']
def cancels(acts): return [a for a in acts if a[0] == 'cancel']
def stops(acts):   return [a for a in acts if a[0] == 'stop']


def check(name, cond, detail=''):
    status = 'ok' if cond else 'FAIL'
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ''))
    if not cond:
        raise SystemExit(f"FAILED: {name} {detail}")


print("-- pedal quantity rules (S30.2/S30.9) --")
for held, want in {1: 1, 3: 1, 4: 2, 9: 4, 13: 6}.items():
    check(f'443 chase_qty({held}) == {want}',
          chase_qty(held, 'floor') == want, f'got {chase_qty(held, "floor")}')
for held, want in {1: 1, 2: 1, 9: 5, 13: 7}.items():
    check(f'352 chase_qty({held}) == {want}',
          chase_qty(held, 'ceil') == want, f'got {chase_qty(held, "ceil")}')
check('load_qty: 1 unit floors', load_qty(1_000_000, 96_000) == 10)
check('load_qty: min 1 share', load_qty(1_000_000, 2_900_000) == 1)

print("-- price trimming --")
check('KR buy floors to tick', trim_buy_price(T, 92_160) == 92_100)
check('KR sell ceils to tick', trim_sell_price(T, 98_880) == 98_900)
check('US buy floors to cent', trim_buy_price('NVDA', 187.6789) == 187.67)

print("-- market phases (S30.8) --")


def utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


# Tue 2026-07-14: KST 10:00 = 01:00 UTC → KR regular.
check('KR 10:00 KST = REGULAR', market_phase(T, utc(2026, 7, 14, 1, 0)) == 'REGULAR')
check('KR 08:30 KST = PRE', market_phase(T, utc(2026, 7, 13, 23, 30)) == 'PRE')
check('KR 16:00 KST = AFTER', market_phase(T, utc(2026, 7, 14, 7, 0)) == 'AFTER')
check('KR 22:00 KST = CLOSED', market_phase(T, utc(2026, 7, 14, 13, 0)) == 'CLOSED')
check('KR Saturday = CLOSED', market_phase(T, utc(2026, 7, 18, 1, 0)) == 'CLOSED')
# US summer (DST): 14:00 UTC = 10:00 ET → regular; winter: = 09:00 ET → pre.
check('US Jul 10:00 ET = REGULAR',
      market_phase('NVDA', utc(2026, 7, 14, 14, 0)) == 'REGULAR')
check('US Jan 09:00 ET = PRE',
      market_phase('NVDA', utc(2026, 1, 13, 14, 0)) == 'PRE')
check('US Jul 16:30 ET = AFTER',
      market_phase('NVDA', utc(2026, 7, 14, 20, 30)) == 'AFTER')

print("-- watcher EMPTY: nothing rests before the trigger --")
eng = Daily443Engine(T, anchor=100_000, trading_date='2026-07-09',
                     log=lambda m: LOG.append(m))
acts = eng.poll(snap(price=99_000))
check('state EMPTY', eng.state == 'EMPTY')
check('NO order placed above the load line', not acts, str(acts))
check('status shows watching', 'watching' in eng.status, eng.status)
check('load line 96,000 x10', eng.lines['load'] == (96_000, 10),
      str(eng.lines))
check('psell pseudo exit present', eng.lines['psell'][0] == 98_900,
      str(eng.lines.get('psell')))

print("-- load trigger fires once, then waits for the fill --")
acts = eng.poll(snap(price=95_900))
check('LOAD fired on touch', places(acts)[0][1:4] == ('BUY', 96_000, 10),
      str(acts))
acts = eng.poll(snap(price=95_900,
                     orders=[order('b1', 'BUY', 96_000, 10)]))
check('waits while our buy rests (no duplicates)', not acts, str(acts))

print("-- watch-only (WATCH mode): trigger reported, not fired --")
eng_w = Daily443Engine(T, anchor=100_000, trading_date='2026-07-09')
acts = eng_w.poll(snap(price=95_900, can_trade=False))
check('no order in watch-only', not acts, str(acts))
check('status says trigger met', 'trigger met' in eng_w.status, eng_w.status)

print("-- DEPLOYED: between the lines the watcher does nothing --")
acts = eng.poll(snap(price=95_000, shares=10, avg=96_000))
check('LOAD fill logged + event', any('LOAD filled' in m for m in LOG)
      and eng.events[-1]['kind'] == 'LOAD', str(eng.events))
check('state DEPLOYED', eng.state == 'DEPLOYED')
check('NO resting sell pre-placed', not acts, str(acts))
check('lines sell 98,900 / chase 92,100 x5',
      eng.lines['sell'] == (98_900, 10) and eng.lines['chase'] == (92_100, 5),
      str(eng.lines))

print("-- chase trigger fires a buy; bounce trigger fires the exit --")
acts = eng.poll(snap(price=92_000, shares=10, avg=96_000))
check('CHASE fired on touch', places(acts)[0][1:4] == ('BUY', 92_100, 5),
      str(acts))
acts = eng.poll(snap(price=92_000, shares=10, avg=96_000,
                     orders=[order('b2', 'BUY', 92_100, 5)]))
check('waits while chase buy rests', not acts, str(acts))
acts = eng.poll(snap(price=99_000, shares=10, avg=96_000,
                     orders=[order('b2', 'BUY', 92_100, 5)]))
check('exit first: cancels OUR buy + fires SELL',
      cancels(acts)[0][1] == 'b2'
      and places(acts)[0][1:4] == ('SELL', 98_900, 10), str(acts))

print("-- foreign orders are never cancelled --")
acts = eng.poll(snap(price=99_000, shares=10, avg=96_000,
                     orders=[order('x1', 'BUY', 91_000, 3, mine=False)]))
check('sell fired, foreign buy untouched',
      not cancels(acts) and places(acts)[0][1] == 'SELL', str(acts))
acts = eng.poll(snap(price=99_000, shares=10, avg=96_000,
                     orders=[order('x2', 'SELL', 99_500, 10, mine=False)]))
check('foreign sell resting → wait (no duplicate exit)', not acts, str(acts))

print("-- our unfilled sell + price falls to chase → swap sides --")
eng2 = Daily443Engine(T, anchor=100_000, trading_date='2026-07-09')
eng2.poll(snap(price=95_000, shares=10, avg=96_000))
acts = eng2.poll(snap(price=92_000, shares=10, avg=96_000,
                      orders=[order('s1', 'SELL', 98_900, 10)]))
check('cancels OUR sell + fires chase',
      cancels(acts)[0][1] == 's1'
      and places(acts)[0][1:4] == ('BUY', 92_100, 5), str(acts))

print("-- chase fill updates the campaign log --")
acts = eng.poll(snap(price=92_300, shares=15, avg=94_700))
check('CHASE event with backed-out price 92,100',
      eng.events[-1]['kind'] == 'CHASE'
      and abs(eng.events[-1]['price'] - 92_100) < 1, str(eng.events[-1]))
check('campaign has LOAD + CHASE',
      [e['kind'] for e in eng.events] == ['LOAD', 'CHASE'],
      str([e['kind'] for e in eng.events]))

print("-- full sell: campaign log CLEARED, anchor = sell, -3% reload --")
eng.poll(snap(price=97_700, shares=15, avg=94_700))   # sell trigger fires
acts = eng.poll(snap(price=97_700, shares=0, avg=0.0))
check('campaign log cleared after the full sell', eng.events == [],
      str(eng.events))
check('anchor = sell price 97,600', eng.anchor == 97_600, str(eng.anchor))
check('reload line -3% (94,600)', eng.lines['load'][0] == 94_600,
      str(eng.lines))
check('no order until the reload triggers', not places(acts), str(acts))

print("-- HOLD seed when armed onto an existing position --")
eng3 = Daily443Engine(T, anchor=None, trading_date='2026-07-09')
eng3.poll(snap(price=95_000, shares=7, avg=96_000))
check('HOLD event seeds the campaign log',
      eng3.events and eng3.events[0]['kind'] == 'HOLD'
      and eng3.events[0]['qty'] == 7, str(eng3.events))

print("-- pedal 352: deeper chase, quicker exit, aggressive size --")
eng3.set_pedal('352')
eng3.poll(snap(price=95_000, shares=15, avg=96_000))
check('352 sell = avg×1.02 ceiled (98,000)',
      eng3.lines['sell'][0] == 98_000, str(eng3.lines))
check('352 chase = avg×0.95 floored (91,200) ×8 (ceil)',
      eng3.lines['chase'] == (91_200, 8), str(eng3.lines))
eng4 = Daily443Engine(T, anchor=100_000, trading_date='2026-07-09',
                      pedal='352')
eng4.poll(snap(price=99_000))
check('352 load = anchor×0.97 (97,000)', eng4.lines['load'][0] == 97_000,
      str(eng4.lines))

print("-- day rollover: campaign log SURVIVES, anchor resets when empty --")
eng5 = Daily443Engine(T, anchor=100_000, trading_date='2026-07-09')
eng5.poll(snap(price=95_900))                                   # load fires
eng5.poll(snap(price=95_900, shares=10, avg=96_000))            # filled
acts = eng5.poll(snap(price=95_000, shares=10, avg=96_000,
                      date='2026-07-10', prev_close=95_500))
check('campaign log kept across days',
      [e['kind'] for e in eng5.events] == ['LOAD'], str(eng5.events))
check('chase count reset on the new day', eng5.chase_count == 0)
eng6 = Daily443Engine(T, anchor=97_600, trading_date='2026-07-09')
eng6.anchor_source = 'sell'
eng6.poll(snap(price=99_000, date='2026-07-10', prev_close=99_000))
check('empty next day: anchor = prev close, -4% again',
      eng6.anchor == 99_000 and eng6.anchor_source == 'close'
      and eng6.lines['load'][0] == trim_buy_price(T, 99_000 * 0.96),
      f"{eng6.anchor} {eng6.anchor_source} {eng6.lines}")

print("-- reserve gate OFF (S30.7): low reserve does not stop --")
check('gate off by default', ap443.RESERVE_GATE is False)
eng7 = Daily443Engine(T, anchor=100_000, trading_date='2026-07-09')
acts = eng7.poll(snap(price=95_900, bp=100_000))
check('load still fires with tiny reserve', len(places(acts)) == 1,
      str(acts))

print("-- reserve stop works when the gate is re-enabled --")
ap443.RESERVE_GATE = True
eng8 = Daily443Engine(T, anchor=100_000, trading_date='2026-07-09')
acts = eng8.poll(snap(price=95_900, bp=100_000))
check('stop emitted with gate on', len(stops(acts)) == 1, str(acts))
check('state STOPPED', eng8.state == 'STOPPED')
eng8.resume()
check('resume() re-arms', eng8.state == 'ARMING')
ap443.RESERVE_GATE = False

print("\nAll Daily 443 watcher checks passed.")
