"""Headless checks for the Daily v^ grid cockpit (gui/autopilot_window.py).

Run:  python scripts/test_autopilot_ui.py

Two layers, and the second one exists because of the v0.2.0 lesson: a
window tested only against hand-written payloads proved nothing when the
controller's real payload differed. So:

  1. the window's pure helpers (next transitions, banner lines, fills,
     scale styling, grid rows) against constructed ui dicts;
  2. the REAL AutopilotController driven through a full `_cycle` against a
     fake provider, asserting on the payload it actually emits — including
     that no campaign-era field survives in it;
  3. a real window built against that payload (withdrawn Tk root), with the
     inline LIVE confirmation exercised — no messagebox anywhere.
"""

import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import gui.autopilot_window as win_mod
from gui.autopilot_window import (AutopilotWindow, ScaleSelector, fills_text,
                                  next_line, next_transitions, scale_style)
from core.autopilot import (GRID_SCALES, LEVEL_CAP, cum_weight,
                            level_raw_price, target_inventory)

passed = 0


def ok(cond, name, info=''):
    global passed
    assert cond, f'FAIL: {name} {info}'
    passed += 1
    print(f'  ok  {name}')


def make_grid(anchor=100.0, step=0.03, base=10, unit=1, anchor_level=0):
    return [{'level': k,
             'price': level_raw_price(anchor, k, anchor_level, step=step),
             'target': target_inventory(k, base, unit),
             'anchor': k == anchor_level}
            for k in range(LEVEL_CAP, -LEVEL_CAP - 1, -1)]


def grid_ui(**over):
    """A mid-adventure ui dict: anchor 100 @ L0, base 10, unit 1, level 0."""
    ui = {
        'ticker': 'NVDA', 'state': 'WATCHING', 'status': 'watching',
        'ts': '10:31:02', 'mode': 'WATCH', 'phase': 'REGULAR',
        'grid': make_grid(), 'level': 0, 'grid_ready': True,
        'anchor': 100.0, 'anchor_level': 0, 'step': 0.03,
        'scale_locked': False, 'day_v_avg': 9.0,
        'reference_close': 100.0, 'opening_price': 100.5,
        'gap_mode': 'NONE', 'base_inventory': 10, 'unit_qty': 1,
        'buy_value': 0.0, 'sell_value': 0.0, 'fills': 0, 'events': [],
        'buy_state': 'OK', 'alert': None,
        'price': 100.5, 'shares': 10, 'avg_cost': 98.0,
        'buying_power': 4200.0, 'unit_cash': 100.0, 'orders': [],
        'ticks': [(1.0, 100.0), (2.0, 100.5)],
    }
    ui.update(over)
    return ui


print('— next transitions —')
up, dn = next_transitions(grid_ui())
ok(up and up['level'] == 1 and up['side'] == 'SELL' and up['qty'] == 1,
   'from L0 the up transition sells one unit at L+1', str(up))
ok(dn and dn['level'] == -1 and dn['side'] == 'BUY' and dn['qty'] == 1,
   'and the down transition buys one unit at L-1', str(dn))
up2, dn2 = next_transitions(grid_ui(level=2, shares=7))
ok(up2['qty'] == abs(target_inventory(3, 10, 1) - 7) == 3
   and up2['side'] == 'SELL',
   'weights grow with depth: L+3 trades three units', str(up2))
up_edge, dn_edge = next_transitions(grid_ui(level=5, shares=0))
ok(up_edge is None, 'above the zone there is no up transition')
up0, dn0 = next_transitions(grid_ui(grid=make_grid(base=0),
                                    base_inventory=0, shares=0))
ok(up0['side'] == '—' and dn0['side'] == 'BUY',
   'nothing to sell → up is watch-only, while the first dip still buys',
   f'{up0} {dn0}')

print('— the banner lines —')
line = next_line(grid_ui(), 'USD')
ok('▲ L+1 @ 103.00 → SELL 1' in line and '▼ L-1 @ 97.00 → BUY 1' in line,
   'the two adjacent transitions are named with their trades', line)
