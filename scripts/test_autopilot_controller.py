"""Headless checks for the autopilot controller (Daily v^ grid edition).

Run:  python scripts/test_autopilot_controller.py

The controller is deliberately thin: read the broker, hand the snapshot to
the grid engine, execute what it returns, save, push. These checks cover the
parts that are its own responsibility rather than the engine's — order
ownership, mode handling, the grid-scale gate, what a poll actually emits,
and the rules that protect a commander who also trades by hand.

A withdrawn Tk root is created so the real AutopilotController can be driven
end to end against a fake Toss provider.
"""

import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk

import gui.autopilot_ctrl as ctrl_mod
from gui.autopilot_ctrl import (AutopilotController, _BOT_CLIENT_ID_PREFIX,
                                avg_completed_day_v, is_bot_owned_order,
                                market_phase)

passed = 0


def ok(cond, name, info=''):
    global passed
    assert cond, f'FAIL: {name} {info}'
    passed += 1
    print(f'  ok  {name}')


# ── Fakes ────────────────────────────────────────────────────────────────────

class FakeProvider:
    """Just enough Toss surface for a poll cycle."""

    def __init__(self, price=95.0, shares=31, avg=92.0, bp=1_000_000.0):
        self.price, self.shares, self.avg, self.bp = price, shares, avg, bp
        self.orders = []
        self.placed = []
        self.cancelled = []
        self.place_result = (200, {'result': {'orderId': 'o1'}})

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
        # 5 completed sessions ending at close 95; ranges vary per day.
        return [{'ts': f'2026-07-2{i}T00:00:00', 'date': f'07/2{i}',
                 'open': 95.0, 'high': 95.0 + i, 'low': 92.0 - i,
                 'close': 95.0} for i in range(5)]

    def get_candles(self, ticker, count=6):
        return self.get_completed_daily_bars(ticker, count)

    def place_limit_order(self, ticker, side, price, qty, seq,
                          client_order_id=None):
        self.placed.append((side, price, qty, client_order_id))
        return self.place_result

    def cancel_order(self, oid, seq):
        self.cancelled.append(oid)
        self.orders = [o for o in self.orders if o.get('orderId') != oid]
        return 200, {}


class StubRow:
    def __init__(self, ticker):
        self.ticker = ticker
        self.badges = []
        self.volatility = None
        self.touched = 0

    def set_autopilot(self, key):
        self.badges.append(key)

    def update_live(self, price=None, volatility=None, **kw):
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


def fresh(**kw):
    root = tk.Tk()
    root.withdraw()
    prov = FakeProvider(**kw)
    app = StubApp(root, prov)
    c = AutopilotController(app)
    c._log = lambda *a: None            # keep the suite out of logs/
    c._store = {}
    c._save_state = lambda *a: True     # and out of data/
    return root, prov, app, c


# The grid builds its adventure only during REGULAR hours — pretend the
# market is open for the whole suite, and restore the clock at the end.
_real_phase = ctrl_mod.market_phase
ctrl_mod.market_phase = lambda *a, **k: 'REGULAR'

# ── Order ownership: the rule that protects hand trading ────────────────────
print('— order ownership —')
ok(is_bot_owned_order({'clientOrderId': _BOT_CLIENT_ID_PREFIX + 'x'}),
   'our clientOrderId prefix proves ownership when the broker echoes it')
ok(is_bot_owned_order({'orderId': 'a1'}, ('a1',)),
   'so does an order id placed during this run')
ok(not is_bot_owned_order({'orderId': 'zzz', 'clientOrderId': 'app-made'}),
   'an order from the app or the web is NEVER ours')
ok(not is_bot_owned_order({}, ()), 'and nothing at all is not ours either')

# ── The daily volatility indicator ───────────────────────────────────────────
print('— daily volatility (the grid’s own number) —')
bars = [{'ts': f'2026-07-2{i}T00:00:00', 'date': f'07/2{i}',
         'high': 103.0, 'low': 100.0, 'close': 101.0} for i in range(5)]
v = avg_completed_day_v(bars, '2026-07-30')
ok(v is not None and abs(v - 3.0) < 0.01,
   'avg day V is the mean of completed (H−L)/L ranges — 3% here', f'{v}')
bars_today = bars + [{'ts': '2026-07-30T00:00:00', 'date': '07/30',
                      'high': 200.0, 'low': 100.0, 'close': 150.0}]
