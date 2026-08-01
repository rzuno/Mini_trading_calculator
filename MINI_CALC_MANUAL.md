# AI Seesaw Mini-Calculator
## Project Manual v0.4

**Sister project of:** AI Seesaw Trading (main program)
**Goal:** Single-window, reactive calculator, bookkeeper, and autopilot cockpit for an AI-sector portfolio.
**Strategy:** Gearbox V-Commandos campaigns — see [`Gearbox V-Commandos Autopilot Manual.md`](Gearbox%20V-Commandos%20Autopilot%20Manual.md) for the full specification. This manual covers the app around it.
**Automation level:** The commander picks the battlefield and the gear; the bot watches and executes the lines. Every card can be traded by hand in the broker app using the same numbers.
**Interface:** Single tkinter window with a scrollable card grid, plus a per-stock autopilot cockpit window.

---

## 0. Philosophy and Scope

The Mini-Calculator is a **field tool**, not a command center. It reads the position snapshot (from Toss, or from CSV in manual mode), fetches live prices and FX, and displays the arithmetic of one V campaign per stock:

1. Where is a flat stock's entry (LOAD) trigger, and how many shares?
2. Where is my next CHASE, and how many shares does it add?
3. Where does the whole position come out (the single EXIT)?
4. Is the FX rate far enough from its 3-month average to switch some won/dollar?

**The card and the bot are one system.** Whatever a card shows is exactly what the autopilot watches: the card pushes `{gear, exit_tier, cap_units}` to the engine on every recompute. The same numbers can be typed into the broker app by hand — hand trades and bot trades land in the same campaign log.

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
| 16 | SKHY | SK Hynix ADR | USD | No |

To add a stock: add a name in `STOCK_NAMES` (`core/calc.py`), a default in `_PORTFOLIO` (`core/csv_io.py`), and a row in `data/positions.csv`.

### 1.2 Classification: KR vs US

Stocks are classified only by **market**: Korean (`.KS`, priced in KRW) or US (priced in USD). The classification is derived from the ticker suffix — there is no separate field to maintain.

- **Korean stocks carry a `(KR)` suffix**; US stocks do not. Bold marks a **DEPLOYED** card (a live campaign), not the market.
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
ticker        : str    — market symbol
tier          : str    — legacy 'Major'/'Minor' (no longer affects math)
is_deployed   : bool   — derived/maintained; True when a position is open
shares        : int    — shares held (0 = flat)
avg_cost      : float  — average cost per share
cost_basis    : float  — shares × avg_cost
gear          : int    — 1..5, the campaign's fixed gear
exit_tier     : int    — 1..3, the ONE armed exit tier
auto_mode     : bool   — AUTO (gear from volatility while flat) vs MANUAL
last_updated  : date   — timestamp of last CSV write
```

The pre-gearbox columns (`load_gear`, `buy_pct`, `t1_pct`…`t3_active`) are still written, derived from the gear, so older builds and scripts keep reading the file. On load a legacy file migrates automatically: a bait drop percent becomes its gear (4%→G1 … 8%→G5) and the lowest active sell tier becomes the single exit tier.

### 2.3 Stock States

| State | Condition | Section |
|-------|-----------|---------|
| **DEPLOYED** | `is_deployed` (shares > 0) — a live campaign | Upper |
| **FLAT** | otherwise — waiting for a LOAD | Lower |

On **Save & Refresh** the program auto-promotes an empty stock to deployed when `shares > 0` and `avg_cost > 0`, and auto-demotes a deployed stock to empty when `shares ≤ 0`.

---

## 3. The Gearbox (AUTO / MANUAL)

Each card has an **AUTO / MANUAL** toggle and a **gear picker**. One gear fixes the whole campaign:

| Gear | Name | 5-day range | LOAD | CHASE | Add size | Exit tiers |
|---|---|---|---|---|---|---|
| 1 | Smooth | ≤ 15% | −6% | −4% | ×1/2 | +1 / **+3** / +5% |
| 2 | Moderate | ≤ 20% | −7% | −5% | ×2/3 | +2 / **+4** / +6% |
| 3 | Balanced | ≤ 25% | −8% | −6% | ×3/4 | +3 / **+5** / +7% |
| 4 | Deep | ≤ 30% | −9% | −7% | ×4/5 | +4 / **+6** / +8% |
| 5 | Extreme | > 30% | −10% | −8% | ×1.0 | +5 / **+7** / +9% |

```
5-day range V = 100 × (High5 − Low5) / High5
```

- **AUTO** picks the gear from V **while the card is FLAT**, then floors it by the heavy-unit rule (§3.1). Once the card is DEPLOYED the gear is **pinned** — a campaign never changes gear by itself.
- **MANUAL** lets the commander pick any gear. On a deployed card the picker stays live in AUTO too: using it *is* the override, and the bot logs `GEAR_OVERRIDE`.
- The gear badge (a colored circle 1–5) makes the whole grid of cards readable at a glance.

The cut points were **raised** from the old 8/12/16/20 ladder on 2026-08-01, so gear 5 — which buys the entire position again on every chase — is reserved for stocks that actually move 30%+ in a week. Bounds are inclusive: exactly 20.0% is gear 2.

### 3.1 Heavy-unit entry floor

A stock whose single share eats a large fraction of a unit cannot run a shallow ladder — `×1/2` on 2 shares rounds to nothing useful. The **entry** gear is floored by `share_price / unit_cash`:

```
≤ 1.2 → G1 ok    > 1.2 → G2+    > 1.6 → G3+    > 2.0 → G4+    > 2.5 → G5 only
```

The card writes `▲heavy` next to the gear when this, not volatility, chose it.

---

## 4. LOAD (FLAT stocks)

```
vantage     = High5 — the highest completed-session high of the previous
              5 trading days (previous close is the fallback)
