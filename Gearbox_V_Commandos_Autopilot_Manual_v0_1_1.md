# Gearbox V-Commandos Autopilot Manual

**Version:** 0.1.1 — reconciled operational baseline
**Strategy ID:** `V_COMMANDOS_GEARBOX`
**Subtitle:** Fixed-Gear V-Campaign Trading System
**Status:** Strategy-origin specification, reconciled with the current mixed manual/LIVE safety contract. The current as-built UI and implementation record is `Gearbox V-Commandos Autopilot Manual.md`.

> **Operational precedence.** Broker quantity and average cost decide whether a campaign exists. Held shares are continued from broker truth. Zero shares plus a verified completed full SELL today arms the −3% reload from the actual fill. Zero shares without that evidence resets to Dynamic High5; no fill price is guessed. Card/app trading and LIVE automation use the same Gear, selected exit tiers, Vantage, and broker position. Opening a window records nothing.

---

## 1. Purpose

Gearbox V-Commandos Autopilot is a campaign-based trading system designed to:

```text
wait for a meaningful pullback,
LOAD one selected stock,
lower the actual average cost with a fixed reinforcement gear,
sell the entire position on the right side of the V,
recover the army,
and automatically prepare the next campaign.
```

The strategy does not try to capture every upward move. It intentionally waits for a left-side decline and trades the rebound.

The central design principle is:

> Observe the two ends of the V. Enter deeply enough on the left, keep the exit gap reachable on the right, and complete the entire campaign cleanly.

This system replaces deployment-step-driven gear adaptation with one visible five-speed gearbox. In AUTO, the selected Gear may continue to follow five-day volatility while WATCH or LIVE; in MANUAL, the commander may select any Gear before or during a campaign. Either choice simply moves the watched price lines for the next poll.

Gear and exit-tier choices are saved configuration, not trades, so they do not create rows in `RECORDED FILLS`. Actual buy and sell position changes are the events logged there.

---

## 2. Why This Strategy Exists

Earlier V-Commandos designs successfully lowered average cost and exited on rebounds, but execution became difficult when:

- army capital was divided across too many stocks;
- the Gear changed automatically as deployment increased;
- the user had to recalculate cards after every fill;
- partial exit tiers left small residual positions;
- the bot's internal Step count diverged from manual trades in the broker app.

The Gearbox design simplifies the system:

```text
actual broker quantity
+ actual broker average cost
+ selected Gear
+ selected Exit Tier(s)
= next CHASE line and selected EXIT line(s)
```

The bot does not need perfect knowledge of every historical Step to continue safely. It can adopt or reconcile a position already traded manually.

---

## 3. Relationship to Other Strategy Modes

The daily bidirectional grid remains a recoverable historical strategy, but it does not run alongside Gearbox V-Commandos in the current app.

```text
V_COMMANDOS_GEARBOX
    Campaign-based
    BUY low → chase average → full SELL
    Designed for one V cycle
    High5 or campaign Vantage
    Full-position exit

DAILY_V_HAT_LINEAR_GRID
    Daily coordinate-grid strategy
    BUY-then-SELL and SELL-then-BUY
    Designed to scalp repeated intraday oscillation
    Daily anchor and inventory targets
```

The broker/account concepts may be shared, but two engines must not trace or manage the same holding at the same time. The current application therefore exposes one card and one Gearbox autopilot engine per ticker.

---

## 4. Core Philosophy

### 4.1 Campaign Thinking

A campaign begins when LOAD fills and ends only when actual broker holdings become zero.

```text
FLAT
→ ARMED
→ LOAD
→ CHASE as needed
→ full EXIT
→ FLAT
```

### 4.2 One Clean Exit by Default; Optional Tiered Exit

One selected Exit Tier is the default and sells the entire position. This is the recommended, simplest campaign.

The commander may instead arm two or all three tiers. The actual holding is split as evenly as possible, with a remainder assigned to the middle tier first, then low, then high. Filled tiers are spent; a later CHASE re-arms the selected ladder. The campaign is complete only when broker quantity is zero.

Reasons to prefer the one-shot default:

- partial exits create residual positions;
- the remaining average and profit accounting become harder to interpret;
- campaign completion becomes ambiguous;
- reloading and automated state transitions become more complex.

### 4.3 Long-Term Quality Still Matters

The system trades short V cycles, but it should prefer stocks that remain credible long-term holdings if a campaign becomes slow.

The expected long-term drift is a secondary protection layer, not the trigger.

### 4.4 Missed Upside Is an Accepted Cost

If a stock rises continuously without reaching LOAD, the system may not enter.

This is not a bug. The strategy pays for safety by refusing to chase upward prices.

A broader candidate universe and a seesaw-style battlefield rotation process are used to find another stock currently forming a left-side decline.

---

## 5. Unit System

One unit is a user-defined amount of capital.

Examples:

```text
1 unit = KRW 1,000,000
1 unit = KRW 800,000
1 unit = USD 700
```

The normalized Gear tables remain valid when the cash value of one unit changes.

Only these values change:

```text
unit_cash
initial LOAD share quantity
actual integer reinforcement quantities
```

The ratio structure remains unchanged.

### 5.1 Initial LOAD Quantity

```text
load_qty = round_half_up(unit_cash / planned_load_price)
minimum load_qty = 1 share
```

### 5.2 Reinforcement Quantity

```text
chase_qty = round_half_up(actual_current_shares × gear_add_ratio)
minimum chase_qty = 1 share
```

The program must calculate the new average and all next lines from actual broker fills, not theoretical table values.

---

## 6. Five-Speed Gearbox

