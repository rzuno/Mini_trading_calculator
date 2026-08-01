"""Focused controller regressions for the lightweight Gearbox watcher.

These checks avoid Tk construction and real broker access. Run directly:

    python scripts/test_autopilot_controller.py
"""

from collections import Counter
from types import SimpleNamespace
import os
import sys
import threading
import time

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui.autopilot_ctrl as controller_module
from gui.autopilot_ctrl import AutopilotController
from gui.main_window import App as MainWindow


passed = 0


def ok(condition, label, detail=''):
    global passed
    if not condition:
        raise AssertionError(f'{label}: {detail}')
    passed += 1
    print(f'  ok {passed:02d} - {label}')


class CountingProvider:
    def __init__(self):
        self.calls = Counter()
        self.price = 100.0
        self.shares = 10
        self.avg = 90.0
        self.bp = 5000.0
        self.open_rows = []

    def get_prices(self, tickers):
        self.calls['price'] += 1
        return {ticker: self.price for ticker in tickers}

    def get_holdings(self, seq, ticker=None):
        self.calls['holdings'] += 1
        return {'items': [{'symbol': ticker, 'quantity': self.shares,
                           'averagePurchasePrice': self.avg}]}

    def get_open_orders(self, seq, ticker=None):
        self.calls['open'] += 1
        return list(self.open_rows)

    def get_buying_power(self, seq, currency):
        self.calls['buying_power'] += 1
        return self.bp

    def get_order(self, seq, order_id):
        self.calls['detail'] += 1
        return {
            'orderId': order_id, 'clientOrderId': 'vcg-ap-nvda-b-1',
            'side': 'BUY', 'price': '95', 'quantity': '2',
            'status': 'PARTIAL_FILLED',
            'execution': {'filledQuantity': '1',
                          'averageExecutedPrice': '94.5'},
        }

    def get_closed_orders(self, *args, **kwargs):
        self.calls['closed'] += 1
        return []


print('snapshot call boundary')
snapshot_ctrl = AutopilotController.__new__(AutopilotController)
snapshot_ctrl._log = lambda *args: None
provider = CountingProvider()

plain = snapshot_ctrl._real_snapshot(provider, 1, 'NVDA', set(),
                                     prev_shares=10)
ok(provider.calls == Counter({
       'price': 1, 'holdings': 1, 'open': 1, 'buying_power': 1}),
   'ordinary poll performs exactly price, holdings, OPEN, and buying power',
   str(provider.calls))
ok(not plain['fills_correlated'] and not plain['recent_fills'],
   'ordinary poll never enables gross history replay')

provider.calls.clear()
provider.open_rows = [{
    'orderId': 'bot-1', 'clientOrderId': 'vcg-ap-nvda-b-1',
    'side': 'BUY', 'price': '95', 'quantity': '2', 'status': 'PENDING',
    'execution': {'filledQuantity': '0'},
}]
snapshot_ctrl._real_snapshot(
    provider, 1, 'NVDA', set(), prev_shares=10,
    pending_order_id='bot-1', pending_client_order_id='vcg-ap-nvda-b-1')
ok(provider.calls['detail'] == 0 and provider.calls['closed'] == 0
   and sum(provider.calls.values()) == 4,
   'visible pending OPEN row avoids exact detail and CLOSED',
   str(provider.calls))

provider.calls.clear()
provider.shares = 11
snapshot_ctrl._real_snapshot(
    provider, 1, 'NVDA', set(), prev_shares=10,
    pending_order_id='bot-1', pending_client_order_id='vcg-ap-nvda-b-1')
ok(provider.calls['detail'] == 1 and provider.calls['closed'] == 0,
   'holdings delta requests exact pending detail once', str(provider.calls))

provider.calls.clear()
provider.open_rows = []
snapshot_ctrl._real_snapshot(
    provider, 1, 'NVDA', set(), prev_shares=11,
    pending_order_id='bot-1', pending_client_order_id='vcg-ap-nvda-b-1')
ok(provider.calls['detail'] == 1 and provider.calls['closed'] == 0,
   'pending missing from OPEN requests exact detail once', str(provider.calls))


print('daily Vantage retry boundary')


