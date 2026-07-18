"""Offline tests for the unified gear system + line-watcher engine.

Run:  python scripts/test_watcher.py     (no network, no tkinter)

Covers the unified rules:
  - gear table G1 -4%/+3% … G5 -8%/+7% (load = buy), -8% ×1.0
  - volatility bands ≤8 / ≤12 / ≤16 / ≤20 / >20
  - heavy-unit minimum entry gear (1.2/1.6/2.0/2.5)
  - vantage-point load (prev close), same-day re-bait (exit −4%, G1 pin)
  - sell tiers: middle default, multi-tier chase with tier progress,
    gap-up sells everything at the highest crossed line
  - army exhausted → buy stops, sell stays watched
  - WATCH exposes triggers without placing; exit-first cancel
"""

import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.calc import (AUTO_GEARS, BUY_GEAR_INFO, VOL_THRESHOLDS,
                       select_auto_gear, weight_min_gear,
                       effective_entry_gear, calc_buy_shares,
                       calc_load_ladder, calc_sell_tiers)
from core.autopilot import WatcherEngine

T = '005930.KS'          # KRW: tick rules apply
UNIT = 1_000_000.0
D1, D2 = '2026-07-17', '2026-07-18'

passed = 0


def ok(cond, name, info=''):
    global passed
    assert cond, f'FAIL: {name} {info}'
    passed += 1
    print(f'  ok  {name}')


def card(gear, actives=(False, True, False)):
    g = AUTO_GEARS[gear]
    return {'gear': gear, 'pct': g['pct'], 'tier_pcts': list(g['tiers']),
            'tier_actives': list(actives)}


def snap(price, shares=0, avg=0.0, cfg=None, bp=None, prev_close=None,
         date=D1, can=True, orders=()):
    return {'price': price, 'shares': shares, 'avg_cost': avg,
            'orders': list(orders), 'buying_power': bp, 'unit_cash': UNIT,
            'trading_date': date, 'prev_close': prev_close,
            'can_trade': can, 'phase': 'REGULAR', 'card': cfg}


def places(acts):
    return [a for a in acts if a[0] == 'place']


def cancels(acts):
    return [a for a in acts if a[0] == 'cancel']


print('— gear tables —')
ok([AUTO_GEARS[g]['pct'] for g in range(1, 6)] == [4, 5, 6, 7, 8],
   'pcts G1..G5 = 4..8')
ok(AUTO_GEARS[1]['tiers'] == (1, 3, 5) and AUTO_GEARS[5]['tiers'] == (5, 7, 9),
   'tiers G1 (1,3,5) … G5 (5,7,9)')
ok(BUY_GEAR_INFO[8]['ratio'] == 1.0, '-8% ratio = x1.0')
ok(BUY_GEAR_INFO[8]['label'] == '8% drop (x1.0)'
   and BUY_GEAR_INFO[8]['frac'] == '1.0',
   'G5 user-facing wording = 8% x1.0')
ok(VOL_THRESHOLDS == (8.0, 12.0, 16.0, 20.0), 'vol cut points 8/12/16/20')
for v, g in ((7.9, 1), (8.0, 1), (8.1, 2), (12.0, 2), (12.1, 3), (16.0, 3),
             (16.1, 4), (20.0, 4), (20.1, 5), (None, 1)):
    ok(select_auto_gear(v) == g, f'V {v} → G{g}')

print('— buy sizes (half-up, min 1) —')
for shares, pct, want in ((12, 4, 6), (12, 5, 8), (12, 6, 9), (12, 7, 10),
                          (12, 8, 12), (1, 4, 1), (7, 4, 4)):
    ok(calc_buy_shares(shares, pct) == want, f'{shares} sh @ -{pct}% → +{want}')

print('— heavy-unit minimum entry gear —')
for r, g in ((1.0, 1), (1.2, 1), (1.3, 2), (1.6, 2), (1.7, 3), (2.0, 3),
             (2.1, 4), (2.5, 4), (2.6, 5)):
    ok(weight_min_gear(r * UNIT, UNIT) == g, f'1 share = {r}u → min G{g}')
ok(effective_entry_gear(5.0, 1.8 * UNIT, UNIT) == 3,
   'V→G1 but 1.8u share → entry G3')
ok(effective_entry_gear(21.0, 1.8 * UNIT, UNIT) == 5,
   'V→G5 beats a lower weight gear')

print('— vantage load ladder (load = buy pct) —')
ladder, lp, lq = calc_load_ladder(100_000, 5, UNIT, rescues=2)
ok(lp == 95_000 and lq == 11, 'load = vantage -5% ×1 unit', f'{lp} x{lq}')
ok(abs(ladder[1]['price'] - 95_000 * 0.95) < 1e-6,
   'rescue 1 cascades at the SAME -5%')

