"""Headless checks for Autopilot presentation helpers.

Run:  python scripts/test_autopilot_ui.py

No Tk window is created; these checks cover the render decisions that can be
verified without a display.
"""

import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui.autopilot_window import (AutopilotWindow, campaign_status_text,
                                  gear_text_color)
from gui.candle_chart import (bounded_label_layout, candle_color,
                              required_label_pad)
from gui.stock_row import _AP_BUTTON_TOP_GAP


passed = 0


def ok(cond, name, info=''):
    global passed
    assert cond, f'FAIL: {name} {info}'
    passed += 1
    print(f'  ok  {name}')


print('— campaign current status —')
deployed_events = []
deployed = {
    'state': 'DEPLOYED', 'mode': 'WATCH', 'shares': 12,
    'avg_cost': 100.0, 'price': 101.0, 'buy_state': 'OK',
    'lines': {'chase': (95.0, 8), 'tier2': (104.0, 12)},
    'events': deployed_events,
}
text = campaign_status_text(deployed, 'USD')
ok('CURRENT — DEPLOYED · WATCH' in text, 'deployed status names its state')
ok('Holding: 12 sh @ 100.00 avg' in text,
   'deployed status shows current holding and average')
ok('Next BUY: 8 @ 95.00' in text and 'Next SELL: T2 12@104.00' in text,
   'deployed status shows next buy and sell lines')
ok(deployed_events == [], 'rendering current status creates no fill event')

empty_events = []
empty = {
    'state': 'EMPTY', 'mode': 'WATCH', 'shares': 0, 'price': 98.0,
    'anchor': 100.0, 'anchor_source': 'close', 'buy_state': 'EXHAUSTED',
    'lines': {'load': (95.0, 11), 'psell': (99.0, 11)},
    'events': empty_events,
}
text = campaign_status_text(empty, 'USD')
ok('CURRENT — EMPTY · WATCH' in text and 'no position (0 sh)' in text,
   'empty status is visibly different from deployed status')
ok('Vantage: 100.00 (previous close)' in text,
   'empty status spells out Vantage')
ok('Next LOAD: 11 @ 95.00  [NO ARMY]' in text,
   'empty status shows unavailable next load')
ok('After LOAD, first SELL: 11 @ 99.00' in text,
   'empty status shows the pseudo first sell as context')
ok(empty_events == [], 'empty current status is not persisted as a fill')

print('— gear colors and card spacing —')
gear_colors = [gear_text_color(g) for g in range(1, 6)]
ok(gear_colors == ['#C62828', '#A64B00', '#806800', '#18733C', '#1565C0'],
   'G1..G5 use red/orange/yellow/green/blue')
ok(len(set(gear_colors)) == 5, 'every gear color is distinguishable')
ok(_AP_BUTTON_TOP_GAP == 18, 'Autopilot card button has one line of top gap')

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
long_label = 'SELL (pseudo) 1,333.69 ×11'
ok(required_label_pad([long_label], lambda s: len(s) * 10,
                      minimum=100, padding=16) == len(long_label) * 10 + 16,
   'long chart labels receive enough measured right margin')
x, anchor, wrap = bounded_label_layout(160, 100)
ok(anchor == 'e' and wrap is None and x - 100 >= 0,
   'narrow fallback keeps a fitting label inside both canvas edges')
x, anchor, wrap = bounded_label_layout(160, 300)
ok(anchor == 'w' and x >= 0 and x + wrap <= 160,
   'label wider than the canvas receives a bounded wrap width')

window = AutopilotWindow.__new__(AutopilotWindow)
window.ccy = 'USD'
ui = {'pct': 5, 'buy_state': 'OK',
      'lines': {'load': (95.0, 11), 'psell': (99.0, 11)}}
rows = window._live_reference_rows(
    ui, ui['lines'], deployed=False, avg=0, anchor=100.0)
labels = [row[2] for row in rows]
ok(any(label.startswith('Vantage 100.00') for label in labels)
   and not any('V.P.' in label for label in labels),
   'live chart uses Vantage instead of V.P.')
ok('SELL (pseudo) 99.00 ×11' in labels,
   'live pseudo-sell label is assembled in full')

print(f'\nALL {passed} UI CHECKS PASSED')
