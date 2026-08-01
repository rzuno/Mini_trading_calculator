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
import json
import tempfile
import threading
import time

import requests

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk
import gui.autopilot_ctrl as autopilot_ctrl_module

from core.calc import (calc_load_gap_rate, calc_volatility, fmt_price,
                       gear_button_color, load_gap_color, select_auto_gear,
                       sell_pct_color)
from gui.campaign_window import (CampaignWindow, buy_lines, campaign_age_line,
                                 campaign_line, can_pin_vantage,
                                 empty_fill_status, fill_log_rows, next_line,
                                 sell_lines, sell_tier_colors,
                                 status_banner_color, toggled_tiers,
                                 vantage_presentation)
from gui.autopilot_ctrl import (AutopilotController, fill_evidence_key,
                                merge_live_bar, normalize_open_order,
                                normalize_recent_fills)
from gui.candle_chart import (bar_day_labels, bar_is_selected, bar_pick_label,
                              bar_range_v,
                              bounded_label_layout, candle_color,
                              required_label_pad)
from gui.main_window import (App as MainWindow, card_gap_order_key,
                             strategy_preferences)
from gui.stock_row import _AP_BUTTON_TOP_GAP
from providers.toss_market_provider import TossMarketProvider

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
ok('Vantage 100.00 (Campaign Vantage · frozen at LOAD)' in line,
   'a deployed campaign names its frozen Vantage source', line)
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
ok('current DEPLOYED: 12 shares @ 91.50 average' in empty_fill_status({
       'events': [], 'campaign_state': 'CHASE_PENDING', 'shares': 12,
       'avg_cost': 91.5}, 'USD'),
   'an empty fill log still reports the current deployed position')
ok('current FLAT: no shares' in empty_fill_status({
       'events': [], 'campaign_state': 'FLAT', 'shares': 0}, 'USD'),
   'an empty fill log distinguishes a genuinely flat position')

note = fill_log_rows({'events': [
    {'date': '2026-08-01', 'ts': '08/01 12:00', 'kind': 'GEAR', 'qty': 0,
     'price': None, 'shares': 31, 'avg': 92.0, 'source': 'BOT',
     'note': 'G3 → G5 (chase -8% ×1.0)'}]}, 'USD')
ok(note == [],
   'legacy gear/adopt decisions stay out of the trade-only campaign log')
mixed_note = fill_log_rows({'events': [
    {'date': '2026-08-01', 'ts': '08/01 12:01', 'kind': 'CHASE', 'qty': 3,
     'price': 90.0, 'shares': 14, 'avg': 91.5, 'source': 'MIXED',
     'note': 'bot 2, external/net 1'}]}, 'USD')
ok('[bot + hand]' in mixed_note[-1][0],
   'a mixed polling-interval fill is visibly distinguished')

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

print('— Vantage selection presentation —')
dynamic = vantage_presentation(flat_ui()['campaign'], 'USD')
ok(not dynamic['pinned'] and dynamic['text'].startswith(
       'AUTOMATIC — Automatic Dynamic High5:'),
   'dynamic Vantage is explicitly named as automatic', dynamic['text'])
pinned_campaign = dict(flat_ui()['campaign'], vantage_manual=103.5,
                       vantage=103.5, vantage_src='manual',
                       vantage_manual_label='07/30 high')
pinned = vantage_presentation(pinned_campaign, 'USD')
ok(pinned['pinned'] and pinned['text'] == 'PINNED — 07/30 high: 103.50',
   'manual Vantage names its selected day and high', pinned['text'])
ok(can_pin_vantage(flat_ui()) and not can_pin_vantage(deployed_ui()),
   'Vantage day selection is available flat and locked while deployed')
ok(not can_pin_vantage(flat_ui(campaign_state='ARMED_LOAD')),
   'Vantage selection is also locked while a LOAD order is pending')
ok(toggled_tiers([False, True, False], 2) == [False, True, False],
   'the final armed exit tier silently stays on')
ok(toggled_tiers([False, True, False], 1) == [True, True, False],
   'another tier applies immediately without a confirmation state')

pick_bar = {'date': '07/30', 'ts': '2026-07-30T00:00:00',
            'open': 100.0, 'high': 103.5, 'low': 99.0, 'close': 102.0}
ok(bar_pick_label(pick_bar, '07/30') == '07/30 high',
   'OHLC rows persist a readable selected-high label')
ok(bar_is_selected(pick_bar, 103.5, '07/30 high', '07/30'),
   'the selected candle can be highlighted from the persisted label')
ok(not bar_is_selected(pick_bar, 104.0, '07/30 high', '07/30'),
   'a different high is not highlighted accidentally')

print('— actionable card gap and global order —')
close_gap = calc_load_gap_rate(95.0, 92.0)
far_gap = calc_load_gap_rate(100.0, 92.0)
crossed_gap = calc_load_gap_rate(90.0, 92.0)
ok(near(close_gap, (92.0 - 95.0) / 95.0 * 100.0),
   'FLAT gap is (LOAD-current)/current', str(close_gap))
