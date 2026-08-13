# Gearbox V-Commandos Manual

**Version:** 0.10.0 — *the card system's spec; the bot role returned to the v^ grid*
**Internal strategy ID:** `V_COMMANDOS_GEARBOX` — retained for state-file compatibility
**Subtitle:** Fixed-Gear V-Campaign Trading System
**Status since 2026-08-13:** **the CARD system, live** (`core/calc.py`, `gui/stock_row.py`, `scripts/test_calc.py`) — **the campaign BOT, retired** (engine + cockpit recoverable from commit `f011d8f`; never run LIVE).

> ### v0.10.0 — the campaign is the commander's hand, not the bot's (2026-08-13)
>
> The commander's ruling: the autopilot's real advantage lies in the v^
> system — chasing the curve in real time inside a fixed daily ladder,
> harvesting the tooth cycle. The V-Commandos campaign is better done the
> convenient way: **read the numbers off the card, type them into the Toss
> app.** So the bot role rolled back to the Daily v^ grid
> (`Daily v^ Grid Autopilot Manual.md`), and this manual's campaign ENGINE
> (§§10–13, the cockpit of §15, the runtime rules of §§18–19) was retired
> on 2026-08-13 — code deleted, recoverable from `f011d8f`, exactly the
> courtesy the grid received on 2026-08-01.
>
> **What stays live from this manual — the card system:**
>
> ```text
> §5–§7    unit sizing, the five-speed gearbox, the exit-tier split law
> §6.1     the 10/15/20/25 volatility ladder (V = 5-day span, §17.1)
> §6.2     the heavy-unit entry floor (▲heavy)
> §9.1     the Dynamic High5 vantage the EMPTY card's LOAD hangs off
> §17      the card as the gear dashboard
> Appendix A/B — the normalized ladders and the campaign card
> ```
>
> `scripts/test_calc.py` keeps checking all of it against these tables.
> Campaign state saved at `ticker#VCG` in `data/autopilot_state.json` is
> left untouched by the grid, so a future restoration finds its history
> intact — the same promise §3 once made in the other direction.
>
> Historical v0.9.0: **records the v^-shaped rewrite as the specification.** The engine and controller were rewritten on the old v^ grid's discipline (2,116 → 626 and 1,869 → 831 lines) because the previous build had grown safeguard on safeguard until its behavior could not be anticipated. This manual now describes what the small bot actually does: there is **no same-day reload** (§9.3 — selling out stands the bot down; arming a campaign is the commander's act), the state machine is **three states** (§13), and order ownership is **runtime plus a client-id prefix, with no durable store** (§12.6). Two behaviors are stated the way they work because they are *wanted*: **off is off** — stopping WATCH, leaving LIVE, or closing the cockpit never cancels a resting order, which stands at the broker like any hand order (§15.2); and the campaign log's hand-trade prices are honest estimates while every trading line derives from the broker average alone (§14.2). v0.9.0 also unifies **V**: the card once read it from four completed sessions plus today's partial bar and could sit a whole gear below the cockpit — both now read the five completed sessions (§17.1) — and the engine's AUTO honors the heavy-unit entry floor while EMPTY, exactly as the card always showed (§6.2).
>
> Historical v0.8.0: the card grid detaches from the bot (§12.7). The autopilot keeps its **own** Gear, AUTO flag and exit tiers, saved with its campaign and remembered across restarts. The card grid is the commander's worksheet for trading by hand: it refreshes only when **Save & Refresh** is pressed, the poll thread never writes to it, and changing a card cannot move a live line. The single crossing is the card's **`sync to autopilot`** button, which copies that card's Gear and tiers into the bot when it is pressed and at no other time. The card regained its **big Gear number** and its exit tiers went back to plain **check boxes**; the cockpit needs neither, because the cockpit is not a worksheet.
>
> Historical v0.7.0: the simplified reconciliation and UI pass. Normal polling reads price, broker shares/average, OPEN orders, and buying power. Each newly observed share-quantity delta is accepted once with the current broker average, with no delayed-history rewrite or net-zero reconstruction. External, offline, mixed, or unknown full sells reset to EMPTY/Dynamic High5 without reload; only an immediate proven bot full sell with an actual execution price may auto-reload. LIVE curve-chasing uses the bot's own selected Gear, tiers and Vantage against the actual broker position (§§10–12).
>
> The cockpit no longer exposes a broad Cancel button or a conditional “use rolling high” control. Dynamic High5 is the normal EMPTY-state Vantage; clicking a candle clearly pins that candle's high, and the visible reset control returns to Dynamic High5. Routine Gear, tier, and Vantage choices apply directly; entering LIVE uses inline modeless confirmation (§15). The campaign log records position deltas only and separately displays the current EMPTY/DEPLOYED status (§14).
>
> Historical v0.5.0: multi-select exits; self-healing bot-owned orders; Dynamic High5 plus an explicit EMPTY-state pin; trade-only campaign log.
>
> Historical v0.4.0: gear ladder 10/15/20/25 (§6); V is one number everywhere (§17.1); gear/tier choosable in the cockpit (§15.1); the internally named `FLAT` state publishes chase 1 (§10).
> v0.3.0: no capital cap (§8); the gear is not pinned by a live campaign (§11); the daily v^ grid removed (§3); manual Buy/Sell removed (§19).

---

## 1. Purpose

Gearbox AUTOPILOT is a campaign-based trading system designed to:

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
+ selected Exit Tier(s)
= next CHASE line and selected EXIT line(s)
```

The bot does not need perfect knowledge of every historical Step to continue safely. It can adopt or reconcile a position already traded manually.

---

## 3. Whose Strategy This Is — as of 2026-08-13, the commander's

*(v0.10.0 inverts this section. From 2026-08-01 to 2026-08-13 the
`V_COMMANDOS_GEARBOX` engine was the app's only bot and the grid was the
removed one; the paragraphs below record the layout as it stands now.)*

The V-Commandos campaign is executed BY HAND: the card computes the LOAD,
CHASE and EXIT lines, and the commander places them in the Toss app. The
card's one bot button:

```text
[ AUTOPILOT ]   arms WATCH on that stock and opens the Daily v^ grid cockpit
```

The campaign ENGINE this manual specified (§§10–13) was **retired on
2026-08-13** so the bot role could return to the grid. It was working and it
is not repudiated — engine, cockpit and offline suite are recoverable from
commit `f011d8f`. Restoring it means bringing back `core/vcommandos.py`,
`gui/campaign_window.py` and the engine half of its test suite, and giving
`gui/autopilot_ctrl.py` a strategy switch again.

Campaign state is saved under `ticker#VCG` in `data/autopilot_state.json`.
The grid saves under the bare ticker key and never touches `#VCG` — so a
future campaign restoration finds its own history intact, exactly the
courtesy this section once promised in the other direction.