print('— sell tier split —')
tiers = calc_sell_tiers(7, 100_000, [3, 5, 7], [True, True, False])
ok((tiers[0]['qty'], tiers[1]['qty']) == (3, 4) and tiers[2]['price'] is None,
   '7 sh, 2 tiers → 3+4 (mid gets the remainder)')

# ── Engine: empty → load at the vantage point ────────────────────────────────
print('— engine: EMPTY load —')
e = WatcherEngine(T, trading_date=D1)
acts = e.poll(snap(99_000, cfg=card(2), prev_close=100_000))
ok(e.lines['load'] == (95_000, 11), 'G2 load line 95,000 ×11 from prev close',
   str(e.lines))
ok(not places(acts) and e.trigger['BUY'] is None, 'above the line: no action')

acts = e.poll(snap(94_900, cfg=card(2), prev_close=100_000, can=False))
ok(e.trigger['BUY'] == {'side': 'BUY', 'price': 95_000, 'qty': 11,
                        'label': '-5% load'},
   'WATCH: crossed line exposed as trigger', str(e.trigger))
ok(not places(acts), 'WATCH places nothing')

acts = e.poll(snap(94_900, cfg=card(2), prev_close=100_000))
ok(places(acts) == [('place', 'BUY', 95_000, 11, '-5% load')],
   'LIVE fires the load on touch')

# ── Engine: deployed lines from the card gear ────────────────────────────────
print('— engine: DEPLOYED chase + tier —')
acts = e.poll(snap(95_500, shares=11, avg=95_000, cfg=card(2)))
ok(e.lines['chase'] == (90_200, 7), 'chase -5% ×2/3 (trimmed to tick)',
   str(e.lines))
ok(e.lines['tier2'] == (98_800, 11), 'single mid tier +4% carries all')

acts = e.poll(snap(98_850, shares=11, avg=95_000, cfg=card(2)))
p = places(acts)
ok(p and p[0][1] == 'SELL' and p[0][2] == 98_800 and p[0][3] == 11,
   'SELL fired on the tier touch', str(p))

# full exit fill → same-day re-bait (G1, exit fill −4%) even with card G2
acts = e.poll(snap(98_900, shares=0, avg=0, cfg=card(2), prev_close=100_000))
ok(e.anchor == 98_800 and e.anchor_source == 'sell',
   're-bait vantage = exit fill', f'{e.anchor} {e.anchor_source}')
ok(e.pct == 4 and e.gear == 1, 're-bait watches G1 -4% (card still G2)')
ok(e.lines['load'] == (94_800, 11), 're-bait line = fill -4%', str(e.lines))
ok(e.events == [], 'campaign log cleared on the full exit')

# re-bait fill → the new campaign is PINNED to gear 1
acts = e.poll(snap(94_700, shares=11, avg=94_800, cfg=card(3)))
ok(e.gear1_pinned, 're-bait fill pins the campaign to G1')
ok(e.pct == 4 and e.lines['chase'][0] == 91_000,
   'pinned chase -4% regardless of card G3',
   str(e.lines))
ok(e.lines['tier2'][0] == 97_700, 'pinned exit +3% (G1 mid tier)')

# full exit of the pinned campaign clears the pin
e.note_manual_order('SELL', 97_700, 11, tiers=[1])
acts = e.poll(snap(97_800, shares=0, avg=0, cfg=card(3), prev_close=100_000))
ok(not e.gear1_pinned and e.anchor == 97_700 and e.anchor_source == 'sell',
   'pin cleared on exit; a NEW same-day re-bait arms at the new fill')

# unfilled re-bait dies at the day roll → back to prev close + card gear
acts = e.poll(snap(99_000, cfg=card(3), prev_close=99_500, date=D2))
ok(e.anchor == 99_500 and e.anchor_source == 'close',
   'day roll: re-bait dropped, vantage = prev close')
ok(e.pct == 6, 'gear back to the card (G3)')

# ── Engine: -8% ×1.0 ─────────────────────────────────────────────────────────
print('— engine: G5 x1.0 —')
e2 = WatcherEngine(T, trading_date=D1)
e2.poll(snap(100_000, shares=10, avg=100_000, cfg=card(5)))
ok(e2.lines['chase'] == (92_000, 10), '-8% chase buys the WHOLE position again')
e2.poll(snap(91_900, shares=10, avg=100_000, cfg=card(5), can=False))
ok(e2.trigger['BUY']['label'] == '-8% x1.0 chase (G5)',
   'G5 crossed-line/order wording uses 8% x1.0')

