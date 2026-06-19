# AI Seesaw Mini-Calculator Automation Plan

**File:** `automation_plan.md`  
**Version:** 0.1.0  
**Date:** 2026-06-19  
**Project:** `rzuno/Mini_trading_calculator`  
**Status:** Implementation roadmap  
**Primary environment:** Windows 11 + VS Code + Python  
**Broker/API target:** Toss Securities Open API

---

## 0. Purpose

This document defines the step-by-step plan for evolving the current **AI Seesaw Mini-Calculator** into a safe semi-automatic and eventually fully automatic trading program.

The project will not jump directly from a calculator to unrestricted live trading.

The migration order is:

1. Replace Yahoo Finance market data with Toss market data.
2. Read the real Toss account and holdings.
3. Generate an order preview without sending orders.
4. Send an order only after a user presses a button and confirms it.
5. Detect fills and recalculate the next orders.
6. Enable restricted automation only after the previous phases are verified.
7. Consider always-on cloud execution only after local live operation is stable.

The strategy itself remains deterministic. AI coding agents may help write, test, and review the code, but they are not the live trading runtime.

---

## 1. Core Philosophy

### 1.1 Trading philosophy

- Watch many AI-related stocks.
- Fight only a few battles at once.
- Do not chase rapidly rising stocks.
- Enter only when a precomputed dip trigger is reached.
- After entry, defend the position with average-cost-based rescue buys.
- Exit using precomputed limit sell prices.
- Idle cash is reserve capital, not wasted capital.
- The system should automate disciplined execution, not invent new predictions.

### 1.2 Engineering philosophy

- Read first.
- Compare second.
- Preview third.
- Confirmed execution fourth.
- Automatic execution last.
- Every phase must remain reversible.
- Broker account data is the source of truth for real holdings.
- Strategy settings remain under user control.
- Any ambiguous or inconsistent state must stop new orders.

### 1.3 Non-goals for the first versions

The first automation versions will not:

- use market orders,
- perform high-frequency trading,
- chase intraday prices,
- rely on an LLM to decide trades,
- automatically convert currencies,
- submit unrestricted multi-stock orders,
- automatically modify source code while trading,
- assume that an order request means a fill occurred.

---

## 2. Current Project Baseline

The current Mini-Calculator already has useful separation.

### 2.1 Existing entry point

```text
main.py
└── creates the Tkinter App
```

### 2.2 Existing strategy calculations

`core/calc.py` already contains most of the mathematical engine:

- stock catalogue and display order,
- continuous LOAD percentage range,
- volatility calculation,
- volatility-to-gear mapping,
- initial buy price calculation,
- initial buy share calculation,
- rescue trigger calculation,
- rescue quantity calculation,
- projected rescue cascade,
- sell tier price and quantity calculation,
- price formatting and display helpers.

This file should remain as close as possible to a **pure calculation module**.

It should not:

- call Toss endpoints,
- read environment secrets,
- submit orders,
- update the GUI directly,
- write broker state directly.

### 2.3 Existing data source

`core/data_feed.py` currently uses `yfinance`.

It fetches:

- current price,
- 5-day high,
- 5-day low,
- recent closes,
- recent OHLC,
- USD/KRW,
- 3-month average USD/KRW.

This module is the first major replacement target.

### 2.4 Existing local state

`core/csv_io.py` currently stores:

- ticker,
- deployed state,
- shares,
- average cost,
- cost basis,
- load gear,
- rescue gear,
- sell tier percentages,
- sell tier active flags,
- automatic/manual gear mode.

After account synchronization is introduced, the CSV must stop acting as the authoritative record for real shares and average cost.

### 2.5 Existing GUI behavior

The GUI already separates:

- empty stocks,
- deployed stocks,
- LOAD calculation,
- rescue cascade,
- sell tiers,
- volatility auto gear,
- FX 3-month-average ladder.

This GUI should evolve rather than be discarded.

---

## 3. Target Automation Levels

The project has six functional phases.

| Phase | Market data | Account data | Order sending | Fill handling |
|---|---|---|---|---|
| 1 | Toss | Manual | None | None |
| 2 | Toss | Toss read-only | None | Read-only |
| 3 | Toss | Toss read-only | Preview only | Read-only |
| 4 | Toss | Toss | Button + confirmation | Manual refresh / read |
| 5 | Toss | Toss | Button + confirmation | Automatic detection and recalculation |
| 6 | Toss | Toss | Restricted automatic | Automatic |

Each phase must be usable on its own.

---

## 4. Toss API Rules

Official documentation:

