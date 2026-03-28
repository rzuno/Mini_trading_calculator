# AI Seesaw Mini-Calculator
## Project Manual v0.2

**Sister project of:** AI Seesaw Trading (main program v1.4.2)
**Goal:** Single-window, fully manual, stateless calculator and bookkeeper for a 14-stock AI-sector portfolio.
**Automation level:** Zero. All decisions are made by the user. The program only computes numbers.
**Interface:** Single tkinter window. No pop-ups. No tabs. All information visible at once.

---

## 0. Philosophy and Scope

The Mini-Calculator is a **field tool**, not a command center. It strips the full strategy machine down to the three questions answered each session:

1. Where is my next buy trigger?
2. How many shares do I buy there?
3. Where do I set my sell orders, and in what quantities?

Everything else — perk scoring, idle flags, regime detection, anchor management — belongs in the main program only.

---

## 1. Portfolio Definition

### 1.1 Stock List

| # | Ticker | Name | Exchange | Currency | Tier |
|---|--------|------|----------|----------|------|
| 1 | 005930.KS | Samsung Electronics | KRX | KRW | Major |
| 2 | 000660.KS | SK Hynix | KRX | KRW | Major |
| 3 | NVDA | NVIDIA | NASDAQ | USD | Major |
| 4 | GOOGL | Alphabet | NASDAQ | USD | Major |
| 5 | MU | Micron | NASDAQ | USD | Minor |
| 6 | MSFT | Microsoft | NASDAQ | USD | Minor |
| 7 | WDC | Western Digital | NASDAQ | USD | Minor |
| 8 | AMD | AMD | NASDAQ | USD | Minor |
| 9 | TSM | TSMC | NYSE | USD | Minor |
| 10 | AVGO | Broadcom | NASDAQ | USD | Minor |
| 11 | PLTR | Palantir | NYSE | USD | Minor |
| 12 | AAPL | Apple | NASDAQ | USD | Minor |
| 13 | AMZN | Amazon | NASDAQ | USD | Minor |
| 14 | STX | Seagate | NASDAQ | USD | Minor |

### 1.2 Tier Rules (LOAD only)

| Tier | Load Multiplier |
|------|----------------|
| Major | 1.0× |
| Minor | 0.5× |

Tier multiplier applies to LOAD only. All rescue and sell calculations are tier-independent.

---

## 2. Core Definitions

### 2.1 Capital Units

```
N          : total army units (user-editable, default 20)
unit_cash  : total_capital / N
             KRW for Korean stocks, USD for US stocks — selected automatically
```

### 2.2 Per-Stock State (Stored in CSV)

```
ticker         : Yahoo Finance symbol
is_deployed    : bool — whether the stock appears in the Deployed section
shares         : int  — shares currently held (0 = empty)
avg_cost       : float — average cost per share
cost_basis     : float — shares × avg_cost
load_gear      : str  — L1..L5 (user selects, persists per stock)
buy_pct        : int  — drop % for rescue trigger: 4, 5, or 6
t1_pct         : float — sell tier 1 profit % (default 4)
t2_pct         : float — sell tier 2 profit % (default 6)
t3_pct         : float — sell tier 3 profit % (default 8)
t1_active      : bool — tier 1 enabled (default true)
t2_active      : bool — tier 2 enabled (default true)
t3_active      : bool — tier 3 enabled (default true)
last_updated   : date
```

### 2.3 Stock States

| State | Condition | Section |
|-------|-----------|---------|
| **DEPLOYED** | `is_deployed = True` | Upper section |
| **EMPTY** | `is_deployed = False` | Lower section |

The user controls state via **[Deploy]** and **[Reset]** buttons.

---

## 3. Buy Logic

### 3.1 Buy Gear (Single Threshold)

The buy gear selects a drop percentage and a buy proportion.

| Gear | Drop | Buy Ratio | Color |
|------|------|-----------|-------|
| 4% drop (×0.5) | −4% from avg_cost | 50% of shares | Blue |
| 5% drop (×0.6) | −5% from avg_cost | 60% of shares | Purple |
| 6% drop (×0.7) | −6% from avg_cost | 70% of shares | Red |

**Formula:**
```
trigger_price = avg_cost × (1 − buy_pct / 100)
buy_ratio     = {4%: 0.5, 5%: 0.6, 6%: 0.7}
buy_shares    = max(1, round_half_up(current_shares × buy_ratio))
```

**Example:** 12 shares, 5% gear → trigger at avg×0.95, buy 7 shares (12×0.6=7.2→7).

