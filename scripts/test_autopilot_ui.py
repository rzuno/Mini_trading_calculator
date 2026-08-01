"""Headless checks for the campaign cockpit and the controller.

Run:  python scripts/test_autopilot_ui.py

The render checks create no Tk window — they exercise the pure presentation
helpers of `gui/campaign_window.py` and `gui/candle_chart.py`. The last
section uses a withdrawn root so the real `AutopilotController` can be driven
end to end against a fake Toss provider: that is the path that silently broke
once (a leftover grid-only field in the UI push made every poll throw before
anything reached the screen), so it is covered here on purpose.
"""

import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

from core.calc import fmt_price
from gui.campaign_window import (CampaignWindow, buy_lines, campaign_age_line,
                                 campaign_line, fill_log_rows, next_line)
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


def deployed_ui(**over):
    """A mid-campaign ui dict: G3, 31 sh @ 92.00, price 91.20."""
    ui = {
        'ticker': 'NVDA', 'state': 'DEPLOYED', 'campaign_state': 'DEPLOYED',
        'status': 'watching', 'ts': '10:31:02', 'mode': 'WATCH',
        'phase': 'REGULAR', 'buy_state': 'OK',
        'campaign': {'gear': 3, 'gear_name': 'Balanced', 'exit_tier': 2,
                     'exit_pct': 5, 'load_pct': 8, 'chase_pct': 6,
                     'add_frac': '3/4', 'vantage': 100.0,
                     'vantage_src': 'high5',
                     'campaign_id': 'NVDA-20260801-090000',
                     'campaign_start': '2026-08-01 09:00', 'chase_count': 1,
                     'campaign_low': 86.0, 'max_qty': 31, 'max_cost': 2852.0,
                     'manually_modified': False, 'exit_price': 96.6,
                     'gross_target': 142.6},
        'lines': {
            'chase':  {'price': 86.48, 'qty': 23, 'kind': 'chase',
                       'label': 'CHASE -6% ×3/4', 'armed': True},
            'chase2': {'price': 84.26, 'qty': 41, 'kind': 'chase2',
                       'label': 'chase 2', 'armed': False},
            'chase3': {'price': 82.09, 'qty': 71, 'kind': 'chase3',
                       'label': 'chase 3', 'armed': False},
            'exit':   {'price': 96.60, 'qty': 31, 'kind': 'exit',
                       'label': 'EXIT T2 +5%', 'armed': True},
        },
        'crossed': {'BUY': None, 'SELL': None},
        'events': [
            {'date': '2026-07-31', 'ts': '07/31 09:31', 'kind': 'LOAD',
             'qty': 11, 'price': 92.0, 'shares': 11, 'avg': 92.0,
             'source': 'BOT', 'note': ''},
            {'date': '2026-08-01', 'ts': '08/01 11:02', 'kind': 'CHASE',
             'qty': 20, 'price': 86.5, 'shares': 31, 'avg': 92.0,
             'source': 'EXT', 'note': '#1'},
        ],
        'price': 91.2, 'shares': 31, 'avg_cost': 92.0,
        'buying_power': 4200.0, 'unit_cash': 1000.0, 'orders': [],
        'ticks': [(1.0, 92.0), (2.0, 91.5), (3.0, 91.2)], 'day_v_avg': 3.1,
    }
    ui.update(over)
    return ui


def flat_ui(**over):
    ui = {
        'ticker': 'NVDA', 'state': 'FLAT', 'campaign_state': 'FLAT',
        'status': 'watching', 'ts': '10:31:02', 'mode': 'WATCH',
        'phase': 'REGULAR', 'buy_state': 'OK',
        'campaign': {'gear': 3, 'gear_name': 'Balanced', 'exit_tier': 2,
                     'exit_pct': 5, 'load_pct': 8, 'chase_pct': 6,
                     'add_frac': '3/4', 'vantage': 100.0,
                     'vantage_src': 'high5', 'campaign_id': None,
                     'campaign_start': None, 'chase_count': 0,
                     'campaign_low': None, 'max_cost': 0.0,
                     'manually_modified': False, 'gross_target': None},
        'lines': {
            'load':   {'price': 92.0, 'qty': 11, 'kind': 'load',
                       'label': 'LOAD -8%', 'armed': True},
            'chase2': {'price': 86.48, 'qty': 8, 'kind': 'chase2',
                       'label': 'chase 2', 'armed': False},
            'pexit':  {'price': 96.60, 'qty': 11, 'kind': 'pexit',
                       'label': 'exit if loaded T2 +5%', 'armed': False},
        },
        'crossed': {'BUY': None, 'SELL': None}, 'events': [],
        'price': 95.0, 'shares': 0, 'avg_cost': 0.0,
        'buying_power': 4200.0, 'unit_cash': 1000.0, 'orders': [],
        'ticks': [], 'day_v_avg': 3.1,
    }
    ui.update(over)
    return ui


