# AI Seesaw Mini-Calculator — Changelog

## v0.3 — 2026-03-28

### Changed
- **Buy ratio per gear**: 4% drop buys 50% of shares, 5% drop buys 60%, 6% drop buys 70%. Labels now show ratio (e.g., "5% drop (×0.6)").
- **Color scheme**: Simplified to red/blue palette. Load gear: blue (cautious) → red (aggressive). Buy gear: blue → red. Sell tier: weak red → strong red. Gap: red for profit, blue for loss.
- **Non-reactive outputs**: Deployed stock outputs (buy trigger, sell tiers, etc.) only update when Save & Refresh is clicked, not on every keystroke.
- **Single Save & Refresh button**: Replaces separate Refresh + Save + per-stock Reset buttons. Setting shares to 0 and clicking Save & Refresh auto-resets stock to empty section.
- **Manual stock ordering**: ▲▼ arrows on each stock row to swap positions. No automatic sorting.
- **5-day close values**: Empty rows display last 5 closing prices below main info line.
- **5-day candle chart**: Graph button on all rows opens popup with candlestick chart, per-day OHLC, and volatility stats.
- **Full labels**: "5D High", "Current", "Sell Tier 1", "Buy Trigger", "Load Target", "Total Cost" etc.
- **Bigger output fonts**: Output values use Segoe UI 10 Bold for readability.
- **Window sizing**: Initial 1280×880 with 1000×600 minimum. Scrollable content area.
- **Refresh timestamp**: Shows full YYYY-MM-DD HH:MM:SS.
- **Data feed optimization**: Single API call per ticker fetches price, 5D closes, and OHLC data.

## v0.2 — 2026-03-28

### Changed
- **Buy gear**: Simplified from three rescue zones (A/B/C at -4/-5/-6%) to a single selectable drop percentage (4%, 5%, or 6%). Fixed proportion of 0.5u regardless of chosen gear.
- **Sell tiers**: Replaced fixed A–E gear presets with three independently configurable tiers. Each tier has its own profit-% spinbox and an activate/deactivate checkbox.
- **Sell quantity split**: Now driven by number of active tiers (1 active → 100%; 2 active → 50%+100%; 3 active → 50%+50%+100%). Enables tactical partial-selling workflow.
- **Deploy / Reset buttons**: Each empty stock has a [Deploy] button; each deployed stock has a [Reset] button. Clicking rebuilds the appropriate section in place.
- **Stock names**: Display now shows full name followed by ticker in parentheses, e.g. "NVIDIA  (NVDA)".
- **% Army**: Deployed section shows each stock's share of total deployed capital (converted to KRW via live FX rate for cross-currency comparison).
- **Sort order**: Deployed stocks sorted by cost-basis descending; empty stocks sorted by load gear (L1→L5) then Major→Minor.
- **Color-coded gear dropdowns**: Load gear and buy gear buttons are colored yellow→orange→red→brown to visually communicate entry depth.
- **CSV schema**: New fields — `is_deployed`, `buy_pct`, `t1_pct`, `t2_pct`, `t3_pct`, `t1_active`, `t2_active`, `t3_active`. Old `buy_gear` (A/B/C) and `sell_gear` (A–E) are auto-migrated on load.

## v0.1 — 2026-03-27

- Initial specification and skeleton implementation.
