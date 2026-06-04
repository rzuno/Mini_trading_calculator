import math

# ── Stock catalogue ──────────────────────────────────────────────────────────
STOCK_NAMES = {
    '005930.KS': 'Samsung Electronics',
    '000660.KS': 'SK Hynix',
    'NVDA':  'NVIDIA',
    'GOOGL': 'Alphabet',
    'MU':    'Micron',
    'MSFT':  'Microsoft',
    'SNDK':  'SanDisk',
    'AMD':   'AMD',
    'TSM':   'TSMC',
    'AVGO':  'Broadcom',
    'PLTR':  'Palantir',
    'AAPL':  'Apple',
    'AMZN':  'Amazon',
    'STX':   'Seagate',
    'INTC':  'Intel',
}

TIER_MULTIPLIER = {'Major': 1.0, 'Minor': 0.5}

# ── Fixed stock display order ────────────────────────────────────────────────
MAJOR_ORDER = ['005930.KS', '000660.KS', 'GOOGL', 'NVDA']

def stock_sort_key(ticker):
    """Majors first in MAJOR_ORDER, then minors alphabetically by name."""
    if ticker in MAJOR_ORDER:
        return (0, MAJOR_ORDER.index(ticker))
    name = STOCK_NAMES.get(ticker, ticker)
    return (1, name.lower())


def display_name(ticker: str) -> str:
    """Short display name for the compact cards: collapse a long multi-word
    name to its first word (e.g. 'Samsung Electronics' -> 'Samsung'); short
    names pass through unchanged."""
    name = STOCK_NAMES.get(ticker, ticker)
    if len(name) > 12 and ' ' in name:
        name = name.split(' ', 1)[0]
    return name

# ── Load gear (continuous -4% ... -15% drop, 1% steps) ──────────────────────
LOAD_PCT_MIN = 4
LOAD_PCT_MAX = 15

# Blue gradient: light (-4%) -> dark (-15%). The -4/-5/-6% entries match the
# buy-gear colors for cross-tool consistency; the rest fill the 1% gaps.
LOAD_PCT_COLORS = {
    4:  '#B0C4DE',
    5:  '#88AAC8',
    6:  '#6690B2',
    7:  '#5583A7',
    8:  '#44769C',
    9:  '#396991',
    10: '#2E5C86',
    11: '#28517C',
    12: '#224773',
    13: '#1F406B',
    14: '#1C3A6A',
    15: '#1A3366',
}

# Legacy gear keys (L1-L7) -> drop percent, for reading old CSV/config files.
LEGACY_LOAD_GEARS = {'L1': 4, 'L2': 5, 'L3': 6, 'L4': 8,
                     'L5': 10, 'L6': 12, 'L7': 15}


def clamp_load_pct(pct) -> int:
    return max(LOAD_PCT_MIN, min(LOAD_PCT_MAX, int(pct)))


def normalize_load_pct(value) -> int:
    """Accept a legacy gear key ('L1'-'L7'), an int, or a numeric string and
    return a drop percent clamped to LOAD_PCT_MIN..LOAD_PCT_MAX."""
    if isinstance(value, str):
        v = value.strip()
        if v in LEGACY_LOAD_GEARS:
            return LEGACY_LOAD_GEARS[v]
        try:
            value = float(v)
        except ValueError:
            return 5
    try:
        return clamp_load_pct(value)
    except (ValueError, TypeError):
        return 5

# ── Buy gear (matching load gear blue tones for −4/−5/−6%) ──────────────────
BUY_GEAR_PCTS = [4, 5, 6]

BUY_GEAR_INFO = {
    4: {'label': '4% drop (\u00d70.5)', 'ratio': 0.5, 'color': '#B0C4DE'},
    5: {'label': '5% drop (\u00d70.6)', 'ratio': 0.6, 'color': '#88AAC8'},
    6: {'label': '6% drop (\u00d70.7)', 'ratio': 0.7, 'color': '#6690B2'},
}
BUY_GEAR_LABELS = [BUY_GEAR_INFO[p]['label'] for p in BUY_GEAR_PCTS]
BUY_LABEL_TO_PCT = {v['label']: k for k, v in BUY_GEAR_INFO.items()}


def buy_pct_color(pct: int) -> str:
    return BUY_GEAR_INFO.get(pct, {}).get('color', '#FFFFFF')


def load_pct_color(pct) -> str:
    return LOAD_PCT_COLORS.get(clamp_load_pct(pct), '#FFFFFF')


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


# ── Auto gear (5-day-volatility-driven gear selection) ───────────────────────
# Each gear bundles a load drop %, a buy/reload gear %, and the three sell-tier
# percentages. In auto mode the whole bundle is chosen from the 5-day
# volatility; the buy_pct keys line up with BUY_GEAR_INFO (4->×0.5, 5->×0.6,
# 6->×0.7), so the reload ratio follows automatically.
AUTO_GEARS = {
    1: {'load_pct': 6, 'buy_pct': 4, 'tiers': (2, 4, 6)},
    2: {'load_pct': 7, 'buy_pct': 5, 'tiers': (3, 5, 7)},
    3: {'load_pct': 8, 'buy_pct': 6, 'tiers': (4, 6, 8)},
}