print('— campaign banner —')
line = campaign_line(deployed_ui(), 'USD')
ok('G3 Balanced' in line and 'LOAD -8%' in line and 'CHASE -6% ×3/4' in line,
   'the banner names the whole gear in one line', line)
ok('EXIT T2 +5% (full)' in line,
   'the banner says the exit is a FULL-position sell')
ok('vantage 100.00 (High5)' in line, 'the vantage and its source are shown')
ok('cap' not in line, 'no capital cap is advertised — the army is the wall')
ok(campaign_line({}, 'USD') == 'arming…', 'an empty ui reads as arming')

age = campaign_age_line(deployed_ui(), 'USD')
ok('NVDA-20260801-090000' in age and 'chases 1' in age and 'low 86.00' in age,
   'the campaign line carries id, chases and the campaign low', age)
ok(campaign_age_line(flat_ui(), 'USD') == '',
   'no campaign line before the first LOAD fills')

print('— the next lines, and the ones after —')
nxt = next_line(deployed_ui(), 'USD')
ok(nxt.index('▲ EXIT 96.60 × 31') < nxt.index('▼ CHASE'),
   'the EXIT is named first (top of the chart), then the next buy', nxt)
ok('▼ CHASE -6% ×3/4 86.48 × 23' in nxt,
   'the armed buy carries its price AND its size', nxt)
ok('then 84.26 × 41  ·  82.09 × 71' in nxt,
   'the two chases after the armed one are shown', nxt)
ok('[NO ARMY]' in next_line(deployed_ui(buy_state='EXHAUSTED'), 'USD'),
   'an unfundable buy line is flagged in the next-line row')

fnxt = next_line(flat_ui(), 'USD')
ok('▲ exit if loaded 96.60 × 11' in fnxt and '▼ LOAD -8% 92.00 × 11' in fnxt,
   'a flat card shows the LOAD and marks its exit as projected', fnxt)
ok(next_line({'lines': {}}, 'USD') == '▲ no exit line      ▼ no buy line',
   'a bare ui degrades to a readable placeholder')

ladder = buy_lines(deployed_ui())
ok([e['kind'] for e in ladder] == ['chase', 'chase2', 'chase3'],
   'the buy ladder comes back in firing order')
ok(ladder[0]['armed'] and not any(e['armed'] for e in ladder[1:]),
   'exactly one buy line is armed')

print('— campaign log —')
rows = fill_log_rows(deployed_ui(), 'USD')
text = '\n'.join(r[0] for r in rows)
ok(len([r for r in rows if r[1] == 'DAY']) == 2,
   'each trading day gets its own header row', text)
ok('2026-07-31' in text and '2026-08-01' in text,
   'the log records the DAY, not only the clock')
ok('LOAD    +11 @ 92.00  → 11 sh @ 92.00' in text,
   'a fill row reads trade, price, resulting position and average', text)
ok('[hand]' in text, 'a fill made in the broker app is marked')
ok([r[1] for r in rows if r[1] != 'DAY'] == ['LOAD', 'CHASE'],
   'each row is tagged with its kind so the log can be colored')
ok(fill_log_rows({'events': []}, 'USD') == [],
   'no log rows before the first fill')

note = fill_log_rows({'events': [
    {'date': '2026-08-01', 'ts': '08/01 12:00', 'kind': 'GEAR', 'qty': 0,
     'price': None, 'shares': 31, 'avg': 92.0, 'source': 'BOT',
     'note': 'G3 → G5 (chase -8% ×1.0)'}]}, 'USD')
ok('GEAR    G3 → G5' in note[-1][0],
   'a gear shift on a live campaign is written into the log', note[-1][0])

