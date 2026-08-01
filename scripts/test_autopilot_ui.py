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

from core.calc import calc_volatility, fmt_price, select_auto_gear
from gui.campaign_window import (CampaignWindow, buy_lines, campaign_age_line,
                                 campaign_line, fill_log_rows, next_line,
                                 sell_lines)
from gui.autopilot_ctrl import AutopilotController, merge_live_bar
from gui.candle_chart import (bar_day_labels, bar_range_v,
                              bounded_label_layout, candle_color,
                              required_label_pad)
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
                     'manually_modified': False, 'gross_target': 142.6,
                     'exit_tiers': [True, True, True],
                     'tier_done': [True, False, False],
                     'tier_pcts': (3, 5, 7), 'tier_text': 'T1+T2+T3'},
        'lines': {
            'chase':  {'price': 86.48, 'qty': 23, 'kind': 'chase',
                       'label': 'CHASE -6% ×3/4', 'armed': True},
            'chase2': {'price': 84.26, 'qty': 41, 'kind': 'chase2',
                       'label': 'chase 2', 'armed': False},
            'chase3': {'price': 82.09, 'qty': 71, 'kind': 'chase3',
                       'label': 'chase 3', 'armed': False},
            'exit2':  {'price': 96.60, 'qty': 16, 'kind': 'exit2',
                       'tier': 1, 'label': 'EXIT T2 +5%', 'armed': True},
            'exit3':  {'price': 98.44, 'qty': 15, 'kind': 'exit3',
                       'tier': 2, 'label': 'EXIT T3 +7%', 'armed': True},
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
        'ticks': [(1.0, 92.0), (2.0, 91.5), (3.0, 91.2)], 'vol5': 18.0,
        'card_auto': True,
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
                     'manually_modified': False, 'gross_target': None,
                     'exit_tiers': [False, True, False],
                     'tier_done': [False, False, False],
                     'tier_pcts': (3, 5, 7), 'tier_text': 'T2'},
        'lines': {
            'load':   {'price': 92.0, 'qty': 11, 'kind': 'load',
                       'label': 'LOAD -8%', 'armed': True},
            'chase1': {'price': 86.48, 'qty': 8, 'kind': 'chase1',
                       'label': 'chase 1', 'armed': False},
            'chase2': {'price': 84.26, 'qty': 14, 'kind': 'chase2',
                       'label': 'chase 2', 'armed': False},
            'pexit2': {'price': 96.60, 'qty': 11, 'kind': 'pexit2',
                       'tier': 1, 'label': 'exit if loaded T2 +5%',
                       'armed': False},
        },
        'crossed': {'BUY': None, 'SELL': None}, 'events': [],
        'price': 95.0, 'shares': 0, 'avg_cost': 0.0,
        'buying_power': 4200.0, 'unit_cash': 1000.0, 'orders': [],
        'ticks': [], 'vol5': 18.0, 'card_auto': True,
    }
    ui.update(over)
    return ui


print('— campaign banner —')
line = campaign_line(deployed_ui(), 'USD')
ok('G3 Balanced' in line and 'LOAD -8%' in line and 'CHASE -6% ×3/4' in line,
   'the banner names the whole gear in one line', line)
ok('EXIT T1 +3% / T2 +5% / T3 +7% (split)' in line,
   'the banner names every armed tier and says the exit is split', line)
ok('(full)' in campaign_line(flat_ui(), 'USD'),
   'and says (full) when only one tier is armed')
ok('vantage 100.00 (Dynamic High5)' in line,
   'the vantage and its source are shown', line)
ok('cap' not in line, 'no capital cap is advertised — the army is the wall')
ok(campaign_line({}, 'USD') == 'arming…', 'an empty ui reads as arming')

age = campaign_age_line(deployed_ui(), 'USD')
ok('NVDA-20260801-090000' in age and 'chases 1' in age and 'low 86.00' in age,
   'the campaign line carries id, chases and the campaign low', age)
ok(campaign_age_line(flat_ui(), 'USD') == '',
   'no campaign line before the first LOAD fills')

print('— the next lines, and the ones after —')
nxt = next_line(deployed_ui(), 'USD')
ok(nxt.index('▲ EXIT') < nxt.index('▼ CHASE'),
   'the exits are named first (top of the chart), then the next buy', nxt)
ok('T2 96.60 × 16  ·  T3 98.44 × 15' in nxt,
   'every armed tier is listed with its own price and portion', nxt)
ok([e['kind'] for e in sell_lines(deployed_ui())] == ['exit2', 'exit3'],
   'the exit ladder comes back low tier first')