class EmptyDailyProvider:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def get_completed_daily_bars(self, ticker, count):
        self.calls += 1
        if self.fail:
            raise RuntimeError('daily bars unavailable')
        return []


daily_ctrl = AutopilotController.__new__(AutopilotController)
daily_ctrl._trading_date = lambda ticker: '2026-08-02'
for daily_provider in (EmptyDailyProvider(), EmptyDailyProvider(fail=True)):
    daily_slot = {
        'daily_date': None, 'daily_retry_after': 0.0,
        'prev_close': None, 'high5': None, 'bars': [],
    }
    daily_ctrl._daily_vantage(daily_provider, 'NVDA', daily_slot)
    daily_ctrl._daily_vantage(daily_provider, 'NVDA', daily_slot)
    ok(daily_provider.calls == 1
       and daily_slot['daily_retry_after'] > time.time(),
       'empty/error completed bars are throttled instead of fetched every poll')


print('full-cycle candle maintenance boundary')


class MaintenanceProvider(CountingProvider):
    def _bars(self, count):
        return [
            {'ts': f'2026-07-{day:02d}T16:00:00-04:00',
             'date': f'07/{day:02d}', 'open': 95.0, 'high': 105.0,
             'low': 90.0, 'close': 100.0}
            for day in range(25, 25 + count)
        ]

    def get_completed_daily_bars(self, ticker, count):
        self.calls['completed_bars'] += 1
        return self._bars(count)

    def get_candles(self, ticker, count=6):
        self.calls['candles'] += 1
        return self._bars(max(0, count - 1)) + [{
            'ts': '2026-08-02T12:00:00-04:00', 'date': '08/02',
            'open': 101.0, 'high': 121.0, 'low': 94.0, 'close': 110.0,
        }]


maintenance_provider = MaintenanceProvider()
maintenance_app = SimpleNamespace(
    _toss_provider=lambda: maintenance_provider,
    _account_seq=lambda prov: 1,
    _ohlc_data={},
)
maintenance_ctrl = AutopilotController.__new__(AutopilotController)
maintenance_ctrl.app = maintenance_app
maintenance_ctrl._lock = threading.Lock()
maintenance_ctrl._units = {'KRW': 0.0, 'USD': 1000.0}
maintenance_ctrl._slots = {}
maintenance_ctrl._saved_for = lambda ticker: None
maintenance_ctrl._trading_date = lambda ticker: '2026-08-02'
maintenance_ctrl._data_recovered = lambda *args: None
maintenance_ctrl._data_failure = lambda *args: None
maintenance_ctrl._apply_vantage_request = lambda *args, **kwargs: None
maintenance_ctrl._execute = lambda *args, **kwargs: None
maintenance_ctrl._retry_unresolved_submission = lambda *args, **kwargs: None
maintenance_ctrl._save_state = lambda *args: True
maintenance_ctrl._push_ui = lambda *args, **kwargs: None
maintenance_ctrl._log = lambda *args: None
class MaintenanceEngine:
    def __init__(self):
        self._pending = None
        self.dirty = False
        self.polls = []

    def poll(self, snap):
        self.polls.append(dict(snap))
        return []


maintenance_engine = MaintenanceEngine()
maintenance_slot = {
    'engine': maintenance_engine, 'engine_lock': threading.RLock(),
    'mode': 'WATCH', 'stopping': False, 'card': None,
    'broker_shares': 10, 'my_ids': set(), 'ticks': [],
    'prev_close': None, 'high5': None, 'vol5': None,
    'bars': [], 'daily_date': None, 'daily_retry_after': 0.0,
    'ohlc': None, 'ohlc_ts': 0.0, 'ohlc_view': None,
}
maintenance_ctrl._slots['NVDA'] = maintenance_slot
old_market_phase = controller_module.market_phase
controller_module.market_phase = lambda ticker: 'REGULAR'
try:
    maintenance_ctrl._cycle('NVDA', maintenance_slot)
    ok(maintenance_provider.calls['completed_bars'] == 0
       and maintenance_provider.calls['candles'] == 1,
       'initial full cycle uses one raw candle request for chart and Vantage',
       str(maintenance_provider.calls))
    ok(maintenance_slot['ohlc'][-1]['high'] == 121.0
       and maintenance_engine.polls[-1]['highs'][-1]
           == ('2026-08-02', 121.0)
       and maintenance_slot['high5'] == 105.0,
       "today's intraday high is immediate while completed High5 stays separate",
       str(maintenance_engine.polls[-1].get('highs')))
    maintenance_slot['ohlc_ts'] -= controller_module._OHLC_REFRESH_S + 1
    maintenance_ctrl._cycle('NVDA', maintenance_slot)
