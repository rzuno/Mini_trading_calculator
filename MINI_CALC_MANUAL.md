# AI Seesaw Mini-Calculator
## Project Manual v0.1

**Sister project of:** AI Seesaw Trading (main program v1.4.2)  
**Goal:** Single-window, fully manual, stateless calculator and bookkeeper for a 14-stock AI-sector portfolio.  
**Automation level:** Zero. All decisions are made by the user. The program only computes numbers.  
**Interface:** Single tkinter window. No pop-ups. No tabs. All information visible at once.

---

## 0. Philosophy and Scope

The Mini-Calculator is a **field tool**, not a command center. The main Seesaw program is a full strategy machine with a perk engine, anchor tracking, and semi-autonomous signals. The Mini-Calculator strips all of that away and retains only the arithmetic that the user performs manually every trading session:

1. Where is my next buy trigger?
2. How many shares do I buy there?
3. Where do I set my sell orders, and in what quantities?

Everything else — perk scoring, idle flags, regime detection, anchor management — belongs in the main program only. The Mini-Calculator has no state machine. It reads a position snapshot from a CSV file, fetches live prices, and displays computed output numbers.

---

## 1. Portfolio Definition

### 1.1 Stock List (Hardcoded, Expandable by Editing CSV)

| # | Ticker | Name | Exchange | Currency | Tier |
|---|--------|------|----------|----------|------|
| 1 | 005930.KS | Samsung Electronics | KRX | KRW | Major |
| 2 | 000660.KS | SK Hynix | KRX | KRW | Major |
| 3 | NVDA | NVIDIA | NASDAQ | USD | Major |
| 4 | GOOGL | Alphabet | NASDAQ | USD | Major |
| 5 | MU | Micron | NASDAQ | USD | Minor |
| 6 | MSFT | Microsoft | NASDAQ | USD | Minor |
| 7 | WDC | Western Digital (SanDisk) | NASDAQ | USD | Minor |
| 8 | AMD | AMD | NASDAQ | USD | Minor |
| 9 | TSM | TSMC | NYSE | USD | Minor |
| 10 | AVGO | Broadcom | NASDAQ | USD | Minor |
| 11 | PLTR | Palantir | NYSE | USD | Minor |
| 12 | AAPL | Apple | NASDAQ | USD | Minor |
| 13 | AMZN | Amazon | NASDAQ | USD | Minor |
| 14 | STX | Seagate | NASDAQ | USD | Minor |

### 1.2 Tier Rules

| Tier | Load Multiplier | Meaning |
|------|----------------|---------|
| Major | 1.0× | Full unit entry at LOAD |
| Minor | 0.5× | Half unit entry at LOAD |

**This multiplier applies to LOAD only.** RESCUE logic is identical for Major and Minor stocks.  
The reason: once a position is open, the rescue calculation is purely based on how deep the stock has dropped and how many units are already deployed — the Major/Minor distinction no longer matters.

---

## 2. Core Definitions

### 2.1 Capital Units

```
N          : total number of army units in the portfolio (user-defined, e.g. 20)
unit_cash  : total_capital / N  (in KRW for Korean stocks, USD for US stocks)
```

For US stocks, `unit_cash` is tracked in USD. The FX rate (USD/KRW) is fetched live and displayed as reference only — it does not convert unit_cash automatically. The user manages the KRW/USD split manually.

### 2.2 Per-Stock State Variables (Stored in CSV)

```
ticker         : str    — Yahoo Finance symbol
shares         : int    — shares currently held (0 = empty)
avg_cost       : float  — average cost per share (0.0 if empty)
cost_basis     : float  — total cash deployed (shares × avg_cost)
peak_5d        : float  — highest High of the last 5 trading days (auto-fetched)
buy_gear       : str    — 'A', 'B', or 'C'  (user selects)
sell_gear      : str    — 'A', 'B', 'C', 'D', or 'E'  (user selects)
last_updated   : date   — timestamp of last CSV write
```

### 2.3 Stock States

Each stock is in exactly one of two states:

| State | Condition | Display Section |
|-------|-----------|-----------------|
| **DEPLOYED** | `shares > 0` | Upper section of window |
| **EMPTY** | `shares == 0` | Lower section of window |

---

## 3. Buy Logic

### 3.1 RESCUE Zones (DEPLOYED stocks only)

