# Daily v^ Linear Weighted Grid — the Daily Adventure Autopilot

**Version:** 0.2.0 (system edition)
**Adapted from:** strategy manual `DAILY_V_HAT_LINEAR_WEIGHTED_GRID_MANUAL` v0.1.0
**Implemented in:** `core/autopilot.py` (`GridEngine`), `gui/autopilot_ctrl.py`, `gui/autopilot_window.py`
**Status:** simulated offline (57 engine checks + fuzz); LIVE-ready pending a small real trial

This file replaces the old "Daily V-Commandos Autopilot Manual". The old
average-cost V-only systems (443, adaptive gears, 타짜, the card-line
follower) are retired as BOT strategies; their history lives in git. The
**card gear system stays in the app unchanged** — it is the manual-trading
aid (the same mechanics the commander can run by hand through the app/web).
The autopilot no longer reads the cards: it runs THIS strategy.

---

## 1. Purpose

The manual V-Commandos style waited for a fall and sold the rebound — a V.
The API bot can also do the opposite conditionally: sell a rise and rebuy
the dip — a ^. This strategy harvests BOTH sides of a wobble, repeatedly,
inside one fixed daily coordinate system:

```text
V movement:  price falls  -> BUY  -> price rebounds -> SELL
^ movement:  price rises  -> SELL -> price falls    -> REBUY
```

It is NOT an average-cost rescue ladder, not a full-position take-profit
system, not martingale, not a moving-anchor grid, and not a prediction
engine. Average cost is removed from the tactical map (it stays in the
financial ledger only). The edge, if any after costs, comes from repeated
executable oscillation.

Each trading day is one campaign: the **DAILY ADVENTURE**. The grid is
rebuilt every morning; inventory carries overnight; P&L history never
resets.

---

## 2. The grid (v0.2 defaults)

```yaml
strategy_id: DAILY_V_HAT_LINEAR_GRID
level_cap: 5                       # CHANGED from 3 → 5 (user decision)
grid_type: ARITHMETIC_PERCENT_OF_ANCHOR
grid_scales: [2%, 2.5%, 3%, 3.5%, 4%]   # selectable PER STOCK, see §2.1
grid_step_default: 0.03
grid_weights: [1, 2, 3, 4, 5]      # linear; cumulative W = 1,3,6,10,15
opening_gap_threshold: = the chosen step (gap ≥ 1 level)
session_anchor_source: PREV_CLOSE_AT_L0_OR_GAP_OPEN_AT_L1   # see §5.1
daily_rebase: true
one_action_per_poll: true
max_open_strategy_orders: 1
pairing_mode: TARGET_INVENTORY
insufficient_cash_policy: SKIP_UNTIL_ARMY_RETURNS   # see §7
poll_interval_seconds: 5
trade_session: REGULAR_ONLY
```

Structural constants live at the top of `core/autopilot.py`
(`GRID_SCALES`, `LEVEL_CAP`, `GRID_WEIGHTS`, `MIN_INVENTORY`,
`MAX_INVENTORY`, `CORE_INVENTORY`). The SCALE is chosen in the window;
everything else is edit-and-restart.

### 2.1 Grid scale — one grid per day

Some stocks wobble 3–4% a day, some 10%: the spacing must be adaptive.
The selector sits directly UNDER the LIVE button (`grid 3%`), and the
choice is remembered PER STOCK across days (Samsung on 2% stays on 2%
tomorrow).

```text
allowed   while this adventure has NO grid trade and no unresolved
          order, and LIVE is off.  Changing the scale then RESETS the
          grid completely and re-initializes on the next poll exactly
          like a fresh day-start on the new step (anchor = prev close
          if price is inside ±step of it, else compressed at the
          current quote as L∓1).  Nothing carries over — levels of one
          scale mean nothing on another.

locked    the moment the first grid trade of the day exists (or an
          order is unresolved): the selector greys out (grid 3% 🔒)
          until the next adventure.  ONE GRID PER DAY — records from a
          3% ladder must never steer a 2% ladder.  Manual (folded)
          fills do NOT lock it; only the bot's own grid trades do.
```