ok(crossed_gap > close_gap > far_gap,
   'the gap rises as price approaches and crosses the bait')
ok(load_gap_color(close_gap) == '#6699CC'
   and load_gap_color(far_gap) == '#003399'
   and load_gap_color(crossed_gap) == '#B7D3F0',
   'FLAT gaps use historical light/dark blue contrast, not purple/orange')


class _GapRow:
    def __init__(self, ticker, gap, deployed):
        self.ticker, self._gap, self.deployed = ticker, gap, deployed


mixed = [_GapRow('NVDA', far_gap, False),
         _GapRow('AAPL', 1.5, True),
         _GapRow('GOOGL', close_gap, False)]
ordered = sorted(mixed, key=card_gap_order_key)
ok([r.ticker for r in ordered] == ['AAPL', 'GOOGL', 'NVDA'],
   'deployed and flat cards share one descending actionable-gap order',
   str([r.ticker for r in ordered]))

prefs = strategy_preferences({'ticker': 'NVDA', 'shares': 10, 'avg_cost': 90,
                              'gear': 4, 'auto_mode': False,
                              't1_active': True})
ok(prefs == {'gear': 4, 't1_active': True, 'auto_mode': False},
   'prompt persistence filters out changing broker-position fields', str(prefs))

print('— cockpit color contrast —')
ok([gear_button_color(g) for g in range(1, 6)] ==
   ['#C62828', '#E08000', '#E6B800', '#2E8B57', '#1565C0'],
   'G1..G5 are red, orange, yellow, green, blue')
low_bg, low_fg = sell_tier_colors(3)
high_bg, high_fg = sell_tier_colors(9)
ok(low_bg == sell_pct_color(3) and high_bg == sell_pct_color(9)
   and low_bg != high_bg and low_fg == 'black' and high_fg == 'white',
   'armed cockpit exits reuse percentage-dependent sell contrast')
ok(status_banner_color(deployed_ui(buy_state='EXHAUSTED')) == '#6B4A2B',
   'ordinary no-reserve status stays neutral/brown instead of alarming red')
ok(status_banner_color(deployed_ui(
       campaign_state='PAUSED_RECONCILE')) == '#CC0000',
   'a genuine reconciliation error still receives red status emphasis')

print('— controller reconciliation helpers —')
market_order = normalize_open_order({
    'orderId': 'manual-market', 'side': 'BUY', 'price': None,
    'quantity': '5', 'execution': {'filledQuantity': '0'},
})
ok(market_order['price'] is None and market_order['qty_open'] == 5
   and not market_order['mine'],
   'a price-less manual MARKET order remains visible to the foreign-order pause')
amount_order = normalize_open_order({
    'orderId': 'manual-amount', 'side': 'BUY', 'price': None,
    'orderAmount': '100.00', 'execution': {'filledQuantity': '0'},
})
ok(amount_order['qty_open'] == 1 and not amount_order['mine'],
   'an amount-based manual order receives a pause-only open sentinel')
unpriced_fill = normalize_recent_fills([{
    'orderId': 'filled-1', 'symbol': 'NVDA', 'side': 'SELL',
    'price': '133.33', 'execution': {
        'filledQuantity': '2', 'averageFilledPrice': None,
        'filledAt': '2026-08-01T10:00:00-04:00'},
}], 'NVDA')
ok(len(unpriced_fill) == 1 and unpriced_fill[0]['price'] is None,
   'a limit price is never substituted for missing actual execution price')


class _PendingEngine:
    def __init__(self):
        self.unresolved = []

    def note_order_unresolved(self, **kw):
        self.unresolved.append(kw)
        return True


_helper = AutopilotController.__new__(AutopilotController)
_slot = {'fill_transition': None}
_pending = {'accepted': True, 'side': 'BUY', 'price': 90.0, 'qty': 2,
            'order_id': 'bot-1', 'filled_seen': 0}
_ambiguous = {'shares': 2, 'orders': [], 'recent_fills': [],
              'can_trade': True}
for _attempt in range(3):
    _ready, _status = _helper._fill_transition_ready(
        'NVDA', _slot, _PendingEngine(), _ambiguous, 0, _pending)
    ok(not _ready and 'no order will be sent' in _status,
       f'ambiguous bot fill pauses on evidence retry {_attempt + 1}')
_engine = _PendingEngine()
_ready, _status = _helper._fill_transition_ready(
    'NVDA', _slot, _engine, _ambiguous, 0, _pending)
ok(_ready and _ambiguous['can_trade'] is False and _engine.unresolved,
   'bounded evidence failure retains the unresolved intent in a non-trading poll')