When a stock is held (`shares > 0`), the calculator shows three potential buy zones below the current average cost. These zones represent averaged-down entries.

| Zone | Drop from avg_cost | Buy Size (units) | Note |
|------|--------------------|-----------------|------|
| A | −4% | 0.5u | Same for Major and Minor |
| B | −5% | 0.6u | Same for Major and Minor |
| C | −6% | 0.7u | Same for Major and Minor |

**Trigger price formulas:**
```
price_A = avg_cost × 0.96
price_B = avg_cost × 0.95
price_C = avg_cost × 0.94
```

The user selects which zone is active (A, B, or C) using the buy gear selector. The calculator displays **all three prices always**, but highlights the selected zone.

**Note on Minor stocks:** The calculator shows the same RESCUE quantities for Minor stocks as for Major stocks. The user is expected to exercise judgment — in practice, Minor positions should not be chased as deeply as Major ones. A soft guideline (not enforced by the program) is to cap Minor stock deployment at ~3 units total. This discipline is left to the user and will be formalized in the main Seesaw program later.

### 3.2 Share Quantization (Rounding Rule)

Converting a unit-based buy size to integer shares:

```
target_cash = buy_ratio × unit_cash
raw_shares  = target_cash / current_price
buy_shares  = max(1, round_half_up(raw_shares))
```

**round_half_up:** standard rounding where 0.5 always rounds up (not Python's banker's rounding).

```python
import math
def round_half_up(x):
    return math.floor(x + 0.5)
```

**Minimum 1 share** is always enforced regardless of unit size.

**Example:**
```
unit_cash = 1,000,000 KRW
avg_cost  = 80,000 KRW
Zone A: buy_ratio = 0.5
target_cash = 0.5 × 1,000,000 = 500,000 KRW
price_A     = 80,000 × 0.96 = 76,800 KRW
raw_shares  = 500,000 / 76,800 ≈ 6.51
buy_shares  = round_half_up(6.51) = 7
```

### 3.3 LOAD Logic (EMPTY stocks only)

When a stock has zero shares, the entry point is computed from the 5-day high (fetched from Yahoo Finance), not from avg_cost.

#### Load Gear Table

The load gear is **user-selectable per stock**. It controls how far the stock must drop from its recent peak before the user considers entering. Default is **L2 (−5%)** for all stocks.

| Load Gear | Drop Threshold | Character | Typical Use |
|-----------|---------------|-----------|-------------|
| L1 | −4% | Eager | Catch breakouts early; very active |
| **L2** | **−5%** | **Default** | **Standard entry for all stocks** |
| L3 | −6% | Patient | Wait for a cleaner dip |
| L4 | −10% | Selective | Only enter on a real correction |
| L5 | −15% | Dormant | Park the slot; only wake on a crash |

**L4 and L5 are primarily intended for Minor stocks** that the user wants to deprioritize during periods when major stocks are active. Setting a minor stock to L5 effectively says "ignore this for now — only alert me if it crashes."

#### LOAD Formula

```
peak_5d      = max(High) over last 5 completed trading days
load_drop    = selected load gear drop %  (4 / 5 / 6 / 10 / 15)
load_price   = peak_5d × (1 - load_drop / 100)
load_units   = 1.0 × tier_multiplier       (Major: 1.0u  /  Minor: 0.5u)
target_cash  = load_units × unit_cash
buy_shares   = max(1, round_half_up(target_cash / load_price))
```

The tier_multiplier (Major=1.0, Minor=0.5) **only appears here** in the LOAD formula.  
Once a position is open, all subsequent RESCUE calculations use plain unit ratios (0.5 / 0.6 / 0.7) regardless of tier.

#### EMPTY Row Display

The calculator shows for each empty stock:
- `peak_5d` — the 5-day high fetched live
- `load_price` — the computed entry trigger price
- `buy_shares` — recommended number of shares to buy at load
- `load_gear` — currently selected gear (L1–L5), user-editable

No sell ladder is shown until a position exists.

---

## 4. Sell Logic

### 4.1 The Five Sell Gears

| Gear | Label | Tier 1 | Tier 2 | Tier 3 | Character |
|------|-------|--------|--------|--------|-----------|
| A | Emergency | +2% | +4% | +6% | Evacuation |
| B | Conservative | +3% | +5% | +7% | Cautious |
| C | Default | +4% | +6% | +8% | Standard |
| D | Confident | +5% | +7% | +9% | Holding |
| E | Greedy | +6% | +8% | +10% | Patient |