The scale-picking indicator is the 5-day average day-V (§8): read
`avg/3` and choose the nearest scale (9% average → 3% grid, 6% → 2%).
Automating that mapping is a possible future step; today it is manual.

**The cap means "stop trading", not "buy no more":** the chased zone is
`-5 … 0 … +5`. Outside the zone NOTHING is chased in either direction —
the level parks at ±5 and the first crossing back INSIDE the zone trades
again.

For anchor `A = 100`:

| Level | Price | Cum. weight | Target (base 10, unit 1) |
|---:|---:|---:|---:|
| +5 | 115 | 15 | 0 (clamped) |
| +4 | 112 | 10 | 0 |
| +3 | 109 | 6 | 4 |
| +2 | 106 | 3 | 7 |
| +1 | 103 | 1 | 9 |
|  0 | 100 | 0 | 10 |
| -1 | 97 | 1 | 11 |
| -2 | 94 | 3 | 13 |
| -3 | 91 | 6 | 16 |
| -4 | 88 | 10 | 20 |
| -5 | 85 | 15 | 25 |

Army planning note: a full ride to -5 needs **W(5) = 15 units** of cash on
top of the base position. The army rarely funds all of it — that is fine:
unfunded buys are skipped and self-recover (§7). Size `unit_cash` (the app
header unit) accordingly.

---

## 3. Signal vs accounting

Signals come ONLY from: the session anchor, the grid levels, the current
level, the level weights, the session base inventory, actual broker
inventory, and the inventory bounds.

Accounting (broker average cost, cash flows, fees, realized/unrealized
P&L) never generates a signal. The daily rebase resets the tactical map,
never the money history.

---

## 4. Target inventory — the whole state machine

```text
unit_qty   = max(1, round_half_up(unit_cash / session_anchor))   # frozen per session
target(-n) = base_inventory + W(n) × unit_qty
target(+n) = base_inventory − W(n) × unit_qty
target(0)  = base_inventory
target     = clamp(MIN_INVENTORY, target, MAX_INVENTORY)         # MAX None = uncapped
order      = target(new_level) − actual_inventory
```

`order > 0` → BUY that many; `order < 0` → SELL that many; `order = 0` →
the level advances **silently** (see §7). The bot never infers quantities
from its trade history — it reconciles actual broker inventory straight to
the new level's target, which makes partial fills, skipped buys and
external trades self-healing.

---

## 5. The daily adventure lifecycle

### 5.1 Initialization — WHAT THE ANCHOR IS

```text
reference_close  = previous completed regular close (provider prev_close)
opening_price    = the FIRST regular-session quote the watcher sees
                   (arming mid-session: the first quote after arming)
base_inventory   = actual broker shares at that moment
unit_qty         = from unit_cash and the anchor, frozen for the day
```

The anchor is ONE concrete price, and it carries the **(A)** marker on
its level line:

```text
normal day  (|open/close − 1| < 3%):
    anchor = YESTERDAY'S CLOSE, sitting at L+0 (A); start level 0.

gap day     (open ≥ 3% away from yesterday's close):
    anchor = TODAY'S OPENING PRICE ITSELF, sitting at L-1 (A) on a
    down gap or L+1 (A) on an up gap; the day starts there by trading
    one unit (§5.2).
```

Every level is spaced 3%-OF-THE-ANCHOR from it:

```text
price(k) = anchor × (1 + 0.03 × (k − anchor_level))
```

So on a down-gap day with open 90: L-1 (A) = 90.00, L+0 = 92.70,
L-2 = 87.30 — the grid hangs off the open, and nothing is interpolated
between yesterday's close and today's open.

Before the regular open the window shows a preview grid around the
previous close (`WAIT_OPEN`); nothing trades.

### 5.2 Opening gap (compressed one-level)

If `|opening/reference − 1| ≥ 3%`: the opening price becomes the anchor
at L∓1 (A) as above; skipped levels from the overnight move are NEVER
executed. Only the minimum weight-1 action trades (BUY 1 unit / SELL
1 unit toward the ∓1 target, at the open price):

