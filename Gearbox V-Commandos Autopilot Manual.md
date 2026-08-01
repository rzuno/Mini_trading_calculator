# Gearbox V-Commandos Autopilot Manual

**Version:** 0.2.0 — *as built*
**Strategy ID:** `V_COMMANDOS_GEARBOX`
**Subtitle:** Fixed-Gear V-Campaign Trading System
**Status:** Implemented in `core/calc.py`, `core/vcommandos.py`, `gui/stock_row.py`, `gui/campaign_window.py`. Verified offline by `scripts/test_vcommandos.py`. Not yet run live.

> **v0.2.0 changes are listed in §22.** The largest one: the volatility→gear cut points were **raised** so that gear 5 — the share-doubling gear — is reserved for genuinely violent stocks. The second: the card and the bot are now **one system**, not two.

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

This system replaces automatic intra-campaign gear adaptation with a five-speed gearbox. A Gear is selected before LOAD and remains stable so that deployment, expected depth, and exit behavior are known in advance.

A manual Gear override is allowed, but it must be explicit and logged.

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
+ selected Exit Tier
= next CHASE line and full EXIT line
```

The bot does not need perfect knowledge of every historical Step to continue safely. It can adopt or reconcile a position already traded manually.

---

## 3. Relationship to Other Strategy Modes

This strategy coexists with, and does not replace, the separate daily bidirectional grid strategy.

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

The broker/account layer, logging layer, order reconciliation, market-hours gate, and the candle panel are shared.

The trading logic must not be mixed inside one campaign.

### 3.1 How the two are selected (as built)

Every stock card carries **two** autopilot buttons:

```text
[ V-COMMANDOS ]   the campaign bot — big button, the default
[   v^ grid   ]   the daily grid bot — small button
```

`gui/autopilot_ctrl.py` runs **one strategy per stock at a time**. Asking for the other one is refused while that stock is LIVE or holds shares — a campaign and a grid must never share a holding. Each strategy keeps its own saved state (`data/autopilot_state.json`, keyed `ticker#strategy`), so switching never overwrites the other's campaign.

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

### 4.2 One Clean Exit

The campaign uses one selected Exit Tier and sells the entire position.

No automatic 33% / 33% / 34% sequential exit is used.

Reasons:

- partial exits create residual positions;
- the remaining average and profit accounting become harder to interpret;
- campaign completion becomes ambiguous;
- reloading and automated state transitions become more complex.

**As built:** the card *displays* all three tiers of the current gear so the trade-off is visible, but exactly one is armed (marked `▶`) and it carries the whole share count. The other two are shown greyed, without a quantity.

### 4.3 Long-Term Quality Still Matters

The system trades short V cycles, but it should prefer stocks that remain credible long-term holdings if a campaign becomes slow.

The expected long-term drift is a secondary protection layer, not the trigger.

### 4.4 Missed Upside Is an Accepted Cost

If a stock rises continuously without reaching LOAD, the system may not enter.

This is not a bug. The strategy pays for safety by refusing to chase upward prices.

A broader candidate universe and a seesaw-style battlefield rotation process are used to find another stock currently forming a left-side decline.

---

## 5. Unit System

