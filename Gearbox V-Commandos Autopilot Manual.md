# Gearbox V-Commandos Autopilot Manual

**Version:** 0.4.0 — *as built*
**Strategy ID:** `V_COMMANDOS_GEARBOX` — the only strategy in the app
**Subtitle:** Fixed-Gear V-Campaign Trading System
**Status:** Implemented in `core/calc.py`, `core/vcommandos.py`, `gui/stock_row.py`, `gui/campaign_window.py`, `gui/autopilot_ctrl.py`. Verified offline by `scripts/test_vcommandos.py` (106 checks) and `scripts/test_autopilot_ui.py` (70). Not yet run live.

> **v0.4.0 changes are listed in §22.** The gear ladder is now **10/15/20/25** — G5 arms above a 25% range (§6). V is **one number, everywhere**: 100×(High5−Low5)/High5, the same figure the card, the cockpit and the candle panel all show (§17). Gear and exit tier are choosable **in the cockpit** as well as on the card (§15). A FLAT stock now publishes chase 1 as well as chase 2 — it was silently dropped (§10).
>
> v0.3.0 before it: no capital cap, the army is the only wall (§8); the gear is not pinned by a live campaign (§11); the daily v^ grid removed (§3); manual Buy/Sell removed (§19).

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

This system replaces per-fill gear escalation with a five-speed gearbox. A Gear is selected before LOAD, so deployment, expected depth, and exit behavior are known in advance — and because every line is derived from the broker's average cost alone, it can be shifted later without unwinding anything (§11).

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

## 3. The Only Strategy

`V_COMMANDOS_GEARBOX` is the app's only bot. Each card carries one button:

```text
[ V-COMMANDOS ]   arms WATCH on that stock and opens its cockpit
```

The separate `DAILY_V_HAT_LINEAR_GRID` (the daily v^ adventure) was **removed on 2026-08-01** so this strategy could be stabilised on its own. It was working, and it is not repudiated — its engine, cockpit and 86-check test suite are recoverable from commit `e148da6`, and its specification is kept in `Daily v^ Grid Autopilot Manual.md`. Restoring it means bringing back `core/autopilot.py`, `gui/autopilot_window.py` and `scripts/test_grid.py`, and re-adding the strategy branch in `gui/autopilot_ctrl.py`.

Campaign state is saved under `ticker#VCG` in `data/autopilot_state.json`. Any v^ grid record already saved under the bare ticker key is left untouched, and the engine refuses to restore it as a campaign — so a future restoration finds its own history intact.

The two trading logics must never share a holding. That is why only one runs.

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

| Gear | Name | 5D range | LOAD | CHASE | Add ratio | Exit Tiers | Default | Chases in the 32u table | Final BUY | Capital |
|---:|:--|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | Smooth   | ≤ 10% | −6% | −4% | ×1/2 | 1%/3%/5% | Tier 2 | 8 | 82.15 | 23.02u |
| 2 | Moderate | ≤ 15% | −7% | −5% | ×2/3 | 2%/4%/6% | Tier 2 | 7 | 78.26 | 31.01u |
| 3 | Balanced | ≤ 20% | −8% | −6% | ×3/4 | 3%/5%/7% | Tier 2 | 6 | 75.92 | 24.57u |
| 4 | Deep     | ≤ 25% | −9% | −7% | ×4/5 | 4%/6%/8% | Tier 2 | 6 | 72.26 | 28.14u |
| 5 | Extreme  | > 25% | −10% | −8% | ×1 | 5%/7%/9% | Tier 2 | 5 | 70.33 | 26.09u |

Bounds are **inclusive** on the upper edge: exactly 15.0% is gear 2, 15.1% is gear 3.

The last three columns are the normalized reference ladder (§21, Appendix A): how far a campaign would run **if** 32 units were spent on it. They are a yardstick for comparing gears, **not a cap** — see §8.

