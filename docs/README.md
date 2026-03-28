# AI Seesaw Mini-Calculator

A single-window field tool for manual trading decisions on a 14-stock AI-sector portfolio.

## Quick Start

```bash
pip install yfinance pandas
python main.py
```

## What it does

- Shows **buy triggers** (rescue) for deployed positions and **load triggers** for empty ones
- Shows a **3-tier sell ladder** with configurable profit percentages per tier
- Fetches live prices and the USD/KRW FX rate via `yfinance`
- Persists position state in `data/positions.csv`

## Workflow

1. Launch → prices auto-fetch
2. Click **[Deploy]** on any empty stock to move it into the deployed section
3. Enter **Shares** and **Avg Cost** in the deployed row
4. Set your **Buy Gear** (drop %) and **Sell Tier %s**
5. Click **[Save]** to persist state

## Sell tier tactics

- All 3 tiers active → standard 50% / 25% / 25% ladder
- Deactivate T1 after selling → remaining shares split across T2 / T3
- Activate only one tier → 100% sell at that price
- Adjust any % freely — T1 < T2 < T3 is enforced automatically

## Files

| File | Purpose |
|------|---------|
| `config.json` | N, unit_cash_krw, unit_cash_usd |
| `data/positions.csv` | Per-stock state |
| `docs/MANUAL.md` | Full specification |
| `docs/CHANGELOG.md` | Version history |