bounded_fills = normalize_recent_fills([
    {'orderId': 'old', 'symbol': 'NVDA', 'side': 'BUY',
     'execution': {'filledQuantity': '1', 'averageFilledPrice': '88',
                   'filledAt': '100'}},
    {'orderId': 'inside', 'symbol': 'NVDA', 'side': 'SELL',
     'execution': {'filledQuantity': '1', 'averageFilledPrice': '89',
                   'filledAt': '200'}},
    {'orderId': 'future', 'symbol': 'NVDA', 'side': 'BUY',
     'execution': {'filledQuantity': '1', 'averageFilledPrice': '90',
                   'filledAt': '300'}},
], 'NVDA', filled_after=150, filled_before=250)
ok([f['order_id'] for f in bounded_fills] == ['inside'],
   'CLOSED evidence is limited to the requested correlation window')


class _SnapshotProvider:
    def __init__(self):
        self.detail_calls = []
        self.closed_requests = []

    def get_prices(self, tickers):
        return {tickers[0]: 91.0}

    def get_holdings(self, seq, ticker):
        return {'items': [{'symbol': ticker, 'quantity': '2',
                           'averagePurchasePrice': '90'}]}

    def get_open_orders(self, seq, ticker):
        return [{'orderId': 'manual-market', 'side': 'SELL', 'price': None,
                 'quantity': '1', 'execution': {'filledQuantity': '0'}}]

    def get_order(self, seq, order_id):
        self.detail_calls.append(order_id)
        return {'orderId': order_id, 'clientOrderId': 'vcg-ap-test',
                'symbol': 'NVDA', 'side': 'BUY', 'quantity': '1',
                'execution': {'filledQuantity': '1',
                              'averageFilledPrice': '90.5',
                              'filledAt': '2026-08-01T10:00:00-04:00'}}

    def get_closed_orders(self, seq, ticker, limit=20, from_date=None):
        self.closed_requests.append({'limit': limit, 'from_date': from_date})
        return [{'orderId': 'manual-fill', 'symbol': 'NVDA', 'side': 'BUY',
                 'quantity': '1', 'execution': {
                     'filledQuantity': '1', 'averageFilledPrice': '91.5',
                     'filledAt': '2026-08-01T10:00:01-04:00'}}]

    def get_buying_power(self, seq, ccy):
        return 1000.0


_helper._log = lambda *args: None
_snapshot_provider = _SnapshotProvider()
_snapshot = _helper._real_snapshot(
    _snapshot_provider, 1, 'NVDA', set(), prev_shares=2,
    pending_order_id='bot-fill', fill_after=0, fill_before=4_000_000_000)
ok(len(_snapshot['orders']) == 1 and _snapshot['orders'][0]['price'] is None,
   'the real snapshot keeps a price-less foreign OPEN order')
ok(len(_snapshot['recent_fills']) == 2
   and {f['mine'] for f in _snapshot['recent_fills']} == {True, False},
   'exact bot detail and manual fill history are combined in one bounded scan')
ok(_snapshot_provider.detail_calls == ['bot-fill']
   and _snapshot['pending_order']['terminal'],
   'exact pending-order detail is queried even when holdings are unchanged')
ok(_snapshot['closed_fills_complete'] and _snapshot['fills_correlated'],
   'a successful complete CLOSED scan enables gross/net-zero replay')
_seen = {fill_evidence_key(f) for f in _snapshot['recent_fills']}
_repeat = _helper._real_snapshot(
    _snapshot_provider, 1, 'NVDA', set(), prev_shares=2,
    pending_order_id='bot-fill', fill_after=0, fill_before=4_000_000_000,
    seen_fill_keys=_seen)
ok(_repeat['recent_fills'] == [],
   'overlapping CLOSED scans discard already-consumed evidence keys')
_us_date_provider = _SnapshotProvider()
_helper._real_snapshot(
    _us_date_provider, 1, 'NVDA', set(), prev_shares=2,
    fill_after=1_785_600_000, fill_before=1_785_600_060)
ok(_us_date_provider.closed_requests[-1]['from_date'] == '2026-08-01',
   'a KST-next-day scan requests US CLOSED history from the prior Eastern date')


class _IncompleteClosedSnapshotProvider(_SnapshotProvider):
    def __init__(self):
        super().__init__()
        self.history = TossMarketProvider.__new__(TossMarketProvider)
        self.history._closed_orders_supported = None
        self.page_calls = 0

        def incomplete_get(path, params, account=None):
            self.page_calls += 1
            if self.page_calls == 1:
                return {'orders': [{
                    'orderId': 'partial-page-fill', 'symbol': 'NVDA',
                    'side': 'SELL', 'quantity': '1', 'status': 'FILLED',
                    'execution': {'filledQuantity': '1',
                                  'averageFilledPrice': '92',
                                  'filledAt': '2026-08-01T10:00:02-04:00'},
                }], 'hasNext': True, 'nextCursor': 'next-page'}
            raise RuntimeError('second CLOSED page failed')

        self.history._get = incomplete_get

    def get_closed_orders(self, seq, ticker, **kwargs):
        return self.history.get_closed_orders(seq, ticker, **kwargs)


