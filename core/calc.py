"""Gearbox V-Commandos — catalogue, the five-speed gearbox, and every line
the cards and the campaign bot draw.

ONE gearbox drives the card AND the autopilot (`core/vcommandos.py`). A Gear
is picked before the campaign starts and stays fixed for its whole life:

    LOAD   vantage (High5) × (1 - gear.load%)      ~1 unit of cash
    CHASE  actual avg cost × (1 - gear.chase%)     actual shares × gear.ratio
    EXIT   actual avg cost × (1 + tier%)           the WHOLE position

The exit is one clean full-position sell at ONE selected tier — no 33/33/34
split (see the manual, §4.2 / §7.2). The daily v^ grid is a separate,
selectable bot; it does not share these tables.
"""

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
    'SKHY':  'SK Hynix ADR',
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


# ── The five-speed gearbox ───────────────────────────────────────────────────
# Each gear bundles the whole campaign: how deep the LOAD hangs below the
# vantage, how deep each CHASE hangs below the running average, how much size
# a chase adds, and the three exit tiers it can be closed at.
#
#   vol_max   upper bound (inclusive) of the 5-day range that recommends this
#             gear; None = open-ended (gear 5 catches everything above).
#   max_chase reference only — how many chases 32 units would fund in the
#             normalized table (manual Part II). It is NOT a cap: the army is.
GEARS = {
    1: {'name': 'Smooth',   'load':  6, 'chase': 4, 'ratio': 1 / 2,
        'frac': '1/2', 'tiers': (1, 3, 5), 'vol_max': 15.0, 'max_chase': 8,
        'color': '#D8ECFF', 'fg': 'black'},
    2: {'name': 'Moderate', 'load':  7, 'chase': 5, 'ratio': 2 / 3,
        'frac': '2/3', 'tiers': (2, 4, 6), 'vol_max': 20.0, 'max_chase': 7,
        'color': '#9ED0FF', 'fg': 'black'},
    3: {'name': 'Balanced', 'load':  8, 'chase': 6, 'ratio': 3 / 4,
        'frac': '3/4', 'tiers': (3, 5, 7), 'vol_max': 25.0, 'max_chase': 6,
        'color': '#5FA7EF', 'fg': 'black'},
    4: {'name': 'Deep',     'load':  9, 'chase': 7, 'ratio': 4 / 5,
        'frac': '4/5', 'tiers': (4, 6, 8), 'vol_max': 30.0, 'max_chase': 6,
        'color': '#2478D4', 'fg': 'white'},
    5: {'name': 'Extreme',  'load': 10, 'chase': 8, 'ratio': 1.0,
        'frac': '1.0', 'tiers': (5, 7, 9), 'vol_max': None, 'max_chase': 5,
        'color': '#123E8A', 'fg': 'white'},
}

# 5-day range (%) cut points, upper bound INCLUSIVE. Raised from the old
# 8/12/16/20 ladder so that gear 5 — which doubles the share count on every
# chase — is reserved for genuinely violent stocks instead of ordinary ones.
VOL_THRESHOLDS = tuple(GEARS[g]['vol_max'] for g in (1, 2, 3, 4))

DEFAULT_GEAR = 3
DEFAULT_EXIT_TIER = 2            # the middle tier is the standing default
EXIT_TIERS = (1, 2, 3)

# There is NO fixed campaign capital cap. The only wall is the army: a chase
# fires while the broker's cash covers it and stops when it does not. The
# 32-unit figure in the normalized tables is a reference scale, not a limit.

# After a full EXIT the same session, one fast reload hangs at the actual
# final sell fill −3%. This is NOT the gear's load drop (manual §9.3).
RELOAD_DROP_PCT = 3


def clamp_gear(gear) -> int:
    try:
        gear = int(gear)
    except (TypeError, ValueError):
        gear = DEFAULT_GEAR
    return max(1, min(5, gear))


def clamp_tier(tier) -> int:
    try:
        tier = int(tier)
    except (TypeError, ValueError):
        tier = DEFAULT_EXIT_TIER
    return max(1, min(3, tier))


def gear_params(gear) -> dict:
    return GEARS[clamp_gear(gear)]


def load_drop(gear) -> int:
    return gear_params(gear)['load']


def chase_drop(gear) -> int:
    return gear_params(gear)['chase']


def add_ratio(gear) -> float:
    return gear_params(gear)['ratio']


def exit_pct(gear, tier) -> int:
    return gear_params(gear)['tiers'][clamp_tier(tier) - 1]


def gear_label(gear) -> str:
    return f"G{clamp_gear(gear)}" if gear else "G?"