Only one trigger price and one buy-share count is shown per deployed stock.

### 3.2 LOAD Logic (EMPTY stocks only)

Entry point computed from the 5-day high.

| Gear | Drop | Color |
|------|------|-------|
| L1 (−4%) | −4% from 5D high | Light blue (cautious) |
| L2 (−5%) | −5% from 5D high | Blue (default) |
| L3 (−6%) | −6% from 5D high | Purple (transition) |
| L4 (−10%) | −10% from 5D high | Light red (aggressive) |
| L5 (−15%) | −15% from 5D high | Dark red (very aggressive) |

```
load_price  = peak_5d × (1 − load_drop / 100)
load_units  = 1.0 × tier_multiplier   (Major: 1.0u / Minor: 0.5u)
target_cash = load_units × unit_cash
buy_shares  = max(1, round_half_up(target_cash / load_price))
```

### 3.3 Share Rounding

```python
def round_half_up(x):
    return math.floor(x + 0.5)   # 0.5 always rounds up
```

---

## 4. Sell Logic

### 4.1 Sell Tiers — Flexible Three-Tier Ladder

Each deployed stock has **three independently configurable sell tiers**. The user sets:
- A profit % for each tier (any integer 1–20)
- Whether each tier is active (checkbox)

Percentages must satisfy: **T1% < T2% < T3%** (enforced automatically).

### 4.2 Quantity Split by Active Tiers

The split depends entirely on how many tiers are activated:

| Active tiers | T1 qty | T2 qty | T3 qty |
|-------------|--------|--------|--------|
| T1, T2, T3 | 50% | 50% of remainder | 100% of remainder |
| T1, T2 only | 50% | 100% of remainder | — |
| T1, T3 only | 50% | — | 100% of remainder |
| T2, T3 only | — | 50% | 100% of remainder |
| T1 only | 100% | — | — |
| T2 only | — | 100% | — |
| T3 only | — | — | 100% |

**Example — 12 shares, tiers at 4% / 6% / 8%:**
```
T1: 6 shares at avg_cost × 1.04
T2: 3 shares at avg_cost × 1.06
T3: 3 shares at avg_cost × 1.08
```

### 4.3 Tactical Deactivation — The Army Maneuver

The tier activation system enables real-world multi-step selling:

**Scenario: Sold T1 (6 shares). Remaining: 6 shares.**
1. Uncheck T1 (deactivate). Edit Shares field to 6.
2. Now T2 is "first active" → shows 50% (3 shares) at T2 price.
3. T3 shows remaining 100% (3 shares) at T3 price.

**Scenario: Market won't reach T2. Lower the bar.**
- Option A: Lower T2% from 6% to 4% directly in the spinbox.
- Option B: Reactivate T1, set T1 at 2%, giving 50% exit at 2% before T2.

**Scenario: Emergency full exit at one price.**
- Deactivate T2 and T3. Activate T1 only.
- T1 now shows 100% of shares at T1 price.

This system makes the calculator a live companion during volatile sessions.

---

## 5. Display & Sorting

### 5.1 Deployed Section — Card Layout

Each deployed stock is a **card (bordered frame)** with two rows:

**Row 1 — Inputs:**
`#. Name (KR)` · Avg Cost (entry) · Shares (entry) · Buy Gear (dropdown) · T1/T2/T3 (checkbox + % spinbox) · Army % · ▲▼ arrows · Graph button

**Row 2 — Computed Outputs (only update on Save & Refresh):**
Total Cost · Current · Gap % (red=profit, blue=loss) · Buy Trigger: price × qty · Sell Tier 1/2/3: price × qty

- No ticker displayed — stock name only, with "(KR)" suffix for Korean stocks
- No currency column — KRW implied by (KR), USD for all others
- KRW values formatted without decimals: `1,000` not `1000.0`
- Output values use larger bold fonts for readability
- **No reactive computation** — outputs only update when user clicks Save & Refresh
- Setting Shares to 0 and clicking Save & Refresh auto-resets stock to empty section

### 5.2 Empty Section — Compact Two Lines

**Line 1:** `#. Name (KR)` · Tier · 5D High · Current · Load Gear (dropdown) · Load Target: price × qty · Deploy/▲▼/Graph buttons
**Line 2:** 5D Close values (e.g., `156.50  145.20  154.30  157.80  161.00`)

### 5.3 Stock Ordering