_incomplete_snapshot_provider = _IncompleteClosedSnapshotProvider()
_incomplete_snapshot = _helper._real_snapshot(
    _incomplete_snapshot_provider, 1, 'NVDA', set(), prev_shares=2,
    pending_order_id='bot-fill', fill_after=0,
    fill_before=4_000_000_000)
ok(_incomplete_snapshot_provider.page_calls == 2
   and not _incomplete_snapshot['closed_fills_complete']
   and not _incomplete_snapshot['fills_correlated']
   and [f['order_id'] for f in _incomplete_snapshot['recent_fills']]
       == ['bot-fill'],
   'an incomplete later CLOSED page contributes no partial rows or gross replay proof')


class _UnsupportedClosedSnapshotProvider(_SnapshotProvider):
    _closed_orders_supported = False

    def get_closed_orders(self, seq, ticker, **kwargs):
        return []


_unsupported_snapshot = _helper._real_snapshot(
    _UnsupportedClosedSnapshotProvider(), 1, 'NVDA', set(), prev_shares=2,
    pending_order_id='bot-fill', fill_after=0,
    fill_before=4_000_000_000)
ok(not _unsupported_snapshot['closed_fills_complete']
   and not _unsupported_snapshot['fills_correlated']
   and [f['order_id'] for f in _unsupported_snapshot['recent_fills']]
       == ['bot-fill'],
   'closed-not-supported keeps exact pending evidence but disables gross replay')

_foreign_slot = {'fill_transition': None}
_foreign_snap = {
    'shares': 2, 'orders': [], 'fills_correlated': True,
    'recent_fills': [{'side': 'BUY', 'qty': 2, 'mine': False,
                      'order_id': 'manual-buy', 'filled_at': '200'}],
    'can_trade': True,
}
_foreign_engine = _PendingEngine()
_ready, _status = _helper._fill_transition_ready(
    'NVDA', _foreign_slot, _foreign_engine, _foreign_snap, 0, _pending)
ok(_ready and _status is None and not _foreign_engine.unresolved,
   'time-correlated manual evidence explains a delta without claiming a bot fill')


class _AmbiguousEngine:
    def __init__(self):
        self._pending = {
            'accepted': False, 'unresolved': False, 'side': 'BUY',
            'price': 90.0, 'qty': 2, 'kind': 'LOAD', 'order_id': None,
            'client_order_id': None, 'submitted_at': None,
        }
        self.status = ''
        self.failed = False

    def note_order_submitted(self, side, price, qty, client_id):
        if (side, price, qty) != ('BUY', 90.0, 2):
            return False
        self._pending.update({
            'accepted': True, 'unresolved': True,
            'client_order_id': client_id,
            'submitted_at': self._pending.get('submitted_at') or time.time(),
        })
        return True

    def note_order_unresolved(self, side=None, price=None, qty=None,
                              reason='', order_id=None,
                              client_order_id=None):
        self._pending['unresolved'] = True
        if client_order_id:
            self._pending['client_order_id'] = client_order_id
        return True

    def note_order_accepted(self, side, price, qty, order_id=None,
                            client_order_id=None):
        self._pending.update({
            'accepted': True, 'unresolved': False, 'order_id': order_id,
            'client_order_id': client_order_id,
        })
        return True

    def note_order_failed(self, side, price, qty):
        self.failed = True
        self._pending = None
        return True

    def to_dict(self):
        return {'strategy': 'V_COMMANDOS_GEARBOX',
                'pending': (dict(self._pending) if self._pending else None)}


class _AmbiguousProvider:
    def __init__(self):
        self.client_ids = []

    def place_limit_order(self, ticker, side, price, qty, seq,
                          client_order_id=None):
        self.client_ids.append(client_order_id)
        if len(self.client_ids) == 1:
            raise TimeoutError('reply lost')
        return 200, {'result': {'orderId': 'broker-accepted',
                                'clientOrderId': client_order_id}}


class _DiskBoundaryProvider:
    def __init__(self, state_path):
        self.state_path = state_path
        self.transmissions = 0
        self.pending_seen_at_post = None

    def place_limit_order(self, ticker, side, price, qty, seq,
                          client_order_id=None):
        self.transmissions += 1
        with open(self.state_path, encoding='utf-8') as f:
            saved = json.load(f)
        self.pending_seen_at_post = saved['NVDA#VCG']['pending']
        raise TimeoutError('simulate process loss after transmission')


_disk_helper = AutopilotController.__new__(AutopilotController)
_disk_helper._log = lambda *args: None
_disk_helper._store = {}
_disk_slot = {'my_ids': set(),
              'backoff_until': {'BUY': 0.0, 'SELL': 0.0}}
