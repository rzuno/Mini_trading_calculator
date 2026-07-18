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
    'ORCL':  'Oracle',
}

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

# ── Unified drop percent (ONE pct drives both the load and the chase) ───────
# G1 -4% … G5 -8%. The same bait system runs the empty entry (from the
# vantage point) and the deployed rescue (from the avg cost).
LOAD_PCT_MIN = 4
LOAD_PCT_MAX = 8

# Legacy gear keys (L1-L7) -> old drop percents, for reading old CSV/config
# files; anything outside 4..8 is clamped into the unified range.
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
            return clamp_load_pct(LEGACY_LOAD_GEARS[v])
        try:
            value = float(v)
        except ValueError:
            return 5
    try:
        return clamp_load_pct(value)
    except (ValueError, TypeError):
        return 5

# ── Buy size per drop percent (G1..G5: shallower -> deeper baits) ───────────
BUY_GEAR_PCTS = [4, 5, 6, 7, 8]

# Rescue buy size as a fraction of current shares. Deeper bait buys more;
# -8% buys the WHOLE position again (묻고 더블로 가 — share-count doubling).
BUY_GEAR_INFO = {
    4: {'label': '4% drop (1/2)',    'frac': '1/2', 'ratio': 1 / 2, 'color': '#B0C4DE'},
    5: {'label': '5% drop (2/3)',    'frac': '2/3', 'ratio': 2 / 3, 'color': '#88AAC8'},
    6: {'label': '6% drop (3/4)',    'frac': '3/4', 'ratio': 3 / 4, 'color': '#6690B2'},
    7: {'label': '7% drop (4/5)',    'frac': '4/5', 'ratio': 4 / 5, 'color': '#5583A7'},
    8: {'label': '8% drop (double)', 'frac': '더블', 'ratio': 1.0,   'color': '#44769C'},
}
BUY_GEAR_LABELS = [BUY_GEAR_INFO[p]['label'] for p in BUY_GEAR_PCTS]
BUY_LABEL_TO_PCT = {v['label']: k for k, v in BUY_GEAR_INFO.items()}

GEAR_BUTTON_COLORS = {
    1: '#D8ECFF',
    2: '#9ED0FF',
    3: '#5FA7EF',
    4: '#2478D4',
    5: '#123E8A',
}
GEAR_BUTTON_FG = {
    1: 'black',
    2: 'black',
    3: 'black',
    4: 'white',
    5: 'white',
}


def buy_pct_color(pct: int) -> str:
    return BUY_GEAR_INFO.get(pct, {}).get('color', '#FFFFFF')


def gear_button_color(gear) -> str:
    return GEAR_BUTTON_COLORS.get(clamp_gear(gear), '#FFFFFF')


def gear_button_fg(gear) -> str:
    return GEAR_BUTTON_FG.get(clamp_gear(gear), 'black')


def clamp_gear(gear) -> int:
    try:
        gear = int(gear)
    except (TypeError, ValueError):
        gear = 3
    return max(1, min(5, gear))


def gear_for_pct(pct) -> int:
    """Drop percent (load or chase — they are the same now) -> gear 1..5."""
    try:
        pct = int(abs(pct))
    except (TypeError, ValueError):
        return 3
    return clamp_gear(pct - AUTO_GEARS[1]['pct'] + 1)


def gear_for_sell_pct(pct):
    try:
        pct = int(pct)
    except (TypeError, ValueError):
        return None
    for gear, params in AUTO_GEARS.items():
        if params['tiers'][1] == pct:
            return gear
    return None


def gear_label(gear) -> str:
    return f"G{gear}" if gear else "G?"


def sell_pct_color(pct: float) -> str:
    """High-contrast sell colors from white/amber to deep red."""
    if pct <= 1:  return '#FFFFFF'
    if pct <= 2:  return '#FFF2CC'
    if pct <= 3:  return '#FFD9A8'
    if pct <= 4:  return '#FFB199'
    if pct <= 5:  return '#FF7F7F'
    if pct <= 7:  return '#D93636'
    return '#880000'


def gap_color(gap_pct: float) -> str:
    """Red for positive (profit), blue for negative (loss)."""
    if gap_pct > 5:    return '#CC0000'
    if gap_pct > 1:    return '#FF6666'
    if gap_pct > -1:   return '#888888'
    if gap_pct > -5:   return '#6699CC'
    return '#003399'