finally:
    controller_module.market_phase = old_market_phase
ok(maintenance_provider.calls['completed_bars'] == 0
   and maintenance_provider.calls['candles'] == 2,
   'later full cycle performs only the scheduled candle maintenance read',
   str(maintenance_provider.calls))


print('one-pass broker delta')


class PollProbeEngine:
    def __init__(self):
        self._pending = {
            'accepted': True, 'order_id': 'bot-1',
            'client_order_id': 'vcg-ap-nvda-b-1',
            'side': 'BUY', 'price': 95.0, 'qty': 2,
        }
        self.polls = []
        self.dirty = False

    def poll(self, snap):
        self.polls.append(dict(snap))
        return []


class CycleProvider(CountingProvider):
    def get_order(self, seq, order_id):
        self.calls['detail'] += 1
        return {}


cycle_provider = CycleProvider()
cycle_provider.shares = 12
cycle_app = SimpleNamespace(
    _toss_provider=lambda: cycle_provider,
    _account_seq=lambda prov: 1,
)
cycle_ctrl = AutopilotController.__new__(AutopilotController)
cycle_ctrl.app = cycle_app
cycle_ctrl._lock = threading.Lock()
cycle_ctrl._units = {'KRW': 0.0, 'USD': 1000.0}
cycle_ctrl._saved_for = lambda ticker: None
cycle_ctrl._daily_vantage = lambda *args: (99.0, 105.0)
cycle_ctrl._refresh_ohlc = lambda *args: None
cycle_ctrl._session_highs = lambda *args: []
cycle_ctrl._data_recovered = lambda *args: None
cycle_ctrl._data_failure = lambda *args: None
cycle_ctrl._apply_vantage_request = lambda *args, **kwargs: None
cycle_ctrl._execute = lambda *args, **kwargs: None
cycle_ctrl._retry_unresolved_submission = lambda *args, **kwargs: None
cycle_ctrl._save_state = lambda *args: True
cycle_ctrl._push_ui = lambda *args, **kwargs: None
cycle_ctrl._log = lambda *args: None
probe_engine = PollProbeEngine()
cycle_slot = {
    'engine': probe_engine, 'engine_lock': threading.RLock(),
    'mode': 'WATCH', 'stopping': False, 'card': None,
    'broker_shares': 10, 'my_ids': set(), 'ticks': [],
}
cycle_ctrl._slots = {'NVDA': cycle_slot}
old_market_phase = controller_module.market_phase
controller_module.market_phase = lambda ticker: 'REGULAR'
try:
    cycle_ctrl._cycle('NVDA', cycle_slot)
finally:
    controller_module.market_phase = old_market_phase
ok(len(probe_engine.polls) == 1 and cycle_slot['broker_shares'] == 12,
   'a holdings delta reaches the engine on its first observation')
ok(cycle_provider.calls['closed'] == 0
   and cycle_provider.calls['detail'] == 1,
   'one-pass delta uses exact pending detail but never CLOSED history',
   str(cycle_provider.calls))


print('WATCH close and reopen lifecycle')


class ImmediateRoot:
    @staticmethod
    def after(delay, callback, *args):
        callback(*args)


class WatchApp:
    _auto = True
    deployed_rows = []
    empty_rows = []

    @staticmethod
    def _get_unit_cash(currency):
        return 1000.0

    @staticmethod
    def _toss_provider():
        return None


watch_ctrl = AutopilotController.__new__(AutopilotController)
watch_ctrl.app = WatchApp()
watch_ctrl.root = ImmediateRoot()
watch_ctrl._lock = threading.Lock()
watch_ctrl._units = {'KRW': 0.0, 'USD': 0.0}
watch_ctrl._wake = threading.Event()
watch_ctrl._listeners = {}
watch_ctrl._apply_row_badge = lambda *args: None
watch_ctrl._notify = lambda *args: None
watch_ctrl._log = lambda *args: None
watch_ctrl._ensure_thread = lambda: None