- Human documentation: `https://developers.tossinvest.com/docs`
- API server: `https://openapi.tossinvest.com`
- Canonical machine-readable specification: the latest Toss OpenAPI JSON. A copy is here in this folder as 'Toss_api.json'
- Authentication: OAuth 2.0 Client Credentials.
- Account, asset, and order endpoints require:
  - `Authorization: Bearer ...`
  - `X-Tossinvest-Account: ...`

The OpenAPI JSON is the source of truth for:

- endpoint paths,
- request bodies,
- response schemas,
- enum values,
- order types,
- price units,
- pagination,
- errors,
- rate limits,
- market-specific behavior.

### 4.1 Never invent endpoint details

Do not guess endpoint names or JSON field names from examples in this document.

Before implementing each API method:

1. Open the current Toss OpenAPI specification.
2. Copy the exact endpoint path.
3. Copy the exact request schema.
4. Copy the exact response schema.
5. Write a small read-only test.
6. Record the working response shape in project notes.

### 4.2 Secret handling

Secrets must be stored outside Git.

Recommended `.env` keys:

```env
TOSS_CLIENT_ID=
TOSS_CLIENT_SECRET=
TOSS_ACCOUNT=
TOSS_API_BASE=https://openapi.tossinvest.com
TRADING_MODE=MANUAL
```

Rules:

- Never commit `.env`.
- Never paste secrets into issues, README files, screenshots, or chat.
- Never print full tokens in logs.
- Mask account identifiers in GUI messages.
- Rotate credentials if exposure is suspected.
- The program must refuse LIVE mode when required secrets are absent.

---

## 5. Source-of-Truth Hierarchy

Different data belongs to different owners.

### 5.1 Toss is authoritative for real account facts

Toss account data is authoritative for:

- holdings,
- share quantity,
- average acquisition price,
- sellable quantity,
- buying power,
- open orders,
- filled quantity,
- broker order identifiers,
- order status,
- cash balance,
- currency balance.

### 5.2 Local settings are authoritative for strategy choices

Local CSV or JSON is authoritative for:

- watchlist,
- automatic/manual gear mode,
- initial-buy percentage,
- rescue percentage,
- sell tier percentages,
- active sell tiers,
- per-stock enable/disable flags,
- maximum allocation,
- safety limits,
- trading mode.

### 5.3 SQLite is authoritative for program history

SQLite should store:

- strategy order intent,
- broker order ID,
- submission result,
- fill history,
- cancellation history,
- campaign ID,
- campaign start/end,
- last full exit,
- anchor reset state,
- reconciliation warnings,
- strategy version.

### 5.4 Yahoo Finance after Phase 1

Yahoo Finance may remain only as:

- optional comparison data,
- debugging fallback,
- historical experiment source.

It must not determine live order prices once Toss market-data mode is active.

---

## 6. Recommended Repository Strategy

Keep one repository and develop through feature branches.

Do not create a separate automated-trading repository yet because:

- the current GUI remains useful,
- the current calculation engine remains useful,
- duplicating the calculator would create two strategy implementations,
- bug fixes could diverge between repositories.

Recommended branch sequence:

```text
main
├── feature/toss-market-data
├── feature/toss-account-sync
├── feature/order-preview
├── feature/confirmed-orders
├── feature/fill-reconciliation
└── feature/trading-worker
```

Rules:

- `main` must remain runnable.
- One functional phase per branch.
- Commit small changes with clear messages.
- Add tests before merging.
- Do not mix GUI redesign with API integration unless necessary.
- Tag stable milestones.

Suggested tags:

```text
v0.3-toss-market-read
v0.4-account-read
v0.5-order-preview
v0.6-confirmed-order
v0.7-fill-watch
v1.0-restricted-auto
```

---

## 7. Target Project Structure

```text
Mini_trading_calculator/
│
├── main.py
├── config.json
├── .env
├── requirements.txt
├── automation_plan.md
│
├── core/
│   ├── calc.py
│   ├── models.py
│   ├── order_planner.py
│   ├── campaign.py
│   ├── risk_rules.py
│   └── reconciliation.py
│
├── providers/
│   ├── __init__.py
│   ├── market_data_base.py
│   ├── yfinance_provider.py
│   └── toss_market_provider.py
│
├── brokers/
│   ├── __init__.py
│   ├── broker_base.py
│   ├── dry_run_broker.py
│   └── toss_broker.py
│
├── services/
│   ├── account_sync.py
│   ├── trading_cycle.py
│   ├── fill_watcher.py
│   └── order_service.py
│
├── storage/
│   ├── database.py
│   ├── schema.sql
│   └── state.db
│
├── gui/
│   ├── main_window.py
│   ├── empty_row.py
│   ├── deployed_row.py
│   ├── order_preview.py
│   └── confirm_order.py
│
├── data/
│   └── positions.csv
│
├── scripts/
│   ├── toss_connection_test.py
│   ├── compare_market_data.py
│   └── account_read_test.py
│
└── tests/
    ├── test_calc.py
    ├── test_order_planner.py
    ├── test_campaign.py
    ├── test_reconciliation.py
    └── fixtures/
```