def load_gap_color(gap_pct: float, trigger_pct: float = 4.0) -> str:
    """Color for an EMPTY stock's gap = current vs the VANTAGE point (kept
    distinct from the deployed red/blue P&L colors so a watch-list of empties
    doesn't read as losses). The bait sits at gap = -trigger_pct: orange once
    the price is at/below the bait, purple above it — deeper purple = farther
    from the bait (more day left before the battle)."""
    rem = gap_pct + abs(trigger_pct)      # distance still to fall to the bait
    if rem <= 0:   return '#E08000'   # orange — already at/below the bait
    if rem < 2:    return '#B084E0'   # light purple — close to a buy
    if rem < 4:    return '#9A5FD0'
    if rem < 6:    return '#7E3FBF'
    return '#5E2CA0'                   # deep purple — far above the bait


def fx_dev_color(pct: float) -> str:
    """Color for the FX deviation from the 3-month average. Positive (FX above
    average) trends red and negative trends blue, deepening with magnitude over
    the typical +/-3% range."""
    if pct >= 3:    return '#CC0000'   # strong red
    if pct >= 2:    return '#E03030'
    if pct >= 1:    return '#FF6666'   # light red
    if pct > 0:     return '#FF9999'   # faint red
    if pct == 0:    return '#888888'
    if pct > -1:    return '#99BBE0'   # faint blue
    if pct > -2:    return '#6699CC'   # light blue
    if pct > -3:    return '#3366CC'
    return '#003399'                    # strong blue


# ── Auto gear (5-day-volatility-driven gear selection) ───────────────────────
# One gear bundles the unified drop % (load AND chase) and the three sell-tier
# percentages. The bait ladder sizes come from BUY_GEAR_INFO (…, -8% = double).
AUTO_GEARS = {
    1: {'pct': 4, 'tiers': (1, 3, 5)},
    2: {'pct': 5, 'tiers': (2, 4, 6)},
    3: {'pct': 6, 'tiers': (3, 5, 7)},
    4: {'pct': 7, 'tiers': (4, 6, 8)},
    5: {'pct': 8, 'tiers': (5, 7, 9)},
}

# The same-day re-bait after a full exit is ALWAYS gear 1 (exit fill −4%),
# whatever the volatility gear says (exception rule 1).
RE_BAIT_GEAR = 1
RE_BAIT_PCT = AUTO_GEARS[RE_BAIT_GEAR]['pct']


def gear_detail(gear) -> str:
    gear = clamp_gear(gear)
    pct = AUTO_GEARS[gear]['pct']
    frac = BUY_GEAR_INFO[pct]['frac']
    return f"(-{pct}%, x{frac})"


def gear_menu_label(gear, deployed: bool = True) -> str:
    return f"{gear_label(clamp_gear(gear))} {gear_detail(gear)}"

# 5-day volatility (%) cut points (upper bound INCLUSIVE):
# G1: V <= 8, G2: 8 < V <= 12, G3: 12 < V <= 16,
# G4: 16 < V <= 20, G5: V > 20.
VOL_THRESHOLDS = (8.0, 12.0, 16.0, 20.0)


def calc_volatility(high_5d, low_5d):
    """5-day volatility as a percent of the 5-day high:
    100 * (high - low) / high. Returns None when inputs are unusable."""
    if not high_5d or high_5d <= 0 or low_5d is None or low_5d < 0:
        return None
    return 100.0 * (high_5d - low_5d) / high_5d


def select_auto_gear(volatility) -> int:
    """Map a 5-day volatility percent to gear 1..5. Falls back to gear 1
    when volatility is unknown."""
    if volatility is None:
        return 1
    for idx, threshold in enumerate(VOL_THRESHOLDS, start=1):
        if volatility <= threshold:
            return idx
    return 5


# ── Heavy-unit entry restriction (exception rule 2) ──────────────────────────
# A stock whose ONE share already eats a big bite of a unit must not enter on
# a shallow bait: its auto ENTRY gear is floored by share size. Once loaded it
# follows the normal volatility gear like everyone else.
WEIGHT_GEAR_THRESHOLDS = (1.2, 1.6, 2.0, 2.5)


def weight_min_gear(share_price, unit_cash) -> int:
    """Minimum entry gear from single-share chunkiness (share/unit ratio):
    <=1.2u → G1 ok, >1.2 → G2+, >1.6 → G3+, >2.0 → G4+, >2.5 → G5 only."""
    if not share_price or not unit_cash or unit_cash <= 0:
        return 1
    r = share_price / unit_cash
    gear = 1
    for th in WEIGHT_GEAR_THRESHOLDS:
        if r > th:
            gear += 1
    return gear


def effective_entry_gear(volatility, share_price=None, unit_cash=None) -> int:
    """Auto ENTRY gear for an empty stock:
    max(volatility gear, weight-based minimum gear)."""
    return max(select_auto_gear(volatility),
               weight_min_gear(share_price, unit_cash))


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


# ── KR order tick grid ───────────────────────────────────────────────────────
def kr_tick_size(price: float) -> int:
    """KRX/NXT price tick by band; a KR limit price must be a multiple of this."""
    if price < 2000:     return 1
    if price < 5000:     return 5
    if price < 20000:    return 10
    if price < 50000:    return 50
    if price < 200000:   return 100
    if price < 500000:   return 500
    return 1000