One unit is a user-defined amount of capital, taken from `config.json` (`unit_cash_krw`, and its FX-derived USD twin).

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
load_qty = round_half_up(unit_cash / load_price)
minimum load_qty = 1 share
```

**As built:** `load_price` here is the **trimmed** (actually orderable) price — floored to the KR tick grid, or to the cent for US names — so the quantity on the card is exactly the quantity that gets ordered.

### 5.2 Reinforcement Quantity

```text
chase_qty = round_half_up(actual_current_shares × gear_add_ratio)
minimum chase_qty = 1 share
```

The program calculates the new average and all next lines from actual broker fills, not theoretical table values.

---

## 6. Five-Speed Gearbox

| Gear | Name | 5D range | LOAD | CHASE | Add ratio | Exit Tiers | Default | Max chase under 32u | Final BUY | Capital |
|---:|:--|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | Smooth   | ≤ 15% | −6% | −4% | ×1/2 | 1%/3%/5% | Tier 2 | 8 | 82.15 | 23.02u |
| 2 | Moderate | ≤ 20% | −7% | −5% | ×2/3 | 2%/4%/6% | Tier 2 | 7 | 78.26 | 31.01u |
| 3 | Balanced | ≤ 25% | −8% | −6% | ×3/4 | 3%/5%/7% | Tier 2 | 6 | 75.92 | 24.57u |
| 4 | Deep     | ≤ 30% | −9% | −7% | ×4/5 | 4%/6%/8% | Tier 2 | 6 | 72.26 | 28.14u |
| 5 | Extreme  | > 30% | −10% | −8% | ×1 | 5%/7%/9% | Tier 2 | 5 | 70.33 | 26.09u |

Bounds are **inclusive** on the upper edge: exactly 20.0% is gear 2, 20.1% is gear 3.

The 5-day range is a starting recommendation, not a mandatory classification rule. Market structure, recent rebound behavior, campaign capital, and the user's desired risk level may justify a lower or higher Gear.

A stock identity does not permanently determine its Gear. For example:

```text
SK hynix may use Gear 2 in a calm period.
SK hynix may use Gear 4 or 5 during an extreme volatility regime.
Alphabet may use Gear 1 or 2.
Samsung may use Gear 2 or 3 depending on the observed V depth.
```

### 6.1 Why the cut points were raised (v0.2.0)

The previous ladder was 8 / 12 / 16 / 20. Under it a stock with a perfectly ordinary 22% five-day range was handed **gear 5** — the gear that buys the entire position again on every chase and can consume 26 units. Gear 5 is a weapon for a stock that actually moves 30%+ in a week; handing it to a merely-normal stock is how a martingale runs out of funding before the V completes.

Raising the ladder to 15 / 20 / 25 / 30 means:

```text
G5 now requires a 5-day range above 30%   (was: above 20%)
G4 now requires above 25%                 (was: above 16%)
most ordinary sessions land on G1 or G2   — shallow ladders, cheap campaigns
```

This is the answer to the funding problem: **not a capital-allocation layer on top of the strategy, but a gearbox that stops selecting the expensive gear by accident.**

### 6.2 Heavy-unit entry floor (implementation addition)

A stock whose single share already eats a large fraction of a unit cannot run a shallow ladder — with 1–2 shares per unit the `×1/2` and `×2/3` ratios round to nothing useful and the campaign has no resolution. Its **entry** gear is therefore floored by share chunkiness:

```text
share_price / unit_cash   ≤ 1.2 → G1 allowed
                          > 1.2 → G2 or higher
                          > 1.6 → G3 or higher
                          > 2.0 → G4 or higher
                          > 2.5 → G5 only