| Gear | Indicative 5D range | LOAD | CHASE | Add ratio | Exit Tiers | Default | Max chase under 32u | Final BUY | Capital |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | ≤ 10% | −6% | −4% | ×1/2 | 1%/3%/5% | Tier 2 | 8 | 82.15 | 23.02u |
| 2 | >10%–15% | −7% | −5% | ×2/3 | 2%/4%/6% | Tier 2 | 7 | 78.26 | 31.01u |
| 3 | >15%–20% | −8% | −6% | ×3/4 | 3%/5%/7% | Tier 2 | 6 | 75.92 | 24.57u |
| 4 | >20%–25% | −9% | −7% | ×4/5 | 4%/6%/8% | Tier 2 | 6 | 72.26 | 28.14u |
| 5 | > 25% | −10% | −8% | ×1.0 | 5%/7%/9% | Tier 2 | 5 | 70.33 | 26.09u |


The indicative 5-day range is a starting recommendation, not a mandatory classification rule.

Market structure, recent rebound behavior, campaign capital, and the user's desired risk level may justify a lower or higher Gear.

A stock identity does not permanently determine its Gear. For example:

```text
SK hynix may use Gear 2 in a calm period.
SK hynix may use Gear 4 or 5 during an extreme volatility regime.
Alphabet may use Gear 1 or 2.
Samsung may use Gear 2 or 3 depending on the observed V depth.
```

---

## 7. Exit Tier System

Each Gear has three selectable Exit Tiers.

| Gear | Tier 1 — Fast Recovery | Tier 2 — Default | Tier 3 — Patient / Higher Reward |
|---:|---:|---:|---:|
| 1 | +1% | **+3%** | +5% |
| 2 | +2% | **+4%** | +6% |
| 3 | +3% | **+5%** | +7% |
| 4 | +4% | **+6%** | +8% |
| 5 | +5% | **+7%** | +9% |

### 7.1 Selection Rule

```text
Tier 1:
    choose when capital recovery speed matters most,
    the campaign has already lasted too long,
    or the expected rebound is weak.

Tier 2:
    default choice,
    balance between completion probability and campaign return.

Tier 3:
    choose when the stock historically produces a strong right-side rebound,
    when the campaign may remain open longer,
    or when a higher volatility premium is deliberately required.
```

### 7.2 One-Shot Default and Multi-Select

At least one Tier is always armed.

```text
one tier     exit_price = actual_average_cost × (1 + selected_exit_rate)
             sell_qty = all actual shares

two/three    each selected tier has its own exit price
             actual shares are divided across the selected tiers
```

Tier and Gear selections apply directly and may change while WATCH or LIVE is calculating. They are saved preferences, not trades, and do not write campaign-fill rows. Entering LIVE is the confirmation boundary.

---

## 8. Army and Concurrency Policy

### 8.1 The Army Is the Implemented Wall

There is no per-campaign capital cap. Before each LOAD or CHASE, the engine compares that one order's cost with current broker buying power:

```text
order is fundable       → the buy may fire when crossed
order is not fundable   → keep the line visible, disable that buy,
                          and continue watching selected SELL lines
```

Funding is checked again every poll, so a buy can become available when another campaign returns cash. The normalized 32-unit tables are reference comparisons, not an enforced ceiling.

### 8.2 Concurrency Is Commander's Discipline

The operational preference remains fewer simultaneous battlefields for deep Gears and at most a small number for shallow Gears. The application does not yet implement a global capital-reservation manager, however. Multiple campaigns can compete for the same buying power; the first actual order consumes it and later unfundable buys remain visibly off.

The manual must not claim that maximum chase count, campaign caps, pre-funding, or global reservation are protections when the code does not enforce them.

---

## 9. Vantage Point and LOAD

### 9.1 Standard Flat-State Vantage

Default:

```text
watch_vantage = max(previous four completed-session highs,
                    current-session high so far)
```

While FLAT, the bot updates this Dynamic High5. A candle high may be pinned explicitly while FLAT; the UI labels the state `Pinned` and offers `Return to Dynamic High5`. Pinning is not available while shares are deployed.

At LOAD fill:

```text
campaign_vantage = the Vantage that generated the LOAD
campaign_vantage becomes frozen
```

The campaign does not reset its Vantage each day.

### 9.2 Standard LOAD

```text
load_price = campaign_vantage × (1 - gear_load_drop)
load_size ≈ 1 unit
```

### 9.3 Same-Day Reload

After a successful full EXIT:

```text
reload_anchor = actual final sell fill
same_day_reload_price = reload_anchor × 0.97
```

This is a special fast-reload rule and does not use the normal Gear LOAD drop.

If the reload fills, a new campaign begins using the currently selected Gear and Exit Tier.

If it does not fill by the end of the session, the order expires or is cancelled according to broker capability. The next trading day returns to the normal flat-state Vantage process.

---

## 10. CHASE and EXIT Calculations

At every account refresh:

```text
actual_qty = broker-reported current shares
actual_avg = broker-reported actual average cost
actual_cost_basis = broker-reported or reconstructed cost basis
```

Then:

```text
next_chase_price
= actual_avg × (1 - selected_gear.chase_drop)

next_chase_qty
= round_half_up(actual_qty × selected_gear.add_ratio)

exit_price
= actual_avg × (1 + selected_exit_rate)

exit_qty
= actual_qty
```

The bot normally watches one buy line and the selected sell line or lines:

```text
next CHASE BUY
one-shot or tiered EXIT SELL
```

If broker buying power makes the next CHASE unaffordable:

```text
disable CHASE
continue watching EXIT only
```

---

## 11. Gear Changes During a Campaign

AUTO follows the current five-day volatility recommendation; MANUAL holds the selected Gear. Either may be changed while watching because the Gear describes lines calculated from the real broker average, not a reconstructed Step.

A manual Gear override may be applied because a fixed Gear does not depend on reconstructing the entire historical ladder.

After a Gear change:

```text
keep actual holdings
keep actual average cost
keep campaign ID
replace next CHASE line using the new Gear
replace every selected EXIT using the new Gear's tier percentages
save the preference; do not create a campaign-fill row
```

The default recommendation remains:

> Keep one Gear for one campaign whenever possible so that backtests and live results remain interpretable.

---

## 12. Manual App Trading and Bot Resumption

The fixed-Gear architecture supports mixed app/bot operation better than the legacy Step-dependent architecture.

### 12.1 Broker Data Is the Source of Truth

At startup or resume, read:

```text
actual holdings
actual average cost
actual buying power
open orders
recent fills when available
```

### 12.2 ADOPT_POSITION

If actual shares are positive but no active bot campaign exists, the bot adopts the real position on the first complete broker snapshot. The selected Gear and tiers are the saved card preferences; a campaign Vantage and capital cap are not required to calculate CHASE/EXIT from actual average cost.

It does not need to know whether the user manually completed Step 2, Step 3, or Step 4.

### 12.3 External BUY

If a manual BUY changes holdings or average cost:

```text
refresh actual qty and average
recalculate next CHASE
recalculate selected EXIT lines
record every definite quantity change; use actual execution price when known
```

### 12.4 External Partial SELL

A partial manual SELL is not part of the normal strategy.

If detected:

```text
refresh broker state
recalculate lines from the remaining position
flag campaign as MANUALLY_MODIFIED
record every definite quantity change; show an unavailable price as --
```

### 12.5 External Full SELL

If broker holdings become zero:

```text
cancel only bot-owned remaining orders

if a completed full SELL fill from today is verified:
    complete and record the campaign at the actual fill
    arm same-day reload at actual fill × 0.97

otherwise:
    clear stale campaign assumptions
    do not guess a fill or reload anchor
    resume Dynamic High5
```

### 12.6 Order Ownership

Bot orders are identified by:

```text
broker_order_id
client_order_id or strategy tag, when supported
```

A strategy-prefixed client id is atomically persisted and flushed before transmission; a failed persistence write sends no order. Definite rejection clears it; a timeout, server failure, or success response without an order id remains an outcome-ambiguous pending intent and is retried only with that same client id. Exact order detail is checked on every poll, including polls where holdings do not move. Client-id-only shutdown recovery also reuses that identity, and a recovered working order is cancelled before cleanup can finish. If no broker order id is recoverable, only the shutdown path may retire the identity: its 10-minute idempotency window must expire, three successful exhaustive OPEN reads must show no working order, and the retired state must save successfully.

The bot must never cancel an order solely because it looks similar to its own order. Any foreign app/web order pauses bot placement for that ticker until it clears. A stale bot-owned order is replaced only after cancellation is confirmed; partial fills are reconciled first.

If holdings move while a bot order remains unresolved, exact order detail is retried before the holdings watermark advances. In one interval containing both bot and manual fills, only broker-proven bot quantity is attributed to BOT and the residual delta is marked MIXED. After bounded evidence failure, the real quantity is accepted on a non-trading reconciliation poll and its source is marked UNKNOWN, but the accepted intent remains guarded; delayed exact detail repairs the row in place. Missing from OPEN is not proof of cancellation. Only exact terminal detail releases the identity for replacement, and no fill or reload price is guessed.

CLOSED evidence is scanned through a bounded overlapping time window and deduplicated. All cursor pages must complete before the interval is considered complete; a later-page failure, malformed cursor, or `closed-not-supported` result is never replayed as a partial set. Exact known-order detail remains independently usable. A complete set whose gross executions exactly reconciles the holding may record opposing bot/manual fills or a net-zero round trip. Stale same-side history outside the transition window is never reused as the current execution or reload anchor.

Toss does not expose dormant app-side conditional/reserved orders in the OPEN-order feed before they trigger. Such an invisible instruction must not be armed on the same ticker while autopilot is LIVE. Once an app/web order is visible as OPEN, the bot safely pauses the whole ticker and never cancels that foreign order.

---

## 13. Campaign State Machine

```text
OFF
    no order actions

FLAT
    no shares
    calculate Vantage and Gear recommendation

ARMED_LOAD
    watch or maintain one LOAD order

DEPLOYED
    actual shares > 0
    watch CHASE and full EXIT

CHASE_PENDING
    wait for submitted BUY to resolve
    no duplicate CHASE

EXIT_PENDING
    wait for full SELL to resolve
    no new BUY unless the SELL fails or is cancelled

COMPLETED
    actual shares == 0
    finalize campaign metrics

RELOAD_ARMED
    watch actual final sell price −3%

PAUSED_RECONCILE
    broker state and bot state disagree
    no new order until resolved

PAUSED_MANUAL_ORDER
    a visible app/web order exists
    preserve it, clear any bot-owned resting order, and place nothing until clear
```

---

## 14. Campaign Fill Log

Every campaign has one persistent campaign ID.

### 14.1 Campaign Header

```text
campaign_id
ticker
market
currency
strategy_mode
selected_gear
selected_exit_tiers
campaign_vantage
standard_load_price
unit_cash
start_timestamp
start_trading_date
status
```

### 14.2 Fill Record

```text
fill_timestamp
source: BOT / EXTERNAL / MIXED / UNKNOWN
broker_order_id
client_order_id
side
event_type: LOAD / CHASE / EXIT / RELOAD / ADJUSTMENT
planned_price
actual_fill_price
planned_qty
actual_fill_qty
qty_before
qty_after
average_before
average_after
cost_basis_after
next_chase_price_after
exit_price_after
```

### 14.3 What Is Not a Fill

```text
window open
position adoption/status announcement
Gear or tier selection
Vantage pin/reset
WATCH/LIVE selection
```

These may appear in the diagnostic application log but never in the cockpit's campaign fill list. Reopening a window must not append “N shares deployed.” The empty list is accompanied by current FLAT/DEPLOYED status so it does not conceal the real position.