ok([e['kind'] for e in sell_lines(flat_ui())] == ['pexit2'],
   'a flat card falls back to the projected exits')
ok('▼ CHASE -6% ×3/4 86.48 × 23' in nxt,
   'the armed buy carries its price AND its size', nxt)
ok('then 84.26 × 41  ·  82.09 × 71' in nxt,
   'the two chases after the armed one are shown', nxt)
ok('[NO ARMY]' in next_line(deployed_ui(buy_state='EXHAUSTED'), 'USD'),
   'an unfundable buy line is flagged in the next-line row')

fnxt = next_line(flat_ui(), 'USD')
ok('▲ exit if loaded T2 96.60 × 11' in fnxt
   and '▼ LOAD -8% 92.00 × 11' in fnxt,
   'a flat card shows the LOAD and marks its exit as projected', fnxt)
ok('then 86.48 × 8  ·  84.26 × 14' in fnxt,
   'a FLAT stock shows chase 1 as well as chase 2 — chase 1 is a projection '
   'there, not the armed line', fnxt)
ok([e['kind'] for e in buy_lines(flat_ui())] == ['load', 'chase1', 'chase2'],
   'the flat ladder is LOAD then chase 1 then chase 2')
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
ok(len(rows) == 7,
   '3 buys + avg + 2 exits + vantage each get a row', str(list(by_text)))
bold = [r for r in rows if r[3]]
ok(len(bold) == 4,
   'the armed buy, the average and both armed exits are bold',
   str([r[2] for r in bold]))
ok(any(t.startswith('CHASE -6% ×3/4 86.48 × 23') for t in by_text),
   'the armed chase row carries its size')
ok(any('chase 2 84.26 × 41' in t for t in by_text),
   'the projected chase rows are drawn too')
ok(any(t.startswith('EXIT T2 +5%') for t in by_text)
   and any(t.startswith('EXIT T3 +7%') for t in by_text),
   'every armed exit tier gets its own chart row')
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

print('— V: the 5-day range, the number the gear is read off —')
bars = [{'date': f'07/1{i}', 'ts': f'2026-07-1{i}T00:00:00', 'open': 100,
         'high': 100.0 + i, 'low': 96.0 - i, 'close': 99.0}
        for i in range(5)]                    # high 104, low 92
ok(near(bar_range_v(bars), calc_volatility(104.0, 92.0)),
   'the panel fallback measures the whole window, not a per-day average',
   f'{bar_range_v(bars):.2f}')
ok(near(bar_range_v(bars), 100 * (104.0 - 92.0) / 104.0),
   'V = 100 × (High5 − Low5) / High5')
ok(bar_range_v([]) is None and bar_range_v(None) is None,
   'no bars → no V')
ok(select_auto_gear(bar_range_v(bars)) == 2,
   'the panel and the gearbox read the same number the same way '
   '(11.5% → G2 on the 10/15/20/25 ladder)',
   str(bar_range_v(bars)))

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

print('— cockpit gearbox strip —')
picked = []
window.ap = {
    'set_gear': lambda g: picked.append(('gear', g)),
    'set_tiers': lambda t: picked.append(('tiers', list(t))),
    'set_auto': lambda a: picked.append(('auto', a)),
}
window.ui = deployed_ui()
window._on_gear(5)
window._on_auto()
ok(picked == [('gear', 5), ('auto', False)],
   'the gear button writes through, and AUTO toggles the MODE without '
   'needing a gear pick — the asymmetry with the card is gone', str(picked))
picked.clear()
window.ui = deployed_ui(card_auto=False)
window._on_auto()
ok(picked == [('auto', True)], 'and toggles back the other way')

print('— day labels are distinct —')
same = [{'date': '07/28', 'ts': '2026-07-28T00:00:00'},
        {'date': '07/28', 'ts': '2026-07-29T00:00:00'},
        {'date': '07/30', 'ts': '2026-07-30T00:00:00'}]
labs = bar_day_labels(same)
ok(len(set(labs)) == 3,
   'duplicate MM/DD labels fall back to the full date, so no two candles '
   'carry the same name', str(labs))
ok(bar_day_labels([{'date': '07/28', 'ts': '2026-07-28T00:00:00'},
                   {'date': '07/29', 'ts': '2026-07-29T00:00:00'}])
   == ['07/28', '07/29'],
   'and stay short when they are already unique')
dup = [{'date': 'x', 'ts': ''}, {'date': 'x', 'ts': ''}]
ok(len(set(bar_day_labels(dup))) == 2,
   'even nameless bars are numbered apart', str(bar_day_labels(dup)))