```

The card shows `▲heavy` next to the gear when this floor, not volatility, chose the gear. It applies only while FLAT; once deployed the campaign keeps whatever gear it started with.

---

## 7. Exit Tier System

Each Gear has three mutually exclusive Exit Tiers.

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

### 7.2 One Tier Only

The active campaign has one selected Tier.

```text
exit_price = actual_average_cost × (1 + selected_exit_rate)
sell_qty   = all actual shares
```

Changing Tier during a campaign is allowed only as an explicit manual override, and the change is recorded as `EXIT_TIER_OVERRIDE` in the campaign event log.

---

## 8. Battlefield and Concurrency Policy

### 8.1 Gear 3–5

Default policy:

```text
one active battlefield globally
```

These Gears can consume 10–30 units quickly and should not depend on capital trapped elsewhere.

### 8.2 Gear 1–2

Possible policy:

```text
one to three active battlefields
```

This is permitted only when:

- each battlefield has its own maximum capital allocation;
- combined worst-case planned deployment remains fully funded;
- no campaign expects reinforcement from capital assigned to another campaign;
- the global buying-power manager prevents double counting.

### 8.3 Pre-Funding Rule

Before LOAD is armed, the maximum fully supported chase row for that campaign should be estimated. If the selected campaign cannot be supported to the configured limit:

```text
do not arm LOAD
```

or explicitly reduce:

```text
unit_cash
maximum_chase_count
campaign_cap_units
```

### 8.4 What is actually enforced (as built) — read this

**Enforced by code, per campaign:**

- `campaign_cap_units` (default **32u**). Before every chase the engine checks `next_chase_cost ≤ cap_units × unit_cash − current_cost_basis`. Over the cap, the chase is disabled, the buy line is drawn muted `✕ … (cap)`, and the campaign keeps watching its EXIT only.
- The broker's own buying power. If the next line costs more than the cash on hand, the buy is not fired and the state reads `EXHAUSTED`; the EXIT stays managed.

**NOT enforced by code:**

- The one-battlefield / three-battlefield concurrency limits of §8.1–8.2. Nothing stops several stocks from running campaigns at once.
- The §8.3 pre-arm refusal. Funding is checked chase-by-chase, not before the LOAD.
- Any global reservation of buying power across campaigns.

Those remain the commander's discipline. **Do not read §8.1–8.3 as an implemented safety net.** The honest statement of the current state is: one campaign cannot exceed its own cap, and no campaign can spend cash that is not there — but two campaigns can still compete for the same reserve.

---

## 9. Vantage Point and LOAD

### 9.1 Standard Flat-State Vantage

Default:

```text
High5 = highest completed-session high from the previous five trading days
watch_vantage = High5
```

While flat, the bot updates the rolling High5 on every trading day. If High5 is not available yet, the previous completed close is used as the fallback and the card says so.

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

If the reload fills, a new campaign begins using the currently selected Gear and Exit Tier, and the fill is logged as `RELOAD`.

If it does not fill by the end of the session it expires: on the next trading day the bot discards it, logs `re-bait expired unfilled`, and returns to the normal High5 vantage. The card marks the reload state as `(reload)` next to the vantage.

---

## 10. CHASE and EXIT Calculations

At every account refresh:

```text
actual_qty        = broker-reported current shares
actual_avg        = broker-reported actual average cost
actual_cost_basis = actual_qty × actual_avg
```

Then:

```text
next_chase_price = actual_avg × (1 - selected_gear.chase_drop)
next_chase_qty   = round_half_up(actual_qty × selected_gear.add_ratio)
exit_price       = actual_avg × (1 + selected_exit_rate)
exit_qty         = actual_qty
```

The bot watches exactly two lines:

```text
next CHASE BUY
full EXIT SELL
```

If the campaign capital cap makes the next CHASE unaffordable:

```text
disable CHASE
continue watching EXIT only
```

**Order priority:** when both lines are crossed in the same poll, the **EXIT wins** — the campaign always prefers to finish. Any resting buy of ours is cancelled first.

**Price trimming:** a BUY line is floored to the tick grid (never bids above the strategy line) and a SELL line is ceiled (never asks below it). KR names snap to the KRX band tick; US names to the cent.

---

## 11. Gear Changes During a Campaign

Automatic adaptive switching is not used.

A manual Gear override may be applied because a fixed Gear does not depend on reconstructing the entire historical ladder.

After a Gear change:

```text
keep actual holdings
keep actual average cost
keep campaign ID
replace next CHASE line using the new Gear
replace EXIT only if the Exit Tier or exit rate also changes
log GEAR_OVERRIDE
```

**As built:** AUTO mode picks the gear from volatility **only while the card is FLAT**. Once the card is DEPLOYED the gear is pinned (the card shows `pinned`) and the picker stays live — using it *is* the override path, and the engine writes `GEAR_OVERRIDE` with the old and new values.

The default recommendation remains:

> Keep one Gear for one campaign whenever possible so that backtests and live results remain interpretable.

---

## 12. Manual App Trading and Bot Resumption

The fixed-Gear architecture supports mixed app/bot operation better than the legacy Step-dependent architecture. This is a first-class use case: **the commander trades by hand in the broker app using the numbers on the card, and the bot chases the same lines.**

### 12.1 Broker Data Is the Source of Truth

At startup or resume, read:

```text
actual holdings
actual average cost
actual buying power
open orders
```

### 12.2 ADOPT_POSITION

If actual shares are positive but no active bot campaign exists, the bot adopts the position automatically on its first poll: it opens a campaign header from the current gear and tier and computes both lines from the broker's average cost. It does not need to know whether Step 2, 3, or 4 was completed by hand.

An adopted campaign is flagged `MANUALLY_MODIFIED` until it closes.

### 12.3 External BUY

If a manual BUY changes holdings or average cost:

```text
record the fill with source = EXTERNAL
refresh actual qty and average
recalculate next CHASE
recalculate full EXIT
```

### 12.4 External Partial SELL

A partial manual SELL is not part of the normal strategy.

If detected:

```text
record PARTIAL (source EXTERNAL)
recalculate both lines from the remaining position
flag the campaign MANUALLY_MODIFIED
```

### 12.5 External Full SELL

If broker holdings become zero:

```text
campaign is completed
record the fill as an EXIT with source = EXTERNAL
arm the same-day reload off the observed price
```

### 12.6 Order Ownership

Bot orders are identified by the broker order id returned when the order was placed, kept in a per-stock `my_ids` set, plus a `client_order_id` tag.

**The bot cancels only orders it placed itself.** A foreign order resting on a side simply blocks that side for the poll; nothing is cancelled on price/quantity resemblance.

---

## 13. Campaign State Machine

```text
OFF
    no order actions