### 14.4 Completion Metrics

```text
campaign_end_timestamp
trading_days_open
calendar_days_open
maximum_chase_count
maximum_capital_used
campaign_low
maximum_drawdown_from_vantage
bottom_to_exit_rebound
gross_profit
fees
taxes
net_profit
return_on_max_capital
```

---

## 15. Cockpit and Charts

The cockpit shows the live stream chart and the 5-day candle chart by default; there is no separate 5-day chart button. Both show the same active BUY, average, selected SELL, projection, and Vantage lines used by the card and engine.

While FLAT, the candle panel explicitly invites selection of a candle HIGH as a pinned Vantage and highlights the chosen candle/OHLC row. The state says `Dynamic High5`, `Pinned`, `Reload`, or `Campaign`; `Vantage` is written in full instead of `V.P.`. A pinned state alone exposes `Return to Dynamic High5`. Pinning is disabled while deployed.

Gear buttons use G1 red, G2 orange, G3 yellow, G4 green, and G5 blue. Exit-tier buttons distinguish armed, unarmed, and spent states. Five-day OHLC text follows the historical convention: red for a rising or unchanged candle and blue for a falling candle. Long BUY/SELL annotations reserve or clamp to a label area so their quantity and WATCH/pseudo suffix are not clipped.

Gear, tier, and Vantage choices apply without routine confirmation dialogs. Entering LIVE remains confirmed. The cockpit has no broad Cancel button; automatic cleanup touches only bot-owned orders. An unfundable buy remains visible and muted on the graph while sell watching continues, and the status banner selects one highest-priority condition rather than displaying contradictory messages.

The current implementation uses the live curve plus the always-visible 5-day panel and day-grouped fills. A future full campaign-spanning chart may add every historical fill, campaign low, and completion marker without changing the trading rules.

---

## 16. Accounting Views

The system should distinguish:

```text
realized campaign profit
current unrealized P&L
maximum deployed capital
capital still free
fees and taxes
campaign duration
```

The strategy is not evaluated only by nominal campaign return.

Important comparison metrics:

```text
campaign completion rate
median trading days to exit
5-day completion rate
10-day completion rate
maximum drawdown
return on maximum deployed capital
annualized capital rotation
comparison with buy-and-hold over the same period
```

---

## 17. Gear Selection Dashboard

The recommended Gear may use:

```text
5D range = (High5 - Low5) / High5
recent low-to-rebound distribution
median V duration
frequency of 10%, 15%, 20%, 25%, and 30% declines
frequency of the rebound required by the chosen Gear and Tier
```

Initial recommendation map:

```text
5D range ≤ 10%       → Gear 1
above 10% to 15%     → Gear 2
above 15% to 20%     → Gear 3
above 20% to 25%     → Gear 4
above 25%            → Gear 5
```

This is a recommendation, not an automatic order command.

The user should be able to select a more conservative Gear when market conditions warrant it.

---

## 18. Operational Safety Rules

1. **Broker state is authoritative.**
2. **One accepted pending strategy order at a time per side.**
3. **No duplicate CHASE while a BUY is unresolved; reconcile partial fills first.**
4. **No new campaign until actual shares are zero.**
5. **Never assume a fill price.**
6. **Recalculate from actual average cost after every fill.**
7. **Do not send an unfundable LOAD or CHASE; keep its line visible.**
8. **The army is the implemented wall; the reference table is not a cap.**
9. **Cancel only durably identified bot-owned orders; pause around foreign orders.**
10. **On ambiguous reconciliation, pause instead of guessing.**
11. **Use WATCH and the offline simulator before LIVE.**
12. **Opening a window or changing a preference is not a campaign fill.**
13. **Never arm a reload from an estimated or intended sell price.**

---

## 19. Runtime Modes

```text
WATCH
    calculate and display only

LIVE
    place real broker orders
```

The same strategy engine calculates both modes. Offline path tests provide deterministic simulation coverage; a separate interactive DRY mode is not implemented.

---

## 20. Configuration Baseline

```yaml
strategy_mode: V_COMMANDOS_GEARBOX

ticker: null
market: null
currency: null

unit_cash: 1000000

gear: 3
exit_tiers: [false, true, false]
auto_mode: true

vantage_mode: HIGH5
high_window: 5

same_day_reload_enabled: true
same_day_reload_drop: 0.03

full_exit_only: false            # one armed tier is still the default
allow_manual_gear_override: true
allow_manual_tier_override: true

rounding_mode: HALF_UP
minimum_order_qty: 1
```

---

## 21. Maximum-Deployment Comparison

The following rows use Vantage 100, theoretical fractional sizing, 32 units as a common reference scale, no fees, and exact fills. The number is not a live campaign cap.

| Gear | Final average | T1 EXIT | T2 EXIT | T3 EXIT | Rebound to T2 | T2 gross profit |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 84.43 | 85.27 | 86.96 | 88.65 | 5.86% | 0.691u |
| 2 | 80.74 | 82.35 | 83.97 | 85.58 | 7.28% | 1.240u |
| 3 | 78.69 | 81.05 | 82.62 | 84.20 | 8.83% | 1.228u |
| 4 | 75.28 | 78.29 | 79.80 | 81.30 | 10.43% | 1.688u |
| 5 | 73.38 | 77.05 | 78.52 | 79.99 | 11.65% | 1.826u |


The observed 15% left-end/right-end gap remains a useful diagnostic, but it is not a hard trading rule.

Higher Gears can tolerate a lower right-side endpoint because they lower average cost more aggressively. They also require larger rebound amplitudes at higher Exit Tiers.

---

# Part II — Normalized Gear Reference Tables

These tables are long-term normalized references.

Assumptions:

```text
Vantage = 100
initial LOAD capital = 1 unit
fractional theoretical sizing
32 units of spend = comparison point, not a live campaign cap
no fees, taxes, slippage, or integer-share rounding
```

Live calculations must use actual broker quantities and actual fills.

---

## Gear 1 — Smooth
```text
Standard LOAD: Vantage −6%
CHASE: actual average cost −4%
Additional size: current shares × 1/2
Exit Tier 1 / 2 / 3: +1% / +3% / +5%
Default: Tier 2
```

| Step | BUY price | Added qty | Total qty | New average | T1 EXIT | T2 EXIT | T3 EXIT | Capital used |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LOAD | 94.00 | 1.000 | 1.000 | 94.00 | 94.94 | 96.82 | 98.70 | 1.00u |
| 1 | 90.24 | 0.500 | 1.500 | 92.75 | 93.67 | 95.53 | 97.38 | 1.48u |
| 2 | 89.04 | 0.750 | 2.250 | 91.51 | 92.43 | 94.26 | 96.09 | 2.19u |
| 3 | 87.85 | 1.125 | 3.375 | 90.29 | 91.19 | 93.00 | 94.80 | 3.24u |
| 4 | 86.68 | 1.688 | 5.062 | 89.09 | 89.98 | 91.76 | 93.54 | 4.80u |
| 5 | 85.52 | 2.531 | 7.594 | 87.90 | 88.78 | 90.54 | 92.29 | 7.10u |
| 6 | 84.38 | 3.797 | 11.391 | 86.73 | 87.59 | 89.33 | 91.06 | 10.51u |
| 7 | 83.26 | 5.695 | 17.086 | 85.57 | 86.43 | 88.14 | 89.85 | 15.55u |
| 8 | 82.15 | 8.543 | 25.629 | 84.43 | 85.27 | 86.96 | 88.65 | 23.02u |

Maximum fully funded row under the 32-unit reference limit:

- Final theoretical BUY price: **82.15** (-17.85% from Vantage 100)
- Capital used: **23.02u**
- Bottom-to-exit rebound: **3.81% / 5.86% / 7.92%** for Tier 1 / 2 / 3
- Gross target profit: **0.230u / 0.691u / 1.151u**


## Gear 2 — Moderate
```text
Standard LOAD: Vantage −7%
CHASE: actual average cost −5%
Additional size: current shares × 2/3
Exit Tier 1 / 2 / 3: +2% / +4% / +6%
Default: Tier 2
```

| Step | BUY price | Added qty | Total qty | New average | T1 EXIT | T2 EXIT | T3 EXIT | Capital used |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LOAD | 93.00 | 1.000 | 1.000 | 93.00 | 94.86 | 96.72 | 98.58 | 1.00u |
| 1 | 88.35 | 0.667 | 1.667 | 91.14 | 92.96 | 94.79 | 96.61 | 1.63u |
| 2 | 86.58 | 1.111 | 2.778 | 89.32 | 91.10 | 92.89 | 94.68 | 2.67u |
| 3 | 84.85 | 1.852 | 4.630 | 87.53 | 89.28 | 91.03 | 92.78 | 4.36u |
| 4 | 83.15 | 3.086 | 7.716 | 85.78 | 87.50 | 89.21 | 90.93 | 7.12u |
| 5 | 81.49 | 5.144 | 12.860 | 84.06 | 85.75 | 87.43 | 89.11 | 11.62u |
| 6 | 79.86 | 8.573 | 21.433 | 82.38 | 84.03 | 85.68 | 87.33 | 18.99u |
| 7 | 78.26 | 14.289 | 35.722 | 80.74 | 82.35 | 83.97 | 85.58 | 31.01u |

Maximum fully funded row under the 32-unit reference limit:

- Final theoretical BUY price: **78.26** (-21.74% from Vantage 100)
- Capital used: **31.01u**
- Bottom-to-exit rebound: **5.22% / 7.28% / 9.35%** for Tier 1 / 2 / 3
- Gross target profit: **0.620u / 1.240u / 1.861u**


## Gear 3 — Balanced
```text
Standard LOAD: Vantage −8%
CHASE: actual average cost −6%
Additional size: current shares × 3/4
Exit Tier 1 / 2 / 3: +3% / +5% / +7%
Default: Tier 2
```

| Step | BUY price | Added qty | Total qty | New average | T1 EXIT | T2 EXIT | T3 EXIT | Capital used |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LOAD | 92.00 | 1.000 | 1.000 | 92.00 | 94.76 | 96.60 | 98.44 | 1.00u |
| 1 | 86.48 | 0.750 | 1.750 | 89.63 | 92.32 | 94.12 | 95.91 | 1.70u |
| 2 | 84.26 | 1.312 | 3.062 | 87.33 | 89.95 | 91.70 | 93.44 | 2.91u |
| 3 | 82.09 | 2.297 | 5.359 | 85.08 | 87.64 | 89.34 | 91.04 | 4.96u |
| 4 | 79.98 | 4.020 | 9.379 | 82.90 | 85.38 | 87.04 | 88.70 | 8.45u |
| 5 | 77.92 | 7.034 | 16.413 | 80.76 | 83.19 | 84.80 | 86.42 | 14.41u |
| 6 | 75.92 | 12.310 | 28.723 | 78.69 | 81.05 | 82.62 | 84.20 | 24.57u |

Maximum fully funded row under the 32-unit reference limit:

- Final theoretical BUY price: **75.92** (-24.08% from Vantage 100)
- Capital used: **24.57u**
- Bottom-to-exit rebound: **6.76% / 8.83% / 10.90%** for Tier 1 / 2 / 3
- Gross target profit: **0.737u / 1.228u / 1.720u**