ok(bar_day_labels([]) == [], 'no bars, no labels')

print('— card spacing —')
ok(_AP_BUTTON_TOP_GAP == 18, 'Autopilot card button has one line of top gap')


# ── The controller, end to end against a fake broker ─────────────────────────
print('— controller: a poll reaches the screen —')


class _StubRow:
    """Stands in for StockRow: the tk vars the cockpit writes through, and
    the line_config the controller reads back."""

    def __init__(self, ticker, gear=3, tier=2):
        self.ticker = ticker
        self.gear_var = tk.IntVar(value=gear)
        self.tier_vars = [tk.BooleanVar(value=(i == tier))
                          for i in (1, 2, 3)]
        self.auto_var = tk.BooleanVar(value=True)
        self.badges = []
        self.live_price = None

    gear = property(lambda self: self.gear_var.get())
    tiers = property(lambda self: [v.get() for v in self.tier_vars])
    auto = property(lambda self: self.auto_var.get())

    def line_config(self):
        return {'gear': self.gear_var.get(),
                'exit_tiers': [v.get() for v in self.tier_vars],
                'auto': self.auto_var.get()}

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
        # high5 = 104, low5 = 86 → V = 100 × 18 / 104
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
_app = _StubApp(_root, _prov)
_card = _app.deployed_rows[0]
ctrl = AutopilotController(_app)
ctrl._log = lambda *a: None            # keep the test out of logs/
ctrl._store = {}
ctrl._save_state = lambda *a: True     # never touch data/

ok(ctrl.watch('NVDA')[0], 'watching arms the campaign engine')
ok(ctrl._slots['NVDA']['card'] == {'gear': 3,
                                   'exit_tiers': [False, True, False],
                                   'auto': True},
   "watching pulls the card's gear config immediately")

slot = ctrl._slots['NVDA']
ctrl._cycle('NVDA', slot)              # the path that used to throw
_root.update()                         # run the queued tk callback
ui = ctrl.ui_state('NVDA')
ok(ui is not None and ui.get('status'), 'one poll produces a ui payload')
ok(ui['state'] == 'DEPLOYED' and ui['shares'] == 31,
   'the poll reconciled the broker position', str(ui['state']))
ok(ui['lines'].get('chase') and ui['lines'].get('exit2'),
   'the ui carries both watched lines', str(list(ui['lines'])))
ok(near(ui['lines']['exit2']['price'], 96.6, 0.011),
   'the EXIT line is the broker average +5%',
   str(ui['lines']['exit2']['price']))
ok(ui['lines']['exit2']['qty'] == 31,
   'one armed tier covers the whole position')
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

# The cockpit's gear buttons write to the CARD, which is the source.
row = ctrl.app.deployed_rows[0] if hasattr(ctrl, 'app') else None
ctrl.set_card_gear('NVDA', gear=5)
_root.update()
ok(_card.gear == 5 and _card.auto is False,
   'picking a gear in the cockpit writes to the card and drops AUTO',
   f'gear={_card.gear} auto={_card.auto}')
ctrl.set_card_gear('NVDA', tiers=[True, False, True])
_root.update()
ok(_card.tiers == [True, False, True],
   'arming two tiers in the cockpit writes both flags to the card',
   str(_card.tiers))
ctrl.set_card_gear('NVDA', auto=True)
_root.update()
ok(_card.auto is True, 'AUTO hands the gear back to volatility')

ctrl._cycle('NVDA', slot)
_root.update()
ui = ctrl.ui_state('NVDA')
ok(ui['campaign']['gear'] == 5
   and ui['campaign']['exit_tiers'] == [True, False, True],
   'and the engine picks the change up on the very next poll',
   str((ui['campaign']['gear'], ui['campaign']['exit_tiers'])))
ok(near(ui['vol5'], calc_volatility(104.0, 86.0), 0.01),
   "the payload carries the strategy's V, from the 5-day high and low",
   str(ui['vol5']))

ctrl.disable('NVDA')
ok(not ctrl.is_enabled('NVDA'), 'disable stops the watch')
ok(ctrl._store_key('NVDA') == 'NVDA#VCG',
   'campaign state saves under its own key, leaving grid state alone')
ctrl._store = {'NVDA': {'anchor': 100.0, 'level': 0}}
ok(ctrl._saved_for('NVDA') is None,
   'a leftover v^ grid record is never restored as a campaign')
_root.destroy()

print(f'\nALL {passed} UI CHECKS PASSED')