v2 = avg_completed_day_v(bars_today, '2026-07-30')
ok(abs(v2 - 3.0) < 0.01,
   "today's still-growing bar is excluded — its range is not done yet")

# ── A poll, end to end ──────────────────────────────────────────────────────
print('— one poll, end to end —')
root, prov, app, ctrl = fresh()
ok(ctrl.watch('NVDA')[0], 'watching arms a slot')
slot = ctrl._slots['NVDA']
ctrl._cycle('NVDA', slot)
root.update()
ui = ctrl.ui_state('NVDA')
ok(ui and ui['grid_ready'] and ui['anchor'] == 95.0
   and ui['anchor_level'] == 0,
   'a poll builds the adventure: anchor = prev close at L+0',
   f"anchor={ui and ui.get('anchor')}")
ok(len(ui['grid']) == 11 and ui['level'] == 0,
   'eleven levels from +5 to -5, starting at L+0')
ok(ui['unit_qty'] == 11 and ui['base_inventory'] == 31,
   'unit sized from unit_cash/anchor, base = actual broker shares',
   f"unit={ui['unit_qty']} base={ui['base_inventory']}")
ok(ui['day_v_avg'] is not None, 'the daily-V scale hint rides the payload')
ok(not prov.placed, 'WATCH sent nothing')
_row = app.deployed_rows[0]
ok(_row.touched == 0,
   'and the poll left the card alone — no live price, no recompute')

# ── The grid scale gate ──────────────────────────────────────────────────────
print('— the grid scale —')
okd, msg = ctrl.set_scale('NVDA', 0.02)
ok(okd, 'the scale changes while nothing has traded', msg)
ctrl._cycle('NVDA', slot)
ui = ctrl.ui_state('NVDA')
ok(abs(ui['step'] - 0.02) < 1e-9 and ui['grid_ready'],
   'and the adventure re-initializes on the new spacing', f"step={ui['step']}")
ok(not ctrl.set_scale('NVDA', 0.05)[0], 'an unoffered scale is refused')
slot['mode'] = 'LIVE'
ok(not ctrl.set_scale('NVDA', 0.03)[0], 'and LIVE locks the selector')
slot['mode'] = 'WATCH'

# ── LIVE actually sends, and stamps ownership ───────────────────────────────
print('— LIVE sends, and stamps every order as ours —')
root2, prov2, app2, ctrl2 = fresh()
ctrl2.watch('NVDA')
slot2 = ctrl2._slots['NVDA']
ctrl2._cycle('NVDA', slot2)          # builds the adventure at 95
prov2.price = 98.0                   # crosses L+1 = 97.85
slot2['mode'] = 'LIVE'
ctrl2._cycle('NVDA', slot2)
root2.update()
ok(prov2.placed, 'a crossed level in LIVE sends the order', str(prov2.placed))
side, price, qty, coid = prov2.placed[0]
ok(side == 'SELL' and qty == 11,
   'one unit leaves at L+1 (target = base − W(1)·unit)', f'{side} {qty}')
ok(coid.startswith(_BOT_CLIENT_ID_PREFIX),
   'and it carries our clientOrderId', coid)
ok('o1' in slot2['my_ids'], 'the returned order id is remembered too')
ok(ctrl2.ui_state('NVDA')['scale_locked'],
   'an unresolved grid order locks the scale — one grid per day')

# ── A foreign order pauses the adventure ────────────────────────────────────
print('— a foreign order pauses, and is never claimed —')
root3, prov3, app3, ctrl3 = fresh()
ctrl3.watch('NVDA')
slot3 = ctrl3._slots['NVDA']
ctrl3._cycle('NVDA', slot3)
prov3.orders = [{'orderId': 'theirs', 'side': 'SELL', 'price': 999.0,
                 'quantity': 3, 'clientOrderId': 'typed-in-the-app'}]
prov3.price = 98.0                   # L+1 crossed, but a foreign order rests
slot3['mode'] = 'LIVE'
ctrl3._cycle('NVDA', slot3)
root3.update()
ui3 = ctrl3.ui_state('NVDA')
ok(not prov3.placed, 'no transition is taken while a foreign order rests')
ok('not' in ui3['status'] and 'mine' in ui3['status'],
   'and the status says why', ui3['status'])
