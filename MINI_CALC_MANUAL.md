# AI Seesaw Mini-Calculator
## Project Manual v0.3

**Sister project of:** AI Seesaw Trading (main program)
**Goal:** Single-window, reactive calculator and bookkeeper for an AI-sector portfolio.
**Automation level:** Decisions are the user's. The program computes numbers, can auto-pick gears from volatility, and tracks a manual dollar-switch ledger.
**Interface:** Single tkinter window with a scrollable card grid. A candlestick chart opens in a popup on demand.

---

## 0. Philosophy and Scope

The Mini-Calculator is a **field tool**, not a command center. It reads a position snapshot from CSV, fetches live prices and FX, and displays the arithmetic the user performs every trading session:

1. Where is my next buy trigger, and how many shares?
2. Where do I set my sell orders, and in what quantities?
3. Where is an empty stock's entry (LOAD) trigger?
4. Is the FX rate far enough from its 3-month average to switch some won/dollar?

It deliberately omits the main program's perk engine, anchor tracking, regime detection, and idle flags.

---

## 1. Portfolio Definition

### 1.1 Stock List (stored in `data/positions.csv`, seeded from `core/csv_io.py`)

| # | Ticker | Name | Currency | Bold? |
|---|--------|------|----------|-------|
| 1 | 005930.KS | Samsung Electronics | KRW | **Yes (KR)** |
| 2 | 000660.KS | SK Hynix | KRW | **Yes (KR)** |
| 3 | NVDA | NVIDIA | USD | No |
| 4 | GOOGL | Alphabet | USD | No |
| 5 | MU | Micron | USD | No |
| 6 | MSFT | Microsoft | USD | No |
| 7 | SNDK | SanDisk | USD | No |
| 8 | AMD | AMD | USD | No |
| 9 | TSM | TSMC | USD | No |
| 10 | AVGO | Broadcom | USD | No |
| 11 | PLTR | Palantir | USD | No |
| 12 | AAPL | Apple | USD | No |
| 13 | AMZN | Amazon | USD | No |
| 14 | STX | Seagate | USD | No |
| 15 | INTC | Intel | USD | No |
| 16 | ORCL | Oracle | USD | No |

To add a stock: add a name in `STOCK_NAMES` (`core/calc.py`), a default in `_PORTFOLIO` (`core/csv_io.py`), and a row in `data/positions.csv`.

### 1.2 Classification: KR vs US

Stocks are classified only by **market**: Korean (`.KS`, priced in KRW) or US (priced in USD). The classification is derived from the ticker suffix — there is no separate field to maintain.

- **Korean stocks are shown in bold** in both the deployed and empty cards, with a `(KR)` suffix. US stocks are plain text.
- The old **Major/Minor tier is retired.** Every stock now loads a **full unit** of cash. The `tier` column still exists in the CSV for backward compatibility but no longer changes any calculation.

---

## 2. Core Definitions

### 2.1 Capital Units

```
N               : total number of army units in the portfolio (user-defined)
unit_cash_krw   : cash value of one unit, in KRW (user-defined)
unit_cash_usd   : auto-derived = unit_cash_krw / FX rate  (display/reference)
```

The KRW unit is the source of truth; the USD unit is recomputed whenever the KRW unit or the FX rate changes.

### 2.2 Per-Stock State (stored in CSV)

```
ticker        : str    — Yahoo Finance symbol
tier          : str    — legacy 'Major'/'Minor' (no longer affects math)
is_deployed   : bool   — derived/maintained; True when a position is open
shares        : int    — shares held (0 = empty)
avg_cost      : float  — average cost per share
cost_basis    : float  — shares × avg_cost
load_gear     : int    — LOAD drop %, 4..15 (per stock)
buy_pct       : int    — RESCUE gear: 4, 5, or 6 (%)
t1_pct/t2_pct/t3_pct       : float — sell-tier percents (enforced T1<T2<T3)
t1_active/t2_active/t3_active : bool — which sell tiers are shown
auto_mode     : bool   — AUTO (gear from volatility) vs MANUAL
last_updated  : date   — timestamp of last CSV write
```

### 2.3 Stock States

| State | Condition | Section |
|-------|-----------|---------|
| **DEPLOYED** | `is_deployed` (shares > 0) | Upper |
| **EMPTY** | otherwise | Lower |

On **Save & Refresh** the program auto-promotes an empty stock to deployed when `shares > 0` and `avg_cost > 0`, and auto-demotes a deployed stock to empty when `shares ≤ 0`.

---

## 3. AUTO / MANUAL Gear (volatility-driven)

Each card has an **AUTO / MANUAL** toggle.