The two trading logics must never share a holding. That is why only one runs
as the bot — and today the other one runs through the commander's hands.

---

## 4. Core Philosophy

### 4.1 Campaign Thinking

A campaign begins when LOAD fills and ends only when actual broker holdings become zero.

```text
EMPTY
→ ARMED
→ LOAD
→ CHASE as needed
→ full EXIT
→ EMPTY
```

### 4.2 The Exit: one clean shot, or a ladder

**One armed tier is the default, and it is the cleanest campaign**: the whole position leaves at one line, the holding is zero, and the campaign is unambiguously over. When the holding reaches zero the bot stands down — LIVE drops to WATCH and the next campaign waits to be armed (§9.3). That remains the recommended way to run it — v0.1.0 of this manual argued for it, and the argument still holds: partial exits leave residual positions, and residual positions make the average, the accounting, and "is this finished?" harder to read.

**But the choice belongs to the commander, and it is a multi-select.** Arm two tiers and the holding halves; arm three and it thirds (the split rule is §7.3). A tier that fills is spent; the ones still armed stay armed; **the campaign is over only when the broker says the holding is zero.**

The reason to want this is not tidiness — it is that the buy side stays live the whole time. Sell a third into a bounce, watch the price fall back, and the CHASE line is still there to buy into. That flexibility is why the three-tier ladder was in the card system for years, and it is back.

The trade-off, stated plainly: a laddered exit *is* the residual-position problem the single exit was designed to avoid. Both are supported because both are sometimes right; neither is hidden from you.

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
| 5 | Extreme  | > 25% | −10% | −8% | ×1.0 | 5%/7%/9% | Tier 2 | 5 | 70.33 | 26.09u |

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

The original ladder was 8 / 12 / 16 / 20. Under it a stock with an ordinary 22% five-day range was handed **Gear 5 ×1.0**. Gear 5 is a weapon for a stock that genuinely swings; handing it to a merely-normal stock is how a martingale runs out of funding before the V completes.

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

The card shows `▲heavy` next to the gear when this floor, not volatility, chose the gear. The **heavy floor** applies only while EMPTY; after deployment, AUTO may still follow the live five-day volatility Gear and MANUAL may still be changed by the commander (§11).

Since v0.9.0 the **engine's own AUTO applies the same floor while EMPTY**, so the card's `▲heavy`, the cockpit's V read-out, and the gear the bot actually LOADs on are the same fact. Deployed, the floor drops away in both places.

---

## 7. Exit Tier System

Each Gear has three selectable Exit Tiers. One tier gives the default one-shot exit; two or three create an optional staged exit.

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

### 7.2 Arming tiers

Any combination of the three may be armed; at least one always is (disarming the last one is refused — a campaign with no way out is not a state worth allowing).

```text
one tier armed     exit_price = avg × (1 + rate)
                   sell_qty   = every share held

two or three       the holding is split across them (§7.3); each line is
                   its own order at its own price
```

Changing the selection applies immediately on the card and in the cockpit. It does not show a routine confirmation dialog and it is not written as a campaign fill. Entering LIVE remains the explicit **inline, modeless** confirmation boundary (§15).

### 7.3 The split rule

The holding divides across the ARMED tiers as evenly as possible, and any remainder goes to the **middle** tier first, then the low, then the high — so the centre is never smaller than the outsides:

```text
all three armed:   9 → 3 / 3 / 3      5 → 2 / 2 / 1      4 → 1 / 2 / 1
                   1 → 0 / 1 / 0      (a lone share goes to the middle)
two armed:        10 → 5 /  ·  / 5
one armed:        11 → the whole position
```

### 7.4 What happens as tiers fill

```text
a tier fills      it is spent. The remaining shares re-split across the tiers
                  still armed, so the ladder always describes what is held.
all armed tiers
spent, shares
remain            the ladder restarts on the remainder (a partial fill, a
                  hand trade — the lines must never describe a fiction).
a CHASE fills     EVERY armed tier is re-armed on the new, larger holding.
holding hits 0    only then is the campaign over.
```

The CHASE line stays live through all of it. Selling a third and then buying into a further fall is the point of arming more than one tier.

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

### 9.1 The Dynamic High5 vantage

The LOAD hangs under the **vantage**, and it is a live number:

```text
watch_vantage = max(the previous FOUR completed-session highs,
                    today's high so far)
```

A five-session window whose fifth session is the one in progress. `today's high so far` is the session high the provider reports, stretched to the live price on every poll.

**Why the live session is in the window.** A peak made this morning is a real left endpoint. Without it the bot could only enter on a pullback from a high that was already history yesterday — it would miss the whole shape of a day that runs up and then sells off. With it, the LOAD line rises the moment the peak does:

```text
vantage 100, gear 3  →  LOAD 92.00
price runs to 110    →  vantage 110, LOAD 101.20   (immediately)
price falls to 101.20 →  the LOAD condition is met
```

This is a trailing-drawdown entry, not a picture of a V.

One thing may override it:

```text
pinned by hand     a day the commander clicked on the 5-day chart; its HIGH
                   becomes the vantage until released.
```

**What the bot deliberately does NOT do:** it never searches old history to date or reconstruct the end of a campaign. **Any** full sell — the bot's own, a hand trade, offline — returns the stock to EMPTY and the Dynamic High5 (§9.3); a manual pin remains available when the commander wants a different Vantage.

At LOAD fill the vantage that generated it is frozen as `campaign_vantage`. The campaign does not reset its vantage each day.

### 9.2 Standard LOAD

```text
load_price = campaign_vantage × (1 - gear_load_drop)
load_size ≈ 1 unit
```

### 9.3 Selling Out Stands the Bot Down — the reload was removed in v0.9.0

When the holding reaches zero — the bot's own exit or a hand sell, it does
not matter which — the campaign is over: the fill log clears, LIVE drops back
to WATCH, and the engine returns to EMPTY on the Dynamic High5. **Starting a
campaign is a deliberate act**, so the bot never begins another one by
itself.

The v0.1.0–v0.8.0 same-day reload (a flat −3% under the proven final bot
sell fill, logged `RELOAD`, expiring at the close) was removed with the
v0.9.0 rewrite. It depended on proving that the exit was the bot's own at its
actual execution price — exactly the kind of history-dating the small engine
refuses to do. Re-entering the same session is still available by hand: pin
the day's high (§15.3), or simply arm LIVE again when the LOAD line reads
right.

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

The bot watches one buy line and the selected sell line or lines:

```text
EMPTY      one LOAD BUY
DEPLOYED   one CHASE BUY + one-shot or tiered EXIT SELL lines
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
EMPTY      armed 'load'   +  projected 'chase1', 'chase2'
```

