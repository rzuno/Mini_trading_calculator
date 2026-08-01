"""Headless checks for autopilot presentation helpers.

Run:  python scripts/test_autopilot_ui.py

Covers both cockpits — the Daily v^ grid window and the V-Commandos campaign
window — plus the controller's strategy-selection rules. The render checks
create no Tk window; the last section uses a withdrawn root so the real
controller can be exercised.
"""

import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui.autopilot_window import (AutopilotWindow, fills_text, next_line,
                                  next_transitions, scale_style)
from gui.campaign_window import (campaign_age_line, campaign_line,
                                 fills_text as campaign_fills,
                                 next_line as campaign_next_line)
import tkinter as tk

from gui.autopilot_ctrl import (AutopilotController, avg_completed_day_v,
                                merge_live_bar)
from gui.candle_chart import (avg_bar_day_v, bounded_label_layout,
                              candle_color, required_label_pad)
from gui.stock_row import _AP_BUTTON_TOP_GAP


passed = 0


def ok(cond, name, info=''):
    global passed
    assert cond, f'FAIL: {name} {info}'
    passed += 1
    print(f'  ok  {name}')


def near(a, b, eps=1e-6):
    return a is not None and abs(a - b) < eps


def grid_ui(anchor_level=0, **over):
    """A ready mid-adventure ui dict: anchor 100, level -1, 11 sh held."""
    grid = [{'level': k,
             'price': 100.0 * (1 + 0.03 * (k - anchor_level)),
             'target': max(0, 10 + (0, 1, 3, 6, 10, 15)[abs(k)]
                           * (1 if k < 0 else -1)),
             'anchor': k == anchor_level}
            for k in range(5, -6, -1)]
    ui = {
        'state': 'WATCHING', 'mode': 'WATCH', 'shares': 11, 'price': 96.5,
        'grid_ready': True, 'grid': grid, 'level': -1, 'anchor': 100.0,
        'anchor_level': anchor_level,
        'gap_mode': 'NONE' if anchor_level == 0 else
                    ('DOWN' if anchor_level < 0 else 'UP'),
        'base_inventory': 10, 'unit_qty': 1,
        'buy_value': 97.0, 'sell_value': 0.0, 'fills': 1,
        'buy_state': 'OK', 'events': [], 'orders': [],
    }
    ui.update(over)
    return ui


print('— next transitions —')
ui = grid_ui()
up, dn = next_transitions(ui)
ok(up and up['level'] == 0 and up['side'] == 'SELL' and up['qty'] == 1,
   'up from L-1 is the level-0 SELL 1', str(up))
ok(dn and dn['level'] == -2 and dn['side'] == 'BUY' and dn['qty'] == 2,
   'down from L-1 is the L-2 BUY 2', str(dn))
edge = grid_ui(level=5, shares=0)
up, dn = next_transitions(edge)
ok(up is None and dn is not None,
   'at +5 there is no upper watch line (outside the zone)')

print('— banner lines —')
events = []
line = next_line(grid_ui(events=events), 'USD')
ok('▲ L+0 @ 100.00 → SELL 1' in line and '▼ L-2 @ 94.00 → BUY 2' in line,
   'next-line names both adjacent transitions', line)
ok(events == [], 'rendering the banner creates no fill event')
ok('[NO ARMY]' in next_line(grid_ui(buy_state='EXHAUSTED'), 'USD'),
   'unfunded down transition is marked in the banner, no popup')
ok(next_line({'grid_ready': False}, 'USD') == '',
   'no next-line before the grid is built')
ok('edge of the zone' in next_line(grid_ui(level=5, shares=0), 'USD'),
   'the zone edge is named at ±5')

ok(fills_text({'events': []}, 'USD') == '',
   'no fills today → the fills block is hidden')
evs = [{'ts': f'07/21 10:{i:02d}', 'kind': 'BUY L-1', 'qty': 1,
        'price': 97.0, 'shares': 10 + i} for i in range(10)]
txt = fills_text({'events': evs}, 'USD')
ok(txt.splitlines()[0] == '오늘 fills (10) — last 8:',
   'a long day truncates to the newest 8 fills', txt.splitlines()[0])
ok(len(txt.splitlines()) == 9 and '10:09' in txt and '10:00' not in txt,
   'the newest fills are the ones kept')
short = fills_text({'events': evs[:2]}, 'USD')
ok(short.startswith('오늘 fills (2):') and '+1 @ 97.00' in short,
   'a short day lists every fill')