edge = next_line(grid_ui(level=5, shares=0), 'USD')
ok('edge of the zone (+5)' in edge, 'the zone edge is named', edge)
army = next_line(grid_ui(buy_state='EXHAUSTED'), 'USD')
ok('[NO ARMY]' in army, 'an unfundable buy carries the NO ARMY tag', army)
ok(next_line(grid_ui(grid_ready=False), 'USD') == '',
   'no line before the grid exists')

print('— fills —')
ok(fills_text(grid_ui(), 'USD') == '', 'no fills → no fills block')
evs = [{'ts': '07/31 09:4%d' % i, 'kind': f'BUY L-{i}', 'qty': +i,
        'price': 97.0, 'shares': 10 + i} for i in range(1, 11)]
txt = fills_text(grid_ui(events=evs), 'USD')
ok(txt.startswith('오늘 fills (10) — last 8'),
   'long days truncate to the newest fills', txt.splitlines()[0])
ok('BUY L-10' in txt and 'BUY L-1 ' not in txt,
   'and the newest are the ones kept')

print('— scale styling —')
clr_small, pt_small = scale_style(0.02)
clr_big, pt_big = scale_style(0.04)
ok(pt_small < pt_big,
   'tight grids read small, wide grids read big', f'{pt_small} vs {pt_big}')
ok(scale_style(0.031)[1] == scale_style(0.03)[1],
   'an off-menu value renders as its nearest offered scale')

print('— grid rows (shared by both charts) —')
_probe = AutopilotWindow.__new__(AutopilotWindow)
_probe.ccy = 'USD'
rows = _probe._grid_rows(grid_ui())
by_text = {r[2]: r for r in rows}
ok(len(rows) == 11, 'one row per level')
anchor_rows = [r for r in rows if '(A)' in r[2]]
ok(len(anchor_rows) == 1 and 'L+0 (A)' in anchor_rows[0][2],
   'exactly one anchor row, marked (A) at L+0', anchor_rows[0][2])
ok(any('SELL 1' in t for t in by_text if 'L+1' in t)
   and any('BUY 1' in t for t in by_text if 'L-1' in t),
   'the two adjacent rows carry their trades')
gap_rows = _probe._grid_rows(grid_ui(
    grid=make_grid(anchor=90.0, anchor_level=-1), anchor=90.0,
    anchor_level=-1, level=-1, gap_mode='DOWN'))
a = [r for r in gap_rows if '(A)' in r[2]]
ok(len(a) == 1 and 'L-1 (A)' in a[0][2],
   'on a down-gap day the anchor marker sits at L-1 — the OPEN is the anchor',
   a[0][2])
noarmy = _probe._grid_rows(grid_ui(buy_state='EXHAUSTED'))
ok(any(t.startswith('✕') and '(no army)' in t for _, _, t, _ in noarmy),
   'an unfundable buy line is muted with ✕ … (no army)')
here = [t for _, _, t, _ in _probe._grid_rows(grid_ui(level=3, shares=4))
        if '← here' in t]
ok(here and 'L+3' in here[0],
   'the current level carries its ← here marker when it is not a watch line',
   str(here))

# ── The real controller feeds the window ────────────────────────────────────
print('— the real controller payload —')
import gui.autopilot_ctrl as ctrl_mod
from gui.autopilot_ctrl import AutopilotController

_real_phase = ctrl_mod.market_phase
ctrl_mod.market_phase = lambda *a, **k: 'REGULAR'


class FakeProvider:
    def __init__(self, price=95.0, shares=10, avg=92.0, bp=1_000_000.0):
        self.price, self.shares, self.avg, self.bp = price, shares, avg, bp
        self.orders = []
        self.placed = []

    def get_prices(self, tickers):
        return {t: self.price for t in tickers}

    def get_holdings(self, seq, ticker=None):
        return {'items': [{'symbol': ticker, 'quantity': self.shares,
                           'averagePurchasePrice': self.avg}]}

    def get_open_orders(self, seq, ticker=None):
        return list(self.orders)

    def get_buying_power(self, seq, ccy):
        return self.bp

    def get_completed_daily_bars(self, ticker, count):
        return [{'ts': f'2026-07-2{i}T00:00:00', 'date': f'07/2{i}',
                 'open': 95.0, 'high': 96.0 + i, 'low': 93.0 - i,
                 'close': 95.0} for i in range(5)]

    def get_candles(self, ticker, count=6):
        return self.get_completed_daily_bars(ticker, count)

    def place_limit_order(self, *a, **k):
        self.placed.append((a, k))
        return 200, {'result': {'orderId': 'o1'}}

    def cancel_order(self, oid, seq):
        return 200, {}