The 5-day range is a starting recommendation, not a mandatory classification rule. Market structure, recent rebound behavior, campaign capital, and the user's desired risk level may justify a lower or higher Gear.

A stock identity does not permanently determine its Gear. For example:

```text
SK hynix may use Gear 2 in a calm period.
SK hynix may use Gear 4 or 5 during an extreme volatility regime.
Alphabet may use Gear 1 or 2.
Samsung may use Gear 2 or 3 depending on the observed V depth.
```

### 6.1 Where the cut points sit

The original ladder was 8 / 12 / 16 / 20. Under it a stock with an ordinary 22% five-day range was handed **gear 5** — the gear that buys the entire position again on every chase. Gear 5 is a weapon for a stock that genuinely swings; handing it to a merely-normal stock is how a martingale runs out of funding before the V completes.

The ladder is now **10 / 15 / 20 / 25**:

```text
G5 requires a 5-day range above 25%   (was: above 20%)
G4 requires above 20%                 (was: above 16%)
G1 keeps only the genuinely quiet     (≤ 10%)
```

It sits deliberately between the two earlier attempts. The first pass at raising it (15/20/25/30) went too far the other way: with a 30% floor, G5 almost never armed and ordinary volatility all collapsed onto G1's shallow −6%/−4% ladder, which is too timid to lower an average meaningfully. 10/15/20/25 keeps G5 for real violence while letting the middle gears do the everyday work.

This is the answer to the funding problem: **not a capital-allocation layer on top of the strategy, but a gearbox that stops selecting the expensive gear by accident.** It is also why no cap is needed (§8.2) — deployment is governed where the choice is still free, before the LOAD.

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

## 8. What Limits a Campaign

### 8.1 The army is the only wall

There is **no capital cap**. A campaign chases as long as the broker's cash covers the next line:

```text
next_chase_cost ≤ buying_power   →  the chase is armed and fires
next_chase_cost > buying_power   →  the chase is disabled, the line is drawn
                                    muted (✕ … no army), and the campaign
                                    keeps watching its EXIT
```

The engine re-checks this every poll, so the buy side comes back by itself the moment cash returns — a completed exit elsewhere refills the army and the line goes live again without intervention.

The 32-unit figure in the normalized tables (§21, Appendix A) is a **reference scale**, not a limit: it is how far the ladder would run on 32 units, published so gears can be compared. Total deployment may exceed it. Nothing in the code stops at any unit count.

### 8.2 Why there is no cap

A fixed cap is a second, softer wall standing in front of the real one, and it fails in the direction that hurts: it stops the ladder while cash is still available — exactly at the bottom of a deep V, where the remaining chases are the cheapest shares of the whole campaign. The army running out is a fact; a cap is a guess about that fact.

The gear ladder is where deployment is actually governed (§6.1): choosing G2 instead of G5 is what makes a campaign cheap, and that decision is made **before** the LOAD, when it is still free to make.

### 8.3 Concurrency — not enforced

Deep gears can consume many units quickly and should not depend on capital trapped elsewhere. The sensible policy is one active battlefield for G3–G5 and up to three for G1–G2.

**None of this is enforced by code.** There is no global buying-power manager, no per-campaign reservation, and no limit on how many stocks may run campaigns at once. Two campaigns can compete for the same reserve, and the second one to reach its line simply finds the army gone.

**Do not read §8.3 as an implemented safety net.** The honest statement of the current state is: no campaign can spend cash that is not there, and that is the whole of the protection.

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

If the army cannot fund the next CHASE:

```text
disable CHASE
continue watching EXIT only
```

and re-check every poll, so buying resumes by itself when cash returns.

Beyond the armed line the engine publishes **two more projected buys**, folded into the running average exactly as the campaign would run them. They are drawn soft on both charts and named in the banner. Nothing is ever ordered from a projection — they exist so the depth of the ladder ahead is visible before it is needed.

Which lines those are depends on the state, and the numbering follows the card:

