"""Headless checks for the autopilot controller.

Run:  python scripts/test_autopilot_controller.py

The controller is deliberately thin: read the broker, hand the snapshot to the
engine, execute what it returns, save, push. These checks cover the parts that
are its own responsibility rather than the engine's — order ownership, mode
handling, what a poll actually emits, and the two rules that protect a
commander who also trades by hand.

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
                                is_bot_owned_order, market_phase)

passed = 0


def ok(cond, name, info=''):
    global passed
    assert cond, f'FAIL: {name} {info}'
    passed += 1
    print(f'  ok  {name}')


# ── Fakes ────────────────────────────────────────────────────────────────────

class FakeProvider:
    """Just enough Toss surface for a poll cycle."""

    def __init__(self, price=91.2, shares=31, avg=92.0, bp=4200.0):
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
        # high5 = 104, low5 = 86
        return [{'ts': f'2026-07-2{i}T00:00:00', 'date': f'07/2{i}',
                 'open': 100.0, 'high': 100.0 + i, 'low': 90.0 - i,
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
        self.gear_var = tk.IntVar(value=3)
        self.tier_vars = [tk.BooleanVar(value=(i == 2)) for i in (1, 2, 3)]
        self.auto_var = tk.BooleanVar(value=True)
        self.shares_var = tk.StringVar()
        self.avg_cost_var = tk.StringVar()
        self.deployed = True
        self.badges = []
        self.volatility = None

    def _fmt_init(self, v):
        return f'{v:,.2f}' if v else ''

    def line_config(self):
        return {'gear': self.gear_var.get(),
                'exit_tiers': [v.get() for v in self.tier_vars],
                'auto': self.auto_var.get()}

    def set_autopilot(self, key):
        self.badges.append(key)

    def update_live(self, price=None, volatility=None, **kw):
        self.volatility = volatility

    def compute(self):
        pass


class StubApp:
    def __init__(self, root, prov):
        self.root = root
        self._auto = True
        self._prov = prov
        self.deployed_rows = [StubRow('NVDA')]
        self.empty_rows = []
        self._ohlc_data = {}
        self.positions = [{'ticker': 'NVDA', 'shares': 31, 'avg_cost': 92.0,
                           'cost_basis': 2852.0, 'is_deployed': True}]
        self.rebuilt = 0

    def _get_unit_cash(self, ccy):
        return 1000.0

    def _toss_provider(self):
        return self._prov

    def _account_seq(self, prov):
        return 1

    def _rebuild_sections(self):
        self.rebuilt += 1

    def _reapply(self):
        pass


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


# ── Order ownership: the rule that protects hand trading ────────────────────
print('— order ownership —')
ok(is_bot_owned_order({'clientOrderId': _BOT_CLIENT_ID_PREFIX + 'x'}),
   'our clientOrderId prefix proves ownership, and survives a restart')
ok(is_bot_owned_order({'orderId': 'a1'}, ('a1',)),
   'so does an order id placed during this run')
ok(not is_bot_owned_order({'orderId': 'zzz', 'clientOrderId': 'app-made'}),
   'an order from the app or the web is NEVER ours')
ok(not is_bot_owned_order({}, ()), 'and nothing at all is not ours either')

# ── A poll, end to end ──────────────────────────────────────────────────────
print('— one poll, end to end —')
root, prov, app, ctrl = fresh()
ok(ctrl.watch('NVDA')[0], 'watching arms a slot')
ok(ctrl._slots['NVDA']['engine'].gear == 3,
   'on the gear the bot itself remembers — the card is not consulted')

slot = ctrl._slots['NVDA']
ctrl._cycle('NVDA', slot)
root.update()
ui = ctrl.ui_state('NVDA')
ok(ui and ui['state'] == 'DEPLOYED' and ui['shares'] == 31,
   'a poll reconciles the broker position into the payload')
ok(ui['lines'].get('chase') and ui['lines'].get('exit2'),
   'and carries the watched lines', str(list(ui['lines'])))
ok(ui['vol5'] is not None and ui['campaign']['gear'] == 3,
   'plus V and the campaign summary')
ok(not prov.placed, 'WATCH sent nothing')
_row = app.deployed_rows[0]
ok(_row.volatility is None,
   'and the poll left the card alone — no live price, no recompute')

# ── LIVE actually sends, and stamps ownership ───────────────────────────────
# LIVE is refused, and self-disarms, outside regular hours — correct, but it
# makes the suite depend on the clock. Pretend the market is open.
_real_phase = ctrl_mod.market_phase
ctrl_mod.market_phase = lambda *a, **k: 'REGULAR'

print('— LIVE sends, and stamps every order as ours —')
root2, prov2, app2, ctrl2 = fresh(price=200.0)   # far above the exit
ctrl2.watch('NVDA')
slot2 = ctrl2._slots['NVDA']
slot2['mode'] = 'LIVE'
ctrl2._cycle('NVDA', slot2)
root2.update()
ok(prov2.placed, 'a crossed line in LIVE sends the order', str(prov2.placed))
side, price, qty, coid = prov2.placed[0]
ok(side == 'SELL' and qty == 31,
   'the whole holding leaves at the armed tier', f'{side} {qty}')
ok(coid.startswith(_BOT_CLIENT_ID_PREFIX),
   'and it carries our clientOrderId, so we can recognise it later', coid)
ok('o1' in slot2['my_ids'], 'the returned order id is remembered too')

# ── Selling out stands the bot down ─────────────────────────────────────────
print('— selling out drops LIVE —')
prov2.shares, prov2.avg = 0, 0.0
ctrl2._cycle('NVDA', slot2)
root2.update()
ok(slot2['mode'] == 'WATCH',
   'the position closed, so LIVE goes off — starting a campaign is a '
   'deliberate act')
ok(slot2['alert'], 'and the reason is on the alert line', str(slot2['alert']))

# ── Cancel touches only our orders ──────────────────────────────────────────
print('— cancel_all spares orders the commander placed —')
root3, prov3, app3, ctrl3 = fresh()
ctrl3.watch('NVDA')
prov3.orders = [
    {'orderId': 'mine', 'side': 'BUY', 'price': 80.0, 'quantity': 5,
     'clientOrderId': _BOT_CLIENT_ID_PREFIX + 'abc'},
    {'orderId': 'theirs', 'side': 'BUY', 'price': 70.0, 'quantity': 9,
     'clientOrderId': 'typed-in-the-app'},
]
okmsg = ctrl3.cancel_all('NVDA')
ok(okmsg[0] and prov3.cancelled == ['mine'],
   'only the bot-owned order is cancelled', str(prov3.cancelled))

# ── A foreign order is visible but not ours ─────────────────────────────────
print('— a foreign order is reported, never claimed —')
prov3.orders = [{'orderId': 'theirs', 'side': 'SELL', 'price': 999.0,
                 'quantity': 3, 'clientOrderId': 'typed-in-the-app'}]
slot3 = ctrl3._slots['NVDA']
ctrl3._cycle('NVDA', slot3)
root3.update()
orders = ctrl3.ui_state('NVDA')['orders']
ok(len(orders) == 1 and orders[0]['mine'] is False,
   "someone else's resting order reaches the payload marked not-ours",
   str(orders))

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

# ── State keys ──────────────────────────────────────────────────────────────
print('— saved state —')
ok(ctrl4._store_key('NVDA') == 'NVDA#VCG',
   'campaigns save under their own key, leaving any v^ grid state alone')
ctrl4._store = {'NVDA': {'anchor': 100.0}}
ok(ctrl4._saved_for('NVDA') is None,
   'a leftover grid record is never restored as a campaign')
ctrl4._store = {'NVDA#VCG': {'strategy': 'V_COMMANDOS_GEARBOX', 'gear': 4}}
ok(ctrl4._saved_for('NVDA')['gear'] == 4, 'and its own record is')

ctrl_mod.market_phase = _real_phase
for r in (root, root2, root3, root4):
    r.destroy()

print(f'\nALL {passed} CONTROLLER CHECKS PASSED')