class StubRow:
    def __init__(self, ticker):
        self.ticker = ticker
        self.badges = []
        self.touched = 0

    def set_autopilot(self, key):
        self.badges.append(key)

    def update_live(self, **kw):
        self.touched += 1

    def compute(self):
        self.touched += 1


class StubApp:
    def __init__(self, root, prov):
        self.root = root
        self._auto = True
        self._prov = prov
        self.deployed_rows = [StubRow('NVDA')]
        self.empty_rows = []
        self._ohlc_data = {}

    def _get_unit_cash(self, ccy):
        return 1000.0

    def _toss_provider(self):
        return self._prov

    def _account_seq(self, prov):
        return 1


_root = tk.Tk()
_root.withdraw()
_prov = FakeProvider()
_app = StubApp(_root, _prov)
_ctrl = AutopilotController(_app)
_ctrl._log = lambda *a: None
_ctrl._store = {}
_ctrl._save_state = lambda *a: True
_ctrl.watch('NVDA')
_slot = _ctrl._slots['NVDA']
_ctrl._cycle('NVDA', _slot)
_root.update()
payload = _ctrl.ui_state('NVDA')
ok(payload['grid_ready'] and len(payload['grid']) == 11
   and payload['anchor'] == 95.0,
   'a full _cycle emits a ready grid payload')
ok(payload['day_v_avg'] is not None,
   'with the daily-V scale hint on board', str(payload['day_v_avg']))
for dead in ('campaign', 'campaign_state', 'lines', 'crossed', 'vol5'):
    ok(dead not in payload,
       f'no campaign-era field survives in the payload ({dead})')
_prov.price = 98.5                       # crosses L+1 = 97.85 in WATCH
_ctrl._cycle('NVDA', _slot)
_root.update()
payload = _ctrl.ui_state('NVDA')
ok(payload['trigger'].get('SELL') and not _prov.placed,
   'crossing a level in WATCH exposes the trigger, and sends nothing',
   str(payload['trigger']))
ok('watching only' in payload['status'],
   'WATCH says plainly that it did not send', payload['status'])
ok(_app.deployed_rows[0].touched == 0,
   'two polls went by and the card was never written to or recomputed')
ok(_app.deployed_rows[0].badges and _app.deployed_rows[0].badges[-1] == 'WATCH',
   'only the badge colour reached the main window')

print('— the cockpit window, driven by that payload —')
ok(not hasattr(win_mod, 'messagebox'),
   'the window does not import messagebox — the LIVE decision is inline '
   'and modeless, so the card grid behind it never freezes')
_win = AutopilotWindow(_root, 'NVDA', 'USD',
                       _ctrl.graph_context('NVDA'))
_root.update()
ok(_win.ui is not None and _win._adv_lbl.cget('text').startswith('anchor'),
   'the window builds from the live payload and names the adventure',
   _win._adv_lbl.cget('text'))
ok('▲' in _win._next_lbl.cget('text'),
   'the next-transition line is on the banner')
ok(not _win._live_confirm_frame.winfo_manager(),
   'the LIVE confirmation starts hidden')
_win._on_live()
ok(_win._live_confirm_frame.winfo_manager(),
   'asking for LIVE shows the inline confirmation instead of a dialog')
ok('rebalances by ITSELF' in _win._live_confirm_lbl.cget('text'),
   'and it spells out what LIVE will do')
_win._hide_live_confirmation()
ok(not _win._live_confirm_frame.winfo_manager(),
   'Keep WATCH puts it away — nothing was armed')
_scale_before = _win._scale.label_text()
_win._on_scale(0.05)
ok('not offered' in _win._status_lbl.cget('text'),
   'a refused scale prints on the status line, not in a popup',
   _win._status_lbl.cget('text'))
_win.win.destroy()
_root.update()
ok(not _ctrl.is_enabled('NVDA'),
   'closing the cockpit in WATCH stops the watch')

ctrl_mod.market_phase = _real_phase
_root.destroy()

print(f'\nALL {passed} UI CHECKS PASSED')