# 5-day volatility (%) cut points: V < LO -> gear 1, LO <= V < HI -> gear 2,
# V >= HI -> gear 3.
VOL_LO = 9.0
VOL_HI = 14.0


def calc_volatility(high_5d, low_5d):
    """5-day volatility as a percent of the 5-day high:
    100 * (high - low) / high. Returns None when inputs are unusable."""
    if not high_5d or high_5d <= 0 or low_5d is None or low_5d < 0:
        return None
    return 100.0 * (high_5d - low_5d) / high_5d


def select_auto_gear(volatility) -> int:
    """Map a 5-day volatility percent to gear 1, 2, or 3. Falls back to gear 1
    when volatility is unknown."""
    if volatility is None or volatility < VOL_LO:
        return 1
    if volatility < VOL_HI:
        return 2
    return 3


def auto_gear_params(volatility) -> dict:
    """Gear parameter bundle (load_pct, buy_pct, tiers) for the given
    volatility."""
    return AUTO_GEARS[select_auto_gear(volatility)]


# ── Rounding ─────────────────────────────────────────────────────────────────
def round_half_up(x: float) -> int:
    return math.floor(x + 0.5)


# ── Price formatting ─────────────────────────────────────────────────────────
def fmt_price(price, currency: str) -> str:
    if price is None:
        return '--'
    if currency == 'KRW':
        return f"{price:,.0f}"
    return f"{price:,.2f}"


# ── Buy (rescue) calculations ────────────────────────────────────────────────
def calc_buy_trigger(avg_cost: float, drop_pct: int) -> float:
    return avg_cost * (1.0 - drop_pct / 100.0)


def calc_buy_shares(shares: int, buy_pct: int) -> int:
    if shares <= 0:
        return 0
    ratio = BUY_GEAR_INFO.get(buy_pct, {}).get('ratio', 0.5)
    return max(1, round_half_up(shares * ratio))


def calc_buy_cascade(shares: int, avg_cost: float, buy_pct: int,
                     levels: int = 3) -> list:
    """Cascade of buy (rescue) triggers at a single fixed gear.

    Each level fires at `buy_pct` below the running average; the bought shares
    (rounded as in calc_buy_shares) are folded into the running average before
    the next level is computed, so trigger N reflects already having caught
    triggers 1..N-1. Returns `levels` dicts {'price', 'qty'}; entries are
    {None, None} when inputs are invalid."""
    result = []
    cur_shares, cur_avg = shares, avg_cost
    for _ in range(levels):
        if cur_shares <= 0 or cur_avg <= 0:
            result.append({'price': None, 'qty': None})
            continue
        price = calc_buy_trigger(cur_avg, buy_pct)
        qty   = calc_buy_shares(cur_shares, buy_pct)
        result.append({'price': price, 'qty': qty})
        new_shares = cur_shares + qty
        cur_avg    = (cur_avg * cur_shares + price * qty) / new_shares
        cur_shares = new_shares
    return result


# ── Load (empty stock entry) calculations ────────────────────────────────────
def calc_load_price(peak_5d: float, load_pct: int) -> float:
    return peak_5d * (1.0 - load_pct / 100.0)


def calc_load_shares(peak_5d: float, load_pct: int, tier: str, unit_cash: float) -> int:
    if peak_5d <= 0 or unit_cash <= 0:
        return 0
    load_price = calc_load_price(peak_5d, load_pct)
    if load_price <= 0:
        return 0
    target_cash = TIER_MULTIPLIER[tier] * unit_cash
    return max(1, round_half_up(target_cash / load_price))


# ── Sell tier calculations ───────────────────────────────────────────────────
def calc_sell_tiers(shares: int, avg_cost: float, tier_pcts: list, tier_actives: list) -> list:
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
        q1 = math.floor(shares * 0.5)
        q2 = shares - q1
        result[i1] = {'price': sell_price(i1), 'qty': q1}
        result[i2] = {'price': sell_price(i2), 'qty': q2}

    else:  # n == 3
        i1, i2, i3 = active_idx
        q1 = math.floor(shares * 0.5)
        q2 = math.floor((shares - q1) * 0.5)
        q3 = shares - q1 - q2
        result[i1] = {'price': sell_price(i1), 'qty': q1}
        result[i2] = {'price': sell_price(i2), 'qty': q2}
        result[i3] = {'price': sell_price(i3), 'qty': max(0, q3)}

    return result


# ── Gap rate ─────────────────────────────────────────────────────────────────
def calc_gap_rate(current_price: float, avg_cost: float) -> float:
    if avg_cost <= 0:
        return 0.0
    return (current_price - avg_cost) / avg_cost * 100.0