bare_slot = {'engine': SimpleNamespace(_pending=None), 'mode': 'WATCH',
             'stopping': False}
watch_ctrl._slots = {'NVDA': bare_slot}
watch_ctrl.disable('NVDA')
ok('NVDA' not in watch_ctrl._slots,
   'closing bare WATCH removes a slot immediately')

pending_engine = SimpleNamespace(_pending={
    'accepted': True, 'order_id': 'bot-1',
    'client_order_id': 'vcg-ap-nvda-b-1',
})
cleanup_slot = {
    'engine': pending_engine, 'mode': 'WATCH', 'stopping': False,
    'cleanup_after': 0.0, 'cleanup_started_at': None,
    'cleanup_client_empty_reads': 0,
    'cleanup_retire_pending_save': False,
}
watch_ctrl._slots = {'NVDA': cleanup_slot}
watch_ctrl.disable('NVDA')
ok(watch_ctrl._slots['NVDA']['stopping'],
   'accepted bot pending keeps a safe asynchronous cleanup slot')
reopened, reopen_message = watch_ctrl.watch('NVDA')
ok(reopened and not cleanup_slot['stopping']
   and cleanup_slot['mode'] == 'WATCH',
   'reopening attaches to cleanup as WATCH even with Toss unavailable',
   reopen_message)


print('local card update boundary')


class LocalRow:
    ticker = 'NVDA'
    deployed = True

    def __init__(self):
        self._gap = None
        self.compute_calls = 0
        self.callback_calls = 0
        self.badges = []

    def set_autopilot(self, key):
        self.badges.append(key)

    def update_broker_position(self, qty, avg):
        return False

    def update_live(self, price=None, **kwargs):
        self._gap = price

    def compute(self):
        self.compute_calls += 1

    def line_config(self):
        return {'gear': 3, 'exit_tiers': [False, True, False], 'auto': True}

    def _on_compute_cb(self):
        self.callback_calls += 1


local_row = LocalRow()
local_app = SimpleNamespace(
    deployed_rows=[local_row], empty_rows=[],
    _reorder_cards=lambda: None,
)
reorders = []
local_app._reorder_cards = lambda: reorders.append(time.time())
local_ctrl = AutopilotController.__new__(AutopilotController)
local_ctrl.app = local_app
local_ctrl._lock = threading.Lock()
local_ctrl._card_reorder_after = 0.0
local_ctrl._wake = threading.Event()
local_slot = {
    'card': {'gear': 3, 'exit_tiers': [False, True, False], 'auto': True},
    'card_order_key': None,
}
local_ctrl._slots = {'NVDA': local_slot}

def card_ui(price):
    return {
        'price': price, 'shares': 10, 'avg_cost': 90.0, 'vol5': 12.0,
        'orders': [], 'campaign': {'vantage': 105.0,
                                   'vantage_src': 'campaign'},
    }


local_ctrl._sync_card_from_ui('NVDA', card_ui(1.0))
local_ctrl._sync_card_from_ui('NVDA', card_ui(2.0))
local_ctrl._sync_card_from_ui('NVDA', card_ui(3.0))
ok(local_row.callback_calls == 0 and local_row.compute_calls == 3,
   'price ticks recompute only the watched row without global callback churn')
ok(len(reorders) == 1,
   'changed gap key reorders once and controller throttle coalesces the burst',
   str(len(reorders)))

flat_row = LocalRow()
flat_row.deployed = False
local_transitions = []


def apply_local_transition(ticker, shares, avg):
    local_transitions.append((ticker, shares, avg))
    flat_row.deployed = bool(shares)
    flat_app.empty_rows = []
    flat_app.deployed_rows = [flat_row]
    return True


def forbidden_catalogue_fetch():
    raise AssertionError('a per-ticker transition must not fetch the catalogue')