print('— grid line rows —')
window = AutopilotWindow.__new__(AutopilotWindow)
window.ccy = 'USD'
rows = window._grid_rows(grid_ui())
by_text = {r[2]: r for r in rows}
ok(len(rows) == 11, 'all 11 levels get a row')
anchor_rows = [r for r in rows if '(A)' in r[2]]
ok(len(anchor_rows) == 1 and anchor_rows[0][3]
   and anchor_rows[0][2].startswith('L+0 (A)'),
   'the anchor row is bold and labeled L+0 (A)', str(anchor_rows))
ok(any('SELL 1' in t for t in by_text),
   'the adjacent up row carries its trade')
ok(any('BUY 2' in t for t in by_text), 'the adjacent down row carries BUY 2')
bold_rows = [r for r in rows if r[3]]
ok(len(bold_rows) == 2,
   'at L-1 the anchor IS the up watch line (anchor + L-2 bold)',
   str([r[2] for r in bold_rows]))

deep = grid_ui(level=-2, shares=13)
bold_rows = [r for r in window._grid_rows(deep) if r[3]]
ok(len(bold_rows) == 3
   and any('(A)' in t for _p, _c, t, _b in bold_rows),
   'at L-2 anchor + both watch lines are bold',
   str([r[2] for r in bold_rows]))

# Gap day: the open is the anchor at L-1; L0 is a plain upper level.
gap_rows = window._grid_rows(grid_ui(anchor_level=-1, level=-1))
gr = {r[2].split(' ')[0]: r for r in gap_rows}
a_row = [r for r in gap_rows if '(A)' in r[2]][0]
ok(a_row[2].startswith('L-1 (A)'), 'gap day: L-1 carries the (A) marker',
   a_row[2])
l0 = [r for r in gap_rows if r[2].startswith('L+0 ')][0]
ok('(A)' not in l0[2] and l0[1] in ('#CC3333', '#E4A9A9'),
   'gap day: L+0 is a plain upper (sell-side) level', str(l0))

rows = window._grid_rows(grid_ui(buy_state='EXHAUSTED'))
muted = [r for r in rows if r[2].startswith('✕')]
ok(len(muted) == 1 and 'no army' in muted[0][2] and muted[0][1] == '#999999',
   'unfunded down line is muted with ✕ … (no army)')

print('— chart labels and candle text —')
ok(candle_color({'open': 10, 'close': 11}) == '#CC3333',
   'up-candle OHLC text is red')
ok(candle_color({'open': 11, 'close': 10}) == '#3366CC',
   'down-candle OHLC text is blue')
ok(candle_color({'open': 10, 'close': 10}) == '#CC3333',
   'doji follows the historical red rule')
ok(required_label_pad(['short'], lambda s: len(s) * 10,
                      minimum=100, padding=16) == 100,
   'short chart labels retain the normal right margin')
long_label = 'L-2 94.00  BUY 2 (no army)'
ok(required_label_pad([long_label], lambda s: len(s) * 10,
                      minimum=100, padding=16) == len(long_label) * 10 + 16,
   'long chart labels receive enough measured right margin')
x, anchor, wrap = bounded_label_layout(160, 100)
ok(anchor == 'e' and wrap is None and x - 100 >= 0,
   'narrow fallback keeps a fitting label inside both canvas edges')
x, anchor, wrap = bounded_label_layout(160, 300)
ok(anchor == 'w' and x >= 0 and x + wrap <= 160,
   'label wider than the canvas receives a bounded wrap width')

print('— gap-day chart rows (superposition fix) —')
gap = grid_ui(anchor_level=1, level=0, shares=10)
rows = window._grid_rows(gap)
a_rows = [r for r in rows if '(A)' in r[2]]
ok(len(a_rows) == 1 and a_rows[0][2].startswith('L+1 (A)')
   and 'SELL' in a_rows[0][2],
   'anchor + coinciding watch line merge into ONE row (no superposition)',
   str([r[2] for r in a_rows]))
here = [r for r in rows if '← here' in r[2]]
ok(len(here) == 1 and here[0][2].startswith('L+0'),
   'the current level L+0 is marked on the chart', str(here))

print('— avg completed-day V (scale indicator) —')
bars6 = ([{'date': f'07/{14 + i}', 'ts': f'2026-07-{14 + i}T00:00:00',
           'open': 100, 'high': 100.0 + i + 1, 'low': 100.0, 'close': 100}
          for i in range(5)]                      # day ranges 1% … 5%
         + [{'date': '07/21', 'ts': '2026-07-21T00:00:00', 'open': 100,
             'high': 130.0, 'low': 100.0, 'close': 120}])   # today, growing
v = avg_completed_day_v(bars6, '2026-07-21')
ok(near(v, 3.0), "today's in-progress bar is excluded (avg of 1..5% = 3%)")
v = avg_completed_day_v(bars6[:5], '2026-07-21')
ok(near(v, 3.0), 'works when today has no bar yet')
ok(avg_completed_day_v([], '2026-07-21') is None, 'no bars → no indicator')
ok(near(avg_bar_day_v(bars6[:5]), 3.0), 'panel fallback averages its bars')

