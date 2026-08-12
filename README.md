# Should I buy GPIX today?

A tiny GitHub Pages tool that answers one question every weekday morning: is today a
better-or-worse-than-average day to buy [GPIX](https://am.gs.com/en-us/advisors/funds/detail/PV109746/38151J286/goldman-sachs-s-p-500-core-premium-income-etf)
(Goldman Sachs S&P 500 Premium Income ETF)?

A sister page (`gpiq.html`) answers the same question for GPIQ, the Nasdaq-100 version
of the fund. It swaps in Nasdaq-specific inputs - VXN instead of VIX, QQQ instead of
SPY, payout bands calibrated to GPIQ's higher (~10%) distribution - and adds one signal
of its own: the **tech fear premium** (VXN/VIX ratio ranked against its own past year),
which measures when fear is concentrated in tech rather than the broad market.

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
score (GPIX: -7..+14 across ten signals; GPIQ: -8..+16 across eleven) to one of four
honest answers, from "better-than-usual entry" (score >= +5) to "no discount today"
(score <= -2).

## The point of the backtest

The page also runs a running backtest: $100/week bought blindly vs $100/week held in cash
waiting for 3%+ dips, with distributions reinvested. It exists to keep the tool honest —
most of the time, the no-timing strategy wins, and the page says so out loud.

## Score history, report card, alerts

Each daily run appends the day's composite score to a per-fund history file
(`docs/history.json`, `docs/history-gpiq.json`). History was backfilled to each fund's
Oct 2023 inception by replaying today's rules on historical data (no lookahead in the
inputs: trailing percentiles, 52-week highs and TTM payouts all end at each date;
backfilled rows are flagged). CNN's Fear & Greed history only reaches back ~1 year, so
earlier days score that signal neutral.

The history powers three page features:

- **Report card** — for each verdict band, the average/median forward 21-trading-day
  total return of days that got that verdict, vs the all-days baseline, with honest
  caveats about overlapping windows and hindsight-designed rules.
- **Score timeline** — a colored strip under the price chart showing what the page
  would have said each day of the chart window.
- **"What would change this verdict"** — the nearest actionable thresholds (price for a
  real discount, VIX percentile levels, 50-day-average crosses, credit-spread widening,
  Fear & Greed levels) rendered as would-help / would-hurt rows.

`docs/feed.xml` is a combined Atom feed for both funds that only emits an entry when a
fund's verdict band changes from the prior day — subscribe via the "Alerts (RSS)" link
on either page.

## Development

```bash
python3 scripts/build_data.py   # regenerates docs/data.json + docs/data-gpiq.json (stdlib only)
python3 -m http.server -d docs  # view locally at http://localhost:8000
```

Pages serves from the `docs/` folder on `main`. The workflow in
`.github/workflows/update-data.yml` refreshes both data files each weekday at 13:35 UTC.

Not financial advice. Built as a personal decision aid.
