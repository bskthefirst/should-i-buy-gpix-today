# Should I buy GPIX today?

A tiny GitHub Pages tool that answers one question every weekday morning: is today a
better-or-worse-than-average day to buy [GPIX](https://am.gs.com/en-us/advisors/funds/detail/PV109746/38151J286/goldman-sachs-s-p-500-core-premium-income-etf)
(Goldman Sachs S&P 500 Premium Income ETF)?

## How it decides

A GitHub Action runs every weekday morning, pulls data from Yahoo Finance, FRED, CNN's
Fear & Greed feed, and Google News, and scores ten transparent signals:

| Signal | Source | Why it matters for a covered-call fund |
| --- | --- | --- |
| VIX vs its own past year (percentile) | Yahoo `^VIX` | High volatility relative to its own recent history = richer option premiums and marked-down shares |
| VIX term structure (VIX/VIX3M) | Yahoo `^VIX3M` | An inverted curve means genuine near-term panic - historically a strong covered-call entry |
| Variance risk premium | VIX minus realized SPY vol | Are option buyers overpaying call-sellers like GPIX right now? |
| Discount from 52-week high | Yahoo `GPIX` | Are you buying at a discount or paying full price? |
| GPIX vs its 50-day average | Yahoo `GPIX` | Short-term stretch or weakness |
| S&P 500 vs its 200-day average | Yahoo `SPY` | Overall market regime |
| High-yield credit spread | FRED `BAMLH0A0HYM2` | Junk-bond spreads flag stress before headlines do; extreme wides have marked strong forward returns |
| Payout vs safe cash | Yahoo dividends + FRED `DGS3MO` | GPIX's trailing yield vs 3-month T-bills - how well are you paid for equity risk? |
| Fear & Greed index | CNN | Contrarian sentiment: extreme fear is a buy zone, extreme greed is priced for perfection |
| Event calendar | Fed + BLS schedules | Flags imminent FOMC decisions and CPI prints (informational only) |

Each non-core source is individually guarded - if an endpoint is down, its signal shows
as skipped (score 0) instead of breaking the daily build. The verdict maps the total
score (range roughly -7 to +12) to one of four honest answers, from "better-than-usual
entry" (score >= +5) to "no discount today" (score <= -2).

## The point of the backtest

The page also runs a running backtest: $100/week bought blindly vs $100/week held in cash
waiting for 3%+ dips, with distributions reinvested. It exists to keep the tool honest —
most of the time, the no-timing strategy wins, and the page says so out loud.

## Development

```bash
python3 scripts/build_data.py   # regenerates docs/data.json (stdlib only, no deps)
python3 -m http.server -d docs  # view locally at http://localhost:8000
```

Pages serves from the `docs/` folder on `main`. The workflow in
`.github/workflows/update-data.yml` refreshes `docs/data.json` each weekday at 13:35 UTC.

Not financial advice. Built as a personal decision aid.