Stocks are ordered **manually** using ▲▼ arrows on each row. No automatic sorting.
Order is persisted in CSV (row order = display order).

### 5.4 5-Day Candle Chart

The **Graph** button (on both deployed and empty rows) opens a popup window showing:
- 5-day candlestick chart (red=up day, blue=down day)
- Per-day OHLC values and intra-day range %
- 5-day high, low, and total volatility %

### 5.5 Color Scheme

| Element | Low/Mild | High/Aggressive |
|---------|----------|-----------------|
| Load Gear | Light blue (L1) | Dark red (L5) |
| Buy Gear | Blue (4%) | Red (6%) |
| Sell Tier % | Light red (low %) | Dark red (high %) |
| Gap % | Blue (loss) | Red (profit) |
| Candle chart | Blue (down day) | Red (up day) |

### 5.4 % Army Calculation

```
total_krw   = Σ (KRW positions' cost_basis)
            + Σ (USD positions' cost_basis × USD/KRW rate)
army_pct(i) = cost_basis_krw(i) / total_krw × 100
```

---

## 6. Real-Time Data

| Data | Source | Method |
|------|--------|--------|
| Current price | Yahoo Finance | `yfinance` fast_info |
| 5-day High | Yahoo Finance | `yfinance` 5-day OHLCV |
| 5-day Closes | Yahoo Finance | `yfinance` 5-day Close series |
| 5-day OHLC | Yahoo Finance | `yfinance` 5-day candle data |
| USD/KRW FX | Yahoo Finance | ticker `USDKRW=X` |

- On launch: auto-fetch all prices, FX, and 5-day data.
- **[Save & Refresh]** button: saves all inputs, fetches fresh prices, recomputes outputs.
- On fetch failure: last cached value shown; status bar shows error.
- Refresh timestamp shows full date and time (YYYY-MM-DD HH:MM:SS).

---

## 7. CSV Persistence

### 7.1 File Location
```
./data/positions.csv
```

Auto-created on first run with all 14 stocks at empty/undeployed state.

### 7.2 Schema
```
ticker, tier, is_deployed, shares, avg_cost, cost_basis,
load_gear, buy_pct,
t1_pct, t2_pct, t3_pct,
t1_active, t2_active, t3_active,
last_updated
```

### 7.3 Config File (`config.json`)
```json
{
  "N": 20,
  "unit_cash_krw": 1000000,
  "unit_cash_usd": 750,
  "fx_ticker": "USDKRW=X",
  "peak_lookback_days": 5
}
```

### 7.4 Save Behavior

- **[Save]** writes all row states + config to disk.
- No auto-save. Launch reads CSV; user controls all writes.

---

## 8. File Structure

```
mini_calculator/
├── main.py              — entry point
├── config.json          — portfolio settings
├── data/
│   └── positions.csv    — position state
├── core/
│   ├── calc.py          — all formulas, constants, color helpers
│   ├── data_feed.py     — yfinance wrappers (concurrent fetch)
│   └── csv_io.py        — read/write CSV and config (schema-versioned)
├── gui/
│   ├── main_window.py   — App class: layout, deploy/reset, refresh, save
│   ├── deployed_row.py  — two-row widget for deployed stocks
│   └── empty_row.py     — single-row widget for empty stocks
└── docs/
    ├── MANUAL.md        — this file
    ├── CHANGELOG.md     — version history
    └── README.md        — quick-start guide
```

---

## 9. Intentional Omissions

| Excluded | Reason |
|----------|--------|
| Perk / trait system | Main program only |
| RELOAD / RESET logic | Too stateful for field tool |
| Anchor tracking | User handles mentally |
| Sell gear auto-selection | User selects manually |
| Full charting suite | 5-day candle chart popup covers basic needs |
| Automation / timers | Fully manual |
| Multiple buy zones shown simultaneously | Simplified to one active gear |

---

## 10. Version History

| Version | Date | Notes |
|---------|------|-------|
| 0.1 | 2026-03-27 | Initial specification |
| 0.2 | 2026-03-28 | Buy gear simplified to single %; sell tiers redesigned as per-tier % with activation; deploy/reset buttons; stock names; army % calculation; sort order; color-coded gear dropdowns |
| 0.3 | 2026-03-28 | Buy ratio per gear (4%→0.5, 5%→0.6, 6%→0.7); red/blue color scheme; non-reactive outputs (Save & Refresh); manual stock ordering (▲▼); 5-day close display; candle chart popup; shares=0 auto-reset; full labels; bigger output fonts |
