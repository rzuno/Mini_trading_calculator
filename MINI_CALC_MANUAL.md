# AI Seesaw Mini-Calculator
## Project Manual v1.0

**Sister project of:** AI Seesaw Trading (main program)
**Goal:** Single-window, reactive calculator, bookkeeper, and autopilot cockpit for an AI-sector portfolio.
**Strategy:** Gearbox AUTOPILOT campaigns — see [Gearbox AUTOPILOT Manual](Gearbox%20V-Commandos%20Autopilot%20Manual.md) for the full specification. This manual covers the app around it.
**Automation level:** The commander picks the battlefield and the gear; the bot sends the order itself when the curve touches a line. Every card can also be traded by hand in the broker app — the bot reconciles those trades into the same campaign, because the broker position is the one thing they share.
**Interface:** Single tkinter window with a scrollable card grid, plus a per-stock autopilot cockpit window.

---

## 0. Philosophy and Scope

The Mini-Calculator is a **field tool**, not a command center. It reads the position snapshot (from Toss, or from CSV in manual mode), fetches live prices and FX, and displays the arithmetic of one V campaign per stock:

1. Where is an EMPTY stock's entry (LOAD) trigger, and how many shares?
2. Where is my next CHASE, and how many shares does it add?
3. Where does the position come out (one-shot EXIT by default, or optional tiers)?
4. Is the FX rate far enough from its 3-month average to switch some won/dollar?

**The card and the bot are two instruments (§3.2).** A card is the commander's worksheet for trading by hand: it refreshes when **Save & Refresh** is pressed and holds its own gear and tiers. The autopilot keeps its **own** gear and tiers, changed in the cockpit and remembered across restarts. They meet in exactly two places — the broker position they both read, and the card's **`sync to autopilot`** button, which copies that card's settings into the bot when it is pressed.

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
gear          : int    — 1..5, the selected gear (AUTO may refresh it from V)
exit_tier     : int    — 1..3, compatibility value (lowest armed tier)
t1_active..t3_active  — actual one-, two-, or three-tier exit selection
auto_mode     : bool   — AUTO (gear follows volatility) vs MANUAL
last_updated  : date   — timestamp of last CSV write
```

The pre-gearbox columns (`load_gear`, `buy_pct`, `t1_pct`…`t3_active`) are still written, derived from the gear, so older builds and scripts keep reading the file. On load a legacy file migrates automatically: a bait drop percent becomes its gear (4%→G1 … 8%→G5), all valid active tier flags are preserved, and `exit_tier` supplies the one-shot selection only when those flags are absent.

### 2.3 Stock States

| State | Condition | Section |
|-------|-----------|---------|
| **DEPLOYED** | `is_deployed` (shares > 0) — a live campaign | Upper |
| **EMPTY** | otherwise — waiting for a LOAD | Lower |

On **Save & Refresh** the program auto-promotes an empty stock to deployed when `shares > 0` and `avg_cost > 0`, and auto-demotes a deployed stock to empty when `shares ≤ 0`.

`FLAT` remains only as an internal engine/state-file identifier for backward compatibility. The card, cockpit, status text, and current manuals use **EMPTY**.

---

## 3. The Gearbox (AUTO / MANUAL)

Each card has an **AUTO / MANUAL** toggle and a **gear picker**. The selected gear defines the complete LOAD/CHASE/EXIT ladder at that moment:

| Gear | Name | 5-day range | LOAD | CHASE | Add size | Exit tiers |
|---|---|---|---|---|---|---|
| 1 | Smooth | ≤ 10% | −6% | −4% | ×1/2 | +1 / **+3** / +5% |
| 2 | Moderate | ≤ 15% | −7% | −5% | ×2/3 | +2 / **+4** / +6% |
| 3 | Balanced | ≤ 20% | −8% | −6% | ×3/4 | +3 / **+5** / +7% |
| 4 | Deep | ≤ 25% | −9% | −7% | ×4/5 | +4 / **+6** / +8% |
| 5 | Extreme | > 25% | −10% | −8% | ×1.0 | +5 / **+7** / +9% |

```
V = 100 × (High5 − Low5) / High5
```

**V is one number.** The span of the whole 5-day window as a percent of its high — not a per-day average. The card, the cockpit's gearbox strip and the candle panel all show this same figure, and the gear is read off it.

- **AUTO** tracks V continuously, deployed or not — a stock that turns violent mid-campaign gets a deeper ladder without being touched. On an EMPTY card the heavy-unit rule (§3.1) floors it.
- **MANUAL** holds whatever the commander picked.
- Either way a gear or tier change is saved as configuration and may enter the diagnostic application log, never `RECORDED FILLS`. Nothing has to be unwound: only the average cost is history, and the watched lines are recomputed from it on the spot.
- Gear identity uses red/orange/yellow/green/blue for G1–G5. In the cockpit, **only the selected Gear** uses its identity color; the other four remain neutral grey. The card carries a **big gear number** in that colour under its picker — across sixteen cards it is the mark that reads at a glance, which is why the cockpit does not need one.
- A card's exit tiers are plain **check boxes**; the cockpit's are coloured buttons. That difference is deliberate: the cockpit's tiers trade, the card's do not.

Bounds are inclusive: exactly 15.0% is gear 2, 15.1% is gear 3. G5's ×1.0 add size arms only above a 25% range.

### 3.2 The card's gear is not the bot's gear

They are separate numbers, and each means something different.

```text
CARD GRID                          AUTOPILOT
a worksheet for hand trading       the thing that actually trades
refreshes on Save & Refresh        refreshes every poll
its own gear and exit tiers        its OWN gear, AUTO flag, exit tiers
saved in positions.csv             saved with the campaign state
never moves a live line            moves every line

                 [sync to autopilot]  →  one way, on the button only