An EMPTY stock has no chase armed — the LOAD is its live line — so **chase 1 is a projection there**, exactly as the EMPTY card prints `Load / Chase 1 / Chase 2`. (Historically, before v0.4.0, the engine numbered projections from 2 in both states, so the internal `FLAT` state drew chases 2 and 3 and silently omitted chase 1.)

**Order priority:** when both lines are crossed in the same poll, the **EXIT wins** — the campaign always prefers to finish. Any resting buy of ours is cancelled first.

**Price trimming:** a BUY line is floored to the tick grid (never bids above the strategy line) and a SELL line is ceiled (never asks below it). KR names snap to the KRX band tick; US names to the cent.

### 10.1 Nothing rests in advance — and nothing is stranded

The engine is a **watcher**: it sends no order until the price actually crosses a line. There is no order sitting at the LOAD waiting all day.

When a line *is* crossed, the bot sends a LIMIT/DAY order at that line, tagged with a strategy-prefixed `clientOrderId`. Ownership is runtime: the broker order id returned by the acceptance is remembered for this run (§12.6). While a bot order rests, it blocks its side.

So on every poll the engine checks its own resting order against the line it is supposed to be at. If the price or quantity no longer matches — the gear shifted, a chase moved the average, a tier was re-armed — it **cancels its own order** and re-arms the current line on the next poll:

```text
resting order == the current line   →  leave it, it is doing its job
resting order != the current line   →  cancel it; the next poll re-arms the
                                        current line
partially filled                    →  reconcile the filled portion first;
                                        never replace it as though nothing filled
not ours                            →  never cancelled; the bot does not place
                                        on a side where a foreign order works
```

This is the 443 engine's `stale LOAD` / `stale SELL` rule, and it is not optional: without it a gear shift leaves an order at the old price that the new line can never get past. The commander does not have to clear the way by hand — **while LIVE**. In WATCH nothing is placed *or* cancelled (§15.2, §19). There is no broad cockpit Cancel button, and app/web orders remain entirely under the commander's control.

---

## 11. Gear Changes During a Campaign

The gear may be changed at any time, deployed or not.

Nothing in a campaign depends on the gear that opened it. The only piece of history the engine carries is the **average cost**, and both lines are derived from it on the spot:

```text
next CHASE   = actual_avg × (1 - new_gear.chase%)
selected EXIT = actual_avg × (1 + new_gear.tier_pct)
```

So a shift is not a repair — it is just a different pair of lines from the same position. After a change:

```text
keep actual holdings
keep actual average cost
keep campaign ID
replace the CHASE line and the projected ladder with the new gear
replace every selected EXIT using the new Gear's tier percentages
save the preference for the next refresh and restart
```

**As built:** the engine holds its own Gear and AUTO flag, and the cockpit edits them directly. In AUTO the gear tracks the 5-day range continuously, deployed or not — a stock that turns violent mid-campaign gets a deeper ladder without being touched. In MANUAL it holds whatever the commander picked, and it survives a restart because it is saved with the campaign.

The card's gear is a **separate number** that means something different: it is what the commander is planning to do by hand, and the bot must not act on a figure that was only being tried out. When the two should agree, the card's `sync to autopilot` button says so (§12.7).

A Gear or tier change is configuration, not a fill. It may be written to the diagnostic app log, but it never creates a row in `RECORDED FILLS` (§14).

The heavy-unit floor (§6.2) is the one exception: it applies only while EMPTY, because it is an **entry** rule about whether a position can be opened with useful resolution — not about one that already exists.

The default recommendation still stands:

> Keep one Gear for one campaign whenever you can, so backtests and live results stay interpretable. Shift it when the stock's behaviour actually changes, not to chase a feeling about the position.

---

## 12. Manual App Trading and Bot Resumption

The fixed-Gear architecture supports mixed app/bot operation better than the legacy Step-dependent architecture. This is a first-class use case: **the commander trades by hand in the broker app using the numbers on the card, while the bot chases its own lines on the same broker position.** The two read the same holdings and the same average cost; what they do not share is the gear preference (§12.7).

### 12.1 Broker Data Is the Source of Truth

Each normal poll reads the smallest authoritative snapshot:

```text
current price
actual broker shares and average cost
OPEN orders
actual buying power
```

Normal polling does **not** query CLOSED history. Reconcile the current snapshot in this order:

```text
actual shares > 0
    → continue or adopt the real quantity and average; recompute CHASE/EXIT

actual shares == 0  (any path: bot exit, hand sell, offline sell)
    → the campaign is over — clear the fill log, stand down (LIVE → WATCH),
      reset EMPTY with Dynamic High5 (§9.3)
```

Each newly observed share-quantity delta is accepted and logged exactly once with the current broker average. A delta on the same side as the bot's own pending order is attributed to that order at its limit price; anything else is `EXT`, its logged price an estimate — a buy implied by the average move, a sell at the current quote (§14.2). Later history never rewrites an observation, and the bot never reconstructs hidden fills or a net-zero round trip. **No estimate ever feeds a trading line** — every line derives from the broker's quantity and average alone.

Chart/Vantage maintenance does not add two equivalent reads when a cockpit opens. One six-bar candle window supplies the completed-session metadata **and** the five-day chart, including today's earlier intraday high; later candle refreshes occur at five-minute boundaries. Empty or unavailable maintenance data retries at most once per minute instead of burdening every five-second decision poll.

### 12.2 ADOPT_POSITION

If actual shares are positive but no active bot campaign exists, the bot adopts the position automatically on its first good poll: it opens campaign status from the current Gear and tiers and computes both lines from the broker's average cost. It does not need to know whether Step 2, 3, or 4 was completed by hand.

Adoption is current status, not a transaction, so opening the window does not append an `ADOPT` row to `RECORDED FILLS`. The cockpit shows the position's current EMPTY/DEPLOYED status and does not show a manual-modification banner.

### 12.3 External BUY

If a manual BUY changes holdings or average cost:

```text
refresh actual qty and average
recalculate next CHASE
recalculate every selected EXIT
    record the definite quantity change; use the actual fill price when known
```

The card updates to the same broker quantity and average. A number copied from the card into the broker app therefore changes the next lines in exactly the same way as an automatic fill.

### 12.4 External Partial SELL

A partial manual SELL is not part of the normal strategy.

If detected:

```text
reconcile the real remaining quantity and average
recalculate the CHASE and selected EXIT lines
    record the definite quantity change; the logged price is the current
    quote — an estimate, marked [hand] (§14.2)
```

### 12.5 External Full SELL

If broker holdings become zero, the outcome is the same on every path:

```text
any full SELL — the bot's own, a hand trade, or offline
    → the campaign is over: log it, clear the fill list, stand down
      (LIVE → WATCH), reset EMPTY with Dynamic High5
    → no reload is armed (§9.3)
```