**Formula for sell prices (from avg_cost):**
```
sell_1 = avg_cost × (1 + tier1_pct / 100)
sell_2 = avg_cost × (1 + tier2_pct / 100)
sell_3 = avg_cost × (1 + tier3_pct / 100)
```

### 4.2 Sell Quantity Split

The 3-tier ladder splits the current share count as follows:

```
Q       = shares currently held
qty_1   = round_half_up(Q × 0.50)          # 50% at Tier 1
qty_2   = round_half_up((Q - qty_1) × 0.50) # 50% of remainder at Tier 2
qty_3   = Q - qty_1 - qty_2                # 100% of remainder at Tier 3
```

**Example:**
```
Q = 10 shares, Gear C (4/6/8%)
qty_1 = 5   → sell at avg_cost × 1.04
qty_2 = 3   → sell at avg_cost × 1.06
qty_3 = 2   → sell at avg_cost × 1.08
```

---

## 5. Real-Time Data

### 5.1 Data Sources

| Data | Source | Method |
|------|--------|--------|
| Current price (all stocks) | Yahoo Finance | `yfinance` library |
| 5-day High (for LOAD) | Yahoo Finance | `yfinance` 5-day OHLCV |
| USD/KRW FX rate | Yahoo Finance | ticker `USDKRW=X` |

### 5.2 Refresh Behavior

- On program launch: auto-fetch all prices and FX rate.
- Manual **[Refresh]** button: re-fetch all live data on demand.
- No automatic refresh loop (no timers). User triggers refreshes manually.
- If a fetch fails (network error), display the last cached value with a warning indicator.

### 5.3 Gap Rate Display

```
gap_rate = (current_price - avg_cost) / avg_cost × 100   [%]
```

Shown in the row for each DEPLOYED stock. Color-coded:
- Green: positive (above avg_cost)
- Red: negative (below avg_cost)
- Gray: within ±1% (roughly flat)

---

## 6. GUI Layout

### 6.1 Single-Window Design

The entire interface is one non-resizable (or fixed-ratio) tkinter window. No pop-ups, no new windows, no tabs. Everything visible simultaneously.

**Window sections (top to bottom):**

```
┌─────────────────────────────────────────────────────────────────┐
│  [Portfolio Header: Capital N, unit_cash, FX rate, timestamp]   │
├─────────────────────────────────────────────────────────────────┤
│  DEPLOYED STOCKS (rows for stocks with shares > 0)              │
│  ─────────────────────────────────────────────────────          │
│  [Column headers]                                               │
│  [Row per stock]                                                │
│  ...                                                            │
├─────────────────────────────────────────────────────────────────┤
│  EMPTY STOCKS (rows for stocks with shares == 0)                │
│  ─────────────────────────────────────────────────────          │
│  [Column headers]                                               │
│  [Row per stock]                                                │
│  ...                                                            │
├─────────────────────────────────────────────────────────────────┤
│  [Refresh]  [Save]  [Status bar: last updated time]             │
└─────────────────────────────────────────────────────────────────┘
```

### 6.2 Column Layout — DEPLOYED Row

| Column | Content | Input/Output |
|--------|---------|-------------|
| # | Row number | — |
| Ticker | Stock symbol | — |
| Shares | Shares held | **Manual input** |
| Avg Cost | Average cost per share | **Manual input** |
| Current | Live price | Auto-fetched |
| Gap % | (current − avg) / avg × 100 | Computed |
| Ccy | Currency (KRW/USD) | — |
| Buy Gear | A / B / C selector | **Manual select** |
| Buy @ | Trigger price for selected zone | Computed |
| Buy # | Number of shares to buy | Computed |
| Sell Gear | A / B / C / D / E selector | **Manual select** |
| T1 Price | Sell tier 1 price | Computed |
| T1 # | Shares to sell at tier 1 | Computed |
| T2 Price | Sell tier 2 price | Computed |
| T2 # | Shares to sell at tier 2 | Computed |
| T3 Price | Sell tier 3 price | Computed |
| T3 # | Shares to sell at tier 3 | Computed |

### 6.3 Column Layout — EMPTY Row