FLAT
    no shares
    rolling High5 vantage, gear recommendation live

ARMED_LOAD
    the LOAD order has been sent / rests

DEPLOYED
    actual shares > 0
    watch CHASE and full EXIT

CHASE_PENDING
    wait for the submitted BUY to resolve
    no duplicate CHASE

EXIT_PENDING
    wait for the full SELL to resolve
    no new BUY unless the SELL fails or is cancelled

COMPLETED
    actual shares == 0
    campaign closed, log finalized

RELOAD_ARMED
    watch the actual final sell price −3% for the rest of the session

PAUSED_RECONCILE
    broker state and bot state disagree
    (shares > 0 but no average cost) — no order until resolved
```

---

## 14. Campaign Fill Log

Every campaign has one persistent campaign ID (`TICKER-YYYYMMDD-HHMMSS`).

### 14.1 Campaign Header

```text
campaign_id
ticker / currency
strategy_mode
selected_gear
selected_exit_tier
campaign_vantage
load_price
unit_cash
campaign_cap_units
campaign_start
campaign_state
```

### 14.2 Fill Record

```text
ts
source: BOT / EXTERNAL
event_type: LOAD / CHASE / EXIT / RELOAD / PARTIAL
price
qty (signed)
shares_after
average_after
```

### 14.3 Override Events

```text
GEAR_OVERRIDE
EXIT_TIER_OVERRIDE
CAP_OVERRIDE
```

Each event records old value, new value, timestamp, and an optional reason.

### 14.4 Completion Metrics

Tracked live and shown in the campaign window banner:

```text
campaign_start
chase_count
campaign_low
max_qty        (peak share count)
max_cost       (peak cash deployed)
manually_modified
```

Not yet computed: trading-days-open, fees, taxes, net profit, return on maximum capital. See §21.

---

## 15. Campaign Chart

**As built:** the campaign window draws two panels side by side —

```text
left    live tick curve since the window opened, with the campaign's own
        reference lines: LOAD or CHASE (blue), the broker average (purple),
        the full EXIT (red), and the vantage (orange)