* the gap action fires as a normal limit at the level line;
* if the army cannot fund the gap BUY, it stays DUE and fires by itself
  when cash returns (no fake fill, no level lie);
* an unfilled gap action dies with the day (DAY orders expire at the
  close; the flag clears at the next rebase).

### 5.3 Runtime — watch the two adjacent lines

From the confirmed level `L`, only `L+1` and `L−1` are watched
(edge levels ±5 have only one neighbor). On a crossing:

```text
new_level = adjacent level crossed
delta     = target(new_level) − actual_inventory
delta<0 → SELL at the level line   delta>0 → BUY at the level line
```

* **One action per poll** (5 s): a straight drop through several levels
  steps one adjacent order at a time, each fill confirmed before the next.
* **Fill-confirmed advancement:** only a broker-confirmed inventory equal
  to the target advances `current_level`. Quote crossings and order
  submissions alone change nothing.
* Order prices are tick-trimmed per side (KR grid / US cents): SELL up,
  BUY down — the bot never trades on the wrong side of its line. A KR BUY
  whose raw line falls between ticks rests one tick below and fills when
  that tick trades.
* Because the order is sent AFTER the crossing is observed (up to one
  poll late), the actual fill can differ slightly from the line — always
  in our favor or equal: a BUY limit at the line fills at the line or
  LOWER, a SELL limit fills at the line or HIGHER.

### 5.4 Repeated oscillation

`-2 → -3 → -2 → -3` trades ±3 units every pass (weight 3 at depth 3).
This repeated SELL-then-REBUY at the same levels is the main reason to
automate; it is impractical to keep doing by hand.

### 5.5 Session end and the daily rebase

The bot trades only while the market phase is REGULAR; LIVE drops back to
WATCH at the close and resting DAY orders die on Toss. At the next
trading date:

```text
adventure summary → log      (fills, buy/sell turnover, net, end level)
day fill list     → cleared  (the window log is per-adventure)
new anchor        = new previous close        new base = actual shares
new unit_qty      = re-sized                  level    = 0
```

Yesterday's deep buys become today's base inventory and can be sold by
today's upper grid — no purchase stays chained to its original exit
price. The log file keeps the full history; nothing financial is erased.

---

## 6. What the bot does NOT do

```text
no average-cost signals          no gear system (cards keep it, manually)
no 밑장빼기 / skims               no stages or idle-day escalation
no martingale doubling           no strategic improvisation at the bounds
no anchor moves after init       no multi-level catch-up orders
no auto-cancel of resting orders (one order at a time, it waits)
no trading outside REGULAR       no trading outside the ±5 zone
```

---

## 7. Capacity rules (the two caveats, resolved)

### Nothing to BUY (reserve army empty)

The crossed BUY transition is simply **not taken**: the level stays, the
line stays crossed, the window shows the muted `✕ … (no army)` line and
the trigger row explains it. No popup. The SELL side keeps being watched
the whole time. The moment cash returns — typically a sell somewhere —
the next poll takes the transition by itself.

### Nothing to SELL (inventory at the lower bound)

The target clamps to the actual inventory, so `delta = 0` and the level
advances **silently**. The grid keeps tracking price upward. On the way
back down, the first BUY crossing trades again, and after that the upper
levels can sell again. (Example with 0 shares: +1/+2 do nothing; -1 buys
1 unit; back at 0 the sell works again.)

### Partial fills

A partially-filled order never advances the level. When the order is gone,
the next touch of the same line re-orders exactly the remainder
(`delta = target − actual`). Nothing is assumed filled.

### Manual trades coexist (external fills fold into the base)

A fill the bot did not place — the commander trading the same ticker from
the app/web — is FOLDED INTO THE BASE: `base_inventory` shifts by the
same amount, so every level target shifts with it and the bot's own
units stay exactly the same size. Manual trading and the bot are
innocuous to each other:

```text
manually BUY 20 more   → base 10 → 30; the next crossings still trade
                         the normal 1/2/3-unit deltas; the manual 20
                         shares are never sold by the bot.
manually SELL it all   → base → 0; upper crossings advance silently
                         (nothing to sell), the first L-n crossing buys
                         its normal unit, and the way back up sells it.
sold below bot depth   → the base clamps at 0 and the bot simply
                         rebuilds toward its normal level targets.
```

A RESTING manual order still pauses new transitions until it clears
(one order at a time is a bot invariant); once it fills, the fold above
applies.

### Restarts (fills that land while the program is OFF)

The state file also keeps the LAST KNOWN share count and the unresolved
order intent. On re-arming the same day, any inventory change that
happened while the watcher was off is attributed:

```text
diff in the pending order's direction → the bot's own DAY fill:
    booked at the intent price; the level advances when the full
    order is covered (partial fills self-heal as usual)
whatever remains                      → a manual trade: folded into
    the base, exactly as it would have been while polling
```

So ON/OFF is symmetric. The one theoretical mislabel — a manual trade
that exactly matches a dead unfilled bot order's size and direction
while the program was off — still leaves inventory consistent with the
shifted targets (a labeling quirk, never a money error). Overnight
restarts are always clean: the daily rebase absorbs everything into the
new base.

### Foreign orders (bot-exclusive order book)

If a resting order the bot did not place appears, new transitions pause
until it clears (status says so). Fills from outside are folded as above
and logged as `ext`.

---

## 8. Modes and the window

```text
WATCH  polling + grid + fill detection; NO orders from the bot.
LIVE   the bot fires by itself on every crossing. Regular hours only;
       auto-drops to WATCH at the close.
```