This makes app and bot trading innocuous to each other. If the commander sells everything in the app while the bot is open or closed, the next good snapshot safely resumes EMPTY/Dynamic High5 without searching history.

### 12.6 Order Ownership — runtime plus prefix, no durable store (v0.9.0)

Every order the bot sends carries a strategy-prefixed `clientOrderId`
(`vcg-ap-…`), and Toss enforces idempotency on it: re-sending the same id
with a different body is rejected (`idempotency-key-conflict`). The broker
order id returned by the acceptance is remembered for the run, and **the bot
cancels only orders it can prove are its own** — an id it placed this run.
Nothing is cancelled or claimed on price/quantity resemblance.

**Ownership does not survive a restart, and that is accepted.** The bundled
API spec (`Toss_api.json`) is explicit that the OPEN-orders list returns
`Order` rows *without* `clientOrderId` — only the placement acknowledgment
echoes it. So after a restart, or after a send whose response was lost, a
resting bot order can no longer be proven ours. It is treated exactly like a
hand order: **never cancelled, never duplicated** — it blocks its own side
until it fills or expires at the close (every bot order is DAY). That is the
safe direction, and it matches how the commander already trades: the truth is
the Toss account, and an order standing there is an order, whoever sent it.

A definite rejection clears the pending intent and backs off (five minutes
when Toss says the buying power is out, five when the market is closed, one
minute otherwise; an `opposite-pending-order-exists` answer simply retries
next poll, because a foreign order is blocking the side). A transport failure
clears it the same way — if the order was in fact accepted, the next poll
finds it resting and the foreign-order rule above takes over.

**Toss limitation:** app-side conditional/reserved orders are not exposed by
the OPEN-order feed until they trigger. Because the bot cannot see and yield
to those dormant instructions, do not arm an app-side conditional order for a
ticker while its autopilot is LIVE. Visible app/web OPEN orders are safe:
they block their side and are never cancelled by the bot.

---

### 12.7 The Card Grid and the Cockpit Are Detached

The card grid and the autopilot look at the same stock, but they are two separate instruments, and since v0.8.0 they are wired that way.

```text
CARD GRID                          AUTOPILOT
a worksheet for hand trading       the thing that actually trades
refreshes on Save & Refresh        refreshes every poll
its own gear and exit tiers        its OWN gear, AUTO flag, exit tiers
saved in positions.csv             saved with the campaign state
never moves a live line            moves every line

                 [sync to autopilot]  →  one way, on the button only
```

**Why.** Three reasons, in the order they were felt. First, the window was heavy: driving sixteen Tk cards from the poll thread on every tick made the grid stutter, and at its worst left the cards unclickable behind the cockpit. Second, a card could not be used for thinking — trying a deeper gear on a card to see what the ladder would look like changed what the bot was about to do, so a question became an instruction. Third, the card is not the trade. It is the sheet the commander reads while placing orders in the broker app, and it has no business being the authority for a bot that sends real orders.

**The rules.**

```text
The poll thread touches ONE thing on the main window: the AUTOPILOT
button's colour, which reports WATCH / LIVE. It reads a badge; it does
not rewrite the sheet underneath it.

Save & Refresh re-reads price, shares and average cost from the provider
and re-sorts the grid. It sends nothing to the autopilot.

The cockpit's Gear, AUTO and tier controls write to the ENGINE. The card
does not change.

The card's `sync to autopilot` button copies that card's gear and exit
tiers into the engine — when pressed, and at no other time. If the bot
is not watching that stock, the button says so and nothing happens.

The bot's settings are saved with its campaign, so a restart resumes on
the gear the bot was using, not on whatever a card happens to show.
```

**What it costs.** The two can now disagree, and that is the point — but it means the cockpit is the only place to read what the bot will actually do. The card's gear is a plan; the cockpit's gear is the order. When they differ, the cockpit is right.

---

## 13. Campaign State Machine — three states (v0.9.0)

The broker's share count is the only authority the engine trusts, so the
share count is the state:

```text
OFF
    not watched; no polling, no order actions

EMPTY (internal state key: `FLAT`)
    no shares
    Dynamic High5 (or an explicit pinned candle high), LOAD line live

DEPLOYED
    actual shares > 0
    watch CHASE and the armed EXIT ladder
```

(The cockpit shows `ARMING` for the moment between opening and the first
good poll.)

The v0.1.0–v0.8.0 states survive as *behaviors*, not states: an unresolved
bot order blocks its side and is reconciled partial-fill-first
(`CHASE_PENDING` / `EXIT_PENDING`); a foreign order blocks placement on its
side (`PAUSED_MANUAL_ORDER`); a failed or priceless poll skips the whole
cycle without placing or cancelling (`PAUSED_RECONCILE`); reaching zero
shares stands the bot down (`COMPLETED`); and there is no `RELOAD_ARMED`
(§9.3).

Opening a cockpit does not cause a state transition or manufacture a fill.
Closing it in WATCH stops the watch immediately — resting orders stand
(§15.2); in LIVE the campaign keeps running in the background with the
card's button still colored.

---

## 14. Campaign Fill Log

There is no persistent campaign object (v0.9.0). "A campaign" is the fills
since the holding was last zero; the log clears the moment shares reach zero,
exactly as the v^ engine cleared its day log.

### 14.1 Campaign Header — derived, not stored

Everything the cockpit banner prints is derived live from the log and the
snapshot, so nothing can drift from the account:

```text
campaign label     'TICKER · N fills' — truthy while a position is held
selected gear / exit tiers / AUTO flag
campaign_vantage   frozen at LOAD while EMPTY→DEPLOYED
campaign_start     timestamp of the first fill in the log
chase_count        CHASE rows in the log
campaign_low       cheapest buy in the log
cost               current shares × average (nothing persisted as a peak)
```

### 14.2 Log Record — trades only

**The campaign log records what was bought and sold. Nothing else.**

```text
date            market-local trading date — the log groups by day
ts              MM/DD HH:MM
source          BOT / EXT   (EXT = hand trade, shown as [hand]; older saved
                campaigns may still carry MIXED / UNKNOWN rows)
kind            LOAD / CHASE / T1 / T2 / T1/T2 / SELL
price, qty      signed quantity
shares, avg     the position AFTER the trade
```

A BOT row's price is the limit price of the order that filled. An `EXT`
row's price is an honest estimate — a buy implied by the average move, a
sell at the current quote. Quantities and averages are broker truth, and no
logged price ever feeds a trading line (§12.1).

The cockpit renders it as `RECORDED FILLS`, with a header row whenever the trading date changes, so a campaign that runs for days is read day by day. Reopening the cockpit does not add a row.