## Gear 4 — Deep
```text
Standard LOAD: Vantage −9%
CHASE: actual average cost −7%
Additional size: current shares × 4/5
Exit Tier 1 / 2 / 3: +4% / +6% / +8%
Default: Tier 2
```

| Step | BUY price | Added qty | Total qty | New average | T1 EXIT | T2 EXIT | T3 EXIT | Capital used |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LOAD | 91.00 | 1.000 | 1.000 | 91.00 | 94.64 | 96.46 | 98.28 | 1.00u |
| 1 | 84.63 | 0.800 | 1.800 | 88.17 | 91.70 | 93.46 | 95.22 | 1.74u |
| 2 | 82.00 | 1.440 | 3.240 | 85.43 | 88.84 | 90.55 | 92.26 | 3.04u |
| 3 | 79.45 | 2.592 | 5.832 | 82.77 | 86.08 | 87.73 | 89.39 | 5.30u |
| 4 | 76.97 | 4.666 | 10.498 | 80.19 | 83.40 | 85.00 | 86.61 | 9.25u |
| 5 | 74.58 | 8.398 | 18.896 | 77.70 | 80.81 | 82.36 | 83.91 | 16.13u |
| 6 | 72.26 | 15.117 | 34.012 | 75.28 | 78.29 | 79.80 | 81.30 | 28.14u |

Maximum fully funded row under the 32-unit reference limit:

- Final theoretical BUY price: **72.26** (-27.74% from Vantage 100)
- Capital used: **28.14u**
- Bottom-to-exit rebound: **8.35% / 10.43% / 12.52%** for Tier 1 / 2 / 3
- Gross target profit: **1.125u / 1.688u / 2.251u**


## Gear 5 — Extreme
```text
Standard LOAD: Vantage −10%
CHASE: actual average cost −8%
Additional size: current shares × 1
Exit Tier 1 / 2 / 3: +5% / +7% / +9%
Default: Tier 2
```

| Step | BUY price | Added qty | Total qty | New average | T1 EXIT | T2 EXIT | T3 EXIT | Capital used |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LOAD | 90.00 | 1.000 | 1.000 | 90.00 | 94.50 | 96.30 | 98.10 | 1.00u |
| 1 | 82.80 | 1.000 | 2.000 | 86.40 | 90.72 | 92.45 | 94.18 | 1.92u |
| 2 | 79.49 | 2.000 | 4.000 | 82.94 | 87.09 | 88.75 | 90.41 | 3.69u |
| 3 | 76.31 | 4.000 | 8.000 | 79.63 | 83.61 | 85.20 | 86.79 | 7.08u |
| 4 | 73.26 | 8.000 | 16.000 | 76.44 | 80.26 | 81.79 | 83.32 | 13.59u |
| 5 | 70.33 | 16.000 | 32.000 | 73.38 | 77.05 | 78.52 | 79.99 | 26.09u |

Maximum fully funded row under the 32-unit reference limit:

- Final theoretical BUY price: **70.33** (-29.67% from Vantage 100)
- Capital used: **26.09u**
- Bottom-to-exit rebound: **9.57% / 11.65% / 13.74%** for Tier 1 / 2 / 3
- Gross target profit: **1.305u / 1.826u / 2.348u**



---

# Part III — Campaign Card

## Gearbox V-Commandos Campaign Card

```text
Ticker:
Market:
Currency:

Campaign ID:
Campaign status:

Vantage:
Selected Gear:
Selected Exit Tier(s):

Unit cash:
Actual buying power:

Actual shares:
Actual average cost:
Actual cost basis:

Next CHASE:
CHASE quantity:

Selected EXIT line(s):
Expected gross profit:

Campaign start:
Trading day number:
Maximum Step reached:
Campaign low:

Bot mode:
Order status:
Reconciliation status:
```

The card is a display and audit surface. While FLAT its gap is `(LOAD − current) / current`; while DEPLOYED it is the raw position move `(current − broker average) / broker average`. Cards sort globally from the larger displayed gap to the smaller. Gap color uses one historical blue family with lightness contrast. The V-COMMANDOS action sits on a separately spaced row below the calculations.

The bot must derive its live decisions from broker state and configuration, not from manually maintained card arithmetic.

---

# Part IV — Implementation Priorities

## Phase 1 — Deterministic Calculator

Implement and test:

```text
Gear parameters
Tier parameters
LOAD calculation
CHASE price
CHASE quantity
EXIT price
buying-power gating
normalized tables
integer rounding
```

## Phase 2 — Campaign Simulator

Support:

```text
OHLC or intraday price paths
LOAD/CHASE/EXIT fills
campaign duration
maximum capital
gear and tier comparisons
fees and taxes
same-day reload
```

When one candle touches CHASE and EXIT, intraday data is required to determine order sequence. If sequence is unavailable, mark the case ambiguous.

## Phase 3 — WATCH and Offline Regression

Implement:

```text
broker/account snapshots
state machine
campaign log
reconciliation
deterministic simulated order placement in the test harness
campaign chart
```

## Phase 4 — LIVE Execution

Begin with:

```text
one ticker
small unit cash
one Gear
Tier 2
verify card-only trades in WATCH, then verify LIVE on the same lines
strict order ownership
```

Manual app/web trades and bot trades may coexist in one campaign after this verification: broker state reconciles both. A visible foreign resting order pauses bot placement, so the two paths do not fight over the ticker. Dormant app-side conditional/reserved instructions are the exception because Toss does not expose them in OPEN before triggering; do not combine them with LIVE on the same ticker.

## Phase 5 — Portfolio Orchestrator

Add:

```text
candidate battlefield ranking
Gear recommendation
KRW/USD army separation
multi-campaign limits for Gear 1–2
one-battlefield enforcement for Gear 3–5
```

---

# Part V — Broker Evidence and Remaining Verification

The execution adapter uses these broker facts:

```text
real-time holdings
actual average cost
buying power
open orders
order IDs
client order IDs or tags
fill timestamp
fill quantity
fill price
partial-fill status
order cancellation
exact order detail for a known order ID
optional completed-order history
regular-session close and High5 data
```

The bundled Toss contract exposes exact order detail for a known bot order in every lifecycle state. Its CLOSED-list filter may return `400 closed-not-supported`, so arbitrary app-side historical fills are optional evidence rather than an assumption. A real quantity change still enters the campaign log; a missing execution price stays `--` and can never arm a guessed reload.

App-side conditional/reserved instructions also remain invisible to OPEN until they trigger. This is why they cannot safely share a ticker with LIVE even though ordinary visible app/web OPEN orders can.

The remaining rules are:

```text
do not infer broker capabilities
do not cancel unidentified orders
do not assume manual and bot fills can always be distinguished by price alone
pause and retry exact evidence before finalizing an ambiguous bot transition
```

---

## Final Strategy Summary

> Select AUTO or a Gear before battle; change the watched Gear deliberately when desired.
> LOAD only after the required pullback.
> Reinforce from the actual average cost using the selected Gear.
> Keep one Exit Tier for the default full-position shot, or deliberately arm two or three for staged exits.
> Recover the army, record the completed V, and automatically prepare the next LOAD or same-day reload.
> Use lower Gears for shallow, calmer V cycles and higher Gears for deep, high-amplitude V cycles.
> Use the card numbers manually or let LIVE chase the same lines; reconcile both from broker truth and never guess a missing fill.

---

# Appendix A — Simplified LOAD Reset and Resume Protocol

**Appendix status:** Operational baseline
**Applies to:** `V_COMMANDOS_GEARBOX`
**Purpose:** Define how the bot restores LOAD behavior after a campaign ends, after manual app trading, or after the bot has been offline.

This appendix deliberately favors a simple and reproducible rule over exact reconstruction of an old campaign’s historical end timestamp.

## A.1 Governing Principle

At startup or resume, the current broker position determines the strategic state.

```text
actual shares > 0
    → a campaign is active

actual shares == 0
    → no campaign is active
    → reset to FLAT
```

If the bot was offline while the user completed a campaign in the broker app, the bot does not need to reconstruct the exact historical campaign-end date before it can resume normal operation.

The simplified rule is:

> If no shares remain, archive the previous campaign as closed and rebuild the next LOAD from the current market state.

This avoids unnecessary dependence on a complete replay of old fills.

## A.2 Startup and Resume Priority

The bot must evaluate the following cases in order.

### Case 1 — Shares Are Still Held

```text
actual_qty > 0
```

Action:

```text
ADOPT or continue the active campaign
read actual average cost
read actual quantity
apply the currently selected Gear
apply the currently selected Exit Tier or tiers
calculate the next CHASE line
calculate the selected EXIT line or lines
```

Historical Step reconstruction is not required.

### Case 2 — No Shares, and a Full SELL Occurred Today

```text
actual_qty == 0
AND
a completed SELL for the ticker is detected in the current session
```

Action:

```text
treat the campaign as completed today
use the latest actual full-exit sell fill as reload_anchor
arm SAME_DAY_RELOAD at reload_anchor × 0.97
```

The reload rule has priority over the normal Gear LOAD during the same session.

Only one entry system may be active:

```text
SAME_DAY_RELOAD
OR
NORMAL_DYNAMIC_HIGH5_LOAD
```

Never both at the same time.

If the reload does not fill before the session ends, it expires. The following trading day returns to normal Dynamic High5 LOAD.

### Case 3 — No Shares, and No Full SELL Occurred Today

```text
actual_qty == 0
AND
no qualifying completed SELL is detected today
```

Action:

```text
discard stale active-campaign assumptions
reset the strategy to FLAT
use the normal Dynamic High5 LOAD process
```

The bot does not need to determine whether the previous campaign ended two days ago, five days ago, or earlier.

## A.3 Meaning of “Full SELL Occurred Today”

The preferred evidence is:

```text
current actual quantity == 0
+
the latest completed SELL fill for the ticker occurred today
+
that SELL completed the remaining position
```

When detailed position-transition reconstruction is available, use it.

When the broker API provides completed SELL fills but not a perfect position replay, a same-day SELL alone is insufficient. The evidence must still identify the transition being reconciled and show enough executed quantity to complete the previously held position. An older same-side fill must not be reused merely because its ticker, side, or trading date matches.

If the sell price cannot be recovered reliably:

```text
do not guess
do not arm an estimated reload
return to NORMAL_DYNAMIC_HIGH5_LOAD
or request a manual reload anchor
```

Historical full-exit recovery from earlier sessions is optional, not required for the first implementation.

## A.4 Dynamic High5 Vantage

When the strategy is FLAT and no same-day reload is active, the bot uses a real-time trailing High5 Vantage.

### Completed Sessions

Read the High values of the previous four completed trading sessions.

```text
completed_highs = last four completed-session High values
```

### Current Session

Track the current session’s highest observed price.

```text
today_high_so_far
= max(previous today_high_so_far, current market price)
```

If the market-data API directly provides the official current-session High, that value may be used instead.

### Watch Vantage

```text
watch_vantage
= max(
    previous four completed-session High values,
    today_high_so_far
)
```

This creates a five-session window consisting of:

```text
four completed sessions
+
the current live session
```

### LOAD Line

```text
load_price
= watch_vantage × (1 - selected_gear.load_drop)
```

Examples:

```text
Gear 1: watch_vantage × 0.94
Gear 2: watch_vantage × 0.93
Gear 3: watch_vantage × 0.92
Gear 4: watch_vantage × 0.91
Gear 5: watch_vantage × 0.90
```

## A.5 Real-Time Rising Vantage