- **MANUAL** — the user sets the gear controls directly (load stepper, buy radios, sell steppers).
- **AUTO** — the gear bundle is chosen from the stock's **5-day volatility** and the controls are locked (the sell-tier on/off checkboxes stay editable).

**5-day volatility:**
```
V = 100 × (5d_high − 5d_low) / 5d_high     [%]
```

**Gear selection from V:**

| Volatility | Gear | LOAD drop | RESCUE gear | Sell tiers (T1/T2/T3) |
|------------|------|-----------|-------------|------------------------|
| V < 9% | 1 | −6% | −4% (×0.5) | +2 / +4 / +6% |
| 9% ≤ V < 14% | 2 | −7% | −5% (×0.6) | +3 / +5 / +7% |
| V ≥ 14% | 3 | −8% | −6% (×0.7) | +4 / +6 / +8% |

(Cut points `VOL_LO = 9`, `VOL_HI = 14`; bundles in `AUTO_GEARS`, all in `core/calc.py`.)

---

## 4. LOAD Logic (EMPTY stocks)

When a stock has no position, the entry is computed from the 5-day high.

```
peak_5d     = highest High over the last 5 trading days (fetched)
load_pct    = LOAD drop %, 4..15 (auto from volatility, or manual stepper)
load_price  = peak_5d × (1 − load_pct / 100)
load_shares = max(1, round_half_up(unit_cash / load_price))
```

**Every stock loads one full unit of cash.** If a single share already costs more than a unit (e.g. SK Hynix), the minimum of **1 share** applies. The LOAD stepper is continuous from −4% to −15% in 1% steps, colored on a blue gradient (light at −4%, dark at −15%).

The empty card shows: 5-day high, current price (turns green when at/below the load price), the LOAD gear, and `load_price × load_shares`. It also carries optional Avg Cost / Shares fields so a fill can be entered and deployed on the next Save.

---

## 5. RESCUE Logic (DEPLOYED stocks)

Three averaging-down buy triggers below `avg_cost`. The RESCUE gear sets the drop and the buy ratio:

| Gear | Drop | Buy ratio |
|------|------|-----------|
| −4% | avg × 0.96 | ×0.5 |
| −5% | avg × 0.95 | ×0.6 |
| −6% | avg × 0.94 | ×0.7 |

The three triggers **cascade**: each level's bought shares (`max(1, round_half_up(shares × ratio))`) are folded into the running average before the next trigger is computed, so trigger 2 already assumes trigger 1 was caught.

---

## 6. SELL Logic (DEPLOYED stocks)

Three sell tiers, each an **on/off toggle + a percent stepper** (1–20%, with `T1 < T2 < T3` enforced). By default only **T2** is active. Sell prices:

```
sell_i = avg_cost × (1 + tier_i_pct / 100)
```

**Quantity split** across the *active* tiers:
- 1 active: 100% of shares at that tier.
- 2 active: 50% at the lower, the remainder at the higher.
- 3 active: 50%, then 50% of the remainder, then the rest.

---

## 7. FX Panel and the Dollar-Switch Tracker

The FX panel sits directly under the header.

### 7.1 Rate and 3-Month Average

```
FX Rate:  1,534.50  (+2.3%)     3-Month Avg:  1,500
```

- The **current USD/KRW rate** is fetched live (`USDKRW=X`).
- The **3-month average** is the mean daily close over the trailing ~3 months (`core/data_feed.fetch_fx_rate`).
- The deviation `(rate − avg) / avg` is shown next to the rate, **colored red when above** the average and **blue when below**, deepening with magnitude (`fx_dev_color`).

### 7.2 The ±3% Ladder

Seven rungs around the average, at −3%, −2%, −1%, 0 (avg), +1%, +2%, +3%, each showing the **target FX price** (`avg × (1 + k/100)`). The rung the live rate currently sits at is **bold**. These are pure proportional calculations off the one real value (the 3-month average).

### 7.3 The Switch Strategy

Capital is split in thirds: **1/3 always in won, 1/3 always in dollars, 1/3 switched** by FX.

```
switch_pool (KRW) = N × unit_cash_krw / 3
```

At each threshold the **marginal** trade is `|k| / 6` of the pool, sized in USD at the neutral (3-month average) rate:

| Threshold | Action | Marginal amount |
|-----------|--------|-----------------|
| +1% | sell USD | 1/6 of pool |
| +2% | sell USD | 2/6 of pool |
| +3% | sell USD | 3/6 of pool |
| −1% | buy USD | 1/6 of pool |
| −2% | buy USD | 2/6 of pool |
| −3% | buy USD | 3/6 of pool |