right   the 5-day candle panel, carrying the same reference lines
```

The full campaign-spanning chart of the original §15 (LOAD marker, every chase marker, campaign low, exit marker, with a 10-trading-day view policy) is **not built yet**. The fill log in the banner carries the same information as text.

---

## 16. Accounting Views

Not built. The card shows per-stock cost basis, army %, and the live gap; the campaign window shows unrealized P&L, peak deployment in units, and the gross target of the armed exit.

Realized campaign profit, fees/taxes, campaign duration statistics, completion-rate comparisons, and buy-and-hold benchmarking remain future work (§21).

---

## 17. Gear Selection Dashboard

**As built:** the dashboard is the card itself. Each card shows

```text
V 18.2% → G3            volatility gear, live
V 18.2% → G5 ▲heavy     the heavy-unit floor overrode volatility
V 18.2% · G3 pinned     deployed — the campaign keeps its gear
V 18.2% → reload -3%    a same-day reload is armed
```

and the gear badge (a colored circle 1–5) makes the selected gear readable across the whole grid of cards at a glance.

The recommendation map:

```text
5D range ≤ 15%       → Gear 1
      ≤ 20%          → Gear 2
      ≤ 25%          → Gear 3
      ≤ 30%          → Gear 4
      > 30%          → Gear 5
```

where `5D range = 100 × (High5 − Low5) / High5`.

This is a recommendation, not an automatic order command. Switching the card to MANUAL lets the commander pick a more conservative gear whenever conditions warrant it.

---

## 18. Operational Safety Rules

1. **Broker state is authoritative.**
2. **One pending strategy order at a time per side.**
3. **No duplicate CHASE while a BUY is unresolved.**
4. **No new campaign until actual shares are zero.**
5. **Never assume a fill price** — a fill's price comes from our own order intent, or is derived from the broker's average-cost move.
6. **Recalculate from actual average cost after every fill.**
7. **Stop CHASE at the configured campaign cap.**
8. **Cancel only bot-owned orders.**
9. **On ambiguous reconciliation, pause instead of guessing.**
10. **A failed data poll skips the whole cycle** — nothing is placed or cancelled on missing data.
11. **Use WATCH before LIVE.**
12. **LIVE runs only during regular market hours** and drops back to WATCH at the close.

---

## 19. Runtime Modes

```text
WATCH
    calculate, watch, detect fills; place nothing.
    A crossed line lights the trigger row so it can be fired by hand
    from the campaign window (attributed exactly like a bot fill).

LIVE
    place real broker LIMIT/DAY orders when a line is crossed.
```

The original §19 also specified a **DRY** runtime mode (simulate orders and fills inside the app). That was not built: its job is done by `scripts/test_vcommandos.py`, an offline simulator that runs whole price paths through the real engine behind a fake broker. WATCH covers the live-observation half.

---

## 20. Configuration Baseline

Live values come from `config.json` and `data/positions.csv`; the rest are constants in `core/calc.py`.

```yaml
strategy_mode: V_COMMANDOS_GEARBOX

# config.json
unit_cash_krw: 1000000          # unit_cash_usd is derived from FX
N: 20                           # army size, in units

# data/positions.csv, per stock
gear: 1..5
exit_tier: 1..3
auto_mode: true                 # gear follows volatility while FLAT

