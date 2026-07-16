"""Offline replay of the TAZZA_DOUBLE engine — 타짜: 묻고 더블로 가 (no GUI).

Covers: ladder auto-pick, −3% LOAD/RELOAD, the full-double chain with the
campaign two-tier exit math (B/S/K formula), tier-1 partial exit and the
remaining-lot reprice, 밑장 빼기 (skim 1/3 → −3% rebuy) success and rebound
failure, the emergency stages 3/6 → 2/4 → 1/2, the 5-idle-day escalations,
FINAL_OUT (손절), campaign completion and the sell-anchored reload, and the
cross-battlefield skim lock.

Run:  python scripts/test_tazza.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.tazza import (TazzaEngine, LADDER_MODES, ladder_pct_for,
                        IDLE_LIMIT_DAYS)
from core.calc import trim_buy_price, trim_sell_price, round_half_up

T = '005930.KS'


def check(name, cond, detail=''):
    status = 'ok' if cond else 'FAIL'
    detail = str(detail).encode('ascii', 'replace').decode()   # cp949 console
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ''))
    if not cond:
        raise SystemExit(f"FAILED: {name} {detail}")


def places(acts):   return [a for a in acts if a[0] == 'place']
def notifies(acts): return [a for a in acts if a[0] == 'notify']


class Sim:
    """Tiny broker: our placed orders fill at their limit the moment the
    polled price crosses them; the engine sees the fill on the NEXT poll
    (exactly like the real 10-second watcher)."""

    def __init__(self, cash=100_000_000, unit=1_000_000):
        self.e = TazzaEngine(T)
        self.shares, self.avg, self.cash = 0, 0.0, cash
        self.unit = unit
        self.orders = {}
        self._next = 1
        self.date = '2026-07-09'
        self.prev_close = None
        self.locks = 0.0
        self.last_acts = []

    def snap(self, price):
        orders = [{'id': oid, 'side': o['side'], 'price': o['price'],
                   'qty_open': o['qty'], 'filled': 0, 'mine': True}
                  for oid, o in self.orders.items()]
        return {'price': price, 'shares': self.shares, 'avg_cost': self.avg,
                'orders': orders, 'buying_power': self.cash,
                'unit_cash': self.unit, 'army_units': 30.0,
                'trading_date': self.date, 'prev_close': self.prev_close,
                'can_trade': True, 'phase': 'REGULAR',
                'skim_locks': self.locks}

    def poll(self, price):
        acts = self.e.poll(self.snap(price))
        self.last_acts = acts
        for a in acts:
            if a[0] == 'place':
                _, side, p, q, _label = a
                self.orders[f'o{self._next}'] = {'side': side, 'price': p,
                                                 'qty': int(q)}
                self._next += 1
            elif a[0] == 'cancel':
                self.orders.pop(a[1], None)
        for oid, o in list(self.orders.items()):
            if o['side'] == 'BUY' and price <= o['price']:
                new = self.shares + o['qty']
                self.avg = (self.avg * self.shares + o['price'] * o['qty']) / new
                self.shares = new
                self.cash -= o['price'] * o['qty']
                del self.orders[oid]
            elif o['side'] == 'SELL' and price >= o['price']:
                self.shares = max(0, self.shares - o['qty'])
                self.cash += o['price'] * o['qty']
                if self.shares == 0:
                    self.avg = 0.0
                del self.orders[oid]
        return acts


print('-- ladder auto-pick: single-share chunkiness vs the unit --')
check('70k share / 1M unit -> -6%', ladder_pct_for(70_000, 1e6) == 0.06)
check('1.5M share / 1M unit -> -7%', ladder_pct_for(1_500_000, 1e6) == 0.07)
check('2.5M share / 1M unit -> -8%', ladder_pct_for(2_500_000, 1e6) == 0.08)
check('unknown unit -> -7% default', ladder_pct_for(70_000, 0) == 0.07)

print('-- skim / tier half-up splits --')
for q, want in ((4, 1), (5, 2), (6, 2), (7, 2), (8, 3)):
    check(f'skim 1/3 of {q} -> {want}',
          max(1, round_half_up(q / 3)) == want)
for q, w1 in ((2, 1), (4, 2), (5, 3), (7, 4)):
    check(f'tier split {q} -> {w1}/{q - w1}', round_half_up(q / 2) == w1)

print('-- LOAD: anchor -3%, one unit --')
s = Sim()
s.prev_close = 100_000
s.poll(99_000)
load_p = trim_buy_price(T, 100_000 * 0.97)
check('anchor from prev close', s.e.anchor == 100_000)
check('load line -3%', s.e.lines['load'][0] == load_p, s.e.lines['load'])
acts = s.poll(load_p)
check('LOAD fires on touch', places(acts) and places(acts)[0][1] == 'BUY')
lq = s.e.lines.get('load', (0, round_half_up(1e6 / load_p)))[1]
s.poll(load_p + 500)
check('campaign opened: B == cost == K',
      abs(s.e.B - s.shares * load_p) < 1 and s.e.K == s.e.B,
      f'B={s.e.B:,.0f}')
check('ladder picked -6%', s.e.ladder_pct == 0.06)
check('1 unit deployed -> single exit tier', 'tier2' not in s.e.lines)
p_single = trim_sell_price(T, (s.e.B + 0.03 * s.e.K) / s.shares)
check('single exit at campaign +3%', s.e.lines['tier1'][0] == p_single,
      s.e.lines['tier1'])

print('-- full DOUBLE chain and the two-tier campaign exit --')
lower1 = trim_buy_price(T, s.avg * 0.94)
check('lower = avg -6%', s.e.lines['lower'][0] == lower1, s.e.lines['lower'])
q0 = s.shares
acts = s.poll(lower1)
check('더블 fires on touch', places(acts) and '더블' in places(acts)[0][4])
s.poll(lower1 + 300)
check('double landed: shares roughly doubled by value',
      s.e.double_count == 1 and s.shares > q0)
check('deployed ~2 units', s.e.deployed_units == 2, s.e.deployed_units)
m = LADDER_MODES['NORMAL_3_6']
Q = s.shares
q1, q2 = round_half_up(Q / 2), Q - round_half_up(Q / 2)
X = (s.e.B + m['g'] * s.e.K - s.e.S) / ((1 + m['r1']) * q1 + (1 + m['r2']) * q2)
check('tier1 price matches formula',
      s.e.lines['tier1'] == (trim_sell_price(T, (1 + m['r1']) * X), q1),
      s.e.lines['tier1'])
check('tier2 price matches formula',
      s.e.lines['tier2'] == (trim_sell_price(T, (1 + m['r2']) * X), q2),
      s.e.lines['tier2'])
check('projection: 3 future doubles + 2 sells',
      len(s.e.projection['buys']) == 3 and len(s.e.projection['sells']) == 2)
check('projection sizes double (x2 then x4)',
      s.e.projection['buys'][1][1] > s.e.projection['buys'][0][1]
      and s.e.projection['buys'][2][1] > 1.5 * s.e.projection['buys'][1][1],
      s.e.projection['buys'])

t1_price = s.e.lines['tier1'][0]
acts = s.poll(t1_price)
check('tier1 SELL fires', places(acts) and places(acts)[0][1] == 'SELL')
B_before, K_before = s.e.B, s.e.K
s.poll(t1_price - 200)
check('tier progress -> TIER_2', s.e.tier_progress == 'TIER_2')
p_rem = trim_sell_price(T, (B_before + m['g'] * K_before - s.e.S) / s.shares)
check('remaining lot repriced to exact campaign target',
      s.e.lines['tier2'] == (p_rem, s.shares), s.e.lines['tier2'])

acts = s.poll(p_rem)
check('tier2 SELL fires', places(acts) and places(acts)[0][1] == 'SELL')
prof_min = m['g'] * K_before * 0.9
s.poll(p_rem - 100)
check('campaign closed at zero shares', not s.e.campaign_active
      and s.shares == 0)
check('reload anchored to the final sell', s.e.anchor == p_rem
      and s.e.anchor_source == 'sell')
check('reload line = sell -3%',
      s.e.lines['load'][0] == trim_buy_price(T, p_rem * 0.97),
      s.e.lines['load'])
gain = s.cash - 100_000_000
check('campaign net >= ~g*K', gain > prof_min, f'net={gain:,.0f}')

print('-- 밑장 빼기: unaffordable double -> skim 1/3, then rebuy success --')
s = Sim(cash=1_500_000)
s.prev_close = 100_000
s.poll(99_000)
s.poll(trim_buy_price(T, 97_000))
s.poll(97_100)                      # load fill seen; cash now ~530k
lower = s.e.lines['lower'][0]
q_before = s.shares
acts = s.poll(lower)
check('skim fires instead of double',
      places(acts) and '밑장' in places(acts)[0][4], places(acts))
check('skim announces once', len(notifies(acts)) == 1)
skim_p = places(acts)[0][2]
s.poll(lower)                       # sell filled at skim_p last cycle
check('sold a third', q_before - s.shares == max(1, round_half_up(q_before / 3)))
check('stage 1, mode 2/4', s.e.emergency_stage == 1
      and s.e.ladder_mode == 'CAUTION_2_4')
rebuy = trim_buy_price(T, skim_p * 0.97)
check('rebuy line = fill -3%', s.e.skim_rebuy_line == rebuy)
check('proceeds locked', s.e.skim_lock_amount == skim_p * (q_before - s.shares))
acts = s.poll(rebuy)
check('rebuy fires on touch', places(acts) and places(acts)[0][1] == 'BUY')
s.poll(rebuy + 100)
check('밑장 success -> back to 3/6 stage 0',
      s.e.emergency_stage == 0 and s.e.ladder_mode == 'NORMAL_3_6'
      and not s.e.skim_pending and s.e.skim_lock_amount == 0.0)

print('-- 밑장 빼기 rebound failure keeps the lowered ladder --')
s = Sim(cash=4_200_000)             # funds two doubles, not three
s.prev_close = 100_000
s.poll(99_000)
s.poll(trim_buy_price(T, 97_000))
s.poll(97_100)
s.poll(s.e.lines['lower'][0])       # first double (affordable) fills
s.poll(91_200)                      # fill seen -> 2 units deployed
check('2 units deployed after the double', s.e.deployed_units == 2)
s.poll(s.e.lines['lower'][0])       # second double (affordable) fills
s.poll(88_300)                      # fill seen -> 4 units deployed
check('4 units deployed after the 2nd double', s.e.deployed_units == 4)
s.poll(s.e.lines['lower'][0])       # third double unaffordable -> skim
s.poll(85_500)                      # skim fill seen -> stage 1, pending
check('stage 1 pending on a 2-tier position', s.e.skim_pending
      and 'tier2' in s.e.lines, s.e.lines)
t1 = s.e.lines['tier1'][0]
acts = s.poll(t1)                   # rebound to the 2/4 upper tier
check('upper tier fires while 밑장 pending',
      places(acts) and places(acts)[0][1] == 'SELL')
s.poll(t1 - 100)
check('failure: lock released, stage 1 and 2/4 stay',
      not s.e.skim_pending and s.e.skim_lock_amount == 0.0
      and s.e.emergency_stage == 1 and s.e.ladder_mode == 'CAUTION_2_4'
      and s.shares > 0)

print('-- 5 idle days -> second skim -> 5 more -> FINAL_OUT (손절) --')
s = Sim(cash=1_500_000)
s.prev_close = 100_000
s.poll(99_000)
s.poll(trim_buy_price(T, 97_000))
s.poll(97_100)
lower = s.e.lines['lower'][0]
s.poll(lower)
s.poll(lower)                       # stage 1, 밑장 pending
mid = int((s.e.skim_rebuy_line + s.e.lines['tier1'][0]) / 2 // 100 * 100)
days = ['2026-07-10', '2026-07-13', '2026-07-14', '2026-07-15', '2026-07-16']
for d in days[:-1]:
    s.date = d
    s.poll(mid)
    check(f'{d}: still waiting', not places(s.last_acts), s.e.status)
s.date = days[-1]
acts = s.poll(mid)
check('idle day 5 -> second skim fires',
      s.e.stage_idle_days >= IDLE_LIMIT_DAYS and places(acts)
      and '밑장' in places(acts)[0][4], (s.e.stage_idle_days, places(acts)))
s.poll(mid)
check('stage 2, mode 1/2', s.e.emergency_stage == 2
      and s.e.ladder_mode == 'FINAL_1_2')
mid2 = int((s.e.skim_rebuy_line + s.e.lines['tier1'][0]) / 2 // 100 * 100)
days2 = ['2026-07-17', '2026-07-20', '2026-07-21', '2026-07-22', '2026-07-23']
for d in days2[:-1]:
    s.date = d
    s.poll(mid2)
s.date = days2[-1]
q_hold = s.shares
acts = s.poll(mid2)
check('idle day 5 at stage 2 -> FINAL_OUT sell',
      notifies(acts) and places(acts) and places(acts)[0][1] == 'SELL'
      and places(acts)[0][3] == q_hold, places(acts))
s.poll(mid2)
check('손절 done: flat, campaign cleared',
      s.shares == 0 and not s.e.campaign_active and not s.e.final_out)

print('-- stage 2 + lower touch + unaffordable double -> immediate 손절 --')
s = Sim(cash=1_400_000)
s.prev_close = 100_000
s.poll(99_000)
s.poll(trim_buy_price(T, 97_000))
s.poll(97_100)
s.poll(s.e.lines['lower'][0])       # unaffordable -> skim #1 fires + fills
s.poll(91_200)                      # fill seen -> stage 1 pending
check('stage 1 after skim #1', s.e.emergency_stage == 1)
s.e._clear_skim()                   # simulate: first rebuy plan resolved
acts = s.poll(90_000)               # below lower again -> skim #2
check('second skim on second unaffordable double',
      places(acts) and '밑장' in places(acts)[0][4], places(acts))
s.poll(90_100)                      # fill seen -> stage 2 pending
check('now stage 2', s.e.emergency_stage == 2
      and s.e.ladder_mode == 'FINAL_1_2')
s.e._clear_skim()
acts = s.poll(85_000)               # stage 2 lower touch, no double money
check('stage 2 lower touch, no double money -> FINAL_OUT',
      notifies(acts) and places(acts) and places(acts)[0][1] == 'SELL',
      places(acts))

print('-- cross-battlefield 밑장 lock blocks the double --')
s = Sim(cash=100_000_000)
s.prev_close = 100_000
s.poll(99_000)
s.poll(trim_buy_price(T, 97_000))
s.poll(97_100)
s.locks = s.cash                    # another battlefield locked everything
acts = s.poll(s.e.lines['lower'][0])
check('double blocked by foreign lock -> skim',
      places(acts) and '밑장' in places(acts)[0][4], places(acts))

print('-- restart recovery: the ledger survives via to_dict --')
s = Sim(cash=100_000_000)
s.prev_close = 100_000
s.poll(99_000)
s.poll(trim_buy_price(T, 97_000))
s.poll(97_100)
s.poll(s.e.lines['lower'][0])
s.poll(96_000)                      # double landed
saved = s.e.to_dict()
e2 = TazzaEngine(T, saved=saved)
snap = s.snap(96_000)
e2.poll(snap)
check('restored B/S/K equal', (e2.B, e2.S, e2.K) == (s.e.B, s.e.S, s.e.K))
check('restored sell lines equal', e2.lines.get('tier1') == s.e.lines.get('tier1'),
      (e2.lines.get('tier1'), s.e.lines.get('tier1')))

print()
print('ALL TAZZA CHECKS PASSED')