flat_app = SimpleNamespace(
    deployed_rows=[], empty_rows=[flat_row],
    _reorder_cards=lambda: None,
    _apply_autopilot_position=apply_local_transition,
    _fetch_bg=forbidden_catalogue_fetch,
)
flat_ctrl = AutopilotController.__new__(AutopilotController)
flat_ctrl.app = flat_app
flat_ctrl._lock = threading.Lock()
flat_ctrl._card_reorder_after = time.time() + 60
flat_ctrl._wake = threading.Event()
flat_ctrl._slots = {'NVDA': {
    'card': {'gear': 3, 'exit_tiers': [False, True, False], 'auto': True},
    'card_order_key': None,
}}
flat_ctrl._sync_card_from_ui('NVDA', card_ui(1.0))
ok(local_transitions == [('NVDA', 10, 90.0)] and flat_row.deployed,
   'EMPTY-to-DEPLOYED uses the fresh ticker snapshot locally')


print('main-window targeted position transition')
transition_calls = []
transition_window = MainWindow.__new__(MainWindow)
transition_window.positions = [{
    'ticker': 'NVDA', 'is_deployed': False, 'shares': 0,
    'avg_cost': 0.0, 'cost_basis': 0.0, 'exit_tier': 1,
}]
transition_window._last_account = {
    'cash_usd': 5000.0,
    'items': [{'ticker': 'AAPL', 'shares': 3, 'avg': 150.0}],
}
transition_window._last_data = {'NVDA': {'price': 100.0}}
transition_window._fx_rate = 1300.0
transition_window._fx_avg_3m = 1280.0
transition_window._rebuild_sections = lambda: transition_calls.append('rebuild')


def apply_cached(data, fx, fx_avg, account=None, open_orders=None,
                 quiet=False):
    transition_calls.append(('apply', data, account, quiet))


transition_window._apply_live = apply_cached
transitioned = MainWindow._apply_autopilot_position(
    transition_window, 'NVDA', 4, 92.5)
nvda_pos = transition_window.positions[0]
cached_nvda = next(
    item for item in transition_window._last_account['items']
    if item.get('ticker') == 'NVDA')
ok(transitioned and nvda_pos['is_deployed']
   and nvda_pos['shares'] == 4 and nvda_pos['avg_cost'] == 92.5
   and cached_nvda['shares'] == 4 and cached_nvda['avg'] == 92.5
   and transition_window._last_account['cash_usd'] == 5000.0,
   'targeted transition updates one position and preserves cached account data')
ok(transition_calls[0] == 'rebuild'
   and transition_calls[1][0] == 'apply'
   and transition_calls[1][2] is None
   and transition_calls[1][3] is True,
   'targeted transition rebuilds from cached market data without stale account reconciliation')
emptied = MainWindow._apply_autopilot_position(
    transition_window, 'NVDA', 0, 0.0)
ok(emptied and not nvda_pos['is_deployed'] and nvda_pos['shares'] == 0
   and all(item.get('ticker') != 'NVDA'
           for item in transition_window._last_account['items']),
   'targeted DEPLOYED-to-EMPTY removes only that cached holding')


print('insufficient army reactivation')


class IntentEngine:
    def __init__(self):
        self.buy_state = 'OK'
        self.status = ''
        self.accepted_order_id = None
        self.arm()

    def arm(self):
        self._pending = {'side': 'BUY', 'price': 90.0, 'qty': 1,
                         'accepted': False}

    def _matches(self, side, price, qty):
        return bool(self._pending and self._pending['side'] == side
                    and self._pending['price'] == price
                    and self._pending['qty'] == qty)

    def note_order_submitted(self, side, price, qty, client_id):
        if not self._matches(side, price, qty):
            return False
        self._pending.update({'accepted': True,
                              'client_order_id': client_id})
        return True

    def note_order_failed(self, side, price, qty):
        if not self._matches(side, price, qty):
            return False
        self._pending = None
        return True

    def note_order_accepted(self, side, price, qty, order_id=None,
                            client_order_id=None):
        if not self._matches(side, price, qty):
            return False
        self._pending.update({'accepted': True, 'order_id': order_id,
                              'client_order_id': client_order_id})
        self.accepted_order_id = order_id
        return True


class RejectThenAcceptProvider:
    def __init__(self):
        self.calls = 0

    def place_limit_order(self, ticker, side, price, qty, seq,
                          client_order_id=None):
        self.calls += 1
        if self.calls == 1:
            return 400, {'error': {'code': 'insufficient-buying-power'}}
        return 200, {'result': {'orderId': 'accepted-after-refill',
                                'clientOrderId': client_order_id}}