# ── Engine: two tiers, progress, and the gap-up case ─────────────────────────
print('— engine: tier progress —')
e3 = WatcherEngine(T, trading_date=D1)
two = card(3, actives=(True, True, False))
e3.poll(snap(100_000, shares=10, avg=100_000, cfg=two))
ok(e3.lines['tier1'] == (103_000, 5) and e3.lines['tier2'] == (105_000, 5),
   'two active tiers split 5+5', str(e3.lines))

acts = e3.poll(snap(103_100, shares=10, avg=100_000, cfg=two))
p = places(acts)
ok(p == [('place', 'SELL', 103_000, 5, '매도 T1')], 'T1 fires alone', str(p))
e3.poll(snap(103_100, shares=5, avg=100_000, cfg=two))       # T1 filled
ok(e3.tier_done[0] and e3.lines.get('tier1') is None
   and e3.lines['tier2'] == (105_000, 5),
   'T1 done → only T2 remains with the rest')

# a chase fill restarts the tier ladder on the whole holding
e3.poll(snap(94_000, shares=5, avg=100_000, cfg=two))         # chase fires
e3.poll(snap(94_000, shares=9, avg=97_400, cfg=two))          # chase filled
ok(e3.tier_done == [False, False, False], 'buy fill resets tier progress')

e4 = WatcherEngine(T, trading_date=D1)
e4.poll(snap(100_000, shares=10, avg=100_000, cfg=two))
acts = e4.poll(snap(106_000, shares=10, avg=100_000, cfg=two))
p = places(acts)
ok(p == [('place', 'SELL', 105_000, 10, '매도 T1+T2')],
   'gap-up through both tiers → ONE order, all shares, highest line', str(p))

# ── Engine: army exhausted → buy off, sell watched, NO popup ────────────────
print('— engine: exhausted —')
e5 = WatcherEngine(T, trading_date=D1)
acts = e5.poll(snap(93_000, shares=10, avg=100_000, cfg=card(2), bp=50_000.0))
ok(e5.buy_state == 'EXHAUSTED', 'unaffordable chase → EXHAUSTED')
ok(not any(a[0] == 'notify' for a in acts),
   'NO popup — the graph shows the muted line instead')
ok(not places(acts) and e5.trigger['BUY'] is None,
   'crossed buy line does NOT fire without army')
ok(e5.trigger_note and 'no reserve army' in e5.trigger_note,
   'trigger note explains why the manual buy is off', str(e5.trigger_note))
ok('manual Buy button is off' in e5.trigger_note,
   'crossed condition names the disabled manual Buy button')
ok(len(e5.trigger_note) <= 60,
   'BUY condition fits the trigger banner without clipping')
ok(e5.lines.get('tier2'), 'sell line stays watched')
acts = e5.poll(snap(93_000, shares=10, avg=100_000, cfg=card(2), bp=50_000.0))
ok(not any(a[0] == 'notify' for a in acts), 'still no popup on later polls')
acts = e5.poll(snap(104_100, shares=10, avg=100_000, cfg=card(2), bp=50_000.0))
ok(places(acts) and places(acts)[0][1] == 'SELL',
   'the sell still fires while exhausted')

# arming on an existing position logs only — no fill-list entry
e5b = WatcherEngine(T, trading_date=D1)
e5b.poll(snap(100_000, shares=10, avg=100_000, cfg=card(2)))
ok(e5b.events == [], 'window (re)opening adds NO fill-list entry')

# The same condition/message applies to an EMPTY stock whose LOAD is crossed.
e5c = WatcherEngine(T, trading_date=D1)
acts = e5c.poll(snap(94_900, cfg=card(2), bp=50_000.0,
                     prev_close=100_000))
ok(e5c.buy_state == 'EXHAUSTED' and e5c.trigger['BUY'] is None
   and not places(acts) and not any(a[0] == 'notify' for a in acts),
   'unaffordable crossed LOAD stays off without a popup')
ok(e5c.trigger_note and 'manual Buy button is off' in e5c.trigger_note,
   'LOAD condition names the disabled manual Buy button')
ok(len(e5c.trigger_note) <= 60,
   'LOAD condition fits the trigger banner without clipping')

# ── Engine: exit first cancels our resting buy ───────────────────────────────
print('— engine: exit first —')
e6 = WatcherEngine(T, trading_date=D1)
e6.poll(snap(100_000, shares=10, avg=100_000, cfg=card(2)))
mine = [{'id': 'o1', 'side': 'BUY', 'price': 95_000, 'qty_open': 7,
         'filled': 0, 'mine': True}]