_disk_engine = _AmbiguousEngine()
with tempfile.TemporaryDirectory() as _state_dir:
    _state_path = os.path.join(_state_dir, 'autopilot-state.json')
    _old_state_path = autopilot_ctrl_module._STATE_STORE
    autopilot_ctrl_module._STATE_STORE = _state_path
    try:
        _disk_provider = _DiskBoundaryProvider(_state_path)
        _placed, _message = _disk_helper._place_real(
            'NVDA', _disk_slot, _disk_provider, 1, _disk_engine,
            'BUY', 90.0, 2, 'LOAD')
    finally:
        autopilot_ctrl_module._STATE_STORE = _old_state_path
ok(not _placed and _disk_provider.transmissions == 1
   and _disk_provider.pending_seen_at_post['accepted']
   and _disk_provider.pending_seen_at_post['unresolved']
   and _disk_provider.pending_seen_at_post['client_order_id']
       == _disk_engine._pending['client_order_id'],
   'the idempotency key is durable on disk before the real POST begins')


class _NeverTransmitProvider:
    def __init__(self):
        self.transmissions = 0

    def place_limit_order(self, *args, **kwargs):
        self.transmissions += 1
        raise AssertionError('POST must not run after a state-save failure')


_failed_save_helper = AutopilotController.__new__(AutopilotController)
_failed_save_helper._log = lambda *args: None
_failed_save_helper._save_state = lambda *args: False
_failed_save_provider = _NeverTransmitProvider()
_placed, _message = _failed_save_helper._place_real(
    'NVDA', {'my_ids': set()}, _failed_save_provider, 1,
    _AmbiguousEngine(), 'BUY', 90.0, 2, 'LOAD')
ok(not _placed and _failed_save_provider.transmissions == 0
   and 'not transmitted' in _message,
   'a failed pre-POST state save transmits no broker order')


_post_helper = AutopilotController.__new__(AutopilotController)
_post_helper._log = lambda *args: None
_post_helper._lock = threading.Lock()
_post_helper._save_state = lambda *args: True
_post_slot = {
    'my_ids': set(), 'backoff_until': {'BUY': 0.0, 'SELL': 0.0},
    'submission_retry_after': 0.0, 'mode': 'LIVE', 'stopping': False,
}
_post_helper._slots = {'NVDA': _post_slot}
_post_engine = _AmbiguousEngine()
_post_provider = _AmbiguousProvider()
_placed, _message = _post_helper._place_real(
    'NVDA', _post_slot, _post_provider, 1, _post_engine,
    'BUY', 90.0, 2, 'LOAD')
ok(not _placed and _post_engine._pending['unresolved'],
   'a timed-out POST retains its accepted idempotent intent')
_post_slot['backoff_until']['BUY'] = 0.0
_post_helper._retry_unresolved_submission(
    'NVDA', _post_slot, _post_provider, 1, _post_engine,
    {'price': 89.0, 'orders': [], 'pending_order': None},
    mode='LIVE', phase='REGULAR')
ok(len(_post_provider.client_ids) == 2
   and len(set(_post_provider.client_ids)) == 1
   and _post_engine._pending['order_id'] == 'broker-accepted',
   'an ambiguous POST retry reuses the exact same clientOrderId')


class _DuplicateClientProvider:
    def place_limit_order(self, ticker, side, price, qty, seq,
                          client_order_id=None):
        return 409, {'error': {'code': 'duplicate-client-order-id'}}


_duplicate_engine = _AmbiguousEngine()
_placed, _message = _post_helper._place_real(
    'NVDA', _post_slot, _DuplicateClientProvider(), 1, _duplicate_engine,
    'BUY', 90.0, 2, 'LOAD', client_id='vcg-ap-existing')
ok(not _placed and not _duplicate_engine.failed
   and _duplicate_engine._pending['unresolved']
   and _duplicate_engine._pending['client_order_id'] == 'vcg-ap-existing',
   'duplicate-client-id conflict preserves the possibly accepted intent')


class _CleanupEngine:
    _pending = {'accepted': True, 'order_id': 'bot-cleanup',
                'client_order_id': 'vcg-ap-cleanup'}


class _CleanupProvider:
    def __init__(self):
        self.terminal = False

    def get_open_orders(self, seq, ticker):
        return []

    def get_order(self, seq, order_id):
        if not self.terminal:
            return {}
        return {'orderId': order_id, 'clientOrderId': 'vcg-ap-cleanup',
                'side': 'BUY', 'quantity': '1', 'status': 'CANCELED',
                'execution': {'filledQuantity': '0'}}


_cleanup_helper = AutopilotController.__new__(AutopilotController)
_cleanup_helper._lock = threading.Lock()
_cleanup_helper._log = lambda *args: None
_cleanup_provider = _CleanupProvider()
_cleanup_slot = {'cleanup_after': 0.0, 'stopping': True,
                 'engine': _CleanupEngine(), 'my_ids': set()}
