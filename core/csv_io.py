import csv
import json
import os
from datetime import date

from core.calc import (DEFAULT_EXIT_TIER, DEFAULT_GEAR, LEGACY_LOAD_GEARS,
                       chase_drop, clamp_tier, gear_for_chase_pct, gear_params,
                       load_drop, normalize_gear, tier_for_exit_pct)

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH    = os.path.join(_HERE, 'config.json')
POSITIONS_PATH = os.path.join(_HERE, 'data', 'positions.csv')

# `gear` (1..5) and `exit_tier` (1..3) are the Gearbox V-Commandos columns.
# The old load_gear / buy_pct / t*_pct / t*_active columns are still written
# (derived from the gear) so older builds and scripts keep reading the file.
FIELDNAMES = [
    'ticker', 'tier', 'is_deployed', 'shares', 'avg_cost', 'cost_basis',
    'gear', 'exit_tier',
    'load_gear', 'buy_pct',
    't1_pct', 't2_pct', 't3_pct',
    't1_active', 't2_active', 't3_active',
    'auto_mode',
    'last_updated',
]

DEFAULT_CONFIG = {
    "N": 20,
    "unit_cash_krw": 1000000,
    "unit_cash_usd": 750,
    "fx_ticker": "USDKRW=X",
    "peak_lookback_days": 5,
    "fx_switch_level": 0,
    "market_provider": "toss",
}

# Every stock loads a full unit now, so the old Major/Minor tier no longer
# changes any math. The column is kept for CSV back-compat; all new defaults
# are 'Major'. Bold display = Korean (.KS) stocks, decided separately.
_PORTFOLIO = [
    ('005930.KS', 'Major'),
    ('000660.KS', 'Major'),
    ('NVDA',  'Major'),
    ('GOOGL', 'Major'),
    ('MU',    'Major'),
    ('MSFT',  'Major'),
    ('SNDK',  'Major'),
    ('AMD',   'Major'),
    ('TSM',   'Major'),
    ('AVGO',  'Major'),
    ('PLTR',  'Major'),
    ('AAPL',  'Major'),
    ('AMZN',  'Major'),
    ('STX',   'Major'),
    ('INTC',  'Major'),
    ('SKHY',  'Major'),
]


def _blank(ticker: str, tier: str) -> dict:
    return dict(_derived(DEFAULT_GEAR, DEFAULT_EXIT_TIER),
                ticker=ticker,
                tier=tier,
                is_deployed=False,
                shares=0,
                avg_cost=0.0,
                cost_basis=0.0,
                auto_mode=True,
                last_updated=str(date.today()))


def _derived(gear: int, exit_tier: int) -> dict:
    """The gear + tier, plus the legacy columns they imply."""
    tiers = gear_params(gear)['tiers']
    return {
        'gear':      gear,
        'exit_tier': exit_tier,
        'load_gear': load_drop(gear),
        'buy_pct':   chase_drop(gear),
        't1_pct':    float(tiers[0]),
        't2_pct':    float(tiers[1]),
        't3_pct':    float(tiers[2]),
        't1_active': exit_tier == 1,
        't2_active': exit_tier == 2,
        't3_active': exit_tier == 3,
    }


def _parse_row(row: dict) -> dict:
    """Convert a CSV DictReader row (all strings) into a typed position dict.

    Legacy files are migrated on read: a bait drop percent (or the older
    'A'/'B'/'C' and 'L1'-'L7' keys) becomes a gear, and the lowest ACTIVE sell
    tier becomes the single selected exit tier."""

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

    # -- Gear: explicit column wins, else migrate the legacy drop percent ----
    # buy_pct/load_gear held a DROP PERCENT, so 4 means G1, not G4.
    if row.get('gear'):
        gear = normalize_gear(row['gear'])
    elif row.get('buy_pct'):
        gear = gear_for_chase_pct(row['buy_pct'])
    elif row.get('load_gear'):
        v = str(row['load_gear']).strip().upper()
        gear = gear_for_chase_pct(LEGACY_LOAD_GEARS.get(v, v))
    elif row.get('buy_gear'):
        gear = gear_for_chase_pct(
            {'A': 4, 'B': 5, 'C': 6}.get(row['buy_gear'], 5))
    else:
        gear = DEFAULT_GEAR

    # -- Exit tier: explicit column wins, else the lowest active legacy tier -
    if row.get('exit_tier'):
        exit_tier = clamp_tier(row['exit_tier'])
    else:
        live = sorted(f(f't{i}_pct', 0.0) for i in (1, 2, 3)
                      if b(f't{i}_active', False))
        exit_tier = next(
            (t for t in (tier_for_exit_pct(gear, p) for p in live if p) if t),
            DEFAULT_EXIT_TIER)

    shares = int(f('shares', 0))
    return dict(_derived(gear, exit_tier),
                ticker=row['ticker'],
                tier=row.get('tier', 'Major'),
                is_deployed=b('is_deployed', shares > 0),
                shares=shares,
                avg_cost=f('avg_cost', 0.0),
                cost_basis=f('cost_basis', 0.0),
                auto_mode=b('auto_mode', True),
                last_updated=row.get('last_updated', ''))


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
            gear = normalize_gear(pos.get('gear', DEFAULT_GEAR))
            exit_tier = clamp_tier(pos.get('exit_tier', DEFAULT_EXIT_TIER))
            d = _derived(gear, exit_tier)
            writer.writerow({
                'ticker':       pos['ticker'],
                'tier':         pos['tier'],
                'is_deployed':  int(bool(pos.get('is_deployed', False))),
                'shares':       pos.get('shares', 0),
                'avg_cost':     pos.get('avg_cost', 0.0),
                'cost_basis':   pos.get('cost_basis', 0.0),
                'gear':         gear,
                'exit_tier':    exit_tier,
                'load_gear':    d['load_gear'],
                'buy_pct':      d['buy_pct'],
                't1_pct':       d['t1_pct'],
                't2_pct':       d['t2_pct'],
                't3_pct':       d['t3_pct'],
                't1_active':    int(d['t1_active']),
                't2_active':    int(d['t2_active']),
                't3_active':    int(d['t3_active']),
                'auto_mode':    int(bool(pos.get('auto_mode', True))),
                'last_updated': today,
            })