This is the target structure, not a requirement to create every file immediately.

---

## 8. Common Data Models

Introduce typed models before order execution.

Example:

```python
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class StrategyAction(str, Enum):
    BUY_1_INITIAL = "BUY_1_INITIAL"
    BUY_RESCUE = "BUY_RESCUE"
    SELL_T1 = "SELL_T1"
    SELL_T2 = "SELL_T2"
    SELL_T3 = "SELL_T3"


@dataclass(frozen=True)
class Holding:
    symbol: str
    shares: Decimal
    avg_cost: Decimal
    sellable_shares: Decimal
    currency: str


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    current_price: Decimal
    previous_close: Decimal | None
    completed_highs: tuple[Decimal, ...]
    completed_lows: tuple[Decimal, ...]
    captured_at: datetime


@dataclass(frozen=True)
class OrderIntent:
    intent_id: str
    symbol: str
    side: Side
    action: StrategyAction
    limit_price: Decimal
    quantity: Decimal
    strategy_version: str
```

Use `Decimal` for money and order prices when integrating the broker.

Floating-point values may remain inside non-critical chart calculations, but the broker boundary should use exact decimal values.

---

## 9. Unified BUY Ladder Design

The user-facing GUI should simplify LOAD and RESCUE into one BUY ladder. Unify Load as Buy 0.

### 9.1 User-facing display

```text
BUY 0
BUY 1
BUY 2
BUY 3
```

### 9.2 Internal meanings

#### BUY 0

BUY 0 is the initial campaign entry.

Conditions:

```text
current holding == 0
```

Reference:

```text
campaign anchor high
```

Formula:

```text
buy_0_price = anchor_high × (1 - initial_buy_pct / 100)
```

Quantity:

```text
one capital unit converted to shares
```

Internal action type:

```text
BUY_1_INITIAL
```

#### BUY 1 and later

These are rescue buys.

Conditions:

```text
current holding > 0
```

Reference:

```text
current real average cost
```

Formula:

```text
next_rescue_price = avg_cost × (1 - rescue_pct / 100)
```

Quantity:

```text
current shares × rescue ratio
```

Internal action type:

```text
BUY_RESCUE
```

### 9.3 Display unification, state separation

The word `LOAD` may disappear from the GUI.

However, the code must still distinguish the initial buy because it:

- starts a campaign,
- uses an anchor high rather than average cost,
- buys one unit,
- initializes sell orders,
- creates a new campaign ID.

### 9.4 Preview versus live orders

The GUI may preview several future BUY levels using projected fills.

For initial live automation:

- show BUY 1, BUY 2, BUY 3 as a projection,
- submit only the next valid BUY order,
- after a real fill, read the actual average cost,
- recalculate the next BUY order.

This prevents projected average cost from diverging due to:

- partial fills,
- price improvement,
- fees,
- quantity differences,
- manual trades,
- broker-side rounding.

---

## 10. Completed-Bar Definition

The program must distinguish:

- live current price,
- current incomplete candle,
- completed historical candles.

For BUY 0:

```text
High5 = maximum High of the five most recent completed trading sessions
```

Do not:

- patch a completed Close with the live price,
- include today’s incomplete High in historical High5,
- let the BUY 0 reference move intraday,
- use one provider for triggers and another provider for execution.

A market snapshot should expose separate values:

```python
{
    "current_price": ...,
    "current_day_high": ...,
    "current_day_low": ...,
    "completed_daily_bars": [...],
    "high_5_completed": ...,
}
```

---

## 11. Campaign and Anchor Reset

The old 5-day-high rule can immediately retrigger after a profitable full exit because the previous campaign’s high remains in the lookback window.

The automated version must track campaign epochs.

### 11.1 Required campaign fields

```text
campaign_id
symbol
started_at
ended_at
last_exit_time
last_exit_price
anchor_epoch_start
anchor_high
status
```

### 11.2 Detecting full exit

A full exit is confirmed when:

```text
previous synchronized holding > 0
current synchronized holding == 0
```

and the order/fill history confirms that the position was sold.

### 11.3 Actions after full exit