The panel always has a separate live status summary, even when there are no fills to list:

```text
EMPTY      no shares deployed · waiting at LOAD …
DEPLOYED   N shares @ average · next CHASE … · selected EXIT …
```

That summary is not history and is not persisted as a campaign fill. Consequently a deployed campaign and an empty campaign never look identical merely because their recorded-fill lists are empty.

### 14.3 What is deliberately NOT in it

Configuration and observation are not trades. Opening a window, changing Gear, arming a tier, adopting an already-held position, pinning the vantage, and polling the broker do not write rows. A log where a gear fiddle or repeated “shares deployed” announcement sits next to a fill is a log you stop reading.

Those events go to `logs/autopilot443.log` with a timestamp, where they can be reconstructed if a campaign ever needs auditing, without diluting the record of what actually traded.

### 14.4 Completion Metrics

Shown live in the cockpit banner, all derived (§14.1):

```text
campaign_start
chase_count
campaign_low
cost           (current shares × average — v0.9.0 keeps no persistent peak;
                the true peak is readable from the day-grouped log)
```

Not yet computed: peak deployment, trading-days-open, fees, taxes, net profit, return on maximum capital. See §21.

---

## 15. The Cockpit

Opened by the card's AUTOPILOT button; opening it arms WATCH.

```text
header   name · campaign state · market phase · LIVE
gearbox  [AUTO] gear [1][2][3][4][5] Balanced -8%/-6% ×3/4
                exit [T1 +3%][T2 +5%][T3 +7%]      V 18.2% → G3
banner   G3 Balanced · LOAD -8% · CHASE -6% ×3/4 · EXIT T2 +5% (full)
                     · Vantage 100.00 (Campaign Vantage · frozen at LOAD)
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

Gear and exit tier are chosen here. This is where the campaign is actually being watched, so this is where the decision belongs — and since v0.8.0 it is the **only** place a live line can be moved (§12.7).

Both controls **write straight to the engine**, which uses the new lines on its very next poll and saves them with the campaign. Picking a gear also drops AUTO (a manual choice must not be overwritten by the next volatility read); the **AUTO** button hands the choice back to volatility. The card grid behind the cockpit does not change and is not consulted. `V` remains the five-day volatility percentage (§17.1); the price anchor is spelled out as **Vantage** instead of the opaque abbreviation `V.P.`.

Selections apply directly without a confirmation popup. Entering LIVE uses an **inline, modeless confirmation** inside the cockpit. Only the selected Gear uses its identity color — **G1 red, G2 orange, G3 yellow, G4 green, G5 blue** — while the other four stay neutral grey; the brown information banner remains neutral. Exit-tier buttons use distinguishable armed, unarmed, and spent states rather than painting every tier the same hot red.

### 15.2 Orders outlive the bot — deliberately (v0.9.0)

There is no cockpit Cancel button, and **stopping the bot never cancels an
order**. Leaving LIVE for WATCH, closing the cockpit, the session ending, the
position closing — none of them touch what is already resting at the broker.
Off means off: the bot stops *acting*, and what it already sent stands,
exactly like an order the commander placed by hand in the app. This is wanted
behavior — the commander trades in the Toss app on the same book, and a
resting order may well be one worth keeping. Watching a sent order ride
without the bot re-managing it is also how the bot's behavior stays legible.

The engine's self-healing (cancel a stale order, re-arm the line) runs only
while LIVE; in WATCH nothing is placed *or* cancelled (§19). The one way to
remove a resting order is the Toss app itself. App/web orders are foreign and
are never cancelled by the bot on any path.

### 15.3 Vantage selection

The strip shows the current Vantage and one unambiguous source:

```text
Dynamic High5             normal EMPTY trailing vantage
Pinned · YYYY-MM-DD       a candle high selected by hand while EMPTY
Campaign                  frozen vantage after LOAD
```

The 5-day chart is always visible; there is no separate “5d chart” button. While EMPTY, its instruction says that a candle can be selected, the pointer and selected candle make that action visible, and the candle's HIGH becomes the pinned Vantage immediately. A clearly labelled **Return to Dynamic High5** control appears only for a pin. Pinning is disabled while shares are deployed because CHASE and EXIT then derive from the broker average, not a new entry Vantage.

The sell line reads first because it sits at the top of the chart; the next buy and **its size** read below it, then where the ladder goes after that. Both charts draw the same rows: the armed buy, the broker average, and the EXIT bold; the projected chases and the Vantage soft. An unfundable buy line remains visible on the graph in grey and is labelled `(no army)`; this disables only that buy, not sell watching. Buying power is re-evaluated on every fresh poll, so a newly fundable reserve automatically restores the armed color and buy behavior. The condition banner reports one precedence-ordered status, such as “LOAD crossed but no reserve army remains,” so it cannot simultaneously claim “no line crossed” and “buy crossed”.

Live BUY/SELL annotations reserve a right-hand label gutter or clamp/wrap their text so quantity and pseudo/WATCH wording stay visible instead of being cut off at the chart edge. The 5-day OHLC text follows the historical candle convention: rising or unchanged candle red, falling candle blue.

**There are no manual Buy/Sell buttons.** The point of the bot is that the offer goes out when the curve touches the line. WATCH shows the crossed line and says plainly that it did not send; LIVE sends.

The full campaign-spanning chart of the original §15 (a LOAD marker, every chase marker, the campaign low, the exit marker, with a 10-trading-day view policy) is **not built**. The 5-day panel plus the day-grouped log carries the same information.

On each stock card, action rows are visually separated from the calculation rows and the AUTOPILOT button sits one line lower. Cards form two stable groups: **DEPLOYED first**, sorted by `(current − broker average) / broker average` descending and colored red/blue by sign; **EMPTY second**, sorted by `(current − LOAD) / LOAD` ascending and colored purple below LOAD or orange above LOAD.

---

## 16. Accounting Views

Not built. The card shows per-stock cost basis, army %, and the live gap; the campaign window shows unrealized P&L, peak deployment in units, and the gross target of the armed exit.

Realized campaign profit, fees/taxes, campaign duration statistics, completion-rate comparisons, and buy-and-hold benchmarking remain future work (§22).

---

## 17. Gear Selection Dashboard

**As built:** the dashboard is the card itself. Each card shows

```text
V 18.25% → G3           the volatility gear, tracked live
V 18.25% → G5 ▲heavy    the heavy-unit floor (EMPTY only) overrode volatility
```

and the Gear control uses the same red/orange/yellow/green/blue G1–G5 identity as the cockpit.

The recommendation map:

```text
V ≤ 10%       → Gear 1
V ≤ 15%       → Gear 2
V ≤ 20%       → Gear 3
V ≤ 25%       → Gear 4
V >  25%      → Gear 5
```

### 17.1 V is one number — and one window

```text
V = 100 × (High5 − Low5) / High5
```

The span of the whole five-day window as a percent of its high — **not** a per-day average, and not a per-day average divided by anything. `core.calc.calc_volatility` computes it, `select_auto_gear` reads the gear off it, and it is the number shown on the card, in the cockpit's gearbox strip, and on the candle panel. One definition, one figure, three places, always agreeing.

**The window is the five COMPLETED sessions** — today's in-progress bar is
not in it. (The Dynamic High5 *vantage* deliberately includes today, §9.1; V
deliberately does not: a gear is chosen off finished days, not off this
morning's half-formed bar.) Until v0.9.0 the card quietly read V from four
completed sessions plus today's partial bar — one finished session short —
and could recommend a whole gear below the cockpit's. Both now read the same
completed-session window the controller fetches (the providers ship it as
`v5_high` / `v5_low`), and V prints with **two decimals**, so a 20.04% read
can never look like the 20.0% cut point while arming the gear above it.

The candle panel briefly showed a different one (the mean of the last five completed days' ranges, plus a `/3` hint) — a leftover from the daily v^ grid, where that average sized the grid spacing. It measured something the gearbox never used and is gone.

This is a recommendation, not an automatic order command. Switching the card to MANUAL lets the commander pick a more conservative gear whenever conditions warrant it.

---

## 18. Operational Safety Rules

1. **Broker state is authoritative.**
2. **One strategy intent at a time per side.** A definite rejection clears it and backs off; an uncertain submission is abandoned, and if it was in fact accepted the next poll finds it resting and it blocks its side as a foreign order would (§12.6).
3. **No duplicate CHASE while a BUY is unresolved.** Reconcile each partial fill before deciding what remains.
4. **No new campaign until actual shares are zero.**
5. **Quantities and averages are broker truth; logged prices are honest estimates.** A delta matching the bot's own pending order records that order's limit price; an EXT buy records the price implied by the average move; an EXT sell records the current quote. No logged price ever feeds a trading line — every line derives from the broker average alone (§12.1, §14.2).
6. **Recalculate from actual average cost after every fill.**
7. **Stop CHASE when the army cannot fund it** — and resume when it can.
8. **Cancel only orders proven bot-owned this run.** A foreign order blocks its side and is never touched (§12.6).
9. **On ambiguous reconciliation, pause instead of guessing.**
10. **A failed data poll skips the whole cycle** — nothing is placed or cancelled on missing data.
11. **Use WATCH before LIVE.**
12. **LIVE runs only during regular market hours** and drops back to WATCH at the close — and when the position closes (§9.3).
13. **Manual Vantage pinning is EMPTY-only.** A deployed campaign follows actual average cost.
14. **Opening a window is observation, never a fill or campaign event.**
15. **Do not rewrite accepted observations from delayed history or reconstruct net-zero activity.**
16. **There is no automatic reload.** Every path to zero shares resets EMPTY/Dynamic High5 and stands the bot down (§9.3).
17. **Stopping the bot never cancels an order** (§15.2). Closing WATCH stops the watch immediately; resting orders stand — the commander's and the bot's alike.

---

## 19. Runtime Modes

```text
WATCH
    calculate, watch, detect fills; place NOTHING and cancel NOTHING.
    A crossed line is drawn and named, and the status says it was not sent.
    An order already resting stands untouched (§15.2).

