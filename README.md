# Perpetual Funding Rate Screener

Real-time perpetual funding rate comparisons across 11 exchanges for top 20 cryptocurrencies and top 20 RWA tokens.

## Outputs

- **`funding_screener.html`** — Main dashboard showing annualized funding rates per token per exchange
- **`arb_screener.html`** — Arbitrage screener showing best cross-exchange opportunities (maker on short, taker on long, volume incentives included)

## Usage

```bash
python funding_screener.py
```

Generates `funding_screener.html` and `arb_screener.html` in the current directory. No API keys required.

## Exchanges

| Exchange | Status | Notes |
|----------|--------|-------|
| Coinbase Derivatives | Live | CDE-specific codes mapped (CDEUS5→SPX, etc.) |
| Hyperliquid | Live | 234 perp tokens incl. ONDO, PAXG, SPX |
| Kraken | Live | xStocks parsing enabled |
| GRVT | Live | Hourly funding |
| Lighter | Live | 64 symbols |
| Variational | Live | Uses API funding_rate with interval-based annualization |
| Aster | Live | Binance-compatible API |
| Binance | Geo-blocked | HTTP 451 from US IPs |
| Nado | Metadata only | No funding rate via REST API |
| Ondo Perps | Estimated | API 403 → dailyInterestRate × 365. ONDO token excluded. |
| Extended | No funding | 0% maker fee model, no funding mechanism |

## Arbitrage Model

Net APY = (max rate − min rate) − (maker fee + taker fee) + (volume rebates)

- Maker on short leg, taker on long leg
- Volume incentives: assumes ~$1M+ monthly volume for standard rebate tiers
- Only shows |net| ≥ 0.1% to filter noise