army_ctrl = AutopilotController.__new__(AutopilotController)
army_ctrl._log = lambda *args: None
army_ctrl._save_state = lambda *args: True
army_ctrl._lock = threading.Lock()
army_engine = IntentEngine()
army_provider = RejectThenAcceptProvider()
army_slot = {
    'my_ids': set(), 'backoff_until': {'BUY': 0.0, 'SELL': 0.0},
    'backoff_reason': {'BUY': None, 'SELL': None},
    'insuff_warned': False, 'mode': 'LIVE', 'stopping': False,
}
army_ctrl._slots = {'NVDA': army_slot}
placed, message = army_ctrl._place_real(
    'NVDA', army_slot, army_provider, 1, army_engine,
    'BUY', 90.0, 1, 'LOAD')
ok(not placed and army_engine.buy_state == 'EXHAUSTED'
   and AutopilotController._side_backoff(army_slot, 'BUY') <= time.time(),
   'broker-insufficient mutes BUY without a 300-second timer', message)

army_engine.arm()
old_market_phase = controller_module.market_phase
controller_module.market_phase = lambda ticker: 'REGULAR'
try:
    army_ctrl._execute(
        'NVDA', army_slot, army_provider, 1, army_engine,
        [('place', 'BUY', 90.0, 1, 'LOAD')],
        snap={'can_trade': True, 'orders': []})
finally:
    controller_module.market_phase = old_market_phase
ok(army_provider.calls == 2
   and army_engine.accepted_order_id == 'accepted-after-refill',
   'fresh fundable BUY can reactivate on the very next poll')


print('LIVE disable / durable-submit boundary')


class BlockingPostProvider:
    def __init__(self):
        self.post_started = threading.Event()
        self.release_post = threading.Event()
        self.posts = []

    def place_limit_order(self, ticker, side, price, qty, seq,
                          client_order_id=None):
        self.posts.append(client_order_id)
        self.post_started.set()
        if not self.release_post.wait(2.0):
            raise TimeoutError('test did not release POST')
        return 200, {'result': {'orderId': 'race-order',
                                'clientOrderId': client_order_id}}


race_ctrl = AutopilotController.__new__(AutopilotController)
race_ctrl.root = ImmediateRoot()
race_ctrl._lock = threading.Lock()
race_ctrl._listeners = {}
race_ctrl._wake = threading.Event()
race_ctrl._apply_row_badge = lambda *args: None
race_ctrl._notify = lambda *args: None
race_ctrl._log = lambda *args: None
race_engine = IntentEngine()
race_provider = BlockingPostProvider()
race_slot = {
    'engine': race_engine, 'mode': 'LIVE', 'stopping': False,
    'my_ids': set(), 'backoff_until': {'BUY': 0.0, 'SELL': 0.0},
    'backoff_reason': {'BUY': None, 'SELL': None},
    'insuff_warned': False, 'cleanup_after': 0.0,
    'cleanup_started_at': None, 'cleanup_client_empty_reads': 0,
    'cleanup_retire_pending_save': False,
}
race_ctrl._slots = {'NVDA': race_slot}
save_started = threading.Event()
release_save = threading.Event()
durable_ids = []


def blocking_save(ticker, engine):
    durable_ids.append(engine._pending.get('client_order_id'))
    save_started.set()
    return release_save.wait(2.0)


race_ctrl._save_state = blocking_save
placement_result = []
placement = threading.Thread(
    target=lambda: placement_result.append(race_ctrl._place_real(
        'NVDA', race_slot, race_provider, 1, race_engine,
        'BUY', 90.0, 1, 'LOAD')))
placement.start()
ok(save_started.wait(1.0) and durable_ids[0],
   'client id is prebound before the broker POST boundary')

disable_entered = threading.Event()
disable_done = threading.Event()


def disable_during_prebind():
    disable_entered.set()
    race_ctrl.disable('NVDA')
    disable_done.set()


disabler = threading.Thread(target=disable_during_prebind)
disabler.start()
ok(disable_entered.wait(1.0) and not disable_done.wait(0.05),
   'disable cannot remove the slot during durable identity prebinding')