acts = e6.poll(snap(104_100, shares=10, avg=100_000, cfg=card(2),
                    orders=mine))
ok(cancels(acts) == [('cancel', 'o1', 'our buy (exit first)')],
   'our resting buy is cancelled before the exit', str(acts))
ok(places(acts) and places(acts)[0][1] == 'SELL', 'then the sell fires')

# ── Trigger suppressed while an order rests (no manual double-fire) ─────────
print('— engine: trigger vs resting orders —')
e9 = WatcherEngine(T, trading_date=D1)
e9.poll(snap(100_000, shares=10, avg=100_000, cfg=card(2)))
resting = [{'id': 's1', 'side': 'SELL', 'price': 104_000, 'qty_open': 10,
            'filled': 0, 'mine': True}]
e9.poll(snap(104_100, shares=10, avg=100_000, cfg=card(2), orders=resting))
ok(e9.trigger['SELL'] is None, 'resting sell hides the SELL trigger')
resting_b = [{'id': 'b1', 'side': 'BUY', 'price': 95_000, 'qty_open': 7,
              'filled': 0, 'mine': True}]
e9.poll(snap(94_900, shares=10, avg=100_000, cfg=card(2), orders=resting_b))
ok(e9.trigger['BUY'] is None, 'resting buy hides the BUY trigger')

# ── Re-bait never crosses a day roll, even without a fresh prev close ───────
print('— engine: re-bait day-roll safety —')
e10 = WatcherEngine(T, trading_date=D1)
e10.anchor, e10.anchor_source = 98_800, 'sell'
e10.poll(snap(99_000, cfg=card(2), prev_close=None, date=D2))
ok(e10.anchor_source == 'close' and e10.anchor is None,
   'no prev close yet → re-bait dropped, engine waits for the vantage')

# ── Persistence round trip ───────────────────────────────────────────────────
print('— persistence —')
e7 = WatcherEngine(T, trading_date=D1)
e7.poll(snap(99_000, cfg=card(2), prev_close=100_000))
e7.poll(snap(94_900, cfg=card(2), prev_close=100_000))
e7.poll(snap(94_900, shares=11, avg=94_900, cfg=card(2)))
d = e7.to_dict()
e8 = WatcherEngine(T, saved=d)
ok(e8.anchor == e7.anchor and e8.trading_date == D1
   and e8.tier_done == e7.tier_done,
   'anchor/date/tier progress survive a restart')

# One-time migration: legacy window-open HOLD rows are not campaign fills.
real_fill = {'ts': '07/18 09:10', 'kind': 'CHASE', 'qty': 5,
             'price': 95_000, 'shares': 15, 'avg': 98_333}
legacy_hold = {'ts': '07/18 09:00', 'kind': 'HOLD', 'qty': 10,
               'price': 100_000, 'shares': 10, 'avg': 100_000}
saved = dict(d)
saved['events'] = [legacy_hold, real_fill]
e11 = WatcherEngine(T, saved=saved)
ok(e11.events == [real_fill] and e11.dirty,
   'restore prunes legacy HOLD rows but keeps real fills')
e11.poll(snap(100_000, shares=15, avg=98_333, cfg=card(2)))
ok(e11.events == [real_fill],
   'first unchanged poll after restore adds no campaign fill')

# Campaign fills are driven only by observed share-count changes.
e12 = WatcherEngine(T, trading_date=D1)
e12.poll(snap(100_000, shares=10, avg=100_000, cfg=card(2)))
e12.poll(snap(100_000, shares=10, avg=100_000, cfg=card(2)))
ok(e12.events == [], 'unchanged holding produces no campaign fill')
e12.poll(snap(100_000, shares=15, avg=98_000, cfg=card(2)))
ok(len(e12.events) == 1 and e12.events[0]['kind'] == 'CHASE'
   and e12.events[0]['qty'] == 5,
   'observed share increase records exactly one CHASE fill')
e12.poll(snap(100_000, shares=15, avg=98_000, cfg=card(2)))
ok(len(e12.events) == 1,
   'unchanged poll after a buy does not duplicate the fill')
e12.poll(snap(100_000, shares=12, avg=98_000, cfg=card(2)))
ok(len(e12.events) == 2 and e12.events[-1]['kind'] == 'SELL'
   and e12.events[-1]['qty'] == -3,
   'observed partial share decrease records exactly one SELL fill')
e12.poll(snap(100_000, shares=12, avg=98_000, cfg=card(2)))
ok(len(e12.events) == 2,
   'unchanged poll after a sell does not duplicate the fill')

print(f'\nALL {passed} CHECKS PASSED')