print('— chart rows —')
window = CampaignWindow.__new__(CampaignWindow)
window.ccy = 'USD'
rows = window._line_rows(deployed_ui())
by_text = {r[2]: r for r in rows}
ok(len(rows) == 6,
   '3 buys + avg + exit + vantage each get a row', str(list(by_text)))
bold = [r for r in rows if r[3]]
ok(len(bold) == 3,
   'only the armed buy, the average and the exit are bold',
   str([r[2] for r in bold]))
ok(any(t.startswith('CHASE -6% ×3/4 86.48 × 23') for t in by_text),
   'the armed chase row carries its size')
ok(any('chase 2 84.26 × 41' in t for t in by_text),
   'the projected chase rows are drawn too')
ok(any(t.startswith('EXIT T2 +5%') for t in by_text), 'the exit row is drawn')
ok(any(t.startswith('avg 92.00') for t in by_text), 'the average is drawn')

muted = window._line_rows(deployed_ui(buy_state='EXHAUSTED'))
mrows = [r for r in muted if r[2].startswith('✕')]
ok(len(mrows) == 1 and 'no army' in mrows[0][2] and mrows[0][1] == '#999999',
   'the unfundable armed buy is muted with ✕ … (no army)')

frows = window._line_rows(flat_ui())
ok(any('(projected)' in r[2] for r in frows),
   "a flat card's exit row says it is a projection")

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
long_label = 'CHASE -6% ×3/4 86.48 × 23 (no army)'
ok(required_label_pad([long_label], lambda s: len(s) * 10,
                      minimum=100, padding=16) == len(long_label) * 10 + 16,
   'long chart labels receive enough measured right margin')
x, anchor, wrap = bounded_label_layout(160, 100)
ok(anchor == 'e' and wrap is None and x - 100 >= 0,
   'narrow fallback keeps a fitting label inside both canvas edges')
x, anchor, wrap = bounded_label_layout(160, 300)
ok(anchor == 'w' and x >= 0 and x + wrap <= 160,
   'label wider than the canvas receives a bounded wrap width')

print('— avg completed-day V —')
bars6 = ([{'date': f'07/{14 + i}', 'ts': f'2026-07-{14 + i}T00:00:00',
           'open': 100, 'high': 100.0 + i + 1, 'low': 100.0, 'close': 100}
          for i in range(5)]                      # day ranges 1% … 5%
         + [{'date': '07/19', 'ts': '2026-07-19T00:00:00', 'open': 100,
             'high': 150.0, 'low': 100.0, 'close': 120}])   # today, 50%
ok(near(avg_completed_day_v(bars6, '2026-07-19'), 3.0),
   "today's in-progress bar is excluded (avg of 1..5% = 3%)")
ok(near(avg_completed_day_v(bars6[:5], '2026-07-19'), 3.0),
   'works when today has no bar yet')
ok(avg_completed_day_v([], '2026-07-19') is None, 'no bars → no indicator')
ok(near(avg_bar_day_v(bars6[:5]), 3.0), 'panel fallback averages its bars')

print('— live candle fold —')
today = [{'date': '07/19', 'ts': '2026-07-19T00:00:00', 'open': 100,
          'high': 105.0, 'low': 98.0, 'close': 101.0}]
folded = merge_live_bar(today, 107.0, '2026-07-19')
ok(folded[-1]['close'] == 107.0 and folded[-1]['high'] == 107.0
   and folded[-1]['low'] == 98.0,
   "today's candle follows the live price (close moves, high stretches)")
ok(today[-1]['close'] == 101.0, 'the source bars stay untouched')
ok(merge_live_bar(today, 107.0, '2026-07-20')[-1]['close'] == 101.0,
   'no fold when the last bar is not today')
ok(merge_live_bar([], 107.0, '2026-07-19') == [], 'empty bars stay empty')

print('— card spacing —')
ok(_AP_BUTTON_TOP_GAP == 18, 'Autopilot card button has one line of top gap')


# ── The controller, end to end against a fake broker ─────────────────────────
print('— controller: a poll reaches the screen —')