_cleanup_helper._slots = {'NVDA': _cleanup_slot}
_cleanup_helper._cleanup_cycle('NVDA', _cleanup_slot, _cleanup_provider, 1)
ok('NVDA' in _cleanup_helper._slots,
   'missing OPEN/detail rows do not finish bot-order cleanup')
_cleanup_slot['cleanup_after'] = 0.0
_cleanup_provider.terminal = True
_cleanup_helper._cleanup_cycle('NVDA', _cleanup_slot, _cleanup_provider, 1)
ok('NVDA' not in _cleanup_helper._slots,
   'exact terminal order detail finishes bot-order cleanup')


class _ClientCleanupEngine(_AmbiguousEngine):
    def __init__(self, submitted_at=None):
        super().__init__()
        self._pending.update({
            'accepted': True, 'unresolved': True,
            'client_order_id': 'vcg-ap-cleanup-client',
            'submitted_at': (time.time() if submitted_at is None
                             else submitted_at),
        })


class _ClientCleanupProvider:
    def __init__(self):
        self.client_ids = []
        self.cancelled = []
        self.open_reads = 0

    def get_open_orders(self, seq, ticker):
        self.open_reads += 1
        return []

    def get_closed_orders(self, seq, ticker, **kwargs):
        return []

    def place_limit_order(self, ticker, side, price, qty, seq,
                          client_order_id=None):
        self.client_ids.append(client_order_id)
        return 200, {'result': {'orderId': 'cleanup-recovered',
                                'clientOrderId': client_order_id}}

    def cancel_order(self, order_id, seq):
        self.cancelled.append(order_id)
        return 200, {'result': {'orderId': order_id}}

    def get_order(self, seq, order_id):
        return {'orderId': order_id,
                'clientOrderId': 'vcg-ap-cleanup-client',
                'side': 'BUY', 'quantity': '2', 'status': 'CANCELED',
                'execution': {'filledQuantity': '0'}}


_client_cleanup_helper = AutopilotController.__new__(AutopilotController)
_client_cleanup_helper._lock = threading.Lock()
_client_cleanup_helper._log = lambda *args: None
_client_cleanup_helper._save_state = lambda *args: True
_client_cleanup_engine = _ClientCleanupEngine()
_client_cleanup_provider = _ClientCleanupProvider()
_client_cleanup_slot = {
    'cleanup_after': 0.0, 'cleanup_started_at': time.time(),
    'cleanup_client_empty_reads': 0, 'stopping': True,
    'engine': _client_cleanup_engine, 'my_ids': set(),
    'backoff_until': {'BUY': 0.0, 'SELL': 0.0},
}
_client_cleanup_helper._slots = {'NVDA': _client_cleanup_slot}
_client_cleanup_helper._cleanup_cycle(
    'NVDA', _client_cleanup_slot, _client_cleanup_provider, 1)
ok(_client_cleanup_provider.client_ids == ['vcg-ap-cleanup-client']
   and _client_cleanup_provider.cancelled == ['cleanup-recovered']
   and _client_cleanup_engine._pending['order_id'] == 'cleanup-recovered'
   and 'NVDA' in _client_cleanup_helper._slots,
   'client-id-only cleanup repeats the same identity and immediately cancels it')
_client_cleanup_slot['cleanup_after'] = 0.0
_client_cleanup_helper._cleanup_cycle(
    'NVDA', _client_cleanup_slot, _client_cleanup_provider, 1)
ok('NVDA' not in _client_cleanup_helper._slots,
   'client-id-only cleanup finishes after exact terminal confirmation')


class _ExpiredCleanupProvider:
    def __init__(self):
        self.open_reads = 0
        self.transmissions = 0

    def get_open_orders(self, seq, ticker):
        self.open_reads += 1
        return []

    def get_closed_orders(self, seq, ticker, **kwargs):
        return []

    def place_limit_order(self, *args, **kwargs):
        self.transmissions += 1
        raise AssertionError('expired cleanup must not create a fresh order')


_expired_helper = AutopilotController.__new__(AutopilotController)
_expired_helper._lock = threading.Lock()
_expired_helper._log = lambda *args: None
_expired_saved = []


def _save_expired_retirement(ticker, engine):
    _expired_saved.append(engine.to_dict())
    # The first retirement write fails. Cleanup must retain its slot and retry
    # this exact already-retired state rather than silently finishing.
    return len(_expired_saved) > 1