LIVE
    the bot sends the real broker LIMIT/DAY order by ITSELF the moment the
    curve crosses a line, and re-prices its own stale orders (§10.1).
    Regular market hours only; drops back to WATCH at the close, and when
    the position closes (§9.3).
```

Manual firing was removed with v0.3.0: the reason to run a bot is that the offer goes out when the curve touches the line. Trades made by hand in the broker app are still first-class — the engine detects them and folds them into the campaign (§12).

The original §19 also specified a **DRY** runtime mode (simulate orders and fills inside the app). That was not built: its job is done by `scripts/test_vcommandos.py`, an offline simulator that runs whole price paths through the real engine behind a fake broker. WATCH covers the live-observation half.

---

## 20. Configuration Baseline

Live values come from `config.json` and `data/positions.csv`; the rest are constants in `core/calc.py`.

```yaml
strategy_mode: V_COMMANDOS_GEARBOX  # internal identifier

# config.json
unit_cash_krw: 1000000          # unit_cash_usd is derived from FX
N: 20                           # army size, in units

# data/positions.csv, per stock
gear: 1..5
exit_tier: 1..3                # compatibility: lowest armed tier
t1_active/t2_active/t3_active  # the actual multi-select
auto_mode: true                 # gear follows volatility; heavy floor is EMPTY-only

# core/calc.py constants
VOL_THRESHOLDS:      [10, 15, 20, 25]
WEIGHT_GEAR_THRESHOLDS: [1.2, 1.6, 2.0, 2.5]
RELOAD_DROP_PCT:     3      # calculator only — the engine no longer reloads (§9.3)
DEFAULT_GEAR:        3
DEFAULT_EXIT_TIER:   2
rounding_mode:       HALF_UP
minimum_order_qty:   1