# core/calc.py constants
VOL_THRESHOLDS:      [15, 20, 25, 30]
WEIGHT_GEAR_THRESHOLDS: [1.2, 1.6, 2.0, 2.5]
CAMPAIGN_CAP_UNITS:  32.0
RELOAD_DROP_PCT:     3
DEFAULT_GEAR:        3
DEFAULT_EXIT_TIER:   2
rounding_mode:       HALF_UP
minimum_order_qty:   1
```

`positions.csv` keeps the pre-gearbox columns (`load_gear`, `buy_pct`, `t1_pct`…`t3_active`) written from the gear, so older builds and scripts still read the file. On load, a legacy file is migrated: a bait drop percent becomes its gear (4%→G1 … 8%→G5) and the lowest active sell tier becomes the single exit tier.

---

## 21. Maximum-Deployment Comparison

The following rows use Vantage 100, theoretical fractional sizing, a 32-unit capital limit, no fees, and exact fills. `scripts/test_vcommandos.py` re-derives every one of them from the shipped gear parameters.

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

## 22. What is built, and what is not

| Manual section | Status |
|---|---|
| §5–§7 gearbox, tiers, sizing | **built** — `core/calc.py`, verified against Part II |
| §6.1 raised volatility ladder | **built** — new in v0.2.0 |
| §6.2 heavy-unit entry floor | **built** — implementation addition |
| §9 High5 vantage, LOAD, same-day reload | **built** |
| §10 two watched lines, tick trimming, exit priority | **built** |
| §11 fixed gear + logged override | **built** |
| §12 adopt / external buy / partial / full exit | **built** |
| §13 state machine | **built** |
| §14.1–14.3 campaign header, fill log, overrides | **built** |
| §8.4 per-campaign cap + buying-power wall | **built** |
| §17 gear dashboard on the card | **built** |
| §19 WATCH / LIVE | **built** (DRY replaced by the offline simulator) |
| §8.1–8.3 concurrency limits, pre-arm funding check | **NOT built** — commander's discipline |
| §14.4 full completion metrics (fees, duration, ROC) | **NOT built** |
| §15 campaign-spanning chart | **NOT built** — 5-day panel + fill log instead |
| §16 accounting views | **NOT built** |
| Portfolio orchestrator (candidate ranking, army split) | **NOT built** |

### v0.2.0 amendment log

1. **Volatility→gear cut points raised** from 8/12/16/20 to 15/20/25/30, with the upper bound inclusive (§6, §6.1, §17). Gear 5 now requires a 5-day range above 30%.
2. **Heavy-unit entry floor** added as a second input to the automatic entry gear (§6.2).
3. **The card and the bot are one system** (§3.1, §11, §17). The card pushes `{gear, exit_tier, cap_units}` to the engine on every recompute; the engine watches exactly the lines the card draws. Previously they ran on separate logic.
4. **Two autopilot buttons per card** (§3.1), one strategy at a time per stock, separate saved state.
5. **§8.4 added** to state honestly which funding rules are enforced by code and which are not.
6. **§5.1 clarified**: LOAD quantity is sized off the trimmed, orderable price.
7. **§19 DRY** replaced by the offline simulator; documented rather than silently dropped.
8. **§15 / §16** marked as not built rather than left reading as specification.

---

## Appendix A — Normalized Gear Reference Tables

Long-term normalized references. Assumptions:

```text
Vantage = 100
initial LOAD capital = 1 unit
fractional theoretical sizing
maximum campaign capital = 32 units
no fees, taxes, slippage, or integer-share rounding
```

Live calculations use actual broker quantities and actual fills.

### Gear 1 — Smooth
```text
Standard LOAD: Vantage −6%   ·   CHASE: actual average cost −4%
Additional size: current shares × 1/2
Exit Tier 1 / 2 / 3: +1% / +3% / +5%   (default: Tier 2)
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

Bottom-to-exit rebound **3.81% / 5.86% / 7.92%** · gross target **0.230u / 0.691u / 1.151u**

### Gear 2 — Moderate
```text
Standard LOAD: Vantage −7%   ·   CHASE: actual average cost −5%
Additional size: current shares × 2/3
Exit Tier 1 / 2 / 3: +2% / +4% / +6%   (default: Tier 2)
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

Bottom-to-exit rebound **5.22% / 7.28% / 9.35%** · gross target **0.620u / 1.240u / 1.861u**

### Gear 3 — Balanced
```text
Standard LOAD: Vantage −8%   ·   CHASE: actual average cost −6%
Additional size: current shares × 3/4
Exit Tier 1 / 2 / 3: +3% / +5% / +7%   (default: Tier 2)
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