_expired_helper._save_state = _save_expired_retirement
_expired_engine = _ClientCleanupEngine(time.time() - 601)
_expired_provider = _ExpiredCleanupProvider()
_expired_slot = {
    'cleanup_after': 0.0, 'cleanup_started_at': time.time() - 601,
    'cleanup_client_empty_reads': 0, 'stopping': True,
    'engine': _expired_engine, 'my_ids': set(),
}
_expired_helper._slots = {'NVDA': _expired_slot}
for _read in range(3):
    _expired_slot['cleanup_after'] = 0.0
    _expired_helper._cleanup_cycle(
        'NVDA', _expired_slot, _expired_provider, 1)
ok('NVDA' in _expired_helper._slots
   and _expired_slot.get('cleanup_retire_pending_save')
   and _expired_engine._pending is None,
   'cleanup keeps its slot when the durable retirement write fails')
_expired_slot['cleanup_after'] = 0.0
_expired_helper._cleanup_cycle(
    'NVDA', _expired_slot, _expired_provider, 1)
ok(_expired_provider.open_reads == 4
   and _expired_provider.transmissions == 0
   and len(_expired_saved) == 2
   and _expired_saved[-1]['pending'] is None
   and _expired_engine._pending is None
   and 'NVDA' not in _expired_helper._slots,
   'expired client-only cleanup finishes only after durable retirement succeeds')


class _UnsupportedClosedResponse:
    status_code = 400

    @staticmethod
    def json():
        return {'error': {'code': 'closed-not-supported'}}


_closed_provider = TossMarketProvider.__new__(TossMarketProvider)
_closed_provider._closed_orders_supported = None
_closed_calls = []


def _closed_not_supported(*args, **kwargs):
    _closed_calls.append((args, kwargs))
    exc = requests.HTTPError('closed-not-supported')
    exc.response = _UnsupportedClosedResponse()
    raise exc


_closed_provider._get = _closed_not_supported
ok(_closed_provider.get_closed_orders(1, 'NVDA') == []
   and _closed_provider.get_closed_orders(1, 'NVDA') == []
   and len(_closed_calls) == 1,
   'a broker CLOSED capability rejection is cached instead of retried every poll')


_paged_closed_provider = TossMarketProvider.__new__(TossMarketProvider)
_paged_closed_provider._closed_orders_supported = None
_paged_params = []


def _paged_closed_get(path, params, account=None):
    _paged_params.append(dict(params))
    if not params.get('cursor'):
        return {'orders': [{'orderId': 'newest'}],
                'hasNext': True, 'nextCursor': 'page-2'}
    return {'orders': [{'orderId': 'older'}],
            'hasNext': False, 'nextCursor': None}


_paged_closed_provider._get = _paged_closed_get
_all_closed = _paged_closed_provider.get_closed_orders(
    1, 'NVDA', limit=100, from_date='2026-08-01')
ok([o['orderId'] for o in _all_closed] == ['newest', 'older']
   and len(_paged_params) == 2
   and 'cursor' not in _paged_params[0]
   and _paged_params[1]['cursor'] == 'page-2'
   and all(p['from'] == '2026-08-01' and p['limit'] == 100
           for p in _paged_params),
   'CLOSED history follows hasNext/nextCursor through the complete interval')

_malformed_closed_provider = TossMarketProvider.__new__(TossMarketProvider)
_malformed_closed_provider._closed_orders_supported = None
_malformed_closed_provider._get = lambda *args, **kwargs: {
    'orders': [{'orderId': 'unsafe-prefix'}],
    'hasNext': True, 'nextCursor': None,
}
_malformed_raised = False
try:
    _malformed_closed_provider.get_closed_orders(1, 'NVDA')
except RuntimeError:
    _malformed_raised = True
ok(_malformed_raised,
   'a malformed incomplete CLOSED cursor chain returns no usable prefix')


class _ImmediateRoot:
    @staticmethod
    def after(delay, callback, *args):
        callback(*args)


class _FetchProvider:
    def __init__(self):
        self.calls = 0

    def fetch_all(self, tickers, fx_ticker):
        self.calls += 1
        return {}, None, None


class _FailedAccountProvider:
    @staticmethod
    def account_snapshot():
        raise RuntimeError('account unavailable')


_fetch_window = MainWindow.__new__(MainWindow)
_fetch_window._fetch_lock = threading.Lock()
_fetch_window.positions = [{'ticker': 'NVDA'}]
_fetch_window.config = {}
_fetch_window._provider = _FetchProvider()
_fetch_window._auto = True
_fetch_window.root = _ImmediateRoot()
_fetch_window._toss_provider = lambda: _FailedAccountProvider()
_fetch_window._account_seq = lambda provider: 1
_fresh_callbacks = []
_fetch_window._apply_live = lambda *args: _fresh_callbacks.append(args)
_fetch_window._fetch_bg()
ok(len(_fresh_callbacks) == 1
   and _fresh_callbacks[0][3:] == (None, None),
   'a failed fresh account read passes no cached holdings or synthetic OPEN list')
