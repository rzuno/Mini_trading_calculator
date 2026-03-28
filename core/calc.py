import math

# ── Stock catalogue ───────────────────────────────────────────────────────────
STOCK_NAMES = {
    '005930.KS': 'Samsung Electronics',
    '000660.KS': 'SK Hynix',
    'NVDA':  'NVIDIA',
    'GOOGL': 'Alphabet',
    'MU':    'Micron',
    'MSFT':  'Microsoft',
    'WDC':   'Western Digital',
    'AMD':   'AMD',
    'TSM':   'TSMC',
    'AVGO':  'Broadcom',
    'PLTR':  'Palantir',
    'AAPL':  'Apple',
    'AMZN':  'Amazon',
    'STX':   'Seagate',
}

TIER_MULTIPLIER = {'Major': 1.0, 'Minor': 0.5}

# ── Load gear (blue=cautious → red=aggressive) ───────────────────────────────
LOAD_GEARS = {
    'L1': {'drop': 0.04, 'label': 'L1 (\u22124%)',  'color': '#B0C4DE'},
    'L2': {'drop': 0.05, 'label': 'L2 (\u22125%)',  'color': '#6699BB'},
    'L3': {'drop': 0.06, 'label': 'L3 (\u22126%)',  'color': '#336699'},
    'L4': {'drop': 0.10, 'label': 'L4 (\u221210%)', 'color': '#264D80'},
    'L5': {'drop': 0.15, 'label': 'L5 (\u221215%)', 'color': '#1A3366'},
}
LOAD_GEAR_KEYS   = list(LOAD_GEARS.keys())
LOAD_GEAR_LABELS = [v['label'] for v in LOAD_GEARS.values()]
LOAD_LABEL_TO_KEY = {v['label']: k for k, v in LOAD_GEARS.items()}

# ── Buy gear (blue=mild → red=deep) ──────────────────────────────────────────
BUY_GEAR_PCTS = [4, 5, 6]

BUY_GEAR_INFO = {
    4: {'label': '4% drop (\u00d70.5)', 'ratio': 0.5, 'color': '#4682B4'},
    5: {'label': '5% drop (\u00d70.6)', 'ratio': 0.6, 'color': '#8B6BA8'},
    6: {'label': '6% drop (\u00d70.7)', 'ratio': 0.7, 'color': '#CC6666'},
}
BUY_GEAR_LABELS = [BUY_GEAR_INFO[p]['label'] for p in BUY_GEAR_PCTS]
BUY_LABEL_TO_PCT = {v['label']: k for k, v in BUY_GEAR_INFO.items()}


def buy_pct_color(pct: int) -> str:
    return BUY_GEAR_INFO.get(pct, {}).get('color', '#FFFFFF')


def load_gear_color(gear: str) -> str:
    return LOAD_GEARS.get(gear, {}).get('color', '#FFFFFF')


def sell_pct_color(pct: float) -> str:
    """Weak red (low profit) -> strong red (high profit)."""
    if pct <= 3:  return '#FFB0B0'
    if pct <= 5:  return '#E08080'
    if pct <= 7:  return '#CC4444'
    if pct <= 9:  return '#AA2222'
    return '#880000'


def gap_color(gap_pct: float) -> str:
    """Red for positive (profit), blue for negative (loss)."""
    if gap_pct > 5:    return '#CC0000'
    if gap_pct > 1:    return '#FF6666'
    if gap_pct > -1:   return '#888888'
    if gap_pct > -5:   return '#6699CC'
    return '#003399'


# ── Rounding ──────────────────────────────────────────────────────────────────
def round_half_up(x: float) -> int:
    return math.floor(x + 0.5)


# ── Price formatting ──────────────────────────────────────────────────────────
def fmt_price(price, currency: str) -> str:
    if price is None:
        return '--'
    if currency == 'KRW':
        return f"{price:,.0f}"
    return f"{price:,.2f}"


# ── Buy (rescue) calculations ─────────────────────────────────────────────────
def calc_buy_trigger(avg_cost: float, drop_pct: int) -> float:
    return avg_cost * (1.0 - drop_pct / 100.0)


def calc_buy_shares(shares: int, buy_pct: int) -> int:
    """Rescue buy quantity based on buy gear.
    4% drop -> 50%, 5% drop -> 60%, 6% drop -> 70% of current holdings."""
    if shares <= 0:
        return 0
    ratio = BUY_GEAR_INFO.get(buy_pct, {}).get('ratio', 0.5)
    return max(1, round_half_up(shares * ratio))


# ── Load (empty stock entry) calculations ─────────────────────────────────────
def calc_load_price(peak_5d: float, load_gear: str) -> float:
    return peak_5d * (1.0 - LOAD_GEARS[load_gear]['drop'])


def calc_load_shares(peak_5d: float, load_gear: str, tier: str, unit_cash: float) -> int:
    if peak_5d <= 0 or unit_cash <= 0:
        return 0
    load_price = calc_load_price(peak_5d, load_gear)
    if load_price <= 0:
        return 0
    target_cash = TIER_MULTIPLIER[tier] * unit_cash
    return max(1, round_half_up(target_cash / load_price))


# ── Sell tier calculations ────────────────────────────────────────────────────
def calc_sell_tiers(shares: int, avg_cost: float, tier_pcts: list, tier_actives: list) -> list:
    """
    tier_pcts:   [t1_pct, t2_pct, t3_pct]   (profit % above avg_cost)
    tier_actives:[t1_on,  t2_on,  t3_on ]   (bool)

    Returns list of 3 dicts:
      {'price': float, 'qty': int}  if the tier is active
      {'price': None,  'qty': None} if inactive

    Quantity split:
      1 active tier  -> 100% at that tier
      2 active tiers -> 50% at first, 100% of remainder at second
      3 active tiers -> 50% at T1, 50% of remainder at T2, 100% of remainder at T3
    """
    active_idx = [i for i, on in enumerate(tier_actives) if on]
    n = len(active_idx)

    result = [{'price': None, 'qty': None} for _ in range(3)]
    if n == 0 or avg_cost <= 0 or shares <= 0:
        return result

    def sell_price(i):
        return avg_cost * (1.0 + tier_pcts[i] / 100.0)

    if n == 1:
        i = active_idx[0]
        result[i] = {'price': sell_price(i), 'qty': shares}

    elif n == 2:
        i1, i2 = active_idx
        q1 = round_half_up(shares * 0.5)
        q2 = shares - q1
        result[i1] = {'price': sell_price(i1), 'qty': q1}
        result[i2] = {'price': sell_price(i2), 'qty': q2}

    else:  # n == 3
        i1, i2, i3 = active_idx
        q1 = round_half_up(shares * 0.5)
        q2 = round_half_up((shares - q1) * 0.5)
        q3 = shares - q1 - q2
        result[i1] = {'price': sell_price(i1), 'qty': q1}
        result[i2] = {'price': sell_price(i2), 'qty': q2}
        result[i3] = {'price': sell_price(i3), 'qty': max(0, q3)}

    return result


# ── Gap rate ──────────────────────────────────────────────────────────────────
def calc_gap_rate(current_price: float, avg_cost: float) -> float:
    if avg_cost <= 0:
        return 0.0
    return (current_price - avg_cost) / avg_cost * 100.0