```text
1. Mark the current campaign CLOSED.
2. Store final exit time and price.
3. Cancel obsolete remaining orders.
4. Create a fresh anchor epoch.
5. Block same-day automatic re-entry.
6. Set the initial new anchor to the final exit price.
```

### 11.4 New anchor after exit

Before five completed sessions have passed:

```text
anchor_high = max(
    last_exit_price,
    completed highs after the exit
)
```

After enough completed sessions exist:

```text
anchor_high = maximum High of the latest five completed sessions
               that belong to the new anchor epoch
```

This prevents stale highs from the previous campaign from producing an immediate new BUY 0.

### 11.5 Manual trades

If the user trades manually in the Toss app:

- account synchronization will detect the holding change,
- reconciliation must classify it as an external/manual event,
- local campaign state must be rebuilt,
- automatic order submission must pause until the state is consistent.

---

## 12. FX Ladder

The FX module uses a proportional pool rather than fixed dollar amounts.

### 12.1 Reference rate

```text
FX reference = trailing 3-month average USD/KRW
```

### 12.2 Switch pool

```text
switch_pool_krw = total_portfolio_capital_krw / 3
```

### 12.3 Marginal level amounts

For deviation level `k` in `{-3, -2, -1, +1, +2, +3}`:

```text
level amount in KRW = |k| / 6 × switch_pool_krw
level amount in USD = level amount in KRW / reference FX
```

The three marginal moves sum to the entire switch pool:

```text
1/6 + 2/6 + 3/6 = 1
```

### 12.4 Meaning

- Positive FX deviation: sell USD according to the rung.
- Negative FX deviation: buy USD according to the rung.
- Level 0 resets the manual switch tracker.
- Permanent KRW and USD reserves remain outside the switch pool.

### 12.5 Automation scope

FX should initially remain:

```text
calculation + display only
```

Automatic currency conversion should be implemented only after stock order automation is stable.

---

## 13. Phase 0 — Preparation

### Goal

Prepare the repository for safe API work without changing strategy behavior.

### Tasks

- Create `automation_plan.md`.
- Create branch `feature/toss-market-data`.
- Add `requirements.txt`.
- Add `.env.example` without real secrets.
- Confirm `.env` is ignored.
- Add logging configuration.
- Add tests for the existing calculation functions.
- Record the current working GUI behavior with screenshots or notes.
- Back up the current local CSV/config files.
- Confirm current application launches successfully.

### Acceptance criteria

- `main` remains unchanged and runnable.
- Feature branch launches the original calculator.
- Existing calculation tests pass.
- No secret is present in Git status.
- A rollback point exists.

---

## 14. Phase 1 — Toss Market Data, Read-Only

### Goal

Replace Yahoo Finance as the live calculation data source while keeping order execution fully manual.

### First script

Create:

```text
scripts/toss_connection_test.py
```

It should:

1. load credentials from `.env`,
2. request an access token,
3. print only masked connection status,
4. make one harmless read-only request,
5. handle HTTP and schema errors,
6. never submit an order.

### Market provider interface

```python
from typing import Protocol


class MarketDataProvider(Protocol):
    def get_quote(self, symbol: str):
        ...

    def get_completed_daily_bars(self, symbol: str, count: int):
        ...

    def get_fx_rate(self):
        ...

    def get_market_status(self, market: str):
        ...
```

Implement:

```text
TossMarketDataProvider
YahooMarketDataProvider
```

### Symbol mapping

Create an explicit symbol map.

Do not assume Yahoo symbols and Toss instrument identifiers are identical.

Example conceptual mapping:

```python
SYMBOL_MAP = {
    "005930.KS": {"internal": "005930.KS", "toss": "..."},
    "000660.KS": {"internal": "000660.KS", "toss": "..."},
    "NVDA": {"internal": "NVDA", "toss": "..."},
}
```

Fill Toss identifiers only from verified API responses or stock-master data.

### Comparison mode

During early testing, show or log:

```text
symbol
Toss current price
Yahoo current price
Toss High5 completed
Yahoo High5
difference
timestamp
```

The calculator must use Toss values while comparison mode is active.

### Required validations

- Korea stock quote.
- US stock quote.
- Recent completed Korean candles.
- Recent completed US candles.
- USD/KRW.
- 3-month FX history or sufficient values to calculate the average.
- Market holiday/status response.
- Correct timezone and trading date handling.

### Acceptance criteria

- GUI displays Toss prices for every watchlist stock.
- High5 uses exactly five completed sessions.
- FX ladder uses Toss-derived data.
- Oracle and Apple both load correctly.
- No order endpoint is called.
- Yahoo can be disabled without breaking the app.
- Data mismatches are logged, not silently ignored.