load_price  = vantage × (1 − gear.load%)
load_shares = max(1, round_half_up(unit_cash / load_price))
```

**Every stock loads one full unit of cash.** If a single share already costs more than a unit, the minimum of 1 share applies. The quantity is sized off the *trimmed* (actually orderable) price, so the card number is the order number.

A flat card shows `Vantage:`, then a projected ladder `Load / Chase 1 / Chase 2` and the three exit tiers computed as if the load had filled. The Current value turns green once the price is at or below the LOAD.

### 4.1 Same-day reload

After a full EXIT the same session, one fast reload arms at the **actual final sell fill −3%** (not the gear's load drop). The card marks the vantage `(reload)`. If it does not fill by the close it expires — the next trading day returns to the rolling High5.

---

## 5. CHASE (DEPLOYED stocks)

```
chase_price = actual_avg_cost × (1 − gear.chase%)
chase_qty   = max(1, round_half_up(actual_shares × gear.ratio))
```

The card shows three chase lines. They **cascade**: each level's shares are folded into the running average before the next is computed, so Chase 2 already assumes Chase 1 filled. The bot only ever arms the first one — after a fill, everything is recomputed from the broker's new real average.

A campaign may not consume more than **32 units** of cash. Over that cap the chase is disabled and only the EXIT keeps being watched.

---

## 6. EXIT (one clean full-position sell)

```
exit_price = actual_avg_cost × (1 + tier%)
exit_qty   = ALL shares
```

The card shows all three tiers of the current gear so the trade-off is visible, but exactly **one is armed** (marked `▶`) and it carries the whole position. The other two are greyed and show no quantity.

There is no 33/33/34 split. Partial exits leave residual positions, make the remaining average hard to read, and make "is this campaign finished?" ambiguous. One tier, one sell, campaign over.

Switching tier mid-campaign is allowed and logged as `EXIT_TIER_OVERRIDE`.

---

## 6.1 Autopilot (two bots, one at a time)

Every card carries two buttons:

```
[ V-COMMANDOS ]  big    the campaign bot — watches the card's LOAD/CHASE/EXIT
[   v^ grid   ]  small  the daily v^ linear weighted grid — its own logic
```

Clicking either arms **WATCH** on that stock (polling only, no orders) and opens its cockpit window. The cockpit's **LIVE** button lets the bot place real orders; LIVE runs only during regular market hours and drops back to WATCH at the close. In WATCH, a crossed line lights the trigger row so it can be fired by hand — a hand-fired trigger is attributed exactly like a bot fill.

Only one strategy runs per stock; switching is refused while that stock is LIVE or holds shares. Button color is the status: grey = off, blue = WATCH, red = LIVE.

The grid bot is documented separately in [`Daily v^ Grid Autopilot Manual.md`](Daily%20v^%20Grid%20Autopilot%20Manual.md).

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

- **DEPLOYED — by gap, highest first.** A campaign closest to its exit floats to the top.
- **FLAT — by 5-day range, widest first.** The most volatile candidate — the next likely battlefield — floats to the top. Stocks whose range is not yet known fall back to the catalogue order.

Both orderings re-grid live after each price fetch (no full rebuild), so cards re-sort without disturbing fields you are editing.

---

## 9. Real-Time Data

| Data | Source |
|------|--------|
| Current price, 5-day High/Low/closes/OHLC, previous close | Toss (default) or Yahoo Finance |
| Holdings, average cost, buying power, open orders | Toss account API |
| USD/KRW rate + 3-month average | Yahoo `USDKRW=X` |

- **Save & Refresh** (single main button): collect inputs → reconcile from Toss (or auto-promote/demote in manual mode) → rebuild → save CSV + config → fetch prices and FX in a background thread → recompute and re-sort.
- Auto-refresh once on launch. No timed refresh loop on the main panel.
- The **autopilot** polls independently every 5 s, but only for stocks being watched, and only that ticker's data.
- The **gap** is `(current − avg)/avg × 100` on a deployed card (red above cost, blue below) and `(current − vantage)/vantage × 100` on a flat card (purple far from the LOAD, orange once it is reached).

---

## 10. Charts

The autopilot cockpit windows carry the charts:

- **Campaign window** (`gui/campaign_window.py`) — live tick curve with the campaign's own lines (LOAD/CHASE blue, broker average purple, full EXIT red, vantage orange), beside a 5-day candle panel carrying the same lines, plus the campaign fill log.
- **Grid window** (`gui/autopilot_window.py`) — the daily v^ grid cockpit, unchanged.

---

## 11. Persistence

### 11.1 `data/positions.csv`

```
ticker,tier,is_deployed,shares,avg_cost,cost_basis,gear,exit_tier,
load_gear,buy_pct,t1_pct,t2_pct,t3_pct,t1_active,t2_active,t3_active,
auto_mode,last_updated
```

`gear` and `exit_tier` are the live fields; the rest of the gear columns are derived and written for back-compat. Created automatically (all tickers, shares = 0) on first run if missing. Legacy files (`A/B/C`, `L1`–`L7`, bait drop percents, three active sell tiers) are migrated on read.

### 11.2 `config.json`

```json
{
  "N": 20,
  "unit_cash_krw": 1000000,
  "unit_cash_usd": 650.01,
  "fx_ticker": "USDKRW=X",
  "peak_lookback_days": 5,
  "fx_switch_level": 0,
  "market_provider": "toss"
}
```

### 11.3 `data/autopilot_state.json`

Engine state per `ticker#strategy`, so a restart re-arms exactly where it left off and the two strategies never overwrite each other. The V-Commandos entry holds the campaign id, gear, exit tier, vantage, chase count, campaign low, peak deployment, the fill log and the override log.