The Vantage must update during the current session.

Example:

```text
previous watch_vantage = 100
today price rises to 110
selected Gear = 3
```

Then:

```text
watch_vantage = 110
LOAD line = 110 × 0.92 = 101.20
```

If the price subsequently falls from 110 and touches 101.20, the LOAD condition is satisfied.

This allows the bot to capture a same-day left endpoint that could not have been known before the intraday high occurred.

The bot is not attempting to draw a visual V pattern. It is implementing a trailing drawdown entry:

```text
new rolling high
→ selected Gear drawdown
→ LOAD
```

## A.6 Intraday Update Rule

While FLAT:

```python
today_high_so_far = max(today_high_so_far, current_price)

watch_vantage = max(
    previous_four_completed_highs,
    today_high_so_far,
)

load_price = watch_vantage * (1 - selected_gear.load_drop)
```

The bot should then watch for the current price to touch or cross the LOAD line from above.

If the bot starts while the market price is already below the calculated LOAD line, the first implementation may treat this as an immediately eligible LOAD condition, subject to:

```text
no pending conflicting order
the immediate order is covered by current buying power
minimum order quantity is valid
LIVE mode was explicitly confirmed
```

This opening/resume condition may be shown in the live status banner, but it is not a campaign-fill row. `RECORDED FILLS` begins only when broker holdings actually change.

A later version may add a stricter crossing-only option.

## A.7 LOAD Fill Freezes the Campaign Vantage

At actual LOAD fill:

```text
campaign_vantage = current watch_vantage
campaign_vantage becomes frozen
campaign status = DEPLOYED
```

After this point, Dynamic High5 no longer controls the campaign.

The campaign uses:

```text
actual average cost
selected Gear CHASE drop
selected Gear add ratio
selected Exit Tier(s)
```

until actual quantity becomes zero.

## A.8 App/Bot Coexistence

The simplified design supports practical mixed operation.

### Manual BUY While Shares Are Held

```text
refresh actual quantity
refresh actual average cost
recalculate next CHASE
recalculate full EXIT
```

The bot does not need to know which historical Step the manual BUY represented.

### Manual Full SELL While Bot Is Running

```text
actual quantity becomes zero
archive campaign as externally completed
detect today’s final SELL fill
arm SAME_DAY_RELOAD at actual sell fill × 0.97
```

### Manual Full SELL While Bot Is Offline

On the next startup:

```text
if today’s full SELL is visible:
    arm SAME_DAY_RELOAD

if the SELL occurred on an earlier day:
    reset to FLAT
    use current Dynamic High5
```

The bot does not backdate a missed reload and does not submit a historical order retroactively.

## A.9 Simplified Campaign Reset

The first implementation does not require a growing post-campaign window such as:

```text
2-day High
→ 3-day High
→ 4-day High
→ 5-day High
```

Instead, after any stale or externally completed campaign is recognized and no same-day reload applies:

```text
use the current rolling Dynamic High5 immediately
```

This is an intentional simplification.

Rationale:

- the current rolling five-session High is easy to calculate and audit;
- the most recent rebound or exit area will often already be represented in the window;
- stale highs naturally leave the window;
- exact campaign-end reconstruction adds complexity without being necessary for normal operation;
- the strategy uses High5 as an operational approximation, not as a perfect visual V-vertex detector.

## A.10 Restart Examples

### Example A — Position Still Open

```text
bot last ran on Day +7
user made additional manual BUYs
bot restarts on Day +10
actual quantity > 0
```

Result:

```text
ADOPT campaign
use actual average and quantity
calculate next CHASE and EXIT
```

### Example B — Campaign Was Sold Earlier

```text
bot last ran on Day +7
user sold all shares on Day +10
bot restarts on Day +12
actual quantity == 0
no full SELL occurred today
```

Result:

```text
reset to FLAT
do not reconstruct Day +10 precisely
do not use a historical same-day reload
calculate current Dynamic High5
arm normal Gear LOAD
```

### Example C — Campaign Was Sold Today

```text
user sells all shares in the app today
bot starts later in the same session
actual quantity == 0
today’s completed full SELL is visible
```

Result:

```text
reload_anchor = latest actual full SELL fill
arm reload_anchor × 0.97
```

### Example D — New Intraday High

```text
rolling High5 before today = 100
today rises to 110
today falls to 100
Gear 3 selected
```

Result:

```text
watch_vantage = 110
LOAD line = 101.20
LOAD triggers during the decline
campaign_vantage freezes at 110
```

## A.11 Minimum Data Requirements

Normal LOAD operation requires:

```text
current price
current-session High or enough live quotes to track it
previous completed-session High values
actual holdings
actual average cost
buying power
```

Same-day reload additionally requires:

```text
today’s completed SELL fill price
```

Useful but not mandatory for the simplified first implementation:

```text
exact historical campaign-end timestamp
full replay of old position transitions
historical external-fill classification
post-exit partial-session High reconstruction
```

## A.12 Final Decision Tree

```text
START OR RESUME
    │
    ├─ actual_qty > 0
    │      └─ ADOPT / CONTINUE CAMPAIGN
    │             ├─ next CHASE from actual average
    │             └─ full EXIT from actual average
    │
    └─ actual_qty == 0
           │
           ├─ qualifying full SELL occurred today
           │      └─ SAME_DAY_RELOAD at final sell fill −3%
           │
           └─ no qualifying full SELL today
                  └─ FLAT RESET
                         └─ NORMAL_DYNAMIC_HIGH5_LOAD
```

## A.13 Implementation Rule

> Current broker holdings determine whether a campaign exists.
> Today’s completed full SELL determines whether same-day reload is available.
> Otherwise, normal LOAD is rebuilt from the current rolling Dynamic High5.
> Exact reconstruction of an older campaign-end timestamp is not required.