# core/vcommandos.py
POLL_SECONDS:        5
PROJECTED_CHASES:    2      # buy lines published beyond the armed one
```

There is no `campaign_cap_units`. The army is the cap (§8).

`positions.csv` keeps the pre-gearbox columns (`load_gear`, `buy_pct`, `t1_pct`…`t3_active`) written from the gear, so older builds and scripts still read the file. On load, a legacy bait drop becomes its gear (4%→G1 … 8%→G5); valid active tier flags are preserved, while `exit_tier` supplies a one-shot selection only when those flags are absent.

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

*(Statuses below are frozen at v0.9.0, the engine's last live day. As of
v0.10.0 every ENGINE row describes the retired bot — recoverable from
`f011d8f` — while the calc/card rows remain live and tested by
`scripts/test_calc.py`.)*

| Manual section | Status |
|---|---|
| §5–§7 gearbox, tiers, sizing | **built** — `core/calc.py`, verified against Part II |
| §6.1 raised volatility ladder | **built** |
| §6.2 heavy-unit entry floor — card AND engine AUTO while EMPTY | **built** (engine side v0.9.0) |
| §8 army-only limit, resume when cash returns | **built** |
| §9 High5 Vantage, LOAD | **built** — same-day reload **removed** in v0.9.0 (§9.3) |
| §10 armed CHASE + full EXIT, two projected chases, tick trimming, exit priority | **built** |
| §11 free gear shift, diagnostic-only (not a fill row) | **built** |
| §12 broker-authoritative adopt / external buy / partial / full exit | **built** |
| §12 four-input normal poll; each position delta accepted once | **built** |
| §12 every full sell resets EMPTY and stands the bot down | **built** (v0.9.0) |
| §12.6 runtime ownership + client-id prefix; foreign-order side block; no durable store | **built** (v0.9.0 — as stated, not more) |
| §13 three-state machine (the old states live on as behaviors) | **built** (v0.9.0) |
| §17.1 one V window — five completed sessions on card, cockpit and panel | **built** (v0.9.0) |
| §7.3 the tier split, §7.4 the ladder lifecycle | **built** |
| §9.1 Dynamic High5 + visible EMPTY-state manual pin | **built** |
| §10.1 stale-order self-healing | **built** |
| §14 campaign log — trades only, grouped by day | **built** |
| §15 cockpit: curve, both charts, day-grouped log | **built** |
| §17 / §17.1 one V number, on the card, the cockpit and the panel | **built** |
| §15.1 gear + tier choosable in the cockpit | **built** |
| §19 WATCH / LIVE, bot fires by itself | **built** (DRY replaced by the offline simulator) |
| §8.3 concurrency limits, global buying-power manager | **NOT built** — commander's discipline |
| §14.4 full completion metrics (fees, duration, ROC) | **NOT built** |
| §15 campaign-spanning chart | **NOT built** — 5-day panel + day-grouped log instead |
| §16 accounting views | **NOT built** |
| Portfolio orchestrator (candidate ranking, army split) | **NOT built** |

### v0.9.0 amendment log — 2026-08-07

1. **The engine and controller are rewritten on the v^ grid's shape** (commits `c16b91b`, `0c44249`, `4697d9e`; 2,116 → 626 and 1,869 → 831 lines). The previous build had grown safeguard on safeguard until its behavior could not be anticipated. The rewrite keeps the loop that can be said in five lines (§10) and drops the machinery; this log records what the drop changed, and this manual now describes the bot as it is.
2. **Selling out stands the bot down; there is no same-day reload** (§4.2, §9.3, §12.5, §18.16). Starting a campaign is the commander's act, every time.
3. **The state machine is three states** (§13). The old pending/paused/reload states survive as behaviors, not names.
4. **Order ownership is runtime + prefix, with no durable store** (§10.1, §12.6, §18.2). The bundled Toss spec returns OPEN rows without `clientOrderId`, so a restart-orphaned bot order is deliberately treated as a hand order: never cancelled, never duplicated, blocking its side until it fills or the DAY order expires.
5. **Stopping the bot never cancels an order** (§15.2, §18.17, §19). Off means off: WATCH, closing the cockpit, the session ending and the stand-down all leave resting orders standing, the same as orders placed by hand in the app. This is wanted behavior — recorded over the earlier "leaving LIVE clears bot orders" rule, which was never how the commander traded.
6. **V is one window as well as one number** (§17.1). The card read four completed sessions plus today's partial bar and could sit a whole gear below the cockpit; both now read the five completed sessions (`v5_high` / `v5_low` from the providers), and V prints with two decimals so a 20.04% can never masquerade as the 20.0% cut point.
7. **The engine's AUTO honors the heavy-unit entry floor while EMPTY** (§6.2), so the card's `▲heavy` and the gear the bot LOADs on are the same fact.
8. **Log prices are honest estimates for hand trades** (§12.1, §14.2, §18.5): a bot fill logs its limit price, an EXT buy the price implied by the average move, an EXT sell the current quote. No logged price feeds a trading line — lines derive from the broker average alone.
9. Housekeeping: the dead card↔bot bridge code from before §12.7 is removed (`_apply_autopilot_position`, `update_broker_position`, unused engine hooks); `positions.csv` and `config.json` writes are atomic (write-temp-then-replace). **Known and accepted:** on a non-trading day the controller folds the stale last price in as "today", which can drop the oldest session's high from the displayed vantage window until the next open; the manual pin (§15.3) covers the rare case where it matters.

### v0.7.0 amendment log — 2026-08-02

1. **The visible system is EMPTY / DEPLOYED and AUTOPILOT.** The legacy names `FLAT` and `V_COMMANDOS_GEARBOX` remain only as explicitly identified internal or historical terms.
2. **Normal reconciliation is snapshot-based** (§12): price, shares/average, OPEN orders, and buying power. Every observed share-quantity delta is recorded once with the current average; there is no routine CLOSED scan, delayed rewrite, or net-zero reconstruction.
3. **Reload proof is intentionally narrow** (§§9, 12): only an immediate proven bot full sell with an actual execution price may reload. External, offline, mixed, and unknown full sells reset EMPTY/Dynamic High5.
4. **The cockpit is modeless and stateful** (§15): only the selected Gear is colored; LIVE confirmation is inline; WATCH closes immediately when no bot order is pending; a grey no-army line automatically reactivates when fresh buying power can fund it; no manual-modification banner is displayed.
5. **Cards use two stable groups** (§15.3): DEPLOYED first, position gap descending with red/blue sign colors; EMPTY second, LOAD gap ascending with purple below LOAD and orange above LOAD.
6. **The watcher stays local and bounded** (§12): its first candle window is shared by Dynamic High5 and the chart; a real EMPTY↔DEPLOYED change rebuilds from the already-fresh ticker snapshot and cached market data instead of launching a full-catalogue network refresh.
7. **Order identity is guarded through interleaving and close races** (§12.6): accepted WORKING detail omitted by OPEN is still cancellable by stable id; positive terminal fills wait for proven holdings convergence; UNKNOWN/MIXED movement never grants replacement permission; LIVE close cannot slip between authorization and durable client-id binding.

### Historical v0.6.0 amendment log (superseded where v0.7.0 differs)

1. **Manual trading and LIVE automation are one campaign path** (§§10–12). Both read the broker's actual quantity and average. A hand fill updates the exact lines the watcher uses; opening a window changes nothing. What they no longer share is the *preference*: the bot keeps its own (§12.7).
2. **Resume has three authoritative cases** (§12): held shares are adopted from broker quantity/average; a verified completed full sell today arms reload from its actual fill; zero shares without that evidence resets safely to Dynamic High5. No quote or intended price is guessed as a fill.
3. **Order ownership survives restart** (§§10.1, 12.6). Only accepted, durably tagged bot orders may be cancelled. A foreign app/web order pauses the ticker. Stale bot orders are removed first and replaced only after cancellation is confirmed; partial fills are reconciled before another order is considered.
4. **The broad Cancel control is removed** (§15.2). WATCH and self-healing perform bot-owned cleanup automatically.
5. **Cockpit interaction is direct and legible** (§15): no routine Gear/tier/Vantage confirmations; one LIVE confirmation; explicit Dynamic High5 vs Pinned state; selectable candle feedback; distinct Gear and exit-tier colors; unclipped live annotations; directional 5-day OHLC colors.
6. **Cards restore the action-distance gap and historical single-hue scale** (§15.3). They sort globally from larger displayed gap to smaller, and the V-COMMANDOS action is separated from the calculation rows.
7. **The fill log is event-driven** (§14). It records every definite broker quantity change, even when the execution price must display `--`; it never records window opens, adoption announcements, or preference changes. A separate status line always identifies FLAT versus DEPLOYED.

### Historical v0.5.0 amendment log

1. **The exit is a multi-select again** (§4.2, §7). One armed tier is still the clean full-position shot and the default; two or three split the holding by the old distribution law (remainder to the middle tier). A tier that fills is spent, the rest stay armed, a chase re-arms all of them, and **the campaign ends only when the holding is zero**.
2. **The bot re-prices its own resting orders** (§10.1) — the 443 engine's `stale LOAD` / `stale SELL` rule, restored. A gear shift no longer strands an order at the old price. Partially-filled orders and other people's orders are still never touched.
3. **The vantage is the Dynamic High5 window** of manual Appendix A.4/A.5 (§9.1): `max(previous four completed highs, today's high so far)`, recomputed live so a fresh intraday peak lifts the LOAD line at once. It can also be **pinned by clicking a day on the 5-day chart**, for a campaign that ended outside the bot. An earlier build of this rule tried to roll the window from the campaign's exit date — that was an invention, and Appendix A.2 Case 3 rules it out explicitly.
4. **The campaign log holds trades only** (§14.2). Gear shifts, tier changes and adoptions go to the app log instead.
5. **Cockpit**: AUTO/MANUAL toggles the mode without forcing a gear pick; tiers are a multi-select with a spent-tier marker; a vantage strip; the log is shorter and the charts are taller; day labels on the 5-day chart are guaranteed distinct and thinned rather than overlapped.
6. **Cards are ordered by gap**, flat and deployed alike — the top of the screen is what is about to happen.

### Historical v0.4.0 amendment log

1. **Gear ladder → 10 / 15 / 20 / 25** (§6, §6.1, §17). G5 arms above a 25% range. The previous 15/20/25/30 pass over-corrected: it left almost everything on G1's timid −6%/−4% ladder.
2. **V is one number everywhere** (§17.1). The candle panel's grid-era read-out (mean completed-day range, plus a `/3` grid hint) is gone; it now shows `V …% → G…` from the same `calc_volatility(High5, Low5)` the gearbox uses. The controller computes it from the five completed daily bars it already fetches for the vantage.
3. **A FLAT stock publishes chase 1** (§10). The projection helper numbered from 2 in both states, so a flat stock's chart drew chases 2 and 3 and omitted chase 1 — which the flat card had been printing all along.
4. **Gear and exit tier are choosable in the cockpit** (§15.1), reaching the engine on the next poll. Picking a gear drops AUTO; the AUTO button hands it back.
5. **Historical:** "Cancel all" became "Cancel N resting". This interim control was removed by v0.6.0 after bot-owned cleanup and order-ownership safety were completed.

### Historical v0.3.0 amendment log

1. **The capital cap is gone** (§8). `CAMPAIGN_CAP_UNITS`, the `CAPPED` buy state and `cap_units` in the card config were removed. The army is the only wall, re-checked every poll. The 32-unit figure survives only as the reference scale of the normalized tables.
2. **The gear is no longer pinned by a live campaign** (§11). AUTO tracks the 5-day range continuously, deployed or not; MANUAL holds. Nothing but the average cost is history, so both lines simply move. Gear/tier changes go to the diagnostic application log, never the trade-only campaign fill list.
3. **The daily v^ grid was removed** (§3) so this bot could be stabilised alone. Recoverable from commit `e148da6`; its manual is kept. The card lost its second button; the controller lost its strategy switch, the grid-scale picker and the shared `_push_ui`.
4. **Manual Buy/Sell was removed** (§15, §19). The bot exists to send the offer when the curve touches the line. Hand trading in the broker app is unaffected and still reconciles (§12). The interim broad Cancel escape hatch was later removed in v0.6.0.
5. **The cockpit was rebuilt** (§15) around the price curve, with the EXIT named first and the next buy carrying its size, followed by the next two projected chases. The campaign log came back in its old `RECORDED FILLS` form, now with a header row per trading day.
6. **Historical:** the campaign log began recording the trading day and, at that stage, also held decisions (`ADOPT`, `GEAR`, `TIER`). v0.5.0 removed those non-trade rows and v0.6.0 migrates old saved rows out of the visible fill log.
7. **Two projected chase lines are published** beyond the armed one (§10), so the depth of the ladder ahead is visible before it is needed.

### Historical v0.2.0 defect

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

The stock card is the commander's worksheet for trading by hand. It is **not** the bot's source for anything (§12.7). It reads:

```text
1. Samsung (KR)        (12.3%)  [AUTO]  V 18.2% → G3
                                Avg Cost [280,315]  Shares [31]   DEPLOYED

Total Cost: 8,689,765   Current: 232,000   Gap: -17.24%

Chase 1: 266,299 × 21   Chase 2: 260,922 × 35   Chase 3: 255,674 × 58
   T1: 285,921             ▶ T2: 291,528 × 31      T3: 297,134

                         Gear                  Exit
                         [G3 Balanced]         [ ] T3 +7%
                         gear:  ( 3 )          [x] T2 +5%
                                               [ ] T1 +3%

                                              [   AUTOPILOT    ]
                                              [sync to autopilot]
```

The **big Gear number** under the Gear picker is the one mark readable across a grid of sixteen cards at a glance, which is why it is on the card and why the cockpit — which shows one stock — does not need it. The exit tiers are plain **check boxes**: a worksheet ticks boxes, and they deliberately read differently from the cockpit's coloured tier buttons, because those trade and these do not.

An EMPTY card shows `Vantage:` instead of `Total Cost:`, and its ladder reads `Load / Chase 1 / Chase 2` with the exit tiers computed as if the load had filled.

The bot derives its live decisions from broker state plus **its own** gear and tiers — never from card arithmetic the commander typed by hand, and never from the card's gear unless `sync to autopilot` was pressed (§12.7).

---

## Final Strategy Summary

> Select AUTO or a Gear before battle; change the watched Gear deliberately when desired.
> LOAD only after the required pullback.
> Reinforce from the actual average cost using the selected Gear.
> Keep the default one-shot Exit Tier, or deliberately arm two or three tiers for a staged exit.
> Recover the army, record the completed V, and stand down — the next campaign is armed by the commander.
> Use lower Gears for shallow, calmer V cycles and higher Gears for deep, high-amplitude V cycles.
> Use the numbers on the card by hand or let LIVE chase the same lines; broker truth reconciles both paths without guessing.