print('— live candle fold —')
bars = [
    {'date': '07/18', 'open': 96.0, 'high': 99.0, 'low': 95.0, 'close': 98.0},
    {'date': '07/21', 'ts': '2026-07-21T00:00:00+09:00', 'open': 100.0,
     'high': 101.0, 'low': 99.5, 'close': 100.4},
]
v = merge_live_bar(bars, 102.3, '2026-07-21')
ok(v[-1]['close'] == 102.3 and v[-1]['high'] == 102.3
   and v[-1]['low'] == 99.5,
   "today's candle follows the live price (close moves, high stretches)")
ok(bars[-1]['close'] == 100.4, 'the source bars stay untouched')
v = merge_live_bar(bars, 97.0, '2026-07-22')
ok(v[-1]['close'] == 100.4, 'no fold when the last bar is not today')
v = merge_live_bar([bars[0]], 97.0, '2026-07-21')
ok(v[-1]['close'] == 98.0, 'no today bar → nothing changes')
ok(merge_live_bar([], 97.0, '2026-07-21') == [], 'empty bars stay empty')
v = merge_live_bar([{'date': '07/21', 'open': 1.0, 'high': 1.0,
                     'low': 1.0, 'close': 1.0}], 1.2, '2026-07-21')
ok(v[0]['high'] == 1.2, 'MM/DD date fallback matches Yahoo-style bars')

print('— grid-scale identity —')
styles = [scale_style(s) for s in (0.02, 0.025, 0.03, 0.035, 0.04)]
colors = [c for c, _pt in styles]
sizes = [pt for _c, pt in styles]
ok(len(set(colors)) == 5 and len(set(sizes)) == 5,
   'each offered scale gets its own color and size', str(styles))
ok(sizes == sorted(sizes),
   'wider grids read bigger (2% smallest → 4% biggest)', str(sizes))
ok(colors[0] == '#8FB8DC' and colors[-1] == '#0A3468',
   'the blue deepens with the scale (faint 2% → deep 4%)', str(colors))
ok(scale_style(0.031) == scale_style(0.03),
   'an off-list value snaps to the nearest offered scale')
ok(scale_style(None) == scale_style(0.03),
   'a missing scale falls back to the middle grid')

print('— card spacing —')
ok(_AP_BUTTON_TOP_GAP == 18, 'Autopilot card button has one line of top gap')

# ── Campaign window presentation (V-Commandos) ───────────────────────────────
print('— campaign banner —')

_CAMPAIGN_UI = {
    'campaign': {'gear': 3, 'gear_name': 'Balanced', 'exit_tier': 2,
                 'exit_pct': 5, 'load_pct': 8, 'chase_pct': 6,
                 'add_frac': '3/4', 'vantage': 100.0, 'vantage_src': 'high5',
                 'cap_units': 32.0, 'campaign_id': 'T-20260801-090000',
                 'campaign_start': '2026-08-01 09:00', 'chase_count': 2,
                 'campaign_low': 84.2, 'max_cost': 4210.0,
                 'manually_modified': False},
    'lines': {'chase': (86.5, 12), 'exit': (96.6, 31)},
    'buy_state': 'OK',
    'events': [{'ts': '08/01 09:31', 'kind': 'LOAD', 'qty': 11, 'price': 92.0,
                'shares': 11, 'avg': 92.0, 'source': 'BOT'},
               {'ts': '08/01 11:02', 'kind': 'CHASE', 'qty': 8, 'price': 86.5,
                'shares': 19, 'avg': 89.7, 'source': 'EXTERNAL'}],
}

line = campaign_line(_CAMPAIGN_UI, 'USD')
ok('G3 Balanced' in line and 'LOAD -8%' in line and 'CHASE -6% ×3/4' in line,
   'the banner names the whole gear in one line', line)
ok('EXIT T2 +5% (full)' in line,
   'the banner says the exit is a FULL-position sell')
ok('vantage 100.00 (High5)' in line, 'the vantage and its source are shown')

nxt = campaign_next_line(_CAMPAIGN_UI, 'USD')
ok('▼ CHASE 86.50 × 12' in nxt and '▲ EXIT 96.60 × 31' in nxt,
   'the two watched lines read down-then-up', nxt)

capped = dict(_CAMPAIGN_UI, buy_state='CAPPED')
ok('[CAP]' in campaign_next_line(capped, 'USD'),
   'a capped chase is flagged in the next-line row')