```text
DEPLOYED   armed 'chase'  +  projected 'chase2', 'chase3'
FLAT       armed 'load'   +  projected 'chase1', 'chase2'
```

A FLAT stock has no chase armed — the LOAD is its live line — so **chase 1 is a projection there**, exactly as the flat card prints `Load / Chase 1 / Chase 2`. (Until v0.4.0 the engine numbered projections from 2 in both states, so a flat stock's chart drew chases 2 and 3 and silently omitted chase 1.)

**Order priority:** when both lines are crossed in the same poll, the **EXIT wins** — the campaign always prefers to finish. Any resting buy of ours is cancelled first.

**Price trimming:** a BUY line is floored to the tick grid (never bids above the strategy line) and a SELL line is ceiled (never asks below it). KR names snap to the KRX band tick; US names to the cent.

---

## 11. Gear Changes During a Campaign

The gear may be changed at any time, deployed or not.

Nothing in a campaign depends on the gear that opened it. The only piece of history the engine carries is the **average cost**, and both lines are derived from it on the spot:

```text
next CHASE = actual_avg × (1 - new_gear.chase%)
full EXIT  = actual_avg × (1 + new_tier%)
```

So a shift is not a repair — it is just a different pair of lines from the same position. After a change:

```text
keep actual holdings
keep actual average cost
keep campaign ID
replace the CHASE line and the projected ladder with the new gear
replace the EXIT if the tier changed
write GEAR / TIER to the campaign log
```

**As built:** the card is the source. In AUTO the gear tracks the 5-day range continuously, deployed or not — a stock that turns violent mid-campaign gets a deeper ladder without being touched. In MANUAL it holds whatever the commander picked. Either way, a change on a live campaign is written into the campaign log, so the reason a chase line moved is always on the record.

The heavy-unit floor (§6.2) is the one exception: it applies only while FLAT, because it is an **entry** rule about whether a position can be opened with useful resolution — not about one that already exists.

The default recommendation still stands:

> Keep one Gear for one campaign whenever you can, so backtests and live results stay interpretable. Shift it when the stock's behaviour actually changes, not to chase a feeling about the position.

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

### 14.2 Log Record

One list holds the whole campaign, trades and decisions together, so the log
reads as a narrative rather than two files to cross-reference:

```text
date            market-local trading date — the log groups by day
ts              MM/DD HH:MM
source          BOT / EXT   (EXT = traded by hand in the broker app)
kind            LOAD / RELOAD / CHASE / EXIT / PARTIAL   (trades)
                ADOPT / GEAR / TIER                      (decisions)
price, qty      signed quantity; blank on a decision row
shares, avg     the position AFTER the row
note            chase number, gross result, or the gear/tier change
```

The cockpit renders it as `RECORDED FILLS` with a header row whenever the trading date changes, so a campaign that runs for days is read day by day.

### 14.3 Decision rows

```text
ADOPT   the bot took over a position it did not open
GEAR    G3 → G5 (chase -8% ×1.0)
TIER    T2 → T3 (+7%)
```

A gear or tier change on a live campaign always writes a row: the reason a chase line moved is on the record even though the shift itself is routine.

### 14.4 Completion Metrics

Tracked live and shown in the cockpit banner:

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

## 15. The Cockpit

Opened by the card's V-COMMANDOS button; opening it arms WATCH.

```text
header   name · campaign state · market phase · Cancel N resting · LIVE
gearbox  [AUTO] gear [1][2][3][4][5] Balanced -8%/-6% ×3/4
                exit [T1 +3%][T2 +5%][T3 +7%]      V 18.2% → G3
banner   G3 Balanced · LOAD -8% · CHASE -6% ×3/4 · EXIT T2 +5% (full)
                     · vantage 100.00 (High5)
         campaign id · since · chases · low · peak
         31 sh @ 92.00 avg · 2.85 u deployed · now 91.20 · P&L -0.87%
                     · target 142.60 · army 4,200.00
         ▲ EXIT 96.60 × 31    ▼ CHASE -6% ×3/4 86.48 × 23
                              then 84.26 × 41 · 82.09 × 71
         engine status + poll time
charts   live tick curve inside the campaign | 5-day candle panel
log      RECORDED FILLS, grouped by trading day
```

### 15.1 The gearbox strip

Gear and exit tier are choosable here as well as on the card — the cockpit is where the campaign is actually being watched, so it is where the decision usually wants to be made.

Both controls **write to the CARD**, which stays the single source of truth; the controller reads the card straight back, so the engine uses the new lines on its very next poll. Picking a gear also flips the card off AUTO (a manual choice must not be overwritten by the next volatility read); the **AUTO** button hands the choice back to volatility. The strip shows V and the gear it recommends, so a manual pick can always be compared against what AUTO would have done.

### 15.2 What "Cancel N resting" is for

The bot **never re-prices an order it has already sent.** One order per side at a time, and while it rests, that side is blocked. So:

```text
gear shifts while a chase rests  →  the resting order is still at the OLD
                                    price, and the new line cannot arm
```

Cancelling clears it, and the next poll re-arms at the current line. It is also how a line is pulled before the market reaches it.

It touches **resting (unfilled) orders only** — a trade that has already filled cannot be cancelled, and is not what this button is about. The button names the count and is disabled when nothing rests, so it never invites a click that would do nothing.

The sell line reads first because it sits at the top of the chart; the next buy and **its size** read below it, then where the ladder goes after that. Both charts draw the same rows: the armed buy, the broker average, and the EXIT bold; the projected chases and the vantage soft. An unfundable buy line is drawn grey and relabelled `✕ … (no army)`.

**There are no manual Buy/Sell buttons.** The point of the bot is that the offer goes out when the curve touches the line. WATCH shows the crossed line and says plainly that it did not send; LIVE sends.

The full campaign-spanning chart of the original §15 (a LOAD marker, every chase marker, the campaign low, the exit marker, with a 10-trading-day view policy) is **not built**. The 5-day panel plus the day-grouped log carries the same information.

---

## 16. Accounting Views

Not built. The card shows per-stock cost basis, army %, and the live gap; the campaign window shows unrealized P&L, peak deployment in units, and the gross target of the armed exit.

Realized campaign profit, fees/taxes, campaign duration statistics, completion-rate comparisons, and buy-and-hold benchmarking remain future work (§22).

---

## 17. Gear Selection Dashboard

**As built:** the dashboard is the card itself. Each card shows

```text
V 18.2% → G3            the volatility gear, tracked live
V 18.2% → G5 ▲heavy     the heavy-unit floor (FLAT only) overrode volatility
V 18.2% → reload -3%    a same-day reload is armed
```

and the gear badge (a colored circle 1–5) makes the selected gear readable across the whole grid of cards at a glance.

The recommendation map:

```text
V ≤ 10%       → Gear 1
V ≤ 15%       → Gear 2
V ≤ 20%       → Gear 3
V ≤ 25%       → Gear 4
V >  25%      → Gear 5
```

### 17.1 V is one number

```text
V = 100 × (High5 − Low5) / High5
```

The span of the whole five-day window as a percent of its high — **not** a per-day average, and not a per-day average divided by anything. `core.calc.calc_volatility` computes it, `select_auto_gear` reads the gear off it, and it is the number shown on the card, in the cockpit's gearbox strip, and on the candle panel. One definition, one figure, three places, always agreeing.

The candle panel briefly showed a different one (the mean of the last five completed days' ranges, plus a `/3` hint) — a leftover from the daily v^ grid, where that average sized the grid spacing. It measured something the gearbox never used and is gone.

This is a recommendation, not an automatic order command. Switching the card to MANUAL lets the commander pick a more conservative gear whenever conditions warrant it.

---

## 18. Operational Safety Rules

1. **Broker state is authoritative.**
2. **One pending strategy order at a time per side.**
3. **No duplicate CHASE while a BUY is unresolved.**
4. **No new campaign until actual shares are zero.**
5. **Never assume a fill price** — a fill's price comes from our own order intent, or is derived from the broker's average-cost move.
6. **Recalculate from actual average cost after every fill.**
7. **Stop CHASE when the army cannot fund it** — and resume when it can.
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
    A crossed line is drawn and named, and the status says it was not sent.

LIVE
    the bot sends the real broker LIMIT/DAY order by ITSELF the moment the
    curve crosses a line. Regular market hours only; drops back to WATCH at
    the close.
```

Manual firing was removed with v0.3.0: the reason to run a bot is that the offer goes out when the curve touches the line. Trades made by hand in the broker app are still first-class — the engine detects them and folds them into the campaign (§12).

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
VOL_THRESHOLDS:      [10, 15, 20, 25]
WEIGHT_GEAR_THRESHOLDS: [1.2, 1.6, 2.0, 2.5]
RELOAD_DROP_PCT:     3
DEFAULT_GEAR:        3
DEFAULT_EXIT_TIER:   2
rounding_mode:       HALF_UP
minimum_order_qty:   1

# core/vcommandos.py
POLL_SECONDS:        5
PROJECTED_CHASES:    2      # buy lines published beyond the armed one
```

There is no `campaign_cap_units`. The army is the cap (§8).

`positions.csv` keeps the pre-gearbox columns (`load_gear`, `buy_pct`, `t1_pct`…`t3_active`) written from the gear, so older builds and scripts still read the file. On load, a legacy file is migrated: a bait drop percent becomes its gear (4%→G1 … 8%→G5) and the lowest active sell tier becomes the single exit tier.

---

## 21. Reference Ladder Comparison

The following rows use Vantage 100, theoretical fractional sizing, **32 units of spend as a common yardstick** (not a cap — see §8), no fees, and exact fills. `scripts/test_vcommandos.py` re-derives every one of them from the shipped gear parameters.

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
| §6.1 raised volatility ladder | **built** |
| §6.2 heavy-unit entry floor | **built** — implementation addition |
| §8 army-only limit, resume when cash returns | **built** |
| §9 High5 vantage, LOAD, same-day reload | **built** |
| §10 armed CHASE + full EXIT, two projected chases, tick trimming, exit priority | **built** |
| §11 free gear shift, logged | **built** |
| §12 adopt / external buy / partial / full exit | **built** |
| §13 state machine | **built** |
| §14 campaign log — trades and decisions, grouped by day | **built** |
| §15 cockpit: curve, both charts, day-grouped log | **built** |
| §17 / §17.1 one V number, on the card, the cockpit and the panel | **built** |
| §15.1 gear + tier choosable in the cockpit | **built** |
| §19 WATCH / LIVE, bot fires by itself | **built** (DRY replaced by the offline simulator) |
| §8.3 concurrency limits, global buying-power manager | **NOT built** — commander's discipline |
| §14.4 full completion metrics (fees, duration, ROC) | **NOT built** |
| §15 campaign-spanning chart | **NOT built** — 5-day panel + day-grouped log instead |
| §16 accounting views | **NOT built** |
| Portfolio orchestrator (candidate ranking, army split) | **NOT built** |

### v0.4.0 amendment log

1. **Gear ladder → 10 / 15 / 20 / 25** (§6, §6.1, §17). G5 arms above a 25% range. The previous 15/20/25/30 pass over-corrected: it left almost everything on G1's timid −6%/−4% ladder.
2. **V is one number everywhere** (§17.1). The candle panel's grid-era read-out (mean completed-day range, plus a `/3` grid hint) is gone; it now shows `V …% → G…` from the same `calc_volatility(High5, Low5)` the gearbox uses. The controller computes it from the five completed daily bars it already fetches for the vantage.
3. **A FLAT stock publishes chase 1** (§10). The projection helper numbered from 2 in both states, so a flat stock's chart drew chases 2 and 3 and omitted chase 1 — which the flat card had been printing all along.
4. **Gear and exit tier are choosable in the cockpit** (§15.1), writing through to the card and reaching the engine on the next poll. Picking a gear drops AUTO; the AUTO button hands it back.
5. **"Cancel all" became "Cancel N resting"** (§15.2) — disabled when nothing rests, listing the orders in its confirmation, and documented: it exists because the bot never re-prices a resting order, so a shifted gear needs the stale one cleared before the new line can arm.

### v0.3.0 amendment log

1. **The capital cap is gone** (§8). `CAMPAIGN_CAP_UNITS`, the `CAPPED` buy state and `cap_units` in the card config were removed. The army is the only wall, re-checked every poll. The 32-unit figure survives only as the reference scale of the normalized tables.
2. **The gear is no longer pinned by a live campaign** (§11). AUTO tracks the 5-day range continuously, deployed or not; MANUAL holds. Nothing but the average cost is history, so both lines simply move. The change is logged as a `GEAR` / `TIER` row instead of an "override".
3. **The daily v^ grid was removed** (§3) so this bot could be stabilised alone. Recoverable from commit `e148da6`; its manual is kept. The card lost its second button; the controller lost its strategy switch, the grid-scale picker and the shared `_push_ui`.
4. **Manual Buy/Sell was removed** (§15, §19). The bot exists to send the offer when the curve touches the line. `Cancel all` remains as the escape hatch. Hand trading in the broker app is unaffected and still reconciles (§12).
5. **The cockpit was rebuilt** (§15) around the price curve, with the EXIT named first and the next buy carrying its size, followed by the next two projected chases. The campaign log came back in its old `RECORDED FILLS` form, now with a header row per trading day.
6. **The campaign log records the day** (§14.2) and holds decisions (`ADOPT`, `GEAR`, `TIER`) alongside trades.
7. **Two projected chase lines are published** beyond the armed one (§10), so the depth of the ladder ahead is visible before it is needed.

### The v0.2.0 defect

v0.2.0 shipped an autopilot that **did nothing at all**, and did it quietly. `_push_ui` — the one function every poll ends in — still read grid-only engine fields (`engine.anchor`) that the campaign engine does not have. Every cycle raised `AttributeError` after the engine had already computed correctly, so the cockpit never received a payload, no chart was ever drawn, and the failure surfaced only as a line in `logs/autopilot443.log`.

Two things let it through: the window was tested against hand-written payloads rather than payloads the controller actually produced, and the controller's poll loop catches every exception per stock so it can survive a bad network read — which also swallowed this one.

`scripts/test_autopilot_ui.py` now drives the **real controller** through a full `_cycle` against a fake provider and asserts on the payload it emits, including that no grid-only field survives in it.

---

## Appendix A — Normalized Gear Reference Tables

Long-term normalized references. Assumptions:

```text
Vantage = 100
initial LOAD capital = 1 unit
fractional theoretical sizing
32 units of spend as a common yardstick (not a cap — see §8)
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
1. Samsung (KR)        (12.3%)  [AUTO]  V 18.2% → G2
                                Avg Cost [280,315]  Shares [31]   DEPLOYED

Total Cost: 8,689,765   Current: 232,000   Gap: -17.24%

Chase 1: 266,299 × 21   Chase 2: 260,922 × 35   Chase 3: 255,674 × 58
   T1: 285,921             ▶ T2: 291,528 × 31      T3: 297,134

                         Gear (G2 Moderate)   Exit (T2 +4%)   [ V-COMMANDOS ]
                         [-7% / -5% ×2/3]     [ T3 +6% ]
                         gear: ②  live        [ T2 +4% ]
                                              [ T1 +2% ]
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