Doing all three steps on one side sums to the whole pool (`1/6 + 2/6 + 3/6 = 1`). Example: pool ₩9,000,000 at avg 1,500 → $6,000 → steps **$1,000 / $2,000 / $3,000**. The amounts auto-resize with `N`, the KRW unit, and the average rate, and the panel prints the pool summary so the numbers are always visible.

### 7.4 The 7 Clickable Buttons (manual ledger)

Below each rung is a button: `-3 -2 -1 0 +1 +2 +3`. They record **how many switch steps you have already done**, so you can tell at a glance whether a trade is still owed.

- Clicking **+k** lights `+1 … +k` green; clicking **−k** lights `−1 … −k` green; clicking **0** clears all.
- Example: after selling at +1% and +2%, click **+2** so `+1` and `+2` are green — you then hold until +3%. If +3% hits, sell the +3 step and click **+3**.
- The single level is saved to `config.json` as `fx_switch_level` and restored on launch.

This is a manual tracker: you can always trade off-schedule when a position demands it; the panel is for the FX-driven switching you control by rule.

---

## 8. Ordering of Cards

- **DEPLOYED — by size, largest first.** Size is the cost basis in a common currency; USD positions are converted to KRW with the live FX rate before comparing. Korean stocks are **no longer forced to the front** — they sit wherever their true value lands.
- **EMPTY — by 5-day volatility, most volatile first.** Under the strategy, a more volatile stock is more profitable, so it floats to the top. Stocks whose volatility is not yet known fall back to the catalogue order.

Both orderings re-grid live after each price fetch (no full rebuild), so cards re-sort without disturbing fields you are editing.

---

## 9. Real-Time Data

| Data | Source |
|------|--------|
| Current price, 5-day High/Low/closes/OHLC | Yahoo Finance via `yfinance` |
| USD/KRW rate + 3-month average | ticker `USDKRW=X` |

- **Save & Refresh** (single main button): collect inputs → auto-promote/demote → rebuild → save CSV + config → fetch prices and FX in a background thread → recompute and re-sort.
- Auto-refresh once on launch. No timed refresh loop.
- The **gap rate** `(current − avg)/avg × 100` is shown per deployed stock, color-coded (red above cost, blue below, gray near flat).

---

## 10. Graph

Each card has a **Graph** button that opens a candlestick popup (`gui/candle_chart.py`) for the last 5 days, with mode-specific reference lines (avg cost + buy/sell levels for deployed; load target for empty) and the current price.

---

## 11. Persistence

### 11.1 `data/positions.csv`

```
ticker,tier,is_deployed,shares,avg_cost,cost_basis,load_gear,buy_pct,
t1_pct,t2_pct,t3_pct,t1_active,t2_active,t3_active,auto_mode,last_updated
```

Created automatically (all tickers, shares = 0) on first run if missing. Legacy gear keys (`A/B/C`, `L1`–`L7`, sell `A`–`E`) are migrated on read.

### 11.2 `config.json`

```json
{
  "N": 30,
  "unit_cash_krw": 1000000,
  "unit_cash_usd": 650.01,
  "fx_ticker": "USDKRW=X",
  "peak_lookback_days": 5,
  "fx_switch_level": 0
}
```

---

## 12. File Structure

```
Mini_trading_calculator/
├── main.py              — entry point, launches GUI
├── config.json          — portfolio settings + fx_switch_level
├── data/
│   └── positions.csv    — position state (volatile; auto-saved on launch)
├── core/
│   ├── calc.py          — catalogue, gears, all formulas, colors
│   ├── data_feed.py     — yfinance wrappers (price, 5d OHLC, FX + 3m avg)
│   └── csv_io.py        — read/write CSV and config
└── gui/
    ├── main_window.py   — header, FX panel, sections, refresh loop
    ├── stock_row.py     — unified stock card (EMPTY + DEPLOYED), 3 gear boxes
    ├── stepper.py       — +/- stepper widget
    └── candle_chart.py  — candlestick popup
```

---

## 13. Version History

| Version | Date | Notes |
|---------|------|-------|
| 0.1 | 2026-03-27 | Initial specification. |
| 0.2 | — | Continuous LOAD gear (−4…−15%), buy 4/5/6% radios, sell-tier steppers, AUTO/MANUAL volatility gear, candlestick graph, KR/US bold styling, deployed-by-size & empty-by-volatility ordering, FX 3-month average + deviation. |
| 0.3 | 2026-06-19 | Added Oracle (ORCL). Every stock loads a full unit (Minor 0.5× tier retired). Deployed order normalized across currencies (KR no longer pinned to front). FX dollar-switch tracker: ±3% ladder, per-threshold amounts (\|k\|/6 of the 1/3 switch pool), and 7 clickable level buttons persisted as `fx_switch_level`. |

---

*This manual reflects the current implementation. When code and manual disagree, update this document.*