| Column | Content | Input/Output |
|--------|---------|-------------|
| # | Row number | — |
| Ticker | Stock symbol | — |
| Tier | Major / Minor | — |
| 5D High | 5-day highest High | Auto-fetched |
| Current | Live price | Auto-fetched |
| Ccy | Currency | — |
| Load Gear | L1 / L2 / L3 / L4 / L5 selector | **Manual select** |
| Load @ | Entry trigger price | Computed |
| Load # | Shares to buy at Load | Computed |

Columns for avg_cost, gap%, rescue gear, and sell tiers are hidden or blank for EMPTY rows.

### 6.4 Portfolio Header Bar

Displays:
```
Total Units (N): [editable]    Unit Cash (KRW): [editable]    Unit Cash (USD): [editable]
USD/KRW: [live]    Last Refresh: [timestamp]
```

`N`, `unit_cash_krw`, and `unit_cash_usd` are editable fields. Changing them triggers recomputation of all buy quantities across all rows.

---

## 7. CSV Persistence

### 7.1 File Location

```
./data/positions.csv
```

Created automatically on first run if not present, pre-populated with all 14 tickers, shares=0.

### 7.2 CSV Schema

```csv
ticker,tier,shares,avg_cost,cost_basis,load_gear,buy_gear,sell_gear,last_updated
NVDA,Major,25,420.50,10512.50,L2,B,C,2026-03-27
005930.KS,Major,0,0.0,0.0,L2,A,C,2026-03-27
MU,Minor,0,0.0,0.0,L4,A,C,2026-03-27
...
```

`load_gear` persists per stock so that a Minor stock parked at L5 stays dormant across sessions until the user consciously changes it.

### 7.3 Save Behavior

- **[Save]** button writes the current state of all rows back to CSV.
- No auto-save (prevents accidental overwrites).
- On launch, CSV is read and the window is populated. Live prices are then fetched.

### 7.4 Config File

A separate `config.json` stores portfolio-level settings:

```json
{
  "N": 20,
  "unit_cash_krw": 1000000,
  "unit_cash_usd": 750,
  "fx_ticker": "USDKRW=X",
  "peak_lookback_days": 5
}
```

---

## 8. What This Program Deliberately Omits

The following features from the main Seesaw program are **intentionally excluded**:

| Excluded Feature | Reason |
|-----------------|--------|
| Perk / trait system | Manual override handles this |
| RELOAD logic | User uses RESCUE zones in practice |
| RESET logic | Too stateful for a manual tool |
| Anchor tracking | User knows when to update mentally |
| G/L/V committee scoring | Not needed for field calculations |
| Sell gear auto-selection | User selects gear manually |
| Graphs / charts | Use brokerage app for visuals |
| Idle engine / flags | Main program handles this |
| Floating entry/exit % | Only fixed presets (3 buy zones, 5 sell gears) |
| Automation / scheduling | Fully manual operation |

---

## 9. Development Notes

### 9.1 Libraries Required

```
yfinance       — market data and FX
tkinter        — GUI (standard library)
pandas         — CSV handling
math           — round_half_up
json           — config file
datetime       — timestamps
```

### 9.2 File Structure

```
mini_calculator/
├── main.py              — entry point, launches GUI
├── config.json          — portfolio settings (N, unit_cash, etc.)
├── data/
│   └── positions.csv    — position state
├── core/
│   ├── calc.py          — all formulas (buy/sell calculations)
│   ├── data_feed.py     — yfinance wrappers (price, 5d high, FX)
│   └── csv_io.py        — read/write CSV and config
└── gui/
    ├── main_window.py   — root window layout
    ├── deployed_row.py  — row widget for DEPLOYED stocks
    └── empty_row.py     — row widget for EMPTY stocks
```

### 9.3 Relationship to Main Program

The Mini-Calculator is an **independent project**. It does not import or depend on any code from the main Seesaw program. The strategy logic it implements is a strict subset of the main program's strategy, but is re-implemented from scratch for clarity and simplicity.

The main Seesaw program changelog should note the creation of this sister project at v1.4.2.

---

## 10. Version History

| Version | Date | Notes |
|---------|------|-------|
| 0.1 | 2026-03-27 | Initial specification. Project started. |

---

*This manual is the authoritative specification for the Mini-Calculator project.  
All implementation decisions should reference this document first.*