---

## 15. Phase 2 — Toss Account Synchronization, Read-Only

### Goal

Stop manually entering real holdings and average prices.

### Required account reads

- available accounts,
- holdings,
- share quantity,
- average acquisition cost,
- sellable quantity,
- buying power,
- cash by currency,
- open orders,
- recent order/fill history where available.

### Account modes

Add:

```text
MANUAL
TOSS_SYNC
```

In `MANUAL`:

- current CSV behavior remains available.

In `TOSS_SYNC`:

- shares and average cost come from Toss,
- account-derived fields are read-only in the GUI,
- strategy fields remain editable.

### Synchronization flow

```text
Fetch Toss account
→ normalize holdings
→ map Toss symbols to internal symbols
→ compare with local state
→ update GUI
→ record discrepancies
```

### Unknown holdings

If the account contains a stock not in the watchlist:

- display it in an `UNMANAGED HOLDINGS` section,
- do not generate automated orders,
- do not silently ignore it.

### Acceptance criteria

- Shares match the Toss app.
- Average prices match the Toss app within documented rounding.
- Empty/deployed classification is automatic.
- Manual app trades appear after refresh.
- Unknown holdings are visible.
- API failure does not erase the last known state.
- New orders remain disabled.

---

## 16. Phase 3 — Order Preview

### Goal

Generate a complete order plan without sending any order.

### Order planner responsibilities

`core/order_planner.py` should convert:

- strategy settings,
- market data,
- synchronized holdings,
- open orders,
- campaign state,

into a list of `OrderIntent` objects.

It must not submit orders.

### Preview examples

```text
ORCL
BUY 1 INITIAL
5 shares @ $130.20
Estimated notional: $651.00
Reason: empty position, anchor -7%

NVDA
BUY RESCUE
3 shares @ $145.30
Reason: average cost -5%

NVDA
SELL T2
4 shares @ $163.10
Reason: active sell tier
```

### Preview statuses

Each intent should show:

```text
READY
BLOCKED
ALREADY ORDERED
INSUFFICIENT CASH
INSUFFICIENT SHARES
MARKET CLOSED
STATE MISMATCH
MANUAL REVIEW
```

### Acceptance criteria

- Preview matches current calculator outputs.
- Existing matching open orders are not duplicated.
- Buy/sell directions and quantities are correct.
- Total notional is displayed.
- No order API is called.
- Preview can be exported to a log for comparison with manual app orders.

---

## 17. Phase 4 — Confirmed Semi-Automatic Orders

### Goal

Send a single selected limit order only after explicit confirmation.

### Button behavior

Each valid preview row gets:

```text
[Prepare Order]
```

Pressing it opens a confirmation window.

The confirmation window must show:

- masked account,
- symbol and company name,
- market,
- buy/sell,
- strategy action,
- limit price,
- quantity,
- estimated notional,
- current holding,
- projected holding,
- open matching orders,
- safety warnings.

Buttons:

```text
[Cancel]
[Send Order]
```

### Post-submit behavior

After submission:

1. display the broker response,
2. store the broker order ID,
3. query the order to confirm it exists,
4. update the GUI order status,
5. disable accidental duplicate submission.

### First live tests

Use the smallest practical size.

Recommended sequence:

1. Submit a limit order far from the market.
2. Confirm it appears in the Toss app.
3. Cancel it through the program.
4. Confirm cancellation.
5. Repeat for one buy.
6. Repeat for one sell only when a real sellable holding exists.
7. Test one tiny fill.

### Acceptance criteria

- One button press cannot submit twice.
- Cancel works.
- Order status refresh works.
- Wrong account/symbol/side is blocked.
- Market orders are impossible.
- Maximum order value is enforced.
- All attempts are logged.
- The program survives network failure without assuming success.

---

## 18. Phase 5 — Fill Detection and Recalculation

### Goal

Detect broker-side changes and produce a new plan automatically, while leaving final order approval manual.

### Polling worker

Until Toss provides and the project implements an appropriate real-time event mechanism, use controlled polling.

Conceptual loop:

```python
while market_session_is_relevant:
    synchronize_account()
    synchronize_open_orders()
    detect_order_changes()
    detect_fills()
    reconcile_state()
    recalculate_order_plan()
    sleep(configured_interval)
```

The actual interval must respect documented API limits.

### Fill response

After a BUY fill:

```text
1. Read actual holding and average cost.
2. Mark the local order as filled or partially filled.
3. Recalculate the next rescue order.
4. Recalculate the sell ladder.
5. Mark old sell intents obsolete.
6. Show replacement order preview.
```