```

The autopilot's poll thread touches exactly **one** thing on the main window: the `AUTOPILOT` button's colour, which reports WATCH/LIVE. It never rewrites a card. **Save & Refresh** re-reads price, shares and average cost from the provider and re-sorts the grid; it sends nothing to the bot. The cockpit's Gear, AUTO and tier controls write to the engine; no card changes.

The reason is that a card should be usable for thinking. Trying a deeper gear on a card to see what the ladder would look like used to change what the bot was about to do — a question became an instruction. It also made the grid heavy, because every tick drove sixteen Tk cards, and at its worst left them unclickable behind the cockpit.

The cost is that the two can now disagree. That is the point, but it means the **cockpit** is the only place to read what the bot will actually do. The card's gear is a plan; the cockpit's gear is the order.

### 3.1 Heavy-unit entry floor

A stock whose single share eats a large fraction of a unit cannot run a shallow ladder — `×1/2` on 2 shares rounds to nothing useful. The **entry** gear — EMPTY cards only — is floored by `share_price / unit_cash`:

```
≤ 1.2 → G1 ok    > 1.2 → G2+    > 1.6 → G3+    > 2.0 → G4+    > 2.5 → G5 only
```

The card writes `▲heavy` next to the gear when this, not volatility, chose it.

---

## 4. LOAD (EMPTY stocks)

```
load_price  = vantage × (1 − gear.load%)
load_shares = max(1, round_half_up(unit_cash / load_price))
```

**The Vantage** is normally the **Dynamic High5** window: `max(the previous four completed-session highs, today's high so far)`. The live session is inside the window, so a peak made this morning lifts the LOAD line immediately. Only an immediate, proven bot full SELL with its actual execution price may temporarily replace Dynamic High5 with a same-day reload Vantage. A day can also be **pinned by hand** by clicking it on the cockpit's always-visible 5-day chart.

**Every stock loads one full unit of cash.** If a single share already costs more than a unit, the minimum of 1 share applies. The quantity is sized off the *trimmed* (actually orderable) price, so the card number is the order number.

An EMPTY card shows `Vantage:`, then a projected ladder `Load / Chase 1 / Chase 2` and the three exit tiers computed as if the load had filled. The Current value turns green once the price is at or below the LOAD.

### 4.1 Same-day reload

After an **immediate, proven bot full EXIT**, one fast reload arms at the **actual final sell fill −3%** (not the Gear's load drop). The card marks the Vantage `(reload)`. If it does not fill by the close it expires; the next trading day returns to Dynamic High5. An external, offline, mixed, or unknown full sell always resets to EMPTY/Dynamic High5 and never auto-arms reload.

---

## 5. CHASE (DEPLOYED stocks)

```
chase_price = actual_avg_cost × (1 − gear.chase%)
chase_qty   = max(1, round_half_up(actual_shares × gear.ratio))
```

The card shows three chase lines. They **cascade**: each level's shares are folded into the running average before the next is computed, so Chase 2 already assumes Chase 1 filled. The bot only ever arms the first one — after a fill, everything is recomputed from the broker's new real average.

**There is no capital cap.** The chase fires while the broker's cash covers it and stops when it does not; the line is then drawn muted and only the EXIT keeps being watched, until cash returns and it re-arms by itself. The 32-unit figure in the strategy manual's tables is a yardstick for comparing gears, not a limit.

---

## 6. EXIT (one tier, or a ladder)

The three tiers are a **multi-select**. Click them on the card or in the cockpit and the selection applies directly; the final armed tier silently stays on so the campaign always has an exit.

```
one armed     exit_price = avg × (1 + tier%)      qty = ALL shares
two armed     the holding halves across them
three armed   the holding thirds across them
```

The split gives any remainder to the **middle** tier first, then the low, then the high — so the centre is never smaller than the outsides (9 → 3/3/3, 5 → 2/2/1, 4 → 1/2/1, 1 → 0/1/0).

**One armed tier is the cleanest campaign** and stays the default: everything leaves at one line, the holding is zero, the campaign is unambiguously finished. But a ladder is supported because the buy side stays live throughout — sell a third into a bounce, watch it fall back, and the CHASE line is still there to buy into.

As tiers fill:

- a filled tier is **spent**; the remaining shares re-split across the tiers still armed
- if every armed tier is spent but shares remain, the ladder **restarts** on the remainder
- a **CHASE re-arms every tier** on the new, larger holding
- **the campaign is over only when the holding is zero**

Every tier shows its price whether armed or not — a disarmed one is faded, not shrunk, because it is a real number you compare against. An armed tier additionally shows the shares it would take, and is marked `▶`.

---

## 6.1 Autopilot

Every card carries one button:

```
[ AUTOPILOT ]   arms WATCH on that stock and opens its cockpit
```

**WATCH** polls, draws the lines and detects fills; it sends nothing, and says so when a line is crossed. **LIVE** lets the bot send the order by itself the moment the curve touches a line — regular market hours only, dropping back to WATCH at the close. Button color is the status: grey = off, blue = WATCH, red = LIVE.

There are no manual Buy/Sell buttons: the reason to run a bot is that the offer goes out when the curve touches the line. Trading by hand in the broker app stays fully supported — the bot detects it and folds it into the campaign.

The cockpit also carries a **gearbox strip** — AUTO, G1–G5, T1/T2/T3 — so the Gear and exit tiers can be changed where the campaign is being watched. It writes to the **bot's own** settings, which the engine picks up on the next poll and remembers across restarts. The card grid does not change with it: the cards are a worksheet for hand trading, and the only way a card setting reaches the bot is the card's **`sync to autopilot`** button. Only the selected Gear uses its red/orange/yellow/green/blue identity color; the other Gear buttons remain neutral. Exit buttons vary by their actual percentage.

Nothing rests in advance — an order goes out only when a line is actually crossed. An order that then rests unfilled is **re-priced by the bot itself** every poll, so a Gear shift never strands one. There is no broad Cancel control: WATCH and self-healing clean up only durably identified bot orders; visible app/web orders pause the ticker and are never cancelled by the bot.

The normal poll deliberately reads only **current price, broker shares/average, OPEN orders, and buying power**. It does not scan CLOSED history on every poll. Each newly observed share-quantity delta is accepted and logged once with the current broker average, using exact current bot-order evidence when that evidence is immediately available. The bot never rewrites that observation later from delayed history and never reconstructs hidden or net-zero round trips. One initial six-bar candle read supplies both completed-session Vantage data and the visible chart, including today's earlier high; later candle maintenance runs on five-minute boundaries, with failed/empty retries limited to once per minute.

If Toss gives an ambiguous placement response, the bot writes the client id to durable state **before** transmitting and pauses instead of sending a fresh identity. The LIVE/slot check and this durable binding are atomic: closing before them prevents transmission, while closing afterward retains cleanup ownership. A failed state write sends no order. A missing OPEN row is not treated as cancellation; exact WORKING detail can still identify and safely cancel that order, while terminal detail is required before replacement. A reported positive fill stays guarded until **proven bot quantity**, not an UNKNOWN/external/MIXED movement, reaches broker holdings. An inseparable overlap remains paused and can never arm a guessed reload. Repeated identical working acknowledgements do not rewrite the state file. Closing WATCH with no such pending order stops immediately, and reopening is not blocked by cleanup.

An external, offline, mixed, or unknown full sell resets the campaign to EMPTY with Dynamic High5 and no automatic reload. Only an immediate, proven bot full sell with an actual execution price may arm the same-day reload.

Dormant Toss app-side conditional/reserved orders are not visible in OPEN before they trigger. Do not combine one with LIVE on the same ticker; the bot can yield only to a manual order the broker exposes.

The daily v^ grid was removed on 2026-08-01 so this bot could be stabilised alone; its specification and a restore recipe are in [`Daily v^ Grid Autopilot Manual.md`](Daily%20v^%20Grid%20Autopilot%20Manual.md).

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

Cards form two stable groups. **DEPLOYED cards come first**, sorted by `(current − average) / average` from highest to lowest; positive gaps are red and negative gaps are blue. **EMPTY cards come second**, sorted by `(current − LOAD) / LOAD` from lowest to highest; negative/below-LOAD gaps are purple and positive/above-LOAD gaps are orange.

The watched-card sorter uses coarse gap keys and re-grids only when a key changes, at most once every 15 seconds. It does not rebuild the whole card grid, so fields being edited are not disturbed.

---

## 9. Real-Time Data

| Data | Source |
|------|--------|
| Current price, 5-day High/Low/closes/OHLC, previous close | Toss (default) or Yahoo Finance |
| Holdings, average cost, buying power, open orders | Toss account API |
| USD/KRW rate + 3-month average | Yahoo `USDKRW=X` |

- **Save & Refresh** (single main button): collect inputs → reconcile from Toss (or auto-promote/demote in manual mode) → rebuild → save CSV + config → fetch prices and FX in a background thread → recompute and re-sort.
- Auto-refresh once on launch. No timed refresh loop on the main panel.
- The **autopilot** polls independently every 5 s, but only for stocks being watched, and only that ticker's data. A failed poll skips the whole cycle and retries — nothing is placed or cancelled on missing data.
- The **gap** is `(current − average)/average × 100` on a DEPLOYED card and `(current − LOAD)/LOAD × 100` on an EMPTY card. DEPLOYED gaps use red/blue by sign; EMPTY gaps use purple below LOAD and orange above LOAD. The Current value turns green once LOAD is crossed.

---

## 10. The cockpit

`gui/campaign_window.py` carries the charts and the log:

- **Live tick curve** with the campaign's own lines — the armed LOAD/CHASE, broker average, and selected EXIT line(s) drawn bold; the next two projected chases and Vantage drawn soft. Its right-hand label area keeps long BUY/SELL WATCH/pseudo text visible. An unfundable buy line goes grey, relabelled `✕ … (no army)`.
- **Always-visible 5-day candle panel** beside it, carrying the same lines. Rising/unchanged OHLC rows are red and falling rows blue. While EMPTY, candle and OHLC rows visibly select and highlight a pinned Vantage; only a pin shows `Return to Dynamic High5`.
- **Banner** naming the gear, the campaign, the position, and then `▲ EXIT …` (every armed tier with its own price and portion) over `▼ next buy … then …`. A DEPLOYED campaign projects chases 2 and 3 beyond its armed chase; an EMPTY one projects chases 1 and 2 beyond its armed LOAD — the same ladder its card prints. An unfundable buy remains drawn in grey; when fresh buying power makes a reserve fundable again, its line automatically returns to its armed color. Only the highest-priority condition is shown.
- **Gearbox strip** — AUTO/MANUAL, G1–G5, the three tiers, `V`, and **Vantage** with its Dynamic/Pinned/Reload/Campaign source. Gear, tier, and Vantage choices are direct. Entering LIVE uses an **inline, modeless confirmation** inside the cockpit, so no modal dialog blocks charts or cleanup.
- **RECORDED FILLS** at the bottom: one immutable row per newly observed broker share-quantity delta, carrying the current average and grouped by trading day. Opening the window, adopting status, and changing Gear/tier/Vantage never add rows. A separate current EMPTY/DEPLOYED status remains visible when the list is empty. There is no `MANUALLY_MODIFIED` banner.

---

## 11. Persistence

### 11.1 `data/positions.csv`

```
ticker,tier,is_deployed,shares,avg_cost,cost_basis,gear,exit_tier,
load_gear,buy_pct,t1_pct,t2_pct,t3_pct,t1_active,t2_active,t3_active,
auto_mode,last_updated
```

`gear`, `auto_mode`, and `t1_active`/`t2_active`/`t3_active` are the live strategy fields; `exit_tier` is the lowest-armed compatibility value and the remaining gear columns are derived for back-compat. Created automatically (all tickers, shares = 0) on first run if missing. Legacy files (`A/B/C`, `L1`–`L7`, bait drop percents, and active sell tiers) are migrated on read without collapsing a valid multi-tier selection.

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

Campaign state per `ticker#VCG`: campaign id, gear, armed/spent exit tiers, Vantage, chase count, campaign low, peak deployment, order-reconciliation state, and the fill log. A restart re-arms from broker truth and durable bot ownership. Any v^ grid record left at a bare ticker key is untouched and is never restored as a campaign.

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
│   ├── vcommandos.py           — internal `V_COMMANDOS_GEARBOX` campaign engine
│   ├── data_feed.py            — yfinance wrappers (price, 5d OHLC, FX)
│   └── csv_io.py               — read/write CSV and config
├── providers/                  — Toss (default) and Yahoo market data
├── gui/
│   ├── main_window.py          — header, FX panel, card grid, refresh
│   ├── stock_row.py            — the campaign card (EMPTY + DEPLOYED)
│   ├── autopilot_ctrl.py       — poll thread, order execution
│   ├── campaign_window.py      — the cockpit
│   ├── stepper.py              — +/- stepper widget
│   └── candle_chart.py         — the candle panel
└── scripts/
    ├── test_vcommandos.py      — gearbox calculator + campaign engine (244)
    ├── test_autopilot_controller.py — polling and lifecycle boundaries (30)
    └── test_autopilot_ui.py    — cockpit presentation + real Tk/controller
                                  smoke coverage (141)
```

---

## 13. Version History

| Version | Date | Notes |
|---------|------|-------|
| 0.1 | 2026-03-27 | Initial specification. |
| 0.2 | — | Continuous LOAD gear (−4…−15%), buy 4/5/6% radios, sell-tier steppers, AUTO/MANUAL volatility gear, candlestick graph, KR/US bold styling, deployed-by-size & empty-by-volatility ordering, FX 3-month average + deviation. |
| 0.3 | 2026-06-19 | Added Oracle (ORCL). Every stock loads a full unit (Minor 0.5× tier retired). Deployed order normalized across currencies (KR no longer pinned to front). FX dollar-switch tracker: ±3% ladder, per-threshold amounts (\|k\|/6 of the 1/3 switch pool), and 7 clickable level buttons persisted as `fx_switch_level`. |
| 0.4 | 2026-08-01 | **Rolled back to the bait-based V-Commandos system, rebuilt as the five-speed Gearbox.** Card and bot unified — the card is the campaign's source of truth for gear and exit tier. Separate LOAD (−6…−10%) and CHASE (−4…−8%) drops. One armed exit tier selling the whole position (33/33/34 split retired). High5 vantage; same-day reload at the sell fill −3%. Volatility→gear cut points raised to 15/20/25/30; heavy-unit entry floor added. Two autopilot buttons per card (V-COMMANDOS / v^ grid), one strategy per stock. New `gear` / `exit_tier` CSV columns with legacy migration. The 2026-07-28 재편안 (slots, fronts, army caps, auto-trim) was **not** adopted — see `docs/경제축_전략재편_2026-07-28.md` §11. |

| 0.5 | 2026-08-01 | **No capital cap** — the army is the only wall on a chase. The gear is no longer pinned by a live campaign: AUTO tracks volatility throughout, and a shift is logged rather than blocked. The daily v^ grid was removed (recoverable from `e148da6`) so the campaign bot could be stabilised alone — one button per card. Manual Buy/Sell removed: the bot sends the order when the curve touches the line. Cockpit rebuilt around the price curve, showing the EXIT, the next buy with its size, and the two chases after it; the `RECORDED FILLS` log returned, grouped by trading day. Fixed the v0.4 defect where a leftover grid-only field made every poll throw before anything reached the screen. |

| 0.6 | 2026-08-01 | Gear ladder **10/15/20/25** (G5 above 25%). **V unified**: one figure — 100×(High5−Low5)/High5 — on the card, in the cockpit and on the candle panel; the panel's grid-era per-day average and `/3` hint removed. A FLAT stock now publishes **chase 1** as well as chase 2 (it was silently dropped from the chart). Gear and exit tier **choosable in the cockpit**, writing through to the card. `Cancel all` → `Cancel N resting`: disabled when nothing rests, and documented — the bot never re-prices a resting order, so a shifted gear needs the old one cleared. |

| 0.7 | 2026-08-01 | **Exit tiers are a multi-select again** — one for a clean full exit, two or three to leave in portions by the old distribution law; a filled tier is spent, a chase re-arms them all, and the campaign ends only at zero shares. The bot **re-prices its own resting orders** (the 443 `stale LOAD`/`stale SELL` rule), so a gear shift never strands one. The **vantage** is the Dynamic High5 window (four completed highs + today's live high, per the strategy manual's Appendix A), and can be pinned by clicking a day on the 5-day chart. The campaign log holds **trades only**. Cockpit: AUTO/MANUAL without forcing a gear pick, a vantage strip, taller charts, distinct day labels. **All cards ordered by gap.** |

| 0.8 | 2026-08-01 | Mixed manual/LIVE reconciliation made broker-authoritative: durable bot ownership, foreign-order pause, exact-fill retries, MIXED attribution, actual-fill-only reload, and no guessed offline sell. Removed broad Cancel and routine strategy confirmations. Made the 5-day Vantage picker permanent and explicit, restored blue FLAT-gap contrast, added Gear/exit color contrast, current empty/deployed log status, unclipped chart labels, and trade-only fill rows. |

| 0.9 | 2026-08-02 | User-facing states are **EMPTY / DEPLOYED** and the sole action is **AUTOPILOT**. DEPLOYED cards sort first by position gap (descending, red/blue); EMPTY cards follow by LOAD gap (ascending, purple below LOAD/orange above). Only the selected cockpit Gear is colored. LIVE confirmation is inline and modeless, and no `MANUALLY_MODIFIED` banner is shown. Normal polling reads price, holdings/average, OPEN orders, and buying power; one raw candle window feeds both Dynamic High5 and the chart, and a card state transition no longer fetches the whole catalogue. Each observed delta is accepted once with no delayed-history rewrite or net-zero reconstruction. Accepted identities survive OPEN/holdings lag and close-during-submit; UNKNOWN/MIXED movement never proves bot convergence or reload eligibility. External/offline/mixed/unknown full sells reset EMPTY/Dynamic High5 without reload; only an immediate proven bot full sell with an actual price may reload. WATCH closes immediately when no bot order is pending, and newly fundable reserve lines reactivate automatically. |

| 1.0 | 2026-08-02 | **The card grid and the autopilot are detached** (§3.2). The bot keeps its own Gear, AUTO flag and exit tiers, saved with its campaign; the cockpit writes to the engine, not to a card. The poll thread touches only the `AUTOPILOT` button's colour, so ticks no longer drive sixteen Tk cards — the freezing and the unclickable grid behind the cockpit go with it. Save & Refresh sends nothing to the bot. The new **`sync to autopilot`** button on each card is the one crossing, and only when pressed. The card's **big gear number** is back, and its exit tiers returned to plain **check boxes** to read differently from the cockpit's trading buttons. |

---

*This manual reflects the current implementation. When code and manual disagree, update this document. The strategy itself is specified in [Gearbox AUTOPILOT Manual](Gearbox%20V-Commandos%20Autopilot%20Manual.md).*
