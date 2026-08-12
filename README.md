# Should I buy GPIX today?

A tiny GitHub Pages tool that answers one question every weekday morning: is today a
better-or-worse-than-average day to buy [GPIX](https://am.gs.com/en-us/advisors/funds/detail/PV109746/38151J286/goldman-sachs-s-p-500-core-premium-income-etf)
(Goldman Sachs S&P 500 Premium Income ETF)?

## How it decides

A GitHub Action runs every weekday morning, pulls data from Yahoo Finance and Google News,
and scores four transparent signals:

| Signal | Why it matters for a covered-call fund |
| --- | --- |
| VIX level | High volatility = richer option premiums (fatter future distributions) and fearful sellers |
| GPIX vs its 52-week high | Are you buying at a discount or paying full price? |
| GPIX vs its 50-day average | Short-term stretch or weakness |
| S&P 500 vs its 200-day average | Overall market regime |
| Fed calendar (FOMC dates) | Flags imminent rate decisions (informational only) |

The verdict maps the total score to one of four honest answers, ranging from
"better-than-usual entry" to "no discount today."

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