The Autopilot window (card's big button → arms WATCH) is ONE information
banner over TWO charts — compact in width, growing in height:

```text
header   name · state (WATCHING / ORDER_PENDING / WAIT_OPEN) · phase · LIVE
banner   anchor @ L∓n (A) · gap · level · unit · base · today net (fills)
         inventory · deployed units · now · reserve · resting orders
         ▲ next up transition · ▼ next down transition [NO ARMY]
         engine status + poll time
         오늘 fills (n): one line per fill — shown only when fills exist
left     live tick curve inside the grid: all 11 level lines (soft),
         the anchor line labeled L+0 (A) — or L∓1 (A) on a gap day —
         plus the two adjacent watch lines bold with their trades,
         corridor between the watch lines shaded
right    5-day candle panel with the anchor + watch lines
```

The 5-day candles are kept honest: the watcher refetches them every
5 minutes, and between refetches the live tick is folded into TODAY's
bar (close follows, high/low stretch) — the candle moves with the Now
line instead of freezing at the last main-panel refresh.

The candle panel draws the SAME grid rows as the live chart (one row per
level, built by one code path): the anchor and a coinciding watch line
merge into a single labeled row — e.g. `L+1 (A) 110.00  SELL 1` on an
up-gap day — never two superposed lines, and the current level always
shows its `← here` marker. Far soft levels appear only when they fall
inside the candles' own price range.

The panel header shows the scale indicator instead of the old gear:

```text
avg day V x.x% → /3 = y.y% grid
```

`x` = the mean range ((H−L)/L) of the past 5 COMPLETED days — today's
still-growing bar is excluded (the watcher fetches one extra bar for
this). `x/3` is the grid-scale hint the commander reads when choosing
2/2.5/3/3.5/4%. Purely an indicator for now; no automation hangs off it.

The old right-hand status/fills column and the manual Buy/Sell/Cancel
buttons were removed (2026-07-21) — the banner carries everything. The
controller still implements manual fire and cancel-all for a future UI;
today the bot is WATCH (look only) or LIVE (trade by itself).

LIVE is hard-gated to the regular session: it cannot be switched on
outside REGULAR hours, and an already-LIVE bot drops back to WATCH on the
first poll after the session leaves REGULAR (pre-market, after-market,
closed). Resting DAY orders die on Toss at the close.

---

## 9. Toss integration (what one poll touches)

Per 5-second cycle, ONLY the watched ticker: price quote, holdings
(symbol), open orders (symbol), buying power. Broker truth: actual
shares, sellable shares, cash, open orders, fills (share diffs ARE the
fill detection). Local truth: anchor, levels, targets, intended
transition.

Orders are LIMIT/DAY with a sanitized `clientOrderId`; our orderIds are
remembered so `mine` is known. Rejections: insufficient-buying-power →
announce once + 5-min backoff (the sell side stays managed);
order-hours-closed → 5-min backoff; opposite-pending → retry next poll.
Data outage → the watcher idles and announces once after ~60 s; no
guessing on missing data.

Persistence: `data/autopilot_state.json` per ticker (anchor, level, base,
unit, day counters, fill list). A restart mid-adventure re-arms exactly
where it left off; a stale or legacy entry falls back to a fresh init.
Logs: `logs/autopilot443.log` (decisions, fills, adventure summaries).

---

## 10. Accounting (implemented vs deferred)

Implemented now (accounting-lite):

```text
per-day buy turnover, sell turnover, net cash flow, fill count
   → window line 2 + adventure status panel
adventure summary at every daily rebase → log file
every fill with level tag → day fill list + log
```

Deferred (spec §19 of the strategy manual): fee/tax capture, marked
equity, daily grid-alpha-vs-hold, buy-and-hold benchmark, campaign
dashboards. The raw material (every fill, every summary) is already in
the logs.

---

## 11. Safety invariants

```text
One strategy order at a time; no duplicates while one is unresolved.
Only a confirmed fill (or confirmed equal inventory) advances the level.
The anchor never moves after init (except the one-time gap init).
Base inventory and unit_qty are frozen for the session.
Only adjacent transitions; only inside the ±5 zone; only REGULAR hours.
No SELL below MIN_INVENTORY / CORE_INVENTORY; no BUY beyond buying power.
Skipped buys never fake a level; bound-clamped sells advance silently.
Stale data → idle; foreign orders → pause; broker is always the truth.
Daily rebase resets tactics only — never financial history.
```

---

## 12. Testing

```text
python scripts/test_grid.py           57 checks: grid math, adventure init,
                                      the manual's deterministic paths (flat,
                                      V tooth, ^ tooth, deep V/^, repeated
                                      tooth, one-way), cap-5 zone edges,
                                      opening gaps (incl. unfunded), no-cash
                                      skip/recover, nothing-to-sell, partial
                                      fills, foreign-order pause, WATCH +
                                      manual fire, daily rebase, persistence,
                                      KR ticks, 2×1500-step random-walk fuzz
                                      with invariants and a cash-ledger match.
python scripts/test_autopilot_ui.py   28 headless window/render checks.
```

The FakeBroker harness in `test_grid.py` replays whole price paths through
the real engine (one action per poll, limit fills on cross) — extend it
with any path you want to preview before going LIVE.

---

## 13. Known limitations

```text
One-way decline accumulates up to W(5)=15 units and then waits (no stop).
One-way rally can underperform buy-and-hold (grid sells into strength).
The daily rebase hides economic drift unless you read the campaign logs.
Fees/taxes/slippage are not yet netted in the day counters.
Overnight gaps are only damped (compressed one-level), not removed.
Quote crossings without liquidity may leave resting orders unfilled.
No cutoff before the close — a last-minute fill can carry overnight
(DAY orders die at the close by themselves).
The ticker should be bot-exclusive while the bot runs it.
```

---

## 14. Rollout

```text
1. Watch one liquid, wobbly stock a full day in WATCH; compare the
   trigger row with what you would have done.
2. Fire one or two transitions with the manual Buy/Sell buttons.
3. Go LIVE on ONE stock with a small unit_cash; check fills in the Toss
   app; read the adventure summary the next morning.
4. Widen slowly. Parameter research (spacing, weights, bounds) comes
   only after live data exists.
```

Final operating principle (unchanged from v0.1.0):

```text
Build one fixed daily coordinate system.  Buy deeper below the anchor,
sell it back on the way up; sell above the anchor, rebuy on the way
down.  Remain deterministic through target inventory.  Carry real shares
overnight, rebuild the map each morning.  Never confuse daily grid
capture with total portfolio profit.
```