ok(len(ui3['orders']) == 1 and ui3['orders'][0]['mine'] is False,
   "someone else's resting order reaches the payload marked not-ours")

# ── Cancel touches only our orders ──────────────────────────────────────────
print('— cancel_all spares orders the commander placed —')
prov3.orders = [
    {'orderId': 'mine', 'side': 'BUY', 'price': 80.0, 'quantity': 5,
     'clientOrderId': _BOT_CLIENT_ID_PREFIX + 'abc'},
    {'orderId': 'theirs', 'side': 'BUY', 'price': 70.0, 'quantity': 9,
     'clientOrderId': 'typed-in-the-app'},
]
okmsg = ctrl3.cancel_all('NVDA')
ok(okmsg[0] and prov3.cancelled == ['mine'],
   'only the bot-owned order is cancelled', str(prov3.cancelled))

# ── Modes ───────────────────────────────────────────────────────────────────
print('— modes —')
ok(ctrl3.set_mode('NVDA', 'NOPE')[0] is False, 'an unknown mode is refused')
ok(ctrl3.set_mode('NVDA', 'LIVE')[0], 'LIVE is allowed during regular hours')

ctrl_mod.market_phase = lambda *a, **k: 'CLOSED'
live_ok, msg = ctrl3.set_mode('NVDA', 'LIVE')
ok(not live_ok and 'regular hours' in msg,
   'and refused outside them, saying why', msg)
slot3['mode'] = 'LIVE'
ctrl3._cycle('NVDA', slot3)
root3.update()
ok(slot3['mode'] == 'WATCH',
   'a session that ends while LIVE disarms it on the next poll')
ctrl_mod.market_phase = lambda *a, **k: 'REGULAR'

ok(ctrl3.set_mode('ZZZZ', 'LIVE') == (False, 'not watching'),
   'a stock that is not watched cannot be armed')

# ── Disable leaves resting orders alone ─────────────────────────────────────
print('— disable —')
prov3.cancelled.clear()
ctrl3.disable('NVDA')
ok(not ctrl3.is_enabled('NVDA'), 'disable stops the watch')
ok(prov3.cancelled == [],
   'and leaves resting orders exactly as they are — closing a window is not '
   'an instruction to trade')

# ── No dialogs from the poll thread ─────────────────────────────────────────
print('— the poll thread never opens a dialog —')
ok(not hasattr(ctrl_mod, 'messagebox'),
   'the controller does not even import messagebox: a modal opened from the '
   'poll thread is application-modal and freezes the card grid behind the '
   'cockpit')
root4, prov4, app4, ctrl4 = fresh()
ctrl4.watch('NVDA')
slot4 = ctrl4._slots['NVDA']
for _ in range(ctrl_mod._FAIL_ANNOUNCE):
    ctrl4._data_failure('NVDA', slot4, 'no price')
ok(slot4['alert'] and 'no data' in slot4['alert'],
   'a run of failed polls raises an alert instead', str(slot4['alert']))
ctrl4._data_recovered('NVDA', slot4)
ok(slot4['alert'] is None and slot4['fail_n'] == 0,
   'and a good poll clears it')

# ── Saved state: the grid owns the bare key, campaigns keep theirs ──────────
print('— saved state —')
ok(ctrl4._store_key('NVDA') == 'NVDA',
   'the grid saves under the bare ticker — where it always did')
ctrl4._store = {'NVDA': {'anchor': 100.0, 'step': 0.03}}
ok(ctrl4._saved_for('NVDA')['anchor'] == 100.0,
   'a pre-removal adventure record restores as its own')
ctrl4._store = {'NVDA#GRID': {'anchor': 101.0, 'grid_ready': False}}
ok(ctrl4._saved_for('NVDA')['anchor'] == 101.0,
   'the short-lived ticker#GRID key of the two-strategy era still reads')
ctrl4._store = {'NVDA': {'strategy': 'V_COMMANDOS_GEARBOX', 'gear': 4},
                'NVDA#VCG': {'strategy': 'V_COMMANDOS_GEARBOX', 'gear': 4}}
ok(ctrl4._saved_for('NVDA') is None,
   'a campaign record is never restored as a grid adventure')

ctrl_mod.market_phase = _real_phase
for r in (root, root2, root3, root4):
    r.destroy()

print(f'\nALL {passed} CONTROLLER CHECKS PASSED')