broke = dict(_CAMPAIGN_UI, buy_state='EXHAUSTED')
ok('[NO ARMY]' in campaign_next_line(broke, 'USD'),
   'an unfundable chase is flagged in the next-line row')

flat = {'campaign': dict(_CAMPAIGN_UI['campaign'], campaign_id=None),
        'lines': {'load': (92.0, 11), 'pexit': (96.6, 11)}, 'events': []}
ok('▼ LOAD 92.00 × 11' in campaign_next_line(flat, 'USD')
   and 'projected' in campaign_next_line(flat, 'USD'),
   'a flat card shows the LOAD and marks its exit as projected')
ok(campaign_age_line(flat, 'USD') == '',
   'no campaign line before the first LOAD fills')

age = campaign_age_line(_CAMPAIGN_UI, 'USD')
ok('T-20260801-090000' in age and 'chases 2' in age and 'low 84.20' in age,
   'the campaign line carries id, chases and the campaign low', age)

log = campaign_fills(_CAMPAIGN_UI, 'USD')
ok('campaign fills (2)' in log and 'LOAD' in log and 'CHASE' in log,
   'the fill log lists this campaign only')
ok('EXT' in log and 'BOT' in log,
   'each fill says whether the bot or the commander made it')
ok(campaign_fills({'events': []}, 'USD') == '',
   'no fill log before the first fill')

# ── Strategy selection (controller, withdrawn root) ──────────────────────────
print('— two strategies, one at a time —')


class _StubRow:
    def __init__(self, ticker, gear=3, tier=2):
        self.ticker = ticker
        self._cfg = {'gear': gear, 'exit_tier': tier, 'cap_units': 32.0}
        self.badges = []

    def line_config(self):
        return dict(self._cfg)

    def set_autopilot(self, key, strategy='VCG'):
        self.badges.append((strategy, key))


class _StubApp:
    def __init__(self, root):
        self.root = root
        self._auto = True
        self.deployed_rows = [_StubRow('NVDA')]
        self.empty_rows = []

    def _get_unit_cash(self, ccy):
        return 1000.0


_root = tk.Tk()
_root.withdraw()
ctrl = AutopilotController(_StubApp(_root))
ctrl._log = lambda *a: None            # keep the test out of logs/

ok(ctrl.watch('NVDA')[0] and ctrl.strategy_of('NVDA') == 'VCG',
   'the default strategy is the V-Commandos campaign bot')
ok(ctrl._slots['NVDA']['card'] == {'gear': 3, 'exit_tier': 2,
                                   'cap_units': 32.0},
   "watching pulls the card's gear config immediately")

ctrl.set_card_config('NVDA', {'gear': 5, 'exit_tier': 1, 'cap_units': 32.0})
ok(ctrl._slots['NVDA']['card']['gear'] == 5,
   'the card pushes a gear change straight through to the engine snapshot')

ok(ctrl.watch('NVDA', 'VCG') == (True, 'already watching'),
   're-opening the same cockpit does not restart the watch')

ctrl._slots['NVDA']['ui']['shares'] = 31
okmsg = ctrl.watch('NVDA', 'GRID')
ok(not okmsg[0] and 'close it' in okmsg[1],
   'switching strategy is refused while the stock holds a position', okmsg[1])

ctrl._slots['NVDA']['ui']['shares'] = 0
ctrl._slots['NVDA']['mode'] = 'LIVE'
okmsg = ctrl.watch('NVDA', 'GRID')
ok(not okmsg[0] and 'LIVE' in okmsg[1],
   'switching strategy is refused while LIVE', okmsg[1])

ctrl._slots['NVDA']['mode'] = 'WATCH'
ok(ctrl.watch('NVDA', 'GRID')[0] and ctrl.strategy_of('NVDA') == 'GRID',
   'a flat, non-LIVE stock switches to the v^ grid')
ok(ctrl.watch('NVDA', 'NOPE') == (False, 'unknown strategy NOPE'),
   'an unknown strategy key is rejected')

ok(ctrl._store_key('NVDA', 'VCG') != ctrl._store_key('NVDA', 'GRID'),
   'each strategy saves its state under its own key')
ctrl._store = {'NVDA': {'legacy': True},
               'NVDA#VCG': {'campaign_id': 'x'}}
ok(ctrl._saved_for('NVDA', 'GRID') == {'legacy': True},
   'a pre-split state file still restores the grid')
ok(ctrl._saved_for('NVDA', 'VCG') == {'campaign_id': 'x'},
   'the campaign restores from its own key')

ctrl.disable('NVDA')
ok(ctrl.strategy_of('NVDA') is None, 'disable stops the watch')
_root.destroy()

print(f'\nALL {passed} UI CHECKS PASSED')
