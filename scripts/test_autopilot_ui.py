"""Headless checks for Autopilot presentation helpers (Daily v^ grid).

Run:  python scripts/test_autopilot_ui.py

No Tk window is created; these checks cover the render decisions that can be
verified without a display.
"""

import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui.autopilot_window import (AutopilotWindow, fills_text, next_line,
                                  next_transitions)
from gui.candle_chart import (bounded_label_layout, candle_color,
                              required_label_pad)
from gui.stock_row import _AP_BUTTON_TOP_GAP


passed = 0


def ok(cond, name, info=''):
    global passed
    assert cond, f'FAIL: {name} {info}'
    passed += 1
    print(f'  ok  {name}')


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

print('— card spacing —')
ok(_AP_BUTTON_TOP_GAP == 18, 'Autopilot card button has one line of top gap')

print(f'\nALL {passed} UI CHECKS PASSED')
