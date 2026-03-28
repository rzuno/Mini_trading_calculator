import csv
import json
import os
from datetime import date

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH    = os.path.join(_HERE, 'config.json')
POSITIONS_PATH = os.path.join(_HERE, 'data', 'positions.csv')

FIELDNAMES = [
    'ticker', 'tier', 'is_deployed', 'shares', 'avg_cost', 'cost_basis',
    'load_gear', 'buy_pct',
    't1_pct', 't2_pct', 't3_pct',
    't1_active', 't2_active', 't3_active',
    'last_updated',
]

DEFAULT_CONFIG = {
    "N": 20,
    "unit_cash_krw": 1000000,
    "unit_cash_usd": 750,
    "fx_ticker": "USDKRW=X",
    "peak_lookback_days": 5,
}

_PORTFOLIO = [
    ('005930.KS', 'Major'),
    ('000660.KS', 'Major'),
    ('NVDA',  'Major'),
    ('GOOGL', 'Major'),
    ('MU',    'Minor'),
    ('MSFT',  'Minor'),
    ('SNDK',  'Minor'),
    ('AMD',   'Minor'),
    ('TSM',   'Minor'),
    ('AVGO',  'Minor'),
    ('PLTR',  'Minor'),
    ('AAPL',  'Minor'),
    ('AMZN',  'Minor'),
    ('STX',   'Minor'),
]


def _blank(ticker: str, tier: str) -> dict:
    return {
        'ticker':      ticker,
        'tier':        tier,
        'is_deployed': False,
        'shares':      0,
        'avg_cost':    0.0,
        'cost_basis':  0.0,
        'load_gear':   'L2',
        'buy_pct':     5,
        't1_pct':      4.0,
        't2_pct':      6.0,
        't3_pct':      8.0,
        't1_active':   True,
        't2_active':   True,
        't3_active':   True,
        'last_updated': str(date.today()),
    }


def _parse_row(row: dict) -> dict:
    """Convert a CSV DictReader row (all strings) into typed position dict."""
    # Handle legacy CSV that had buy_gear (A/B/C) and sell_gear (A-E)
    buy_pct = 5
    if 'buy_pct' in row and row['buy_pct']:
        try:
            buy_pct = int(row['buy_pct'])
        except ValueError:
            pass
    elif 'buy_gear' in row:
        buy_pct = {'A': 4, 'B': 5, 'C': 6}.get(row.get('buy_gear', 'B'), 5)

    # Legacy sell_gear → default tier pcts
    legacy_sell = row.get('sell_gear', 'C')
    SELL_DEFAULTS = {
        'A': (2.0, 4.0, 6.0),
        'B': (3.0, 5.0, 7.0),
        'C': (4.0, 6.0, 8.0),
        'D': (5.0, 7.0, 9.0),
        'E': (6.0, 8.0, 10.0),
    }
    t1d, t2d, t3d = SELL_DEFAULTS.get(legacy_sell, (4.0, 6.0, 8.0))

    def f(key, default):
        v = row.get(key, '')
        try:
            return float(v) if v != '' else default
        except ValueError:
            return default

    def b(key, default):
        v = row.get(key, '')
        if v in ('1', 'True', 'true'):  return True
        if v in ('0', 'False', 'false'): return False
        return default

    shares = int(f('shares', 0))
    return {
        'ticker':      row['ticker'],
        'tier':        row['tier'],
        'is_deployed': b('is_deployed', shares > 0),
        'shares':      shares,
        'avg_cost':    f('avg_cost', 0.0),
        'cost_basis':  f('cost_basis', 0.0),
        'load_gear':   row.get('load_gear', 'L2'),
        'buy_pct':     buy_pct,
        't1_pct':      f('t1_pct', t1d),
        't2_pct':      f('t2_pct', t2d),
        't3_pct':      f('t3_pct', t3d),
        't1_active':   b('t1_active', True),
        't2_active':   b('t2_active', True),
        't3_active':   b('t3_active', True),
        'last_updated': row.get('last_updated', ''),
    }


# ── Public API ────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)


def save_config(config: dict) -> None:
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)


def load_positions() -> list:
    if not os.path.exists(POSITIONS_PATH):
        defaults = [_blank(t, tier) for t, tier in _PORTFOLIO]
        save_positions(defaults)
        return defaults

    rows = []
    with open(POSITIONS_PATH, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(_parse_row(row))
    return rows


def save_positions(positions: list) -> None:
    os.makedirs(os.path.dirname(POSITIONS_PATH), exist_ok=True)
    today = str(date.today())
    with open(POSITIONS_PATH, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for pos in positions:
            writer.writerow({
                'ticker':       pos['ticker'],
                'tier':         pos['tier'],
                'is_deployed':  int(bool(pos.get('is_deployed', False))),
                'shares':       pos.get('shares', 0),
                'avg_cost':     pos.get('avg_cost', 0.0),
                'cost_basis':   pos.get('cost_basis', 0.0),
                'load_gear':    pos.get('load_gear', 'L2'),
                'buy_pct':      pos.get('buy_pct', 5),
                't1_pct':       pos.get('t1_pct', 4.0),
                't2_pct':       pos.get('t2_pct', 6.0),
                't3_pct':       pos.get('t3_pct', 8.0),
                't1_active':    int(bool(pos.get('t1_active', True))),
                't2_active':    int(bool(pos.get('t2_active', True))),
                't3_active':    int(bool(pos.get('t3_active', True))),
                'last_updated': today,
            })