def gear_detail(gear) -> str:
    """Compact one-line signature of a gear: load / chase / add size."""
    g = gear_params(gear)
    return f"-{g['load']}% / -{g['chase']}% ×{g['frac']}"


def gear_menu_label(gear) -> str:
    g = gear_params(gear)
    return f"{gear_label(gear)} {g['name']}  {gear_detail(gear)}"


def tier_label(gear, tier) -> str:
    return f"T{clamp_tier(tier)} +{exit_pct(gear, tier)}%"


def gear_button_color(gear) -> str:
    return gear_params(gear)['color']


def gear_button_fg(gear) -> str:
    return gear_params(gear)['fg']


# ── Legacy config migration ──────────────────────────────────────────────────
# Old CSV/config files stored a DROP PERCENT (the unified 4..8 bait, or the
# even older L1..L7 keys) instead of a gear number. The unified bait percent
# maps 1:1 onto the gearbox chase drop, so 4→G1 … 8→G5.
LEGACY_LOAD_GEARS = {'L1': 4, 'L2': 5, 'L3': 6, 'L4': 8,
                     'L5': 10, 'L6': 12, 'L7': 15}


def gear_for_chase_pct(pct) -> int:
    """Chase drop percent -> gear (4→G1 … 8→G5)."""
    try:
        pct = int(abs(float(pct)))
    except (TypeError, ValueError):
        return DEFAULT_GEAR
    return clamp_gear(pct - GEARS[1]['chase'] + 1)


def normalize_gear(value) -> int:
    """Normalize a GEAR field: a gear number, a 'G3' string, a legacy
    'L1'-'L7' key, or a percent above 5 (which can only be a legacy drop).

    4 and 5 are ambiguous — both real gears and real legacy chase percents —
    so this reads them as gears. A field that is known to hold a drop percent
    must go through gear_for_chase_pct() instead."""
    if isinstance(value, str):
        v = value.strip().upper()
        if v in LEGACY_LOAD_GEARS:
            return gear_for_chase_pct(LEGACY_LOAD_GEARS[v])
        if v.startswith('G'):
            v = v[1:]
        try:
            value = float(v)
        except ValueError:
            return DEFAULT_GEAR
    try:
        value = float(value)
    except (TypeError, ValueError):
        return DEFAULT_GEAR
    # 1..5 is already a gear; anything bigger is a legacy drop percent.
    return clamp_gear(value) if value <= 5 else gear_for_chase_pct(value)


def tier_for_exit_pct(gear, pct):
    """Which tier of this gear a stored exit percent corresponds to (None
    when it matches no tier — the caller falls back to the default)."""
    try:
        pct = int(round(float(pct)))
    except (TypeError, ValueError):
        return None
    tiers = gear_params(gear)['tiers']
    return tiers.index(pct) + 1 if pct in tiers else None


# ── Colors ───────────────────────────────────────────────────────────────────
def sell_pct_color(pct: float) -> str:
    """High-contrast exit colors from white/amber to deep red."""
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


def load_gap_color(gap_pct: float, trigger_pct: float = 6.0) -> str:
    """Color for a FLAT stock's gap = current vs the VANTAGE point (kept
    distinct from the deployed red/blue P&L colors so a watch-list of flats
    doesn't read as losses). The LOAD sits at gap = -trigger_pct: orange once
    the price is at/below it, purple above — deeper purple = farther away."""
    rem = gap_pct + abs(trigger_pct)      # distance still to fall to the LOAD
    if rem <= 0:   return '#E08000'   # orange — already at/below the LOAD
    if rem < 2:    return '#B084E0'   # light purple — close to a buy
    if rem < 4:    return '#9A5FD0'
    if rem < 6:    return '#7E3FBF'
    return '#5E2CA0'                   # deep purple — far above the LOAD


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


# ── Gear recommendation from the 5-day range ─────────────────────────────────
def calc_volatility(high_5d, low_5d):
    """5-day range as a percent of the 5-day high: 100*(high-low)/high.
    Returns None when the inputs are unusable."""
    if not high_5d or high_5d <= 0 or low_5d is None or low_5d < 0:
        return None
    return 100.0 * (high_5d - low_5d) / high_5d


def select_auto_gear(volatility) -> int:
    """5-day range percent -> recommended gear 1..5. Unknown volatility is
    treated as calm (gear 1) — the system never guesses its way into the
    share-doubling gear."""
    if volatility is None:
        return 1
    for gear in (1, 2, 3, 4):
        if volatility <= GEARS[gear]['vol_max']:
            return gear
    return 5