def round_kr_tick(price: float) -> int:
    t = kr_tick_size(price)
    return int(round(price / t) * t)


def trim_buy_price(ticker: str, price: float) -> float:
    """Trim a BUY line DOWN to a clean orderable number: KR floored to its tick
    (never bids above the strategy line), US floored to the cent."""
    if ticker.endswith('.KS'):
        t = kr_tick_size(price)
        return int(math.floor(price / t) * t)
    return math.floor(price * 100) / 100.0


def trim_sell_price(ticker: str, price: float) -> float:
    """Trim a SELL line UP to a clean orderable number: KR ceiled to its tick
    (never asks below the strategy line), US ceiled to the cent."""
    if ticker.endswith('.KS'):
        t = kr_tick_size(price)
        return int(math.ceil(price / t) * t)
    return math.ceil(price * 100) / 100.0


def fmt_order_price(ticker: str, price: float) -> str:
    """Format a limit price for the order API: KR snapped to its tick grid (int),
    US to 2 decimals (>= $1) or 4 decimals (< $1)."""
    if ticker.endswith('.KS'):
        return str(round_kr_tick(price))
    return f"{price:.2f}" if price >= 1 else f"{price:.4f}"


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
# The load hangs off the VANTAGE POINT: the previous session's close, or —
# after a same-day full exit — the actual sell fill price (re-bait).
def calc_load_price(vantage: float, pct: int) -> float:
    return vantage * (1.0 - pct / 100.0)


def calc_load_shares(vantage: float, pct: int, unit_cash: float) -> int:
    """Shares to buy at the load trigger. Every stock loads a full unit of cash
    (KR and US alike); when one share already costs more than a unit, the
    minimum of 1 share applies."""
    if vantage <= 0 or unit_cash <= 0:
        return 0
    load_price = calc_load_price(vantage, pct)
    if load_price <= 0:
        return 0
    return max(1, round_half_up(unit_cash / load_price))


def calc_load_ladder(vantage: float, pct: int,
                     unit_cash: float, rescues: int = 2) -> tuple:
    """Projected buy ladder for an EMPTY stock, treating the LOAD as the first
    (initial) buy of one unit and cascading `rescues` rescue triggers from it
    at the SAME pct (load = buy now), exactly as a deployed stock would once
    it owns the load shares.

    Returns (ladder, load_price, load_shares) where ladder is a list of
    {'price', 'qty'} of length 1 + rescues: index 0 is the LOAD, the rest are
    the projected rescues. All-None entries when inputs are unusable."""
    load_price  = calc_load_price(vantage, pct) if vantage and vantage > 0 else 0.0
    load_shares = calc_load_shares(vantage, pct, unit_cash)
    if load_price <= 0 or load_shares <= 0:
        return ([{'price': None, 'qty': None} for _ in range(1 + rescues)],
                0.0, 0)
    ladder = [{'price': load_price, 'qty': load_shares}]
    ladder += calc_buy_cascade(load_shares, load_price, pct, levels=rescues)
    return ladder, load_price, load_shares


# ── Sell tier calculations ───────────────────────────────────────────────────
def calc_sell_tiers(shares: int, avg_cost: float, tier_pcts: list, tier_actives: list) -> list:
    """Split the held shares across the active sell tiers as evenly as possible,
    giving any remainder to the MID tier first, then LOW, then HIGH. This keeps
    the center tier larger than or equal to the outer tiers when all are active.

    Examples (all three active): 5 -> T1=2,T2=2,T3=1 ; 4 -> T1=1,T2=2,T3=1 ;
    1 -> T1=0,T2=1,T3=0."""
    active_idx = [i for i, on in enumerate(tier_actives) if on]
    n = len(active_idx)

    result = [{'price': None, 'qty': None} for _ in range(3)]
    if n == 0 or avg_cost <= 0 or shares <= 0:
        return result

    base, rem = divmod(shares, n)
    qty_by_tier = {i: base for i in active_idx}
    for i in [1, 0, 2]:
        if rem <= 0:
            break
        if i in qty_by_tier:
            qty_by_tier[i] += 1
            rem -= 1

    for i in active_idx:
        q = qty_by_tier[i]
        result[i] = ({'price': avg_cost * (1.0 + tier_pcts[i] / 100.0), 'qty': q}
                     if q > 0 else {'price': None, 'qty': None})
    return result


# ── Gap rate ─────────────────────────────────────────────────────────────────
def calc_gap_rate(current_price: float, avg_cost: float) -> float:
    if avg_cost <= 0:
        return 0.0
    return (current_price - avg_cost) / avg_cost * 100.0