release_save.set()
ok(race_provider.post_started.wait(1.0) and disable_done.wait(1.0)
   and race_ctrl._slots.get('NVDA') is race_slot
   and race_slot['stopping'] and race_slot['mode'] == 'WATCH'
   and race_engine._pending.get('client_order_id') == durable_ids[0],
   'a close racing the POST retains the same identity as a cleanup slot')
race_provider.release_post.set()
placement.join(2.0)
disabler.join(2.0)
ok(not placement.is_alive() and placement_result == [(True,
   'BUY 1 @ 90.00 placed')] and race_provider.posts == durable_ids,
   'the in-flight POST completes once and remains recoverable by cleanup',
   str(placement_result))


print('accepted-identity recovery boundary')


class NoPostProvider:
    def __init__(self):
        self.posts = 0

    def place_limit_order(self, *args, **kwargs):
        self.posts += 1
        raise AssertionError('recovery gate must not reach broker POST')


reopen_ctrl = AutopilotController.__new__(AutopilotController)
reopen_ctrl._lock = threading.Lock()
reopen_ctrl._log = lambda *args: None
reopen_ctrl._save_state = lambda *args: True
reopen_engine = IntentEngine()
reopen_engine.note_order_submitted('BUY', 90.0, 1, 'saved-client-id')
reopen_slot = {
    'engine': reopen_engine, 'mode': 'WATCH', 'stopping': False,
    'my_ids': set(), 'backoff_until': {'BUY': 0.0, 'SELL': 0.0},
    'backoff_reason': {'BUY': None, 'SELL': None},
}
reopen_ctrl._slots = {'NVDA': reopen_slot}
reopen_provider = NoPostProvider()
recovered, recovery_message = reopen_ctrl._place_real(
    'NVDA', reopen_slot, reopen_provider, 1, reopen_engine,
    'BUY', 90.0, 1, 'LOAD', client_id='saved-client-id',
    cleanup_recovery=True)
ok(not recovered and reopen_provider.posts == 0
   and reopen_engine._pending.get('accepted')
   and reopen_engine._pending.get('client_order_id') == 'saved-client-id',
   'reopening WATCH blocks cleanup POST without erasing accepted identity',
   recovery_message)

retry_ctrl = AutopilotController.__new__(AutopilotController)
retry_ctrl._lock = threading.Lock()
retry_ctrl._log = lambda *args: None
retry_ctrl._save_state = lambda *args: False
retry_engine = IntentEngine()
retry_engine.note_order_submitted('BUY', 90.0, 1, 'retry-client-id')
retry_slot = {
    'engine': retry_engine, 'mode': 'LIVE', 'stopping': False,
    'my_ids': set(), 'backoff_until': {'BUY': 0.0, 'SELL': 0.0},
    'backoff_reason': {'BUY': None, 'SELL': None},
}
retry_ctrl._slots = {'NVDA': retry_slot}
retry_provider = NoPostProvider()
retried, retry_message = retry_ctrl._place_real(
    'NVDA', retry_slot, retry_provider, 1, retry_engine,
    'BUY', 90.0, 1, 'LOAD', client_id='retry-client-id')
ok(not retried and retry_provider.posts == 0
   and retry_engine._pending.get('accepted')
   and retry_engine._pending.get('client_order_id') == 'retry-client-id',
   'failed repeat persistence retains the prior accepted identity and sends no POST',
   retry_message)


print('dialog ownership')


class VisibleWindow:
    @staticmethod
    def winfo_exists():
        return True

    @staticmethod
    def winfo_viewable():
        return True


visible = VisibleWindow()
dialog_root = object()
dialog_ctrl = AutopilotController.__new__(AutopilotController)
dialog_ctrl.root = dialog_root
dialog_ctrl.app = SimpleNamespace(
    _ap_windows={'NVDA': SimpleNamespace(win=visible)})
ok(dialog_ctrl._dialog_parent('NVDA') is visible
   and dialog_ctrl._dialog_parent('AAPL') is dialog_root,
   'controller warnings are owned by the visible campaign or main root')


print(f'ALL {passed} CONTROLLER CHECKS PASSED')