Bottom-to-exit rebound **6.76% / 8.83% / 10.90%** · gross target **0.737u / 1.228u / 1.720u**

### Gear 4 — Deep
```text
Standard LOAD: Vantage −9%   ·   CHASE: actual average cost −7%
Additional size: current shares × 4/5
Exit Tier 1 / 2 / 3: +4% / +6% / +8%   (default: Tier 2)
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

Bottom-to-exit rebound **8.35% / 10.43% / 12.52%** · gross target **1.125u / 1.688u / 2.251u**

### Gear 5 — Extreme
```text
Standard LOAD: Vantage −10%   ·   CHASE: actual average cost −8%
Additional size: current shares × 1
Exit Tier 1 / 2 / 3: +5% / +7% / +9%   (default: Tier 2)
```

| Step | BUY price | Added qty | Total qty | New average | T1 EXIT | T2 EXIT | T3 EXIT | Capital used |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LOAD | 90.00 | 1.000 | 1.000 | 90.00 | 94.50 | 96.30 | 98.10 | 1.00u |
| 1 | 82.80 | 1.000 | 2.000 | 86.40 | 90.72 | 92.45 | 94.18 | 1.92u |
| 2 | 79.49 | 2.000 | 4.000 | 82.94 | 87.09 | 88.75 | 90.41 | 3.69u |
| 3 | 76.31 | 4.000 | 8.000 | 79.63 | 83.61 | 85.20 | 86.79 | 7.08u |
| 4 | 73.26 | 8.000 | 16.000 | 76.44 | 80.26 | 81.79 | 83.32 | 13.59u |
| 5 | 70.33 | 16.000 | 32.000 | 73.38 | 77.05 | 78.52 | 79.99 | 26.09u |

Bottom-to-exit rebound **9.57% / 11.65% / 13.74%** · gross target **1.305u / 1.826u / 2.348u**

---

## Appendix B — The Campaign Card

The stock card is the display and audit surface, and the bot's source for gear and tier. It reads:

```text
1. Samsung (KR)        (12.3%)  [AUTO]  V 18.2% · G3 pinned
                                Avg Cost [280,315]  Shares [31]   DEPLOYED

Total Cost: 8,689,765   Current: 232,000   Gap: -17.24%

Chase 1: 263,496 × 23   Chase 2: 256,762 × 41   Chase 3: 250,114 × 71
   T1: 288,724             ▶ T2: 294,331 × 31      T3: 299,937

                         Gear (G3 Balanced)   Exit (T2 +5%)   [ V-COMMANDOS ]
                         [-8% / -6% ×3/4]     [ T3 +7% ]      [   v^ grid   ]
                         gear: ③              [ T2 +5% ]
                                              [ T1 +3% ]
```

A FLAT card shows `Vantage:` instead of `Total Cost:`, and its ladder reads `Load / Chase 1 / Chase 2` with the exit tiers computed as if the load had filled.

The bot derives its live decisions from broker state plus the card's gear and tier — never from card arithmetic the commander typed by hand.

---

## Final Strategy Summary

> Select a stock and a fixed Gear before battle.
> LOAD only after the required pullback.
> Reinforce from the actual average cost using the selected Gear.
> Choose one Exit Tier and liquidate the entire campaign at that target.
> Recover the army, record the completed V, and automatically prepare the next LOAD or same-day reload.
> Use lower Gears for shallow, calmer V cycles and higher Gears for deep, high-amplitude V cycles.
> Keep the strategy simple enough to run by hand off the card, and automate it so the same campaign can be resumed, reconciled, and repeated without maintaining cards by hand.