After a SELL fill:

```text
1. Read remaining holding.
2. Update sell-tier state.
3. Validate remaining sell orders.
4. Detect full exit.
5. Close/reset the campaign if shares become zero.
```

### Partial fills

Never calculate from requested quantity alone.

Use:

- actual filled quantity,
- actual remaining quantity,
- actual synchronized average cost,
- actual sellable quantity.

### Acceptance criteria

- New fills are detected.
- Partial fills are represented correctly.
- New plans use actual account values.
- Full exits reset campaign anchors.
- Manual app trades trigger reconciliation.
- New replacement orders still require user confirmation.

---

## 19. Phase 6 — Restricted Full Automation

### Goal

Allow deterministic order submission only after all previous mechanisms are stable.

### Initial restrictions

```text
Limit orders only
One explicitly enabled account
One or two explicitly enabled symbols
Small maximum order value
Maximum one new initial buy per day
Maximum one active rescue buy per stock
Maximum configured open sell orders
No automatic FX conversion
No automatic trading on state mismatch
```

### Fail-closed rule

Any of the following must stop new orders:

- token/authentication failure,
- account synchronization failure,
- open-order query failure,
- schema mismatch,
- unrecognized API response,
- holding mismatch,
- duplicate intent,
- insufficient funds,
- insufficient sellable shares,
- market closed,
- price outside permitted increments,
- abnormal price gap,
- stale market data,
- clock/timezone inconsistency,
- local database error.

### Kill switches

Provide:

```text
GLOBAL LIVE OFF
DISABLE NEW BUYS
CANCEL ALL BOT ORDERS
SYMBOL DISABLE
ACCOUNT READ-ONLY
```

LIVE mode must default to off after installation or migration.

### Acceptance criteria

- Restricted automation can run locally for multiple sessions.
- Every broker action is reproducible from logs.
- Restarting the program does not duplicate orders.
- Manual interventions are detected.
- Safety limits cannot be bypassed by GUI state alone.
- A kill switch can stop new orders immediately.

---

## 20. Order Reconciliation

Reconciliation compares desired strategy orders with actual broker orders.

### Inputs

```text
desired order intents
broker open orders
broker holdings
local order history
campaign state
```

### Outcomes

For each desired intent:

```text
CREATE
KEEP
REPLACE
CANCEL
BLOCK
```

Examples:

#### CREATE

A valid desired order does not exist at the broker.

#### KEEP

A broker order already matches:

- account,
- symbol,
- side,
- price,
- remaining quantity,
- strategy action.

#### REPLACE

An existing bot order is obsolete because:

- actual average cost changed,
- active tier settings changed,
- quantity changed,
- strategy gear changed after a fill.

Replacement sequence:

```text
cancel old
confirm cancellation
submit new
confirm new order
```

#### BLOCK

State is ambiguous or unsafe.

### Manual broker orders

Do not automatically cancel an order merely because it was not created by the bot.

Mark broker orders with local metadata where possible.

If ownership cannot be proven:

```text
MANUAL/UNKNOWN ORDER — REVIEW REQUIRED
```

---

## 21. Idempotency and Duplicate Prevention

Every strategy-generated order needs a stable intent ID.

Example:

```text
campaign-17:NVDA:BUY_RESCUE:level-2:version-1
campaign-17:NVDA:SELL_T1:ladder-4
```

Before submitting:

1. Check local order history.
2. Check current broker open orders.
3. Check recently submitted unknown-result requests.
4. Submit only when no equivalent order exists.

### Network timeout rule

A timed-out POST request is not automatically a failed order.

After an uncertain response:

```text
1. Mark request UNKNOWN.
2. Query broker orders.
3. Locate a matching order.
4. Resolve as ACCEPTED or NOT_FOUND.
5. Only then consider resubmission.
```

---

## 22. Risk Rules

Create `core/risk_rules.py`.

Suggested settings:

```python
LIVE_TRADING = False
ALLOW_MARKET_ORDERS = False
MAX_ORDER_VALUE_KRW = 0
MAX_ORDER_VALUE_USD = 0
MAX_NEW_INITIAL_BUYS_PER_DAY = 1
MAX_OPEN_BUY_ORDERS_PER_STOCK = 1
MAX_TOTAL_OPEN_ORDERS = 0
BLOCK_SAME_DAY_REENTRY_AFTER_FULL_EXIT = True
MAX_DATA_AGE_SECONDS = 0
```

Use non-zero real values only when entering the relevant phase.

### Gap guard

Example:

```text
planned BUY 1 = 100
current tradable price = 80
```

A very low execution price may indicate major news rather than a normal dip.

Add a configurable rule:

```text
if current_price is materially below planned trigger:
    block automatic submission
    require manual review
```

The exact percentage must be tested rather than guessed permanently.

---

## 23. Storage Plan

### 23.1 Strategy configuration

CSV or JSON may store:

```text
symbol
enabled
auto_mode
initial_buy_pct
rescue_pct
sell tier percentages
sell tier active flags
maximum stock allocation
manual/automatic execution permission
```

### 23.2 SQLite tables

Suggested schema:

```sql
CREATE TABLE campaigns (
    campaign_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    last_exit_time TEXT,
    last_exit_price TEXT,
    anchor_epoch_start TEXT,
    anchor_high TEXT
);

CREATE TABLE order_intents (
    intent_id TEXT PRIMARY KEY,
    campaign_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    strategy_action TEXT NOT NULL,
    limit_price TEXT NOT NULL,
    quantity TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE broker_orders (
    broker_order_id TEXT PRIMARY KEY,
    intent_id TEXT,
    status TEXT NOT NULL,
    requested_quantity TEXT,
    filled_quantity TEXT,
    remaining_quantity TEXT,
    average_fill_price TEXT,
    submitted_at TEXT,
    updated_at TEXT,
    raw_response_json TEXT
);

CREATE TABLE sync_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    symbol TEXT,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

Store money values as decimal strings if SQLite decimal handling is not explicitly implemented.

---

## 24. Local Runtime versus Cloud Runtime

### 24.1 Local Windows runtime

Use first.

Possible execution methods:

- run manually from VS Code,
- run a headless worker from PowerShell,
- use Windows Task Scheduler,
- restart worker on failure,
- disable sleep during monitored sessions.

Advantages:

- simple debugging,
- direct access to logs,
- no cloud secret management,
- easy manual intervention.

Disadvantages:

- PC must be on for fill monitoring,
- sleep/reboot/network loss stops the worker,
- US-market monitoring may require overnight operation.

### 24.2 Cloud runtime

Consider only after restricted local LIVE is stable.

The cloud machine would run:

```text
headless trading worker
SQLite or managed database
secret manager
logs
health checks
alerts
automatic restart
```

The GUI may remain local and communicate with the worker later.

### 24.3 Coding agents and scheduled agents

Codex, Claude Code, and similar agents may help with:

- writing code,
- running tests,
- reviewing logs,
- creating pull requests,
- scheduled repository maintenance.

They should not be treated as the reliable runtime for live broker order management.

The actual trading runtime must be deterministic Python software under the user’s control.

---

## 25. Testing Strategy

### 25.1 Unit tests

Test all calculation functions with fixed examples.

Required cases:

- initial buy at 4%, 5%, 6%, 7%, 8%, and deeper manual levels,
- one-unit minimum share rule,
- rescue ratio rounding,
- projected cascade average costs,
- one, two, and three active sell tiers,
- KRW and USD price formatting,
- volatility boundary values,
- FX level boundaries,
- post-exit anchor reset.

### 25.2 Provider contract tests

Both Yahoo and Toss providers should return the same internal model shape.

Test:

- missing price,
- empty candle list,
- market holiday,
- timeout,
- malformed response,
- unknown symbol,
- stale response.

### 25.3 Broker simulation tests

`DryRunBroker` should simulate:

- accepted order,
- rejected order,
- full fill,
- partial fill,
- cancellation,
- timeout with unknown result,
- duplicate submission,
- insufficient cash,
- insufficient shares.

### 25.4 Reconciliation tests

Test:

- desired order absent,
- exact matching order,
- same symbol but wrong price,
- same symbol but wrong direction,
- stale old ladder,
- unknown manual order,
- full exit,
- manual purchase outside the program.

### 25.5 Replay tests

Record sanitized market/account snapshots and replay them through the engine.

A replay must produce identical order intents when:

- inputs,
- strategy version,
- configuration

are identical.

---

## 26. Logging

Use structured logs.

Each important event should contain:

```text
timestamp
mode
account mask
symbol
campaign ID
intent ID
broker order ID
action
price
quantity
result
reason
```

Never log:

- client secret,
- full access token,
- full account identifier,
- unmasked sensitive response data.

Suggested files:

```text
logs/app.log
logs/api.log
logs/orders.log
logs/reconciliation.log
```

Rotate logs so they do not grow forever.

---

## 27. GUI Evolution

### 27.1 Header additions

```text
Market Data: Yahoo / Toss
Account Mode: Manual / Toss Sync
Trading Mode: Manual / Preview / Confirmed / Auto
Connection: Connected / Error
Last Account Sync
Last Market Sync
```

### 27.2 BUY display

Replace the visual distinction between LOAD and rescue with:

```text
BUY 1
BUY 2
BUY 3
```

Use tooltips or labels:

```text
BUY 1 — Initial
BUY 2 — Rescue
BUY 3 — Projected Rescue
```

### 27.3 Account fields

In Toss Sync mode:

- shares: read-only,
- average cost: read-only,
- cost basis: read-only,
- sellable shares: visible,
- open order count: visible.

### 27.4 Order buttons by phase

Phase 3:

```text
[Preview]
```

Phase 4:

```text
[Prepare Order]
```

Phase 5:

```text
[Apply New Plan]
```

Phase 6:

```text
AUTO ENABLED / DISABLED
```

AUTO must be visually unmistakable.

---

## 28. Daily Operational Flow

### Read-only phase

```text
1. Start calculator.
2. Authenticate to Toss.
3. Read market data.
4. Read account data when Phase 2 is active.
5. Calculate BUY and SELL levels.
6. Enter orders manually in the Toss app.
7. Compare fills with the next refresh.
```

### Confirmed-order phase

```text
1. Synchronize market, account, and open orders.
2. Generate plan.
3. Review a selected order.
4. Press Send.
5. Verify broker acceptance.
6. Monitor order status.
7. Re-synchronize after fills.
```

### Restricted-auto phase

```text
1. Worker starts.
2. Validate credentials, clock, database, and mode.
3. Synchronize broker state.
4. Reconcile local and broker state.
5. Generate desired intents.
6. Apply safety rules.
7. Create/cancel/replace permitted orders.
8. Poll for changes.
9. Stop safely at session end or kill-switch activation.
```

---

## 29. First Implementation Sprint

The first sprint must remain entirely read-only.

### Branch

```bash
git switch -c feature/toss-market-data
```

### Tasks

1. Create `.env.example`.
2. Install `python-dotenv` and `requests` if needed.
3. Create `scripts/toss_connection_test.py`.
4. Implement token acquisition from the verified Toss schema.
5. Implement one stock-master or quote read.
6. Add sanitized error handling.
7. Create `providers/market_data_base.py`.
8. Move current Yahoo logic into `providers/yfinance_provider.py`.
9. Create `providers/toss_market_provider.py`.
10. Add provider selection to config.
11. Fetch one Korean and one US quote.
12. Fetch completed candles.
13. Calculate High5 from completed sessions.
14. Compare results with the existing calculator.
15. Connect Toss provider to the GUI.
16. Verify all watchlist symbols.
17. Keep every order-related method unimplemented or hard-disabled.

### Definition of done

```text
The Mini-Calculator launches normally.
All stock and FX calculations use Toss market data.
No order endpoint is called.
No account-changing endpoint is called.
Yahoo can be selected only as a fallback/comparison provider.
All secrets stay outside Git.
```

---

## 30. Development Rules for Every Future Step

Before coding:

```text
1. Read the corresponding official Toss endpoint documentation.
2. Write down the exact request and response fields.
3. Create a minimal isolated test script.
4. Run it read-only when possible.
5. Normalize the response into internal models.
6. Add automated tests.
7. Connect it to the GUI or service layer.
8. Commit the small completed step.
```

Before merging:

```text
1. Run tests.
2. Launch the GUI.
3. Inspect Git diff.
4. Confirm no secrets.
5. Verify main behavior.
6. Update this plan or changelog.
```

Before any live-order test:

```text
1. Confirm account.
2. Confirm symbol.
3. Confirm side.
4. Confirm quantity.
5. Confirm price.
6. Confirm maximum notional.
7. Confirm limit order only.
8. Confirm duplicate protection.
9. Confirm cancel path.
10. Confirm logging.
```

---

## 31. Final Roadmap Summary

```text
PHASE 0
Repository preparation and tests

PHASE 1
Toss market data
Read-only

PHASE 2
Toss account synchronization
Read-only

PHASE 3
Order preview
No broker mutation

PHASE 4
Confirmed semi-automatic order
One button, one confirmation, one order

PHASE 5
Fill detection and recalculation
Automatic detection, manual approval

PHASE 6
Restricted full automation
Deterministic and fail-closed

LATER
Cloud worker and optional FX automation
```

The project’s immediate next objective is:

> **Replace Yahoo Finance with verified Toss market data while leaving all trading actions manual.**

Do not advance to the next phase until the current phase’s acceptance criteria are met.