_fetch_window._fetch_lock.acquire()
_fetch_calls = _fetch_window._provider.calls
_fetch_window._fetch_bg()
_fetch_window._fetch_lock.release()
ok(_fetch_window._provider.calls == _fetch_calls,
   'overlapping card-triggered refreshes are coalesced')

_apply_window = MainWindow.__new__(MainWindow)
_apply_window._last_account = {'items': [{'ticker': 'NVDA', 'shares': 9}]}
_apply_window._last_open_orders = [{'orderId': 'old'}]
_apply_window._auto = True
_apply_window._current_prices = {}
_apply_window._current_peaks = {}
_apply_window._ohlc_data = {}
_apply_window._closes_data = {}
_apply_window._volatility = {}
_apply_window.deployed_rows = []
_apply_window.empty_rows = []
_rebuild_calls = []
_apply_window._reconcile_from_toss = lambda account: _rebuild_calls.append(
    ('reconcile', account))
_apply_window._rebuild_sections = lambda: _rebuild_calls.append(('rebuild',))
for _method in ('_update_fx_header', '_reorder_cards',
                '_apply_order_states', '_update_banner'):
    setattr(_apply_window, _method, lambda: None)
_apply_window._update_army = lambda fx: None
_apply_window.autopilot = type(
    '_AutopilotStub', (), {'on_rows_rebuilt': lambda self: None})()
_apply_window._apply_live({}, None, account=None, open_orders=None, quiet=True)
ok(not _rebuild_calls
   and _apply_window._last_account['items'][0]['shares'] == 9
   and _apply_window._last_open_orders[0]['orderId'] == 'old',
   'a failed fresh account snapshot neither rebuilds cards nor erases cached status')


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


try:
    _root = tk.Tk()
except tk.TclError as exc:
    print(f'  skip controller/Tk checks: {exc}')
    print(f'\nALL {passed} HEADLESS UI CHECKS PASSED')
    raise SystemExit(0)
_root.withdraw()

# Build the real cockpit once: this catches Tk/widget wiring and makes sure the
# always-visible candle panel and contextual Vantage control are not merely
# correct in the pure presentation helpers.
_smoke_ui = flat_ui()
_smoke_ui.update({'mode': 'WATCH', 'phase': 'REGULAR', 'price': 95.0,
                  'shares': 0, 'avg_cost': 0.0, 'unit_cash': 1000.0,
                  'buying_power': 5000.0, 'vol5': 17.3, 'ts': '12:00:00'})
_smoke_bars = [
    {'date': '07/28', 'ts': '2026-07-28T00:00:00', 'open': 100.0,
     'high': 103.0, 'low': 94.0, 'close': 101.0},
    {'date': '07/29', 'ts': '2026-07-29T00:00:00', 'open': 101.0,
     'high': 104.0, 'low': 95.0, 'close': 98.0},
]
_smoke_listeners = []
_smoke_ap = {
    'is_enabled': lambda: True,
    'ui_state': lambda: _smoke_ui,
    'mode_of': lambda: 'WATCH',
    'set_mode': lambda mode: (True, mode),
    'disable': lambda: None,
    'subscribe': lambda fn: _smoke_listeners.append(fn),
    'unsubscribe': lambda fn: (_smoke_listeners.remove(fn)
                                if fn in _smoke_listeners else None),
    'ohlc': lambda: _smoke_bars,
    'set_gear': lambda gear: None,
    'set_tiers': lambda tiers: None,
    'set_auto': lambda auto: None,
    'set_vantage': lambda price, label='': (True, 'ok'),
}
_smoke_window = CampaignWindow(_root, 'NVDA', 'USD', _smoke_ap)
_smoke_window.win.withdraw()
_root.update_idletasks()
ok(_smoke_window.candle_panel.winfo_manager() == 'pack',
   'the real cockpit always constructs and shows its 5-day candle panel')
ok(not hasattr(_smoke_window, '_cancel_btn'),
   'the real cockpit has no broad Cancel widget')
ok(not _smoke_window._vantage_free_btn.winfo_manager(),
   'Dynamic High5 does not show a meaningless reset control')
_pinned_ui = dict(_smoke_ui)
_pinned_ui['campaign'] = dict(_smoke_ui['campaign'],
                              vantage=103.0, vantage_src='manual',
                              vantage_manual=103.0,
                              vantage_manual_label='07/28 high')
_smoke_window._on_update(_pinned_ui)
_root.update_idletasks()
ok(_smoke_window._vantage_free_btn.winfo_manager() == 'pack'
   and _smoke_window._vantage_free_btn.cget('text') ==
       'Return to Dynamic High5',
   'a real pinned cockpit exposes the clearly named return control')
_smoke_window.win.destroy()
_root.update()

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
ok(ui['campaign']['gear'] == select_auto_gear(calc_volatility(104.0, 86.0))
   and ui['campaign']['exit_tiers'] == [True, False, True],
   'AUTO reselects the live-volatility gear and preserves the tier change',
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