class _StubRow:
    def __init__(self, ticker, gear=3, tier=2):
        self.ticker = ticker
        self._cfg = {'gear': gear, 'exit_tier': tier}
        self.badges = []
        self.live_price = None

    def line_config(self):
        return dict(self._cfg)

    def set_autopilot(self, key):
        self.badges.append(key)

    def update_live(self, price=None, **kw):
        self.live_price = price

    def compute(self):
        pass


class _FakeProvider:
    """Just enough Toss surface for one poll cycle."""
    name = 'toss'

    def __init__(self, price=91.2, shares=31, avg=92.0, bp=4200.0):
        self.price, self.shares, self.avg, self.bp = price, shares, avg, bp

    def get_prices(self, tickers):
        return {t: self.price for t in tickers}

    def get_holdings(self, seq, ticker=None):
        return {'items': [{'symbol': ticker, 'quantity': self.shares,
                           'averagePurchasePrice': self.avg}]}

    def get_open_orders(self, seq, ticker=None):
        return []

    def get_buying_power(self, seq, ccy):
        return self.bp

    def get_completed_daily_bars(self, ticker, count):
        return [{'ts': f'2026-07-2{i}T00:00:00', 'date': f'07/2{i}',
                 'open': 100.0, 'high': 100.0 + i, 'low': 90.0 - i,
                 'close': 95.0} for i in range(5)]

    def get_candles(self, ticker, count=6):
        return self.get_completed_daily_bars(ticker, count)


class _StubApp:
    def __init__(self, root, prov):
        self.root = root
        self._auto = True
        self._prov = prov
        self.deployed_rows = [_StubRow('NVDA')]
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
_prov = _FakeProvider()
ctrl = AutopilotController(_StubApp(_root, _prov))
ctrl._log = lambda *a: None            # keep the test out of logs/
ctrl._store = {}
ctrl._save_state = lambda *a: True     # never touch data/

ok(ctrl.watch('NVDA')[0], 'watching arms the campaign engine')
ok(ctrl._slots['NVDA']['card'] == {'gear': 3, 'exit_tier': 2},
   "watching pulls the card's gear config immediately")

slot = ctrl._slots['NVDA']
ctrl._cycle('NVDA', slot)              # the path that used to throw
_root.update()                         # run the queued tk callback
ui = ctrl.ui_state('NVDA')
ok(ui is not None and ui.get('status'), 'one poll produces a ui payload')
ok(ui['state'] == 'DEPLOYED' and ui['shares'] == 31,
   'the poll reconciled the broker position', str(ui['state']))
ok(ui['lines'].get('chase') and ui['lines'].get('exit'),
   'the ui carries both watched lines', str(list(ui['lines'])))
ok(near(ui['lines']['exit']['price'], 96.6, 0.011),
   'the EXIT line is the broker average +5%',
   str(ui['lines']['exit']['price']))
ok(ui['lines']['exit']['qty'] == 31,
   'the EXIT covers the whole position')
ok(ui['campaign'] and ui['campaign']['gear'] == 3,
   'the campaign summary rides along')
ok('anchor' not in ui and 'grid' not in ui,
   'no grid-only fields survive in the campaign payload')

# The window must render that payload without touching Tk geometry.
window.ui = ui
ok(len(window._line_rows(ui)) >= 3,
   'the window turns a real poll payload into chart rows')
ok(campaign_line(ui, 'USD').startswith('G3'),
   'and into a readable banner', campaign_line(ui, 'USD'))

_prov.price = 97.0                     # cross the EXIT in WATCH mode
ctrl._cycle('NVDA', slot)
_root.update()
ui = ctrl.ui_state('NVDA')
ok(ui['crossed']['SELL'] and not ui['crossed']['BUY'],
   'crossing the exit in WATCH marks the line, and sends nothing')
ok('not sent' in ui['status'], 'WATCH says plainly that it did not send',
   ui['status'])

ctrl.disable('NVDA')
ok(not ctrl.is_enabled('NVDA'), 'disable stops the watch')
ok(ctrl._store_key('NVDA') == 'NVDA#VCG',
   'campaign state saves under its own key, leaving grid state alone')
ctrl._store = {'NVDA': {'anchor': 100.0, 'level': 0}}
ok(ctrl._saved_for('NVDA') is None,
   'a leftover v^ grid record is never restored as a campaign')
_root.destroy()

print(f'\nALL {passed} UI CHECKS PASSED')