---

## 12. File Structure

```
Mini_trading_calculator/
├── main.py                     — entry point, launches GUI
├── config.json                 — portfolio settings + fx_switch_level
├── Gearbox V-Commandos Autopilot Manual.md   — the strategy specification
├── Daily v^ Grid Autopilot Manual.md         — the secondary strategy
├── data/
│   ├── positions.csv           — position + gear state (volatile)
│   └── autopilot_state.json    — engine state per ticker#strategy
├── core/
│   ├── calc.py                 — catalogue, the gearbox, every line, colors
│   ├── vcommandos.py           — V_COMMANDOS_GEARBOX campaign engine
│   ├── autopilot.py            — DAILY_V_HAT_LINEAR_GRID engine
│   ├── data_feed.py            — yfinance wrappers (price, 5d OHLC, FX)
│   └── csv_io.py               — read/write CSV and config
├── providers/                  — Toss (default) and Yahoo market data
├── gui/
│   ├── main_window.py          — header, FX panel, card grid, refresh
│   ├── stock_row.py            — the campaign card (FLAT + DEPLOYED)
│   ├── autopilot_ctrl.py       — poll thread, order execution, both engines
│   ├── campaign_window.py      — V-Commandos cockpit
│   ├── autopilot_window.py     — v^ grid cockpit
│   ├── stepper.py              — +/- stepper widget
│   └── candle_chart.py         — shared candle panel
└── scripts/
    ├── test_vcommandos.py      — gearbox calculator + campaign engine (87)
    ├── test_grid.py            — grid engine + fuzz (86)
    └── test_autopilot_ui.py    — both cockpits' presentation (58)
```

---

## 13. Version History

| Version | Date | Notes |
|---------|------|-------|
| 0.1 | 2026-03-27 | Initial specification. |
| 0.2 | — | Continuous LOAD gear (−4…−15%), buy 4/5/6% radios, sell-tier steppers, AUTO/MANUAL volatility gear, candlestick graph, KR/US bold styling, deployed-by-size & empty-by-volatility ordering, FX 3-month average + deviation. |
| 0.3 | 2026-06-19 | Added Oracle (ORCL). Every stock loads a full unit (Minor 0.5× tier retired). Deployed order normalized across currencies (KR no longer pinned to front). FX dollar-switch tracker: ±3% ladder, per-threshold amounts (\|k\|/6 of the 1/3 switch pool), and 7 clickable level buttons persisted as `fx_switch_level`. |
| 0.4 | 2026-08-01 | **Rolled back to the bait-based V-Commandos system, rebuilt as the five-speed Gearbox.** Card and bot unified — the card is the campaign's source of truth for gear and exit tier. Separate LOAD (−6…−10%) and CHASE (−4…−8%) drops. One armed exit tier selling the whole position (33/33/34 split retired). High5 vantage; same-day reload at the sell fill −3%. Volatility→gear cut points raised to 15/20/25/30; heavy-unit entry floor added. Two autopilot buttons per card (V-COMMANDOS / v^ grid), one strategy per stock. New `gear` / `exit_tier` CSV columns with legacy migration. The 2026-07-28 재편안 (slots, fronts, army caps, auto-trim) was **not** adopted — see `docs/경제축_전략재편_2026-07-28.md` §11. |

---

*This manual reflects the current implementation. When code and manual disagree, update this document. The strategy itself is specified in `Gearbox V-Commandos Autopilot Manual.md`.*