# ── Heavy-unit entry restriction ─────────────────────────────────────────────
# A stock whose ONE share already eats a big bite of a unit cannot be loaded
# on a shallow gear: with 1-2 shares per unit the chase ratio rounds badly and
# the campaign has no resolution. Its ENTRY gear is floored by share size.
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
    """Recommended ENTRY gear for a flat stock:
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


# ── LOAD (flat stock enters the campaign) ────────────────────────────────────
# The LOAD hangs off the VANTAGE POINT: High5 (the highest completed-session
# high of the previous five trading days), or — after a same-day full EXIT —
# the actual final sell fill (the fast reload, a flat -3%).
def calc_load_price(vantage: float, gear) -> float:
    return vantage * (1.0 - load_drop(gear) / 100.0)


def calc_reload_price(sell_fill: float) -> float:
    return sell_fill * (1.0 - RELOAD_DROP_PCT / 100.0)


def calc_load_shares(vantage: float, gear, unit_cash: float) -> int:
    """Shares at the LOAD trigger: one full unit of cash, minimum 1 share."""
    if vantage <= 0 or unit_cash <= 0:
        return 0
    price = calc_load_price(vantage, gear)
    if price <= 0:
        return 0
    return max(1, round_half_up(unit_cash / price))


# ── CHASE (reinforce a deployed campaign) ────────────────────────────────────
def calc_chase_price(avg_cost: float, gear) -> float:
    return avg_cost * (1.0 - chase_drop(gear) / 100.0)


def calc_chase_shares(shares: int, gear) -> int:
    if shares <= 0:
        return 0
    return max(1, round_half_up(shares * add_ratio(gear)))


def calc_chase_cascade(shares: int, avg_cost: float, gear,
                       levels: int = 3) -> list:
    """The next `levels` chase lines at one fixed gear.

    Each level fires at gear.chase% below the RUNNING average; its shares are
    folded into that average before the next level is computed, so line N
    already assumes lines 1..N-1 filled. Returns `levels` dicts
    {'price', 'qty'}; entries are {None, None} when inputs are unusable."""
    result = []
    cur_shares, cur_avg = shares, avg_cost
    for _ in range(levels):
        if cur_shares <= 0 or cur_avg <= 0:
            result.append({'price': None, 'qty': None})
            continue
        price = calc_chase_price(cur_avg, gear)
        qty = calc_chase_shares(cur_shares, gear)
        result.append({'price': price, 'qty': qty})
        new_shares = cur_shares + qty
        cur_avg = (cur_avg * cur_shares + price * qty) / new_shares
        cur_shares = new_shares
    return result


def calc_load_ladder(vantage: float, gear, unit_cash: float,
                     chases: int = 2) -> tuple:
    """The projected ladder for a FLAT stock: the LOAD (one unit) followed by
    `chases` projected chases cascading off it, exactly as the campaign would
    run once the load fills.

    Returns (ladder, load_price, load_shares); ladder has 1 + chases entries,
    index 0 being the LOAD."""
    load_price = calc_load_price(vantage, gear) if vantage and vantage > 0 else 0.0
    load_shares = calc_load_shares(vantage, gear, unit_cash)
    if load_price <= 0 or load_shares <= 0:
        return ([{'price': None, 'qty': None} for _ in range(1 + chases)],
                0.0, 0)
    ladder = [{'price': load_price, 'qty': load_shares}]
    ladder += calc_chase_cascade(load_shares, load_price, gear, levels=chases)
    return ladder, load_price, load_shares


# ── EXIT (one clean full-position sell) ──────────────────────────────────────
def calc_exit_price(avg_cost: float, gear, tier) -> float:
    return avg_cost * (1.0 + exit_pct(gear, tier) / 100.0)


def calc_exit(shares: int, avg_cost: float, gear, tier) -> dict:
    """The campaign's single EXIT: the WHOLE position at the selected tier."""
    if shares <= 0 or avg_cost <= 0:
        return {'price': None, 'qty': None, 'pct': exit_pct(gear, tier)}
    return {'price': calc_exit_price(avg_cost, gear, tier),
            'qty': shares, 'pct': exit_pct(gear, tier)}


def calc_exit_lines(shares: int, avg_cost: float, gear) -> list:
    """All three tiers of this gear (for display). Each carries the whole
    position — only the SELECTED one is armed."""
    return [calc_exit(shares, avg_cost, gear, t) for t in EXIT_TIERS]


# ── Gap rate ─────────────────────────────────────────────────────────────────
def calc_gap_rate(current_price: float, avg_cost: float) -> float:
    if avg_cost <= 0:
        return 0.0
    return (current_price - avg_cost) / avg_cost * 100.0
